"""送信済みの年収回答メール → salary_replies に記録（零 LLM 正則）。

エージェント（r-agent「年収確認のお願い」「事前確認依頼」等）から希望年収を聞かれ、
本人が返信したメールを Gmail の「送信済み」から拾い、回答額を応募単位で残す。
目的は「年収を答えた後、書類選考を通ったか」を後から統計で見ること。

1 通 = 1 行（gmail_msg_id で冪等）。同じ企業へ何度答えても全部残し、統計側で
「書類選考の結果が出る前の最初の回答」だけを使う。

stage（回答した時点の選考段階）:
  shorui    — 書類の結果が出る前（推薦前確認・年収確認のお願い等）＝統計の対象
  interview — 面接段階に入った後（面接前アンケート・面接後確認等）
  offer     — 内定後の条件交渉
判定は件名の語 → application_events（回答日より前に面接段階へ進んでいたか）の順。

過去分の一括取り込み:
    python3 -m inbox.salary_reply --days 120 --dry-run
    python3 -m inbox.salary_reply --days 120
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata

from inbox.application_ack import match_job
from tracker.db import connect, ensure_salary_replies

# 送信済みから候補を絞る検索語。本文の数値抽出で最終判定するので広めでよい。
GMAIL_QUERY = "in:sent (年収 OR 年俸)"

# 返信本文の引用部の開始（Gmail 日本語 / 英語 UI、転送）
_QUOTE_START = re.compile(
    r"^(?:>|\d{4}年\d{1,2}月\d{1,2}日.*[:：]\s*$|On .+wrote:\s*$|-{3,}\s*(?:Original|Forwarded))",
    re.M,
)

# ラベル → 数値（「万」か【】閉じが必須：日付や件数を拾わない）。最低系を先に置き、
# 「最低希望年収」が「希望年収」として二重に拾われないようにする（finditer は左から消費）。
_AMOUNT = re.compile(
    r"(?P<label>"
    r"(?:辞退しない)?最低[^\n：:【]{0,12}?年収|最低確保したい年収|"
    r"本音希望年収|希望年収|現職?年収|現在年収|"
    r"(?<![^\s・■◇\-])(?:最低|希望)(?=\s*[：:])"
    r")"
    r"\s*[：:]?\s*"
    r"(?:【[^】\d]*】)?"          # 【最低希望年収を記入】のような記入欄の残骸
    r"[【\s]*約?\s*"
    r"(?P<num>\d{3,4})\s*(?:】?\s*万|】)"   # 「【900】」だけの回答（単位省略）も拾う
)

# 件名の語で段階を決める（events より優先：面接前アンケートは events 上まだ applied のことがある）
_OFFER_WORDS = ("内定", "オファー", "条件面談")
_INTERVIEW_WORDS = ("面接", "最終選考", "選考辞退", "面談")
# 面接段階以降の status（application_events）
_POST_SHORUI = ("casual", "recruiter", "tech", "onsite", "offer")


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


_CORP_TOKEN = re.compile(r"(?:株式会社|\(株\))\s?[^\s(]+|[^\s)]+(?:株式会社|\(株\))")
_CORP_MARK = re.compile(r"株式会社|\(株\)|有限会社|合同会社|社$|Inc|Ltd", re.I)


def own_text(body: str) -> str:
    """返信本文から引用部を落とし、本人が書いた部分だけを返す。"""
    m = _QUOTE_START.search(body or "")
    return (body or "")[: m.start()] if m else (body or "")


def _kind(label: str) -> str:
    if "最低" in label:
        return "minimum"
    if label.startswith("現"):
        return "current"
    return "desired"


def parse_amounts(text: str) -> dict:
    """本文から {desired, minimum, current}（万円 int / None）を抽出。各項目は最初の出現。"""
    out: dict = {"desired": None, "minimum": None, "current": None}
    for m in _AMOUNT.finditer(_nfkc(text)):
        kind = _kind(m.group("label"))
        if out[kind] is None:
            out[kind] = int(m.group("num"))
    return out


def company_candidates(subject: str) -> list[str]:
    """件名から社名らしき断片を抽出（照合は呼び出し側）。

    例: 「Re: 【8/25〆 年収確認のお願い】 (株)サンプル/山田 太郎様 【リクルートエージェント】」
        → ["(株)サンプル"]
        「Re: ≪9/16 正午〆：事前確認依頼≫サンプルホールディングス社」→ ["サンプルホールディングス"]
    """
    s = _nfkc(subject)
    s = re.sub(r"^(?:\s*(?:re|fwd?|fw)\s*:\s*)+", "", s, flags=re.I)
    s = re.sub(r"[◆※★☆][^◆※★☆]{0,30}[◆※★☆]", " ", s)
    s = re.sub(r"【[^】]*】|≪[^≫]*≫|<<[^>]*>>|\[[^\]]*\]", " ", s)
    out = []
    for part in re.split(r"[/／]", s):
        part = part.strip()
        if not part or "様" in part or "リクルートエージェント" in part:
            continue
        # 「◯◯社」の敬称的な 社 を落とす（正規化は 社 を社名の一部として残すため）。
        # 株式会社・合同会社の 社 は落とさない
        out.append(re.sub(r"(?<=[^\s会])社$", "", part).strip())
        # 「面接終了後に… 株式会社サンプル 面接日時…」のように社名が文中に埋まる件名用
        out += _CORP_TOKEN.findall(part)
    return list(dict.fromkeys(p for p in out if p))


def _prefer_applied(job_id: int) -> int:
    """同じ会社の求人が複数あるとき、応募記録のある方へ寄せる。

    match_job は件名に職種名が無いと先頭の 1 件を返すので、応募していない
    別ポジション（LinkedIn で拾った同社の別求人等）に年収回答が付いてしまう。
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT a.job_id FROM jobs j JOIN jobs k ON k.company_norm = j.company_norm "
            "JOIN applications a ON a.job_id = k.id WHERE j.id = ? "
            "ORDER BY a.job_id = ? DESC, a.applied_at DESC LIMIT 1",
            (job_id, job_id),
        ).fetchone()
    return row["job_id"] if row else job_id


