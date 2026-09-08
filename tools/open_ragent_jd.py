#!/usr/bin/env python3
"""r-agent 職缺 JD 開啟工具。

r-agent の /joboffers/{id} は裸 URL では必ずエラー。
`scrapers.recruiter_agent.joboffer_url()`（流入元タグ付き）で CDP 9270 に開く。
Gmail を読むのは `--new-offers`（未登録オファーの列挙）だけ。

用法:
    # DB の job_id 指定
    python3 -m tools.open_ragent_jd 2801 2813

    # source_id (mypage の数字) 指定
    python3 -m tools.open_ragent_jd --source-id 107849810 107935261

    # 未登録オファーメール（本地庫にない分）を全部開く
    python3 -m tools.open_ragent_jd --new-offers
"""
from __future__ import annotations

import argparse
import sqlite3
import urllib.parse
from pathlib import Path

import requests

CDP_PORT = 9270
DB_PATH = Path(__file__).parent.parent / "data" / "jobs.sqlite"


def _get_full_urls(days: int = 120, max_results: int = 300) -> dict[str, str]:
    """Gmail から source_id → full URL（query token 付き）を返す。"""
    from scrapers.recruiter_agent import _collect_full_urls
    return _collect_full_urls(days=days, max_results=max_results)


def _open_via_cdp(urls: list[str]) -> None:
    for url in urls:
        encoded = urllib.parse.quote(url, safe="")
        try:
            r = requests.put(f"http://localhost:{CDP_PORT}/json/new?{encoded}", timeout=5)
            data = r.json()
            print(f"✅ 開啟 {data.get('id','?')[:8]} {url[:80]}")
        except Exception as e:
            print(f"❌ 失敗 {url[:80]} — {e}")


def _source_ids_from_job_ids(job_ids: list[int]) -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"SELECT source_id FROM jobs WHERE id IN ({','.join('?'*len(job_ids))}) AND source='recruiter_agent'",
            job_ids,
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def _registered_source_ids() -> set[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT source_id FROM jobs WHERE source='recruiter_agent'"
        ).fetchall()
    return {r[0] for r in rows}


def main() -> None:
    p = argparse.ArgumentParser(description="r-agent JD を CDP 9270 で開く")
    p.add_argument("job_ids", nargs="*", type=int, help="DB の job_id")
    p.add_argument("--source-id", nargs="+", help="mypage の source_id (数字)")
    p.add_argument("--new-offers", action="store_true", help="本地庫未登録のオファーを全部開く")
    p.add_argument("--days", type=int, default=120, help="Gmail 回溯日数（default 120）")
    args = p.parse_args()

    if not any([args.job_ids, args.source_id, args.new_offers]):
        p.error("job_id / --source-id / --new-offers のいずれかを指定")

    target_sids: list[str] = []

    if args.job_ids:
        target_sids += _source_ids_from_job_ids(args.job_ids)

    if args.source_id:
        target_sids += list(args.source_id)

    if args.new_offers:
        # 未登録オファーの「存在」はメールでしか分からないので、ここだけ Gmail を読む
        print("Gmail から未登録オファーを列挙中...")
        registered = _registered_source_ids()
        target_sids += [sid for sid in _get_full_urls(days=args.days) if sid not in registered]

    # deduplicate, preserve order
    seen: set[str] = set()
    unique_sids = [s for s in target_sids if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]

    if not unique_sids:
        print("対象 source_id なし")
        return

    from scrapers.recruiter_agent import joboffer_url
    urls_to_open = [joboffer_url(sid) for sid in unique_sids]

    print(f"{len(urls_to_open)} 件を CDP port {CDP_PORT} で開く...")
    _open_via_cdp(urls_to_open)


if __name__ == "__main__":
    main()
