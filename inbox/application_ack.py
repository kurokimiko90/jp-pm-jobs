"""応募受付メール（リクルートエージェント自動返信）→ applications に自動記録。

対応フォーマット（pdt_support@r-agent.com「応募手続きを承りました【リクルートエージェント】」）:

    ▼応募手続き求人
    【企業名】　株式会社ｔｒｕｅｓｔａｒ
    【仕事の名称】　【プロダクトマネージャー】リモート可/フレックス
    【求人No】　K20260322-112-01-027

企業名で jobs を引き当て（company_norm 完全一致 → 職務名類似度でタイブレーク）、
applications に status='applied' / channel='r-agent' / applied_at=受信日 で記録。

非破壊・冪等：
  - 既に応募記録がある job には一切書き込まない（rejected/recruiter を applied に
    巻き戻さない）。同じメールを何度処理しても結果は変わらない。
  - 対応する求人が DB に無い場合は記録せず reason='no_job' を返すだけ（求人を捏造しない）。

過去分の一括取り込み:
    python3 -m inbox.application_ack --days 60 --dry-run
    python3 -m inbox.application_ack --days 60
"""
from __future__ import annotations

import argparse
import re
import unicodedata

from tools.dedup_match import normalize_company, title_similarity
from tracker.db import connect, record_application

# 本文/件名にこれが無ければ応募受付メールではない。日程確定メールも【企業名】
# 【仕事の名称】を持つため、このマーカーで必ずゲートする。
ACK_MARKER = "応募手続きを承りました"

_RE_COMPANY = re.compile(r"【企業名】\s*(.+)")
_RE_TITLE = re.compile(r"【仕事の名称】\s*(.+)")
_RE_JOB_NO = re.compile(r"【求人No】\s*(.+)")

# 応募経路：受付メール自体がリクルートエージェント発なので、求人の初出
# ソース（indeed_jp / green 等）に関わらず応募経路は r-agent。
CHANNEL = "r-agent"


def _nfkc(s: str) -> str:
    """全角社名（株式会社ｔｒｕｅｓｔａｒ）を半角化。末尾の全角スペースも除去。"""
    return unicodedata.normalize("NFKC", s or "").strip()


def extract_ack(mail: dict) -> dict | None:
    """応募受付メールから {company, title, job_no} を抽出。該当しなければ None。"""
    body = mail.get("body") or ""
    subject = mail.get("subject") or ""
    if ACK_MARKER not in subject and ACK_MARKER not in body:
        return None

    comp = _RE_COMPANY.search(body)
    if not comp:
        return None
    title = _RE_TITLE.search(body)
    job_no = _RE_JOB_NO.search(body)
    return {
        "company": _nfkc(comp.group(1)),
        "title": _nfkc(title.group(1)) if title else "",
        "job_no": _nfkc(job_no.group(1)) if job_no else "",
    }


def match_job(company: str, title: str = "") -> int | None:
    """企業名で jobs を引き当て（応募済みに限らず全件が対象）。

    1 社 1 レコード方針（tracker.db.upsert_job）のため通常は company_norm 完全一致で
    一意に決まる。複数残っている場合のみ職務名類似度で選ぶ。
    company_norm が未同期の行に備え、ヒット 0 件のときだけ全件を再正規化して照合する。
    """
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


