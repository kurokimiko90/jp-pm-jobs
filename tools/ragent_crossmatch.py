#!/usr/bin/env python3
"""Indeed / Green / LinkedIn 職缺 → r-agent 同一求人の突合。見つかれば r-agent 来源へ切替。

Indeed で拾った求人の多くは r-agent にも同じ求人票がある。r-agent 経由で応募すれば
担当 CA が付くので、見つかったものは source / source_id / url を r-agent に差し替える
（id はそのまま＝gap・応募履歴は保たれる）。

判定（自動で切り替えるのは確信が持てるものだけ）:
  1. r-agent マイページ求人検索 `job_search?fw={会社名}` の一覧カードを取得
  2. `normalize_company` が一致するカードだけを候補にする。英語社名（LinkedIn）は
     `_JA_NAMES` で日本語名に引き直し、検索語にも使い、日本語名を**含む**社名を候補にする
     （r-agent はグループ各社・「〇〇ジャパン」表記で出るため完全一致では拾えない）
  3. 候補の標題類似度（`title_similarity`）最大が TITLE_MIN 以上、かつ 2 位と
     MARGIN 以上離れていれば一致。僅差なら年収レンジの重なりで 1 件に絞れた時だけ一致
  4. 一致した r-agent 求人番号が既に DB にある → 重複として報告のみ（書き換えない）
曖昧なものは書き換えずに通知へ回す（人が見て決める）。

CDP: 9270 / ~/.chrome-bizreach（r-agent ログイン済み、ragent_liveness と共用）。

用法:
    python3 -m tools.ragent_crossmatch                 # 今日 first_seen の Indeed 求人
    python3 -m tools.ragent_crossmatch --days 3        # 直近 3 日
    python3 -m tools.ragent_crossmatch --ids 5844,5866
    python3 -m tools.ragent_crossmatch --source green --days 0   # Green 全件
    python3 -m tools.ragent_crossmatch --dry-run       # DB を書かない
    python3 -m tools.ragent_crossmatch --notify        # 結果を Telegram へ
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
from datetime import date, timedelta

from scrapers.recruiter_agent import joboffer_url
from scrapers.ragent_search import _collect_cards, _load_more, _parse_card
from tools.dedup_match import normalize_company, title_similarity
from tools.ragent_liveness import DEFAULT_PORT, DEFAULT_PROFILE, _ensure_cdp
from tracker import db

SEARCH_URL = "https://mypage.r-agent.com/job_search?fw="
TITLE_MIN = 0.55   # これ未満は別求人とみなす
MARGIN = 0.10      # 1 位と 2 位の差がこれ未満なら「僅差」→ 年収で絞る
LOAD_CLICKS = 2    # 一覧「さらに表示する」回数（大企業は求人数が多い）
SOURCES = ("indeed_jp", "green", "linkedin_jp")  # 突合対象にできる来源

# 検索語から落とす法人格（r-agent の全文検索は法人格込みだとヒットが減る）
_CORP_WORDS = re.compile(r"株式会社|有限会社|合同会社|\(株\)|（株）")


# 英語社名 → r-agent 上の日本語社名（の共通部分）。LinkedIn は外資・大手を英語名で
# 載せるが、r-agent は日本法人の日本語名で載せるので `normalize_company` が一致しない。
# 値は検索語、かつ「この語を含む社名」を候補にする。グループ各社が複数当たっても
# 標題類似度で 1 件に絞れなければ要確認に回る（自動切替はしない）。
# tools.dedup_match._COMPANY_ALIASES（DB の重複統合用）とは別物 — あちらを広げると
# 「1 社 1 レコード」の統合範囲まで変わってしまう。
# 2026-10-18 の LinkedIn 新着 86 件から、日本法人が r-agent に載りうる社だけを収録。
_JA_NAMES_RAW = {
    "Panasonic": "パナソニック",
    "Daiichi Sankyo": "第一三共",
    "Hitachi High-Tech Corporation": "日立ハイテク",
    "KPMGジャパン／KPMG Japan": "KPMG",
    "JETRO - Japan External Trade Organization": "日本貿易振興機構",
    "Universal Studios Japan(USJ LLC)": "ユー・エス・ジェイ",
    "スクレッティング株式会社 Skretting Japan": "スクレッティング",
    "Eli Lilly and Company": "イーライリリー",
    "Pfizer": "ファイザー",
    "Johnson & Johnson Innovative Medicine": "ジョンソン・エンド・ジョンソン",
    "Roche": "ロシュ",
    "Merck Life Science": "メルク",
    "UCB": "ユーシービー",
    "IQVIA": "IQVIA",
    "Parexel": "パレクセル",
    "Intuitive": "インテュイティブ",
    "Bank of America": "BofA",
    "State Street": "ステート・ストリート",
    "Thomson Reuters": "トムソン・ロイター",
    "Keysight Technologies": "キーサイト",
    "Micron Technology": "マイクロン",
    "Autodesk": "オートデスク",
    "PTC": "PTCジャパン",
    "SAS": "SAS Institute",
    "Infor": "インフォア",
    "OpenText": "オープンテキスト",
    "BlackLine": "ブラックライン",
    "Cognizant": "コグニザント",
    "McKinsey & Company": "マッキンゼー",
    "Booz Allen Hamilton": "ブーズ・アレン",
    "ASUS": "ASUS",
    "adidas": "アディダス",
    "Giorgio Armani": "ジョルジオ アルマーニ",
    "Tapestry": "タペストリー",
    "Danone": "ダノン",
    "Valeo": "ヴァレオ",
    "Aptiv": "アプティブ",
    "Celanese": "セラニーズ",
}
_JA_NAMES = {normalize_company(en): ja for en, ja in _JA_NAMES_RAW.items()}


def ja_name(company: str) -> str | None:
    return _JA_NAMES.get(normalize_company(company))


def search_keyword(company: str) -> str:
    return ja_name(company) or _CORP_WORDS.sub("", company or "").strip()


def _same_company(job_company: str, card_company: str) -> bool:
    ja = ja_name(job_company)
    if ja:
        return normalize_company(ja) in normalize_company(card_company)
    return normalize_company(job_company) == normalize_company(card_company)


def select_jobs(days: int | None, ids: list[int] | None,
                source: str = "indeed_jp") -> list[dict]:
    """days=0 → first_seen を問わず全件。"""
    if source not in SOURCES:
        raise ValueError(f"source は {SOURCES} のいずれか")
    sql = ("SELECT id, source, company, title, salary_min, salary_max FROM jobs"
           " WHERE source = ? AND COALESCE(blacklisted, 0) = 0")
    params: list = [source]
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params += ids
    elif days:
        sql += " AND first_seen >= ?"
        params.append((date.today() - timedelta(days=days - 1)).isoformat())
    sql += " ORDER BY COALESCE(recommend_score, 0) DESC, score DESC"
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _salary_overlaps(job: dict, card: dict) -> bool:
    a0, a1 = job.get("salary_min"), job.get("salary_max")
    b0, b1 = card.get("salary_min"), card.get("salary_max")
    if None in (a0, b0):
        return False
    return a0 <= (b1 or b0) and b0 <= (a1 or a0)


def pick_match(job: dict, cards: list[dict]) -> tuple[str, dict | None, list[dict]]:
    """→ (status, matched_card, candidates)。status: matched / ambiguous / none"""
    cands = [c for c in cards if _same_company(job["company"], c["company"])]
    if not cands:
        return "none", None, []
    scored = sorted(
        ({**c, "sim": title_similarity(job["title"], c["title"])} for c in cands),
        key=lambda c: c["sim"], reverse=True,
    )
    top = scored[0]
    if top["sim"] < TITLE_MIN:
        return "none", None, scored[:3]
    close = [c for c in scored if top["sim"] - c["sim"] < MARGIN]
    if len(close) == 1:
        return "matched", top, scored[:3]
    by_salary = [c for c in close if _salary_overlaps(job, c)]
    if len(by_salary) == 1:
        return "matched", by_salary[0], scored[:3]
    return "ambiguous", None, close[:3]


def search_cards(page, keyword: str) -> list[dict]:
    page.goto(SEARCH_URL + urllib.parse.quote(keyword),
              wait_until="domcontentloaded", timeout=45000)
    time.sleep(4)
    _load_more(page, LOAD_CLICKS)
    return [c for c in (_parse_card(c) for c in _collect_cards(page)) if c]


def switch_source(conn, job: dict, source_id: str) -> None:
    conn.execute(
        "UPDATE jobs SET source = 'recruiter_agent', source_id = ?, url = ?"
        " WHERE id = ? AND source = ?",
        (source_id, joboffer_url(source_id), job["id"], job["source"]),
    )


def run(jobs: list[dict], dry_run: bool, port: int, profile: str) -> list[dict]:
    from playwright.sync_api import sync_playwright

    results: list[dict] = []
    if not jobs:
        return results
    _ensure_cdp(port, profile)
    cache: dict[str, list[dict]] = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        page = browser.contexts[0].new_page()
        try:
            for job in jobs:
                kw = search_keyword(job["company"])
                if not kw:
                    results.append({**job, "status": "none", "note": "会社名なし"})
                    continue
                try:
                    if kw not in cache:
                        if page.is_closed():
                            # 他プロセスにタブを閉じられると以降が全件失敗するので開き直す
                            page = browser.contexts[0].new_page()
                        cache[kw] = search_cards(page, kw)
                        time.sleep(2)
                except Exception as e:  # 1 社の失敗で全体を止めない
                    print(f"  #{job['id']} 検索失敗: {e}", file=sys.stderr)
                    results.append({**job, "status": "error", "note": str(e)[:120]})
                    continue
                status, card, cands = pick_match(job, cache[kw])
                results.append({**job, "status": status, "card": card, "cands": cands})
                print(f"  #{job['id']} {job['company']} → {status}"
                      + (f" {card['source_id']}" if card else ""), file=sys.stderr)
        finally:
            if not page.is_closed():
                page.close()

    with db.connect() as conn:
        for r in results:
            if r["status"] != "matched":
                continue
            sid = r["card"]["source_id"]
            dup = conn.execute(
                "SELECT id FROM jobs WHERE source = 'recruiter_agent' AND source_id = ?",
                (sid,)).fetchone()
            if dup:
                r["status"], r["note"] = "duplicate", f"既存 #{dup[0]}"
            elif not dry_run:
                switch_source(conn, r, sid)
        if not dry_run:
            conn.commit()
    if not dry_run:
        switched = [(r["id"], r["card"]["source_id"]) for r in results if r["status"] == "matched"]
        fetch_ragent_jds(switched, port)
    return results


def fetch_ragent_jds(targets: list[tuple[int, str]], port: int = DEFAULT_PORT) -> int:
    """切替えた求人の raw_jd を r-agent 詳細ページの本文で置き換える。

    切替前の raw_jd は Indeed 等の転載文のままで、夜間の `recruiter_agent_cdp` 補全は
    300 字未満しか対象にしないため、ここで取らないと r-agent 版の JD が永久に入らない。
    取れなかった求人は元の raw_jd を残す。戻り値は取得できた件数。
    """
    from playwright.sync_api import sync_playwright

    from scrapers.recruiter_agent import _extract_jd_text

    if not targets:
        return 0
    got = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        page = browser.contexts[0].new_page()
        try:
            for job_id, sid in targets:
                try:
                    if page.is_closed():
                        page = browser.contexts[0].new_page()
                    page.goto(joboffer_url(sid), wait_until="domcontentloaded", timeout=25000)
                    jd = _extract_jd_text(page)
                except Exception as e:  # 1 件の失敗で全体を止めない
                    print(f"  #{job_id} JD 取得失敗: {e}", file=sys.stderr)
                    continue
                if len(jd) > 200:
                    db.update_raw_jd(job_id, jd[:5000])
                    got += 1
                    print(f"  #{job_id} r-agent JD {len(jd)} 字", file=sys.stderr)
                else:
                    print(f"  #{job_id} JD 不足 ({len(jd)} 字)、元の JD を残す", file=sys.stderr)
                time.sleep(1.5)
        finally:
            if not page.is_closed():
                page.close()
    return got


def format_report(results: list[dict], dry_run: bool, source: str = "indeed_jp") -> str:
    def line(r: dict) -> str:
        return f"#{r['id']} {r['company']}｜{r['title'][:40]}"

    groups = {k: [r for r in results if r["status"] == k]
              for k in ("matched", "duplicate", "ambiguous", "none", "error")}
    head = (f"{source}→r-agent 突合 {len(results)} 件"
            f"：切替 {len(groups['matched'])} / 重複 {len(groups['duplicate'])}"
            f" / 要確認 {len(groups['ambiguous'])} / 無し {len(groups['none'])}"
            f" / 失敗 {len(groups['error'])}" + ("（dry-run）" if dry_run else ""))
    out = [head]
    if groups["matched"]:
        out.append("\n■ r-agent に切替")
        out += [f"{line(r)} → {r['card']['source_id']}:{r['card']['title'][:40]}"
                f"（類似度 {r['card']['sim']:.2f}）" for r in groups["matched"]]
    if groups["duplicate"]:
        out.append("\n■ r-agent 版が既に DB にある（元の来源側は手動で整理）")
        out += [f"{line(r)}（{r['note']}）" for r in groups["duplicate"]]
    if groups["ambiguous"]:
        out.append("\n■ 候補が複数（要確認）")
        for r in groups["ambiguous"]:
            ids = ", ".join(f"{c['source_id']}:{c['title'][:20]}" for c in r["cands"])
            out.append(f"{line(r)} → {ids}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="indeed_jp", choices=SOURCES)
    ap.add_argument("--days", type=int, default=1,
                    help="first_seen が直近 N 日（既定 1＝今日、0＝全件）")
    ap.add_argument("--ids", help="job_id をカンマ区切りで指定")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--notify", action="store_true", help="結果を Telegram へ送る")
    ap.add_argument("--cdp", type=int, default=DEFAULT_PORT)
    ap.add_argument("--profile-dir", default=DEFAULT_PROFILE)
    args = ap.parse_args()

    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
    jobs = select_jobs(args.days, ids, args.source)
    results = run(jobs, args.dry_run, args.cdp, args.profile_dir)
    report = format_report(results, args.dry_run, args.source)
    print(report)
    if args.notify and results:
        import notify
        notify.send(report, title=f"{args.source}→r-agent")


if __name__ == "__main__":
    main()
