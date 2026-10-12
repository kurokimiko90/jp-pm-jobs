#!/usr/bin/env python3
"""r-agent 職缺 JD 抓取工具。

用 tools.open_ragent_jd 相同的方法（Gmail 完整 URL + CDP /json/new 開分頁）
把職缺頁打開，再用 Playwright 連 CDP 讀取內文寫回 DB，最後關閉分頁。

用法:
    # DB の job_id 指定
    python3 -m tools.scrape_ragent_jd 3659 3660 3661

    # 短 JD 批次補全（raw_jd < 300 字，最新 N 筆）
    python3 -m tools.scrape_ragent_jd --short --limit 20
"""
from __future__ import annotations

import argparse
import time
import urllib.parse

import requests

from scrapers.recruiter_agent import _extract_jd_text, _extract_title_from_jd, joboffer_url
from tools.open_ragent_jd import CDP_PORT, DB_PATH
from tracker.db import connect, update_liveness, update_raw_jd

MIN_JD_LEN = 300
CHUNK_SIZE = 5  # 同時開分頁数を抑える — 多いと Playwright の CDP attach がタイムアウトする


def _open_tab(url: str) -> str | None:
    """open_ragent_jd と同じ方法で CDP 9270 に新分頁を開く。成功したら target id を返す。"""
    encoded = urllib.parse.quote(url, safe="")
    try:
        r = requests.put(f"http://localhost:{CDP_PORT}/json/new?{encoded}", timeout=5)
        return r.json().get("id")
    except Exception as e:
        print(f"❌ 開啟失敗 {url[:80]} — {e}")
        return None


def _close_tab(target_id: str) -> None:
    try:
        requests.get(f"http://localhost:{CDP_PORT}/json/close/{target_id}", timeout=5)
    except Exception:
        pass


def _select_jobs(job_ids: list[int], short: bool, limit: int) -> list[dict]:
    with connect() as conn:
        if job_ids:
            rows = conn.execute(
                f"SELECT id, source_id, title, company FROM jobs "
                f"WHERE id IN ({','.join('?' * len(job_ids))}) AND source='recruiter_agent'",
                job_ids,
            ).fetchall()
        elif short:
            rows = conn.execute(
                "SELECT id, source_id, title, company FROM jobs "
                "WHERE source='recruiter_agent' AND length(COALESCE(raw_jd,'')) < ? "
                "AND liveness_status IS NOT 'jd_failed' "
                "ORDER BY id DESC LIMIT ?",
                (MIN_JD_LEN, limit),
            ).fetchall()
        else:
            rows = []
    return [dict(r) for r in rows]


def run(job_ids: list[int], short: bool, limit: int) -> int:
    jobs = _select_jobs(job_ids, short, limit)
    if not jobs:
        print("対象なし")
        return 0

    from playwright.sync_api import sync_playwright

    scraped = 0
    for i in range(0, len(jobs), CHUNK_SIZE):
        chunk = jobs[i:i + CHUNK_SIZE]

        # 1) このチャンク分だけ分頁を開く（open_ragent_jd と同じ CDP /json/new 方式）
        to_process = []
        for job in chunk:
            sid = job["source_id"]
            if not sid:
                print(f"⚠️  [{job['id']}] {job['company']} — source_id なし（skip）")
                update_liveness(job["id"], "jd_failed")
                continue
            target_id = _open_tab(joboffer_url(sid))
            if target_id:
                to_process.append({**job, "target_id": target_id})
            time.sleep(0.3)

        if not to_process:
            continue

        time.sleep(3)

        # 2) 分頁が全部存在する状態で新規接続 → ctx.pages に確実に載る
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
            ctx = browser.contexts[0]

            for job in to_process:
                sid = job["source_id"]
                page = next((pg for pg in ctx.pages if sid in pg.url), None)
                if not page:
                    print(f"❌ [{job['id']}] {job['company']} — 分頁が見つからない（skip）")
                    update_liveness(job["id"], "jd_failed")
                    continue

                jd_text = _extract_jd_text(page)
                if jd_text and len(jd_text) > 200:
                    update_raw_jd(job["id"], jd_text[:5000])
                    scraped += 1
                    if job["title"] == job["company"]:
                        extracted = _extract_title_from_jd(jd_text, job["company"])
                        if extracted:
                            with connect() as c:
                                c.execute("UPDATE jobs SET title=? WHERE id=?", (extracted, job["id"]))
                    print(f"✅ [{job['id']}] {job['company'][:25]} ({len(jd_text)} chars)")
                    if len(jd_text) < MIN_JD_LEN:
                        # 頁面本身內容就短，不會再變長 — 標記避免每批重選造成死循環
                        update_liveness(job["id"], "jd_failed")
                else:
                    print(f"⚠️  [{job['id']}] {job['company'][:25]} — JD 太短/取得失敗（skip）")
                    update_liveness(job["id"], "jd_failed")

                _close_tab(job["target_id"])

            browser.close()

    return scraped


def main() -> None:
    p = argparse.ArgumentParser(description="r-agent JD を CDP 9270 で開いて抓取")
    p.add_argument("job_ids", nargs="*", type=int, help="DB の job_id")
    p.add_argument("--short", action="store_true", help="raw_jd が短い職缺を自動選択")
    p.add_argument("--limit", type=int, default=20, help="--short 使用時の最大件数（default 20）")
    args = p.parse_args()

    if not args.job_ids and not args.short:
        p.error("job_id または --short のいずれかを指定")

    n = run(args.job_ids, args.short, args.limit)
    print(f"\n完了：{n} 筆 JD 更新")


if __name__ == "__main__":
    main()
