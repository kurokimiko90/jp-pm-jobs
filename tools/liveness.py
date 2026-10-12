#!/usr/bin/env python3
"""職缺存活檢測 — 批次檢查 jobs.db 中的 URL 是否仍有效。

Zero-token：純 HTTP HEAD/GET，不呼叫 LLM。

用法:
    python3 -m tools.liveness                       # 檢查所有 last_seen > 7 天的職缺
    python3 -m tools.liveness --days 14             # 只查 14 天未更新的
    python3 -m tools.liveness --source indeed_jp    # 只查 indeed
    python3 -m tools.liveness --job-id 123           # 查單筆
    python3 -m tools.liveness --top 20              # 只查高分前 20
    python3 -m tools.liveness --browser             # Playwright 深度檢測（SPA 站）
    python3 -m tools.liveness --cdp 9253            # 連已登入 Chrome（LinkedIn 等需登入站，讀真實內文）
    python3 -m tools.liveness --dry-run             # 預覽不更新 DB

LinkedIn 注意：職缺關閉後 URL 仍回 200、tab title 仍是舊標題，只有登入後的頁面內文才有
「No longer accepting applications」banner。故 LinkedIn liveness 必須用 --cdp 連已登入
Chrome（預設 slot4 = port 9253），httpx / 無登入 headless 都判不準。

輸出 JSON 到 stdout，方便下游消費。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from enum import Enum

import httpx

from tracker import db


class Liveness(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    UNCERTAIN = "uncertain"
    ERROR = "error"


EXPIRED_SIGNALS = [
    "この求人は掲載が終了しています",
    "この求人は既に掲載期間が終了",
    "掲載が終了した",
    "この求人は終了しました",
    "404",
    "not found",
    "page not found",
    "求人が見つかりません",
    "ページが見つかりません",
    "この募集は終了しました",
    "この求人票は無効",
    "募集終了",
    "掲載終了",
    "expired",
    "job has been closed",
    "position has been filled",
    "no longer available",
    "no longer accepting",
]

REDIRECT_EXPIRED_PATTERNS = [
    "/expired",
    "/closed",
    "/not-found",
    "error",
]


def classify_liveness(
    status_code: int, final_url: str, body_snippet: str, original_url: str
) -> Liveness:
    if status_code in (404, 410):
        return Liveness.EXPIRED

    # 403 常是反爬蟲封鎖（如 Indeed），不代表職缺真的下架 — 判 uncertain 待人工/瀏覽器複查
    if status_code == 403:
        return Liveness.UNCERTAIN

    if status_code >= 500:
        return Liveness.ERROR

    lower_body = body_snippet.lower()
    for signal in EXPIRED_SIGNALS:
        if signal.lower() in lower_body:
            return Liveness.EXPIRED

    if final_url != original_url:
        for pattern in REDIRECT_EXPIRED_PATTERNS:
            if pattern in final_url.lower():
                return Liveness.EXPIRED

    if status_code == 200:
        return Liveness.ACTIVE

    return Liveness.UNCERTAIN


def check_url(client: httpx.Client, url: str) -> dict:
    try:
        resp = client.get(url, follow_redirects=True, timeout=15.0)
        body_snippet = resp.text[:3000]
        liveness = classify_liveness(
            resp.status_code, str(resp.url), body_snippet, url
        )
        return {
            "url": url,
            "status_code": resp.status_code,
            "final_url": str(resp.url),
            "liveness": liveness.value,
        }
    except httpx.TimeoutException:
        return {"url": url, "status_code": 0, "liveness": Liveness.ERROR.value, "error": "timeout"}
    except Exception as e:
        return {"url": url, "status_code": 0, "liveness": Liveness.ERROR.value, "error": str(e)[:200]}


def check_url_browser(page, url: str) -> dict:
    """Playwright 深度檢測 — 等待 JS 渲染後判斷 SPA 站內容。"""
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        status_code = resp.status if resp else 0
        body_snippet = page.content()[:5000]
        final_url = page.url
        liveness = classify_liveness(status_code, final_url, body_snippet, url)
        return {
            "url": url,
            "status_code": status_code,
            "final_url": final_url,
            "liveness": liveness.value,
        }
    except Exception as e:
        return {"url": url, "status_code": 0, "liveness": Liveness.ERROR.value, "error": str(e)[:200]}


# LinkedIn 登入後頁面的 liveness 信號（讀 inner_text 後比對小寫）
_CLOSED_STABLE = "no longer accepting applications"   # 穩定：職缺仍在但已關閉應募 → expired
_DELETED_TRANSIENT = ["unable to load the page", "job id provided may not be valid"]  # 可能暫時性
_ALIVE = ["about the job", "\napply\n"]               # 真實職缺內容 → active


def check_url_cdp(page, url: str, tries: int = 3) -> dict:
    """連已登入 Chrome 讀真實內文 — LinkedIn 等需登入站專用。

    只看 inner_text("body")（渲染後文字），**絕不用 tab title** — LinkedIn 職缺
    關閉/刪除後 URL 仍回 200、tab title 仍是舊標題，只有內文才有真相。三種情況：

    1. 內文含「No longer accepting applications」→ expired（穩定信號，立即定論）
    2. 內文含「About the job / Apply」→ active（真實內容，立即定論）
    3. 內文只有「Unable to load the page / Job id ... not be valid」→ 可能是暫時性
       錯誤（rate limit / 載入競態），**重試 tries 次**；每次都是此狀態才判 expired。
    """
    last = "uncertain"
    final_url = url
    err = ""
    for _ in range(tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3500)
            final_url = page.url
            body = page.inner_text("body").lower()
        except Exception as e:
            last = "error"
            err = str(e)[:200]
            time.sleep(2)
            continue
        if _CLOSED_STABLE in body:
            return {"url": url, "status_code": 200, "final_url": final_url,
                    "liveness": Liveness.EXPIRED.value, "evidence": "closed-banner"}
        if any(s in body for s in _ALIVE):
            return {"url": url, "status_code": 200, "final_url": final_url,
                    "liveness": Liveness.ACTIVE.value, "evidence": "has-apply/about"}
        if any(s in body for s in _DELETED_TRANSIENT):
            last = "deleted-page"
            time.sleep(2.5)
            continue
        last = "no-signal"
        time.sleep(2)
    # 重試後仍只剩「deleted-page」→ 判定真的被刪；error 回 error；其餘無法定論
    if last == "deleted-page":
        return {"url": url, "status_code": 200, "final_url": final_url,
                "liveness": Liveness.EXPIRED.value, "evidence": "deleted-page-stable"}
    if last == "error":
        return {"url": url, "status_code": 0, "liveness": Liveness.ERROR.value, "error": err}
    return {"url": url, "status_code": 200, "final_url": final_url,
            "liveness": Liveness.UNCERTAIN.value, "evidence": last}


def get_stale_jobs(days: int, source: str | None, top_n: int | None) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        sql = (
            "SELECT id, url, title, company, source, last_seen, score FROM jobs "
            "WHERE last_seen <= ? AND id NOT IN (SELECT job_id FROM applications)"
        )
        params: list = [cutoff]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY score DESC"
        if top_n:
            sql += " LIMIT ?"
            params.append(top_n)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def run_liveness_check(
    days: int = 7,
    source: str | None = None,
    job_id: int | None = None,
    top_n: int | None = None,
    dry_run: bool = False,
    use_browser: bool = False,
    cdp_port: int | None = None,
) -> dict:
    if job_id:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id, url, title, company, source, last_seen, score FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        jobs = [dict(row)] if row else []
    else:
        jobs = get_stale_jobs(days, source, top_n)

    if not jobs:
        return {"checked": 0, "results": [], "summary": {}}

    results = []

    if cdp_port:
        results = _check_with_cdp(jobs, cdp_port)
    elif use_browser:
        results = _check_with_browser(jobs)
    else:
        results = _check_with_httpx(jobs)

    summary = {"active": 0, "expired": 0, "uncertain": 0, "error": 0}
    for r in results:
        summary[r["liveness"]] = summary.get(r["liveness"], 0) + 1

    if not dry_run:
        _update_db(results)

    return {"checked": len(results), "results": results, "summary": summary}


def _check_with_httpx(jobs: list[dict]) -> list[dict]:
    results = []
    client = httpx.Client(
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        follow_redirects=True,
    )
    try:
        for i, job in enumerate(jobs):
            if not job["url"]:
                continue
            print(f"  [{i+1}/{len(jobs)}] {job['company'] or '?'} — {job['title'][:40]}...", file=sys.stderr)
            result = check_url(client, job["url"])
            result["job_id"] = job["id"]
            result["company"] = job["company"]
            result["title"] = job["title"]
            result["score"] = job["score"]
            results.append(result)
            time.sleep(1.5)
    finally:
        client.close()
    return results


def _check_with_browser(jobs: list[dict]) -> list[dict]:
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        try:
            for i, job in enumerate(jobs):
                if not job["url"]:
                    continue
                print(f"  🌐 [{i+1}/{len(jobs)}] {job['company'] or '?'} — {job['title'][:40]}...", file=sys.stderr)
                result = check_url_browser(page, job["url"])
                result["job_id"] = job["id"]
                result["company"] = job["company"]
                result["title"] = job["title"]
                result["score"] = job["score"]
                results.append(result)
                time.sleep(2.0)
        finally:
            browser.close()
    return results


def _check_with_cdp(jobs: list[dict], port: int) -> list[dict]:
    """連既有已登入 Chrome（CDP）讀真實內文。LinkedIn 等需登入站專用。"""
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            for i, job in enumerate(jobs):
                if not job["url"]:
                    continue
                print(f"  🔌 [{i+1}/{len(jobs)}] {job['company'] or '?'} — {job['title'][:40]}...", file=sys.stderr)
                result = check_url_cdp(page, job["url"])
                result["job_id"] = job["id"]
                result["company"] = job["company"]
                result["title"] = job["title"]
                result["score"] = job["score"]
                results.append(result)
                time.sleep(1.5)
        finally:
            page.close()
    return results


def _update_db(results: list[dict]) -> None:
    today = date.today().isoformat()
    with db.connect() as conn:
        for r in results:
            conn.execute(
                "UPDATE jobs SET liveness_status = ?, liveness_checked_at = ? WHERE id = ?",
                (r["liveness"], today, r["job_id"]),
            )
    expired_count = sum(1 for r in results if r["liveness"] == Liveness.EXPIRED.value)
    if expired_count:
        print(f"  DB 標記 {expired_count} 筆過期", file=sys.stderr)
    print(f"  DB 更新 {len(results)} 筆 liveness 狀態", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description="職缺存活檢測")
    p.add_argument("--days", type=int, default=7, help="檢查 N 天未更新的職缺")
    p.add_argument("--source", help="只查特定來源")
    p.add_argument("--job-id", type=int, help="查單筆 job_id")
    p.add_argument("--top", type=int, help="只查高分前 N 筆")
    p.add_argument("--browser", action="store_true", help="使用 Playwright 深度檢測（SPA 站如 Lever、Ashby）")
    p.add_argument("--cdp", type=int, metavar="PORT", help="連已登入 Chrome（CDP port，LinkedIn 等需登入站；slot4=9253）")
    p.add_argument("--dry-run", action="store_true", help="不更新 DB")
    args = p.parse_args()

    result = run_liveness_check(
        days=args.days, source=args.source, job_id=args.job_id,
        top_n=args.top, dry_run=args.dry_run, use_browser=args.browser,
        cdp_port=args.cdp,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
