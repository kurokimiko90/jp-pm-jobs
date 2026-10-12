"""r-agent の「応募リクエスト」メール → apply_requests に記録（零 LLM 正則）。

応募リクエスト＝r-agent の企業担当（営業）が「ご経歴との親和性が高い」と判断して
個別に送ってくる応募の打診。企業本人からの招待ではない。目的は「リクエストを受けて
応募した求人は、書類選考を通りやすいか」を後から統計で見ること。

1 通 = 1 行（gmail_msg_id で冪等）。応募との対応付けは取り込み時にはしない
（リクエストは応募より先に届く）。統計・一覧側の app_analytics.match_requests が
毎回引くので、後から応募・求人取り込みをしても自動で付く。

過去分の一括取り込み:
    python3 -m inbox.apply_request --days 120 --dry-run
    python3 -m inbox.apply_request --days 120
"""
from __future__ import annotations

import argparse
import re
import unicodedata

from tools.dedup_match import normalize_company
from tracker.db import connect, ensure_apply_requests

# 本人の返信（Re:）は送信者が自分なので from: で自然に落ちる
GMAIL_QUERY = "from:r-agent.com subject:応募リクエスト"

_SUBJECT = re.compile(r"^(?P<company>.+?)への応募リクエスト")
_REPLY_PREFIX = re.compile(r"^\s*(?:re|fwd?|fw)\s*:", re.I)
_OFFER_ID = re.compile(r"r-agent\.com/joboffers/(\d+)")
# 「▼株式会社〇〇\n　◆求人名」— 最初の ▼ ブロックの次行が求人名
_TITLE = re.compile(r"▼[^\n]+\n\s*[◆■]?\s*(?P<title>[^\n]+)")


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def extract(mail: dict) -> dict | None:
    """受信メール → リクエスト情報。応募リクエストの件名でなければ None。"""
    subject = _nfkc(mail.get("subject") or "")
    if _REPLY_PREFIX.match(subject):
        return None
    m = _SUBJECT.match(subject.strip())
    if not m:
        return None
    company = m.group("company").strip()
    body = _nfkc(mail.get("body") or "")
    offer = _OFFER_ID.search(body)
    title = _TITLE.search(body)
    return {
        "gmail_msg_id": mail["gmail_msg_id"],
        "requested_at": mail.get("received_at") or "",
        "company": company,
        "company_norm": normalize_company(company),
        "offer_id": offer.group(1) if offer else None,
        "title": title.group("title").strip() if title else None,
    }


def record(infos: list[dict], dry_run: bool = False) -> list[dict]:
    """apply_requests へ INSERT OR IGNORE。新規行に new=True を付けて返す。"""
    with connect() as conn:
        ensure_apply_requests(conn)
        for info in infos:
            if dry_run:
                info["new"] = not conn.execute(
                    "SELECT 1 FROM apply_requests WHERE gmail_msg_id = ?",
                    (info["gmail_msg_id"],)).fetchone()
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO apply_requests (gmail_msg_id, requested_at, company, "
                "company_norm, offer_id, title) VALUES (?,?,?,?,?,?)",
                (info["gmail_msg_id"], info["requested_at"], info["company"],
                 info["company_norm"], info["offer_id"], info["title"]),
            )
            info["new"] = cur.rowcount == 1
    return infos


def backfill(days: int, max_results: int = 300, dry_run: bool = False) -> list[dict]:
    """受信メールを遡って取り込む。days は必須（別の転職活動期のリクエストを混ぜないため）。"""
    from inbox.fetch import fetch_query

    mails = fetch_query(f"{GMAIL_QUERY} newer_than:{days}d", max_results=max_results)
    infos = sorted((i for i in (extract(m) for m in mails) if i),
                   key=lambda i: i["requested_at"])
    record(infos, dry_run=dry_run)

    print(f"候補 {len(mails)} 通 → 応募リクエスト {len(infos)} 通（直近 {days} 日）")
    for i in infos:
        mark = "✅" if i["new"] else "・"
        print(f"  {mark} {i['requested_at']} #{i['offer_id'] or '-':10} "
              f"{i['company'][:20]:20} {(i['title'] or '')[:30]}")
    new = sum(1 for i in infos if i["new"])
    print(f"\n{'[dry-run] ' if dry_run else ''}新規 {new} 通 / 既存 {len(infos) - new} 通")
    return infos


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="r-agent 応募リクエストメール → apply_requests")
    ap.add_argument("--days", type=int, default=3, help="遡る日数（既定 3、過去分は 120 等）")
    ap.add_argument("--max-results", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true", help="抽出のみ、DB 書き込みなし")
    args = ap.parse_args()
    backfill(args.days, max_results=args.max_results, dry_run=args.dry_run)
