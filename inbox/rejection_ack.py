"""選考結果（不合格）メール → applications に自動記録。

対応フォーマット（r-agent キャリアアドバイザーからの定型連絡、実メール実測）:

    件名: 書類選考結果のご連絡  ← 書類選考段階の不合格
    件名: 選考結果のご連絡      ← 面接以降の段階の不合格（段位は本文に明記されない）

    大変恐れ入りますが今回はご期待に添えない結果となりました。

    【企業名】　株式会社ｔｒｕｅｓｔａｒ
    【仕事の名称】【プロダクトマネージャー】リモート可/フレックス
    【理由】　　　今までのご経験とスキルが企業側の求めるものと若干異なったため

1 通に複数社（【企業名】ブロック）が並ぶダイジェスト形式が多いため findall で全ブロック抽出。
企業名で jobs を引き当て（application_ack.py と同じ normalize_company + title_similarity）、
applications に status='rejected' で記録。rejection_stage は件名で判定
（書類選考結果のご連絡→shorui／選考結果のご連絡→None＝段位不明、捏造しない）。

冪等：既に status='rejected' の job は素通り（同じ内容を毎輪処理しても再通知しない）。
既存の応募記録（applied 等）は rejected に上書きする（これが本モジュールの目的）。
対応する求人が DB に無い場合は記録せず reason='no_job' を返すだけ（求人を捏造しない）。

過去分の一括取り込み:
    python3 -m inbox.rejection_ack --days 60 --dry-run
    python3 -m inbox.rejection_ack --days 60
"""
from __future__ import annotations

import argparse
import re
import unicodedata

from tools.dedup_match import normalize_company, title_similarity
from tracker.db import connect, record_application

# 件名マーカー：「書類選考結果のご連絡」は「選考結果のご連絡」を部分文字列として含むため
# 判定順が重要（application_ack.py の ACK_MARKER 優先順と同じ考え方）。
SUBJECT_SHORUI = "書類選考結果のご連絡"
SUBJECT_GENERIC = "選考結果のご連絡"

# dashboard/backend/applications.py の REJECTION_STAGES と一致させること。
STAGE_SHORUI = "shorui"

CHANNEL = "r-agent"

# 【企業名】ブロックを複数抽出。【理由】は改行を跨ぐことがあるため、次の【企業名】か
# 「残念ですが」「----」区切り線か本文末尾までを理由として拾う（re.DOTALL で複数行可）。
_RE_ENTRY = re.compile(
    r"【企業名】\s*(?P<company>.+?)\s*\n"
    r"【仕事の名称】\s*(?P<title>.+?)\s*\n"
    r"【理由】\s*(?P<reason>.+?)"
    r"(?=\n\s*【企業名】|\n\s*残念ですが|\n\s*-{5,}|\Z)",
    re.S,
)


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


def _detect_stage(subject: str, body: str) -> tuple[bool, str | None]:
    """(是否為不合格通知信, rejection_stage) 。非該類信時第一項為 False。"""
    text = f"{subject}\n{body[:200]}"
    if SUBJECT_SHORUI in text:
        return True, STAGE_SHORUI
    if SUBJECT_GENERIC in text:
        return True, None  # 面接以降の不合格だが本文に段位が無いため断定しない
    return False, None


def extract_rejections(mail: dict) -> list[dict] | None:
    """不合格メールから [{company, title, reason}] を抽出。該当しなければ None。"""
    subject = mail.get("subject") or ""
    body = mail.get("body") or ""
    is_rejection, stage = _detect_stage(subject, body)
    if not is_rejection:
        return None

    entries = []
    for m in _RE_ENTRY.finditer(body):
        entries.append({
            "company": _nfkc(m.group("company")),
            "title": _nfkc(m.group("title")),
            "reason": _nfkc(m.group("reason")),
            "stage": stage,
        })
    return entries or None  # 件名だけ一致してブロック抽出0件＝想定外フォーマット、記録しない


def match_job(company: str, title: str = "") -> int | None:
    """企業名で jobs を引き当て（application_ack.py と同ロジック）。"""
    cnorm = normalize_company(company)
    if not cnorm:
        return None
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title FROM jobs WHERE company_norm = ?", (cnorm,)
        ).fetchall()
        if not rows:
            rows = [
                r for r in conn.execute("SELECT id, title, company FROM jobs").fetchall()
                if normalize_company(r["company"] or "") == cnorm
            ]
    if not rows:
        return None
    if len(rows) == 1 or not title:
        return rows[0]["id"]
    return max(rows, key=lambda r: title_similarity(title, r["title"] or ""))["id"]