def apply_ack(mail: dict, dry_run: bool = False) -> dict | None:
    """抽出 + 求人引き当て + applications へ記録。応募受付メールでなければ None。

    戻り値 info: {company, title, job_no, job_id, recorded, reason}
      reason='recorded' 新規記録 / 'already' 既存記録あり（無変更） / 'no_job' 求人未対応
    dry_run=True では判定だけ行い DB へ書き込まない（reason は 'recorded' のまま、
    recorded=False で区別する）。
    """
    info = extract_ack(mail)
    if not info:
        return None

    job_id = match_job(info["company"], info["title"])
    info.update({"job_id": job_id, "recorded": False, "reason": "no_job"})
    if job_id is None:
        return info

    with connect() as conn:
        jrow = conn.execute("SELECT title FROM jobs WHERE id = ?", (job_id,)).fetchone()
        row = conn.execute(
            "SELECT status FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
    info["title_sim"] = round(title_similarity(info["title"], jrow["title"] or ""), 2) if jrow else None
    if row:
        # 既存の選考ステータス（recruiter / rejected 等）を applied に巻き戻さない
        info["reason"] = "already"
        info["status"] = row["status"]
        return info

    info.update({"reason": "recorded", "status": "applied"})
    if dry_run:
        return info

    notes = f"r-agent 応募受付メール自動記録: {info['title']}"
    if info["job_no"]:
        notes += f"（求人No {info['job_no']}）"
    record_application(
        job_id,
        status="applied",
        applied_at=mail.get("received_at") or None,
        notes=notes,
        channel=CHANNEL,
    )
    info["recorded"] = True
    return info


def notify_applied(applied: list[dict]) -> None:
    """自動記録した応募を Telegram 通知（inbox.reply / backfill 共用）。"""
    lines = [
        f"・{a['company']}  {a['title'][:30]}（job#{a['job_id']}）"
        for a in applied
    ]
    try:
        from notify import send
        send("📮 応募を記録しました（r-agent 応募受付メール）\n" + "\n".join(lines))
    except Exception as e:
        print(f"  （応募記録通知略過：{e}）")


def backfill(days: int = 60, max_results: int = 200, dry_run: bool = False) -> list[dict]:
    """Gmail を件名検索して過去の応募受付メールをまとめて取り込む。

    days は必ず指定する（既定 60 日）。過去の別の転職活動期のメールまで遡ると、
    当時応募した会社が今の DB にも存在する場合に誤って応募済みとして記録されるため。
    """
    from inbox.fetch import fetch_query

    q = f'from:r-agent.com subject:"{ACK_MARKER}" newer_than:{days}d'
    mails = fetch_query(q, max_results=max_results)
    print(f"応募受付メール {len(mails)} 封（直近 {days} 日）")

    results: list[dict] = []
    for mail in sorted(mails, key=lambda m: m.get("received_at") or ""):
        info = apply_ack(mail, dry_run=dry_run)
        if not info:
            print(f"  ⚠ 形式不一致: {(mail.get('subject') or '')[:40]}")
            continue
        results.append(info)
        mark = {"recorded": "✅ 新規", "already": "・既存", "no_job": "❓ 求人なし"}[info["reason"]]
        # 1 社 1 レコード方針のため、DB の求人が応募した職種と別ポジションのことがある。
        # 応募した職名は applications.notes に残るが、面接準備で使う raw_jd は別物なので明示。
        alt = " ⚠職名相違" if info.get("title_sim") is not None and info["title_sim"] < 0.5 else ""
        print(f"  {mark} {mail.get('received_at')} {info['company'][:22]:24} "
              f"job#{info['job_id'] or '-'} {info['title'][:30]}{alt}")

    new = [r for r in results if r["reason"] == "recorded"]
    no_job = [r for r in results if r["reason"] == "no_job"]
    print(f"\n{'[dry-run] ' if dry_run else ''}新規記録 {len(new)} 件 / "
          f"既存 {len(results) - len(new) - len(no_job)} 件 / "
          f"求人未対応 {len(no_job)} 件 / 計 {len(results)} 封")

    # 30 分毎の取りこぼし回収でここが 0 でないなら inbox.reply が拾い損ねた分。
    # 通常輪では 0 件＝無通知なので、inbox.reply 側の通知と二重にはならない。
    if new and not dry_run:
        notify_applied(new)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="応募受付メール → applications 一括取り込み")
    ap.add_argument("--days", type=int, default=60, help="遡る日数（既定 60）")
    ap.add_argument("--max-results", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true", help="抽出と引き当てのみ、DB 書き込みなし")
    args = ap.parse_args()
    backfill(days=args.days, max_results=args.max_results, dry_run=args.dry_run)