def _match(subject: str) -> tuple[int | None, str | None]:
    """件名の断片を順に jobs へ照合。最初に当たった (job_id, 社名) を返す。"""
    cands = company_candidates(subject)
    for c in cands:
        job_id = match_job(c)
        if job_id:
            return _prefer_applied(job_id), c
    # 求人に当たらなくても社名らしい断片は残す（「面談のお願い」等の件名断片は残さない）
    named = [c for c in cands if _CORP_MARK.search(c)]
    return None, (named[0] if named else None)


def decide_stage(subject: str, job_id: int | None, replied_at: str,
                 conn: sqlite3.Connection) -> str:
    s = _nfkc(subject)
    if any(w in s for w in _OFFER_WORDS):
        return "offer"
    if any(w in s for w in _INTERVIEW_WORDS):
        return "interview"
    if job_id is not None:
        row = conn.execute(
            "SELECT 1 FROM application_events WHERE job_id = ? AND date(changed_at) < ? "
            f"AND status IN ({','.join('?' * len(_POST_SHORUI))}) LIMIT 1",
            (job_id, replied_at, *_POST_SHORUI),
        ).fetchone()
        if row:
            return "interview"
    return "shorui"


def extract(mail: dict) -> dict | None:
    """送信メール → 回答情報。希望 / 最低のどちらも拾えなければ None（年収回答ではない）。"""
    amounts = parse_amounts(own_text(mail.get("body") or ""))
    if amounts["desired"] is None and amounts["minimum"] is None:
        return None
    job_id, company = _match(mail.get("subject") or "")
    return {
        "gmail_msg_id": mail["gmail_msg_id"],
        "replied_at": mail.get("received_at") or "",
        "subject": mail.get("subject") or "",
        "job_id": job_id, "company": company,
        "desired_man": amounts["desired"], "minimum_man": amounts["minimum"],
        "current_man": amounts["current"],
    }


def record(infos: list[dict], dry_run: bool = False) -> list[dict]:
    """stage を判定して salary_replies へ INSERT OR IGNORE。新規行に new=True を付けて返す。"""
    with connect() as conn:
        ensure_salary_replies(conn)
        for info in infos:
            info["stage"] = decide_stage(
                info["subject"], info["job_id"], info["replied_at"], conn)
            if dry_run:
                info["new"] = not conn.execute(
                    "SELECT 1 FROM salary_replies WHERE gmail_msg_id = ?",
                    (info["gmail_msg_id"],)).fetchone()
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO salary_replies (gmail_msg_id, job_id, company, "
                "replied_at, stage, desired_man, minimum_man, current_man) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (info["gmail_msg_id"], info["job_id"], info["company"], info["replied_at"],
                 info["stage"], info["desired_man"], info["minimum_man"], info["current_man"]),
            )
            info["new"] = cur.rowcount == 1
    return infos


def backfill(days: int, max_results: int = 300, dry_run: bool = False) -> list[dict]:
    """送信済みを遡って取り込む。days は必須（別の転職活動期の回答を混ぜないため）。"""
    from inbox.fetch import fetch_query

    mails = fetch_query(f"{GMAIL_QUERY} newer_than:{days}d", max_results=max_results)
    infos = [i for i in (extract(m) for m in mails) if i]
    infos.sort(key=lambda i: i["replied_at"])
    record(infos, dry_run=dry_run)

    print(f"送信済み候補 {len(mails)} 通 → 年収回答 {len(infos)} 通（直近 {days} 日）")
    for i in infos:
        amt = "/".join(str(v) if v else "-" for v in
                       (i["desired_man"], i["minimum_man"], i["current_man"]))
        mark = "✅" if i["new"] else "・"
        job = f"job#{i['job_id']}" if i["job_id"] else "❓求人なし"
        print(f"  {mark} {i['replied_at']} {i['stage']:9} {job:10} "
              f"希望/最低/現 {amt:13} {(i['company'] or '')[:24]}")
    new = sum(1 for i in infos if i["new"])
    print(f"\n{'[dry-run] ' if dry_run else ''}新規 {new} 通 / 既存 {len(infos) - new} 通")
    return infos


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="送信済みの年収回答メール → salary_replies")
    ap.add_argument("--days", type=int, default=3, help="遡る日数（既定 3、過去分は 120 等）")
    ap.add_argument("--max-results", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true", help="抽出と照合のみ、DB 書き込みなし")
    args = ap.parse_args()
    backfill(args.days, max_results=args.max_results, dry_run=args.dry_run)