def apply_rejection(mail: dict, dry_run: bool = False) -> list[dict]:
    """抽出 + 求人引き当て + applications へ記録。不合格メールでなければ空リスト。

    各要素 info: {company, title, reason, stage, job_id, recorded, reason_code}
      reason_code='recorded' 新規記録 / 'already' 既に rejected（無変更） / 'no_job' 求人未対応
    """
    entries = extract_rejections(mail)
    if not entries:
        return []

    results = []
    for e in entries:
        job_id = match_job(e["company"], e["title"])
        info = dict(e, job_id=job_id, recorded=False, reason_code="no_job")
        if job_id is None:
            results.append(info)
            continue

        with connect() as conn:
            jrow = conn.execute("SELECT title FROM jobs WHERE id = ?", (job_id,)).fetchone()
            row = conn.execute(
                "SELECT status FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
        info["title_sim"] = round(title_similarity(e["title"], jrow["title"] or ""), 2) if jrow else None

        if row and row["status"] == "rejected":
            info["reason_code"] = "already"
            results.append(info)
            continue

        info["reason_code"] = "recorded"
        if dry_run:
            results.append(info)
            continue

        notes = f"r-agent 不合格メール自動記録: {e['title']}（理由: {e['reason']}）"
        record_application(
            job_id,
            status="rejected",
            applied_at=None if row else (mail.get("received_at") or None),
            notes=notes,
            channel=CHANNEL,
            last_updated=mail.get("received_at") or None,
            rejection_stage=e["stage"],
        )
        info["recorded"] = True
        results.append(info)
    return results


def notify_rejected(rejected: list[dict]) -> None:
    """自動記録した不合格を Telegram 通知（inbox.reply / backfill 共用）。"""
    lines = [
        f"・{r['company']}  {r['title'][:30]}（job#{r['job_id']}）— {r['reason'][:40]}"
        for r in rejected
    ]
    try:
        from notify import send
        send("📪 不合格を記録しました（r-agent 選考結果メール）\n" + "\n".join(lines))
    except Exception as e:
        print(f"  （不合格記録通知略過：{e}）")


def backfill(days: int = 60, max_results: int = 200, dry_run: bool = False) -> list[dict]:
    """Gmail を件名検索して過去の不合格メールをまとめて取り込む。

    days は必ず指定する（既定 60 日）。過去の別の転職活動期のメールまで遡ると、
    当時応募した会社が今の DB にも存在する場合に誤って不合格記録される恐れがあるため。
    """
    from inbox.fetch import fetch_query

    q = (
        f'from:r-agent.com (subject:"{SUBJECT_SHORUI}" OR subject:"{SUBJECT_GENERIC}") '
        f'newer_than:{days}d'
    )
    mails = fetch_query(q, max_results=max_results)
    print(f"不合格メール {len(mails)} 封（直近 {days} 日）")

    all_results: list[dict] = []
    for mail in sorted(mails, key=lambda m: m.get("received_at") or ""):
        infos = apply_rejection(mail, dry_run=dry_run)
        if not infos:
            print(f"  ⚠ 形式不一致/ブロック抽出0件: {(mail.get('subject') or '')[:40]}")
            continue
        all_results.extend(infos)
        for info in infos:
            mark = {"recorded": "✅ 新規", "already": "・既存", "no_job": "❓ 求人なし"}[info["reason_code"]]
            alt = " ⚠職名相違" if info.get("title_sim") is not None and info["title_sim"] < 0.5 else ""
            print(f"  {mark} {mail.get('received_at')} {info['company'][:22]:24} "
                  f"job#{info['job_id'] or '-'} {info['title'][:30]}{alt}")

    new = [r for r in all_results if r["reason_code"] == "recorded"]
    no_job = [r for r in all_results if r["reason_code"] == "no_job"]
    print(f"\n{'[dry-run] ' if dry_run else ''}新規記録 {len(new)} 件 / "
          f"既存 {len(all_results) - len(new) - len(no_job)} 件 / "
          f"求人未対応 {len(no_job)} 件 / 計 {len(all_results)} 件（{len(mails)} 封）")

    if new and not dry_run:
        notify_rejected(new)
    return all_results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="不合格メール → applications 一括取り込み")
    ap.add_argument("--days", type=int, default=60, help="遡る日数（既定 60）")
    ap.add_argument("--max-results", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true", help="抽出と引き当てのみ、DB 書き込みなし")
    args = ap.parse_args()
    backfill(days=args.days, max_results=args.max_results, dry_run=args.dry_run)
