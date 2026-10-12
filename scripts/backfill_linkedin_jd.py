#!/usr/bin/env python3
"""Backfill raw_jd for LinkedIn jobs missing JD detail."""

import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))
from scrapers.linkedin_jp import _fetch_jd_detail, polite_sleep
from tracker.db import DB_PATH, connect

CDP_PORT = 9253


def main() -> None:
    rows = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, url FROM jobs WHERE source = 'linkedin_jp' "
            "AND (raw_jd IS NULL OR raw_jd = '') ORDER BY id"
        ).fetchall()

    if not rows:
        print("全部 LinkedIn 職缺都有 JD，不需 backfill")
        return

    print(f"需 backfill: {len(rows)} 筆")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        ctx = browser.contexts[0]
        page = ctx.new_page()
        updated = 0
        for i, row in enumerate(rows):
            jd_body, salary_hint = _fetch_jd_detail(page, row["url"])
            if not jd_body:
                print(f"  [{i+1}/{len(rows)}] id={row['id']} — 無法取得 JD")
                continue
            raw_jd = jd_body
            if salary_hint and salary_hint not in jd_body:
                raw_jd = f"【年収】{salary_hint}\n\n{jd_body}"
            with connect() as conn:
                conn.execute(
                    "UPDATE jobs SET raw_jd = ? WHERE id = ?",
                    (raw_jd, row["id"]),
                )
            updated += 1
            print(f"  [{i+1}/{len(rows)}] id={row['id']} ✓ ({len(raw_jd)} chars)")
        page.close()
        browser.close()

    print(f"\nBackfill 完成: {updated}/{len(rows)} 筆已更新")
    print("跑 python3 -m tools.salary_parser 重新解析薪資")


if __name__ == "__main__":
    main()
