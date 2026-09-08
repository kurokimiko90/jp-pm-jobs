"""JD 補抓階段 — 對缺 raw_jd 的職位點進詳情頁抓全文，回寫 DB 後重新評分。

用法：
    python3 fetch_jd.py                      # linkedin_jp + bizreach，全部缺 JD 的
    python3 fetch_jd.py --source linkedin_jp # 只補 LinkedIn
    python3 fetch_jd.py --limit 30           # 每源最多補 30 筆（測試用）
    python3 fetch_jd.py --no-rescore         # 只補 JD，不重跑評分

CDP 連線沿用 scrape.py 的既登錄 Chrome：
    linkedin_jp → port 9253（chatgpt4 profile）
    bizreach    → port 9270（Profile 2 / bizreach profile）
"""

import argparse

from playwright.sync_api import sync_playwright

from scrapers.jd_fetch import fetch_jd
from scrapers._common import polite_sleep
from tracker.db import connect, init_db, upsert_job

# 來源 → 已登錄 Chrome 的 CDP port
SOURCE_CDP_PORT = {
    "linkedin_jp": 9253,
    "bizreach": 9270,
}


def jobs_missing_jd(source: str, limit: int | None) -> list[dict]:
    """回傳該來源缺 raw_jd 的職位（id, source, source_id, url, title）。"""
    sql = (
        "SELECT id, source, source_id, url, title FROM jobs "
        "WHERE source = ? AND (raw_jd IS NULL OR length(raw_jd) < 50) "
        "ORDER BY id"
    )
    params: list = [source]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def backfill_source(source: str, limit: int | None) -> int:
    """補抓單一來源的 JD。回傳成功補抓筆數。"""
    port = SOURCE_CDP_PORT.get(source)
    if not port:
        print(f"[{source}] 無對應 CDP port，跳過")
        return 0

    targets = jobs_missing_jd(source, limit)
    if not targets:
        print(f"[{source}] 無缺 JD 的職位")
        return 0

    print(f"\n[{source}] 待補 {len(targets)} 筆，連線 CDP port={port}")
    filled = 0
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
        except Exception as e:
            print(f"[{source}] CDP 連線失敗（Chrome 沒開？）：{type(e).__name__}")
            return 0
        page = browser.contexts[0].new_page()
        for i, job in enumerate(targets, 1):
            jd = fetch_jd(page, job["url"])
            if jd:
                # upsert_job 用 COALESCE，只填空的 raw_jd；附原有欄位避免覆蓋
                upsert_job({
                    "source": job["source"],
                    "source_id": job["source_id"],
                    "url": job["url"],
                    "title": job["title"],
                    "raw_jd": jd,
                })
                filled += 1
            print(f"  [{source}] {i}/{len(targets)} {'✓' if jd else '✗'} {job['title'][:35]}")
            if i < len(targets):
                polite_sleep(2, 5)
        page.close()
    print(f"[{source}] 完成：補抓 {filled}/{len(targets)} 筆")
    return filled


def rescore() -> None:
    """重跑分類 / 薪資 / 評分 / 報告（與 scrape.py 後處理一致）。"""
    print("\n========\n[重新評分] 分類 / 薪資 / 評分")
    from tools.jd_tier_classifier import classify_all
    from tools.salary_parser import parse_all as parse_all_salaries
    from analyzer.jd_scorer import score_all, write_report

    classify_all()
    parse_all_salaries()
    score_all()
    write_report()


def main() -> None:
    parser = argparse.ArgumentParser(description="JD 補抓階段")
    parser.add_argument(
        "--source", nargs="+", default=list(SOURCE_CDP_PORT.keys()),
        choices=list(SOURCE_CDP_PORT.keys()),
    )
    parser.add_argument("--limit", type=int, default=None, help="每源最多補抓筆數")
    parser.add_argument("--no-rescore", action="store_true", help="只補 JD，不重跑評分")
    args = parser.parse_args()

    init_db()
    total = 0
    for source in args.source:
        try:
            total += backfill_source(source, args.limit)
        except Exception as e:
            print(f"[{source}] 異常：{type(e).__name__}: {e}")

    print(f"\n========\n共補抓 {total} 筆 JD")
    if total and not args.no_rescore:
        rescore()


if __name__ == "__main__":
    main()
