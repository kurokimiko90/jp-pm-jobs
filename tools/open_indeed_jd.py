#!/usr/bin/env python3
"""Indeed 職缺 JD 開啟工具。

CDP 9270 で接続済みブラウザに新タブを開く（パラメータなし）。

用法:
    # DB の job_id 指定
    python3 -m tools.open_indeed_jd 2327 2328

    # スコア範囲で一括開く
    python3 -m tools.open_indeed_jd --score-min 80 --score-max 90

    # 未投遞のみ（デフォルト）
    python3 -m tools.open_indeed_jd --score-min 80 --score-max 90 --all
"""
from __future__ import annotations

import argparse
import sqlite3
import urllib.parse
from pathlib import Path

import requests

CDP_PORT = 9270
DB_PATH = Path(__file__).parent.parent / "data" / "jobs.sqlite"


def _open_via_cdp(urls: list[str]) -> None:
    for url in urls:
        encoded = urllib.parse.quote(url, safe=":/?=&")
        try:
            r = requests.put(f"http://localhost:{CDP_PORT}/json/new?{encoded}", timeout=5)
            data = r.json()
            print(f"✅ {data.get('id','?')[:8]} {url[:80]}")
        except Exception as e:
            print(f"❌ {url[:80]} — {e}")


def _urls_from_job_ids(job_ids: list[int]) -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT url FROM jobs WHERE id IN ({','.join('?'*len(job_ids))}) AND source='indeed_jp' AND url IS NOT NULL",
            job_ids,
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def _urls_by_score(score_min: int, score_max: int, include_applied: bool) -> list[tuple[int, str, str, int, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        if include_applied:
            rows = conn.execute(
                "SELECT id, title, company, score, url FROM jobs "
                "WHERE source='indeed_jp' AND score BETWEEN ? AND ? AND url IS NOT NULL "
                "ORDER BY score DESC",
                (score_min, score_max),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, company, score, url FROM jobs "
                "WHERE source='indeed_jp' AND score BETWEEN ? AND ? AND url IS NOT NULL "
                "AND id NOT IN (SELECT job_id FROM applications) "
                "ORDER BY score DESC",
                (score_min, score_max),
            ).fetchall()
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="Indeed JD を CDP 9270 で開く")
    p.add_argument("job_ids", nargs="*", type=int, help="DB の job_id")
    p.add_argument("--score-min", type=int, help="スコア下限")
    p.add_argument("--score-max", type=int, help="スコア上限")
    p.add_argument("--all", action="store_true", help="投遞済みも含める（デフォルト: 未投遞のみ）")
    args = p.parse_args()

    if not args.job_ids and args.score_min is None:
        p.error("job_id か --score-min を指定")

    urls_to_open: list[str] = []

    if args.job_ids:
        urls_to_open += _urls_from_job_ids(args.job_ids)

    if args.score_min is not None:
        score_max = args.score_max or 100
        rows = _urls_by_score(args.score_min, score_max, include_applied=args.all)
        for job_id, title, company, score, url in rows:
            print(f"  [{score}] {company} — {title[:40]}")
            urls_to_open.append(url)

    seen: set[str] = set()
    unique_urls = [u for u in urls_to_open if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

    if not unique_urls:
        print("開く URL なし")
        return

    print(f"\n{len(unique_urls)} 件を CDP port {CDP_PORT} で開く...")
    _open_via_cdp(unique_urls)


if __name__ == "__main__":
    main()
