"""OpenWork 企業評分爬蟲 — CDP 模式搜尋公司名，抓取總合評価。

Usage:
    python3 -m tools.openwork_ratings               # 爬所有未查詢公司（score>=50）
    python3 -m tools.openwork_ratings --company "○○株式会社"  # 單一公司
    python3 -m tools.openwork_ratings --limit 50     # 最多查 50 家
"""

from __future__ import annotations

import argparse
import re
import socket
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.sqlite"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT = 9270
USER_DATA_DIR = str(Path.home() / ".chrome-bizreach")
START_URL = "https://www.openwork.jp/"


def _ensure_cdp() -> subprocess.Popen | None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("localhost", CDP_PORT)) == 0:
            print(f"  既存 Chrome 再利用 (port={CDP_PORT})")
            return None
    proc = subprocess.Popen([
        CHROME_BIN,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={USER_DATA_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        START_URL,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS company_ratings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "company_name TEXT NOT NULL UNIQUE, "
        "openwork_score REAL, "
        "openwork_url TEXT, "
        "fetched_at TEXT NOT NULL)"
    )
    return conn


def _search_openwork(page, company: str) -> tuple[float | None, str | None, bool]:
    """搜尋 OpenWork，回傳 (score, url, page_ok)。page_ok=True 表示頁面正常載入（即使沒搜到）。"""
    search_url = f"https://www.openwork.jp/company_list?src_str={quote(company)}"
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
    except Exception as e:
        print(f"    搜尋失敗 {type(e).__name__}: {company}")
        return None, None, False

    try:
        result = page.evaluate("""
            () => {
                if (document.title.includes('404') || document.title.includes('エラー')
                    || document.querySelector('input[name="captcha"], [class*="captcha"], [class*="recaptcha"]'))
                    return {blocked: true};
                const link = document.querySelector('a[href*="company.php?m_id="]');
                if (!link) return {blocked: false, empty: true};
                let el = link;
                for (let i = 0; i < 8; i++) { el = el.parentElement; if (!el) break; }
                if (!el) return {url: link.href, score: null, blocked: false};
                const scoreEls = el.querySelectorAll('p.totalEvaluation_item, .totalEvaluation_item');
                let score = null;
                for (const s of scoreEls) {
                    const m = s.textContent.trim().match(/^(\\d\\.\\d{2})$/);
                    if (m) { score = parseFloat(m[1]); break; }
                }
                return {url: link.href, score: score, blocked: false};
            }
        """)
        if not result:
            return None, None, False
        if result.get("blocked"):
            return None, None, False
        if result.get("empty") or not result.get("url"):
            return None, None, True
        return result.get("score"), result["url"], True

    except Exception as e:
        print(f"    解析失敗 {type(e).__name__}: {company}")
        return None, None, False


def _companies_to_fetch(conn: sqlite3.Connection, limit: int | None) -> list[str]:
    sql = (
        "SELECT DISTINCT j.company FROM jobs j "
        "LEFT JOIN company_ratings cr ON j.company = cr.company_name "
        "WHERE j.score >= 50 AND j.company IS NOT NULL AND j.company != '' "
        "AND cr.id IS NULL "
        "ORDER BY j.recommend_score DESC NULLS LAST, j.score DESC"
    )
    if limit:
        sql += f" LIMIT {limit}"
    return [r["company"] for r in conn.execute(sql).fetchall()]


def run(companies: list[str] | None = None, limit: int | None = None):
    conn = _get_conn()

    if not companies:
        companies = _companies_to_fetch(conn, limit)

    if not companies:
        print("[openwork] 所有公司已有評分，無需查詢")
        return

    print(f"[openwork] 待查詢 {len(companies)} 家公司")

    max_consecutive_miss = 5
    proc = _ensure_cdp()
    found = 0
    consecutive_miss = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
            ctx = browser.contexts[0]
            page = ctx.new_page()

            for i, company in enumerate(companies, 1):
                print(f"  [{i}/{len(companies)}] {company}...", end=" ")
                score, url, page_ok = _search_openwork(page, company)
                now = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "INSERT OR REPLACE INTO company_ratings (company_name, openwork_score, openwork_url, fetched_at) "
                    "VALUES (?, ?, ?, ?)",
                    (company, score, url, now),
                )
                conn.commit()
                if score is not None:
                    print(f"✓ {score}")
                    found += 1
                    consecutive_miss = 0
                elif page_ok:
                    print("✗ 未收錄")
                    consecutive_miss = 0
                else:
                    print("✗ 頁面異常")
                    consecutive_miss += 1
                    if consecutive_miss >= max_consecutive_miss:
                        print(f"[openwork] 連續 {max_consecutive_miss} 次頁面異常，自動停止（驗證碼/封鎖）")
                        break
                time.sleep(30)

            page.close()
            browser.close()
    finally:
        if proc:
            proc.terminate()

    print(f"[openwork] 完成：{found}/{len(companies)} 家有評分")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", help="單一公司名")
    parser.add_argument("--limit", type=int, help="最多查幾家")
    args = parser.parse_args()

    if args.company:
        run(companies=[args.company])
    else:
        run(limit=args.limit)
