#!/usr/bin/env python3
"""空 raw_jd 重抓 + liveness 檢查。

用法:
    python3 -m tools.refetch_jd                    # 重抓所有空 JD
    python3 -m tools.refetch_jd --source indeed_jp # 只重抓 indeed_jp
    python3 -m tools.refetch_jd --dry-run          # 預覽不寫 DB
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import time

import httpx

from tracker.db import connect, update_liveness, update_raw_jd, update_posting_type
from tools.liveness import classify_liveness, EXPIRED_SIGNALS
from tools.jd_tier_classifier import classify_posting_type

MAX_JD_CHARS = 20000
INDEED_SELECTORS = "#viewJobSSRRoot, #jobDescriptionText, div.jobsearch-JobComponent-description"


def _get_empty_jd_jobs(source: str | None) -> list[dict]:
    with connect() as conn:
        sql = "SELECT id, source, url, company, title FROM jobs WHERE (raw_jd IS NULL OR raw_jd = '')"
        params: list = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY score DESC NULLS LAST"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _fetch_greenhouse(url: str, client: httpx.Client) -> tuple[str, str]:
    """greenhouse-api: 試著從 URL 抓 job_id 再呼叫 v1 API。回傳 (jd_text, liveness)"""
    m = re.search(r'gh_jid=(\d+)|/jobs/(\d+)', url)
    if not m:
        return "", "uncertain"
    jid = m.group(1) or m.group(2)
    try:
        resp = client.get(f"https://boards-api.greenhouse.io/v1/boards/jobs/{jid}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("content", "") or data.get("description", "")
            # 去 HTML tag
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:MAX_JD_CHARS], "active"
        elif resp.status_code == 404:
            return "", "expired"
    except Exception:
        pass
    return "", "uncertain"


def _fetch_lever(url: str, client: httpx.Client) -> tuple[str, str]:
    """lever-api: 從 URL 抓 job UUID 呼叫 v0 postings API。"""
    m = re.search(r'/([0-9a-f-]{36})$', url)
    if not m:
        return "", "uncertain"
    jid = m.group(1)
    # 猜 slug from URL
    slug_m = re.search(r'lever\.co/([^/]+)/', url)
    slug = slug_m.group(1) if slug_m else ""
    if not slug:
        return "", "uncertain"
    try:
        resp = client.get(f"https://api.lever.co/v0/postings/{slug}/{jid}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("descriptionPlain", "") or data.get("description", "")
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:MAX_JD_CHARS], "active"
        elif resp.status_code == 404:
            return "", "expired"
    except Exception:
        pass
    return "", "uncertain"


def _fetch_ashby(url: str, client: httpx.Client) -> tuple[str, str]:
    """ashby-api: board 端點一次回傳整版職缺清單（無單筆 API），依 jobUrl 比對出這筆。

    board 存在但清單裡找不到這個 URL＝該筆已下架（Ashby 不留 404 單頁）。
    """
    m = re.search(r'jobs\.ashbyhq\.com/([^/?#]+)', url)
    if not m:
        return "", "uncertain"
    slug = m.group(1)
    try:
        resp = client.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
            timeout=15,
        )
        if resp.status_code == 404:
            return "", "expired"
        if resp.status_code != 200:
            return "", "uncertain"
        data = resp.json()
        target = url.split("?")[0].split("#")[0].rstrip("/")
        for j in data.get("jobs") or []:
            job_url = (j.get("jobUrl") or "").split("?")[0].split("#")[0].rstrip("/")
            if job_url and job_url == target:
                text = j.get("descriptionPlain", "") or ""
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:MAX_JD_CHARS], "active"
        return "", "expired"
    except Exception:
        pass
    return "", "uncertain"


def _fetch_workable(url: str, client: httpx.Client) -> tuple[str, str]:
    """workable-api: widget API 一次回傳整帳號職缺清單 + description，依 shortcode/URL 比對出這筆。"""
    m = re.search(r'apply\.workable\.com/([^/?#]+)', url)
    if not m:
        return "", "uncertain"
    slug = m.group(1)
    shortcode_m = re.search(r'/j/([A-Za-z0-9]+)', url)
    shortcode = shortcode_m.group(1) if shortcode_m else ""
    try:
        resp = client.get(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
            timeout=10,
        )
        if resp.status_code == 404:
            return "", "expired"
        if resp.status_code != 200:
            return "", "uncertain"
        data = resp.json()
        target = url.split("?")[0].rstrip("/")
        for j in data.get("jobs") or []:
            job_url = (j.get("url") or j.get("shortlink") or "").split("?")[0].rstrip("/")
            matched = (shortcode and j.get("shortcode") == shortcode) or (job_url and job_url == target)
            if matched:
                text = j.get("description", "") or ""
                text = re.sub(r'<[^>]+>', ' ', text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:MAX_JD_CHARS], "active"
        return "", "expired"
    except Exception:
        pass
    return "", "uncertain"


# API 直打（免 CDP）的 source → fetcher 名稱。存名稱而非函數本身，讓 fetch_one/run
# 在呼叫當下透過 globals() 取值 —— 測試 monkeypatch 模組屬性才拿得到替身。
_API_FETCHERS = {
    "greenhouse-api": "_fetch_greenhouse",
    "lever-api": "_fetch_lever",
    "ashby-api": "_fetch_ashby",
    "workable-api": "_fetch_workable",
}


def _ensure_cdp_profile(port: int, chrome_bin: str, user_data_dir: str, profile: str | None, start_url: str):
    """Reuse or launch Chrome CDP with a specific profile. Returns Popen or None."""
    import socket, subprocess
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("localhost", port)) == 0:
            print(f"  既存 Chrome 再利用 (port={port})")
            return None
    print(f"  Chrome 起動中 (port={port}, profile={profile}, data={user_data_dir})...")
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-features=IsolateOrigins,site-per-process",
    ]
    if profile:
        cmd.append(f"--profile-directory={profile}")
    cmd.append(start_url)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.5)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) == 0:
                return proc
    proc.terminate()
    raise RuntimeError(f"Chrome CDP port={port} 啟動失敗")


def _fetch_bizreach_cdp(jobs: list[dict], dry_run: bool) -> None:
    """透過 CDP（port 9270）連 bizreach 已登入的 Chrome，補抓詳情頁 JD。"""
    from pathlib import Path
    from playwright.sync_api import sync_playwright

    PORT = 9270
    CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    USER_DATA = str(Path.home() / ".chrome-bizreach")
    LOGIN_INDICATORS = ("/login", "/members/login", "/auth")

    proc = _ensure_cdp_profile(PORT, CHROME_BIN, USER_DATA, None, "https://www.bizreach.jp/")
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{PORT}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()

            for job in jobs:
                jid = job["id"]
                url = job["url"]
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)

                    if any(ind in page.url for ind in LOGIN_INDICATORS):
                        print(f"  [{jid}] cookie 失效，停止")
                        break

                    status = resp.status if resp else 0
                    body = page.content()[:6000]
                    liveness = classify_liveness(status, page.url, body, url).value

                    if liveness == "expired":
                        print(f"  [{jid}] expired")
                        if not dry_run:
                            update_liveness(jid, "expired")
                        continue

                    text = page.evaluate("""
                        () => {
                            const el = document.querySelector('article') ||
                                       document.querySelector('main') ||
                                       document.querySelector('.pg-job-detail') ||
                                       document.body;
                            return el ? el.innerText.trim() : '';
                        }
                    """) or ""
                    text = text[:MAX_JD_CHARS]
                    status_str = f"{len(text)}c" if text else "no_content"
                    print(f"  [{jid}] {job['company'][:25]} → {liveness} {status_str}")

                    if not dry_run:
                        if text:
                            update_raw_jd(jid, text)
                            pt = classify_posting_type(job["company"], text)
                            update_posting_type(jid, pt)
                        update_liveness(jid, liveness)
                except Exception as e:
                    print(f"  [{jid}] error: {type(e).__name__}: {e!s:.60}")
                    if not dry_run:
                        update_liveness(jid, "error")
                time.sleep(1.0)

            page.close()
            browser.close()
    finally:
        if proc:
            proc.terminate()


def _fetch_indeed_batch(jobs: list[dict], dry_run: bool) -> dict[int, tuple[str, str]]:
    """Playwright 批次抓 indeed_jp 詳情頁。回傳 {job_id: (jd_text, liveness)}"""
    from playwright.sync_api import sync_playwright
    results: dict[int, tuple[str, str]] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        for job in jobs:
            jid = job["id"]
            url = job["url"]
            # 跳過廣告跳轉 URL
            if "pagead/clk" in url or not url.startswith("http"):
                results[jid] = ("", "uncertain")
                continue
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1500)
                status = resp.status if resp else 0
                body = page.content()[:8000]
                liveness = classify_liveness(status, page.url, body, url).value

                if liveness == "expired":
                    results[jid] = ("", "expired")
                    print(f"  [{jid}] expired")
                    continue

                # 抓 JD 全文（含底部企業名/業種）
                text = page.evaluate("""
                    () => {
                        const el = document.querySelector(
                            '#viewJobSSRRoot, #jobDescriptionText, div.jobsearch-JobComponent-description'
                        );
                        return el ? (el.innerText || '').trim() : '';
                    }
                """) or ""
                text = text[:MAX_JD_CHARS]
                results[jid] = (text, liveness if text else "uncertain")
                status_str = f"{len(text)}c" if text else "no_content"
                print(f"  [{jid}] {job['company'][:25]} → {liveness} {status_str}")
            except Exception as e:
                results[jid] = ("", "error")
                print(f"  [{jid}] error: {type(e).__name__}")
            time.sleep(0.8)
        browser.close()
    return results


# ── 単一求人の補抓（Dashboard の gap 分析ボタンから呼ばれる）──────────

# source → 既ログイン Chrome の CDP profile 名と既定 port。
# config/scraping.yaml の cdp_profiles で上書き可（缺檔 = 以下の既定値）。
_DEFAULT_CDP_PORTS = {
    "bizreach": ("bizreach_cdp", 9270),
    "linkedin_jp": ("linkedin_cdp", 9253),
    "recruiter_agent": ("recruiter_agent_cdp", 9270),
    "indeed_jp": ("indeed_cdp", 9280),
}
# ログイン cookie 必須 = CDP が閉じていれば諦める（headless では login wall）。
# indeed_jp はここに入れない（CDP が無ければ headless で試す）。
_LOGIN_REQUIRED = {"bizreach", "linkedin_jp", "recruiter_agent"}


def cdp_port_for(source: str) -> int | None:
    """該当 source の CDP port。対象外の source は None。"""
    entry = _DEFAULT_CDP_PORTS.get(source)
    if not entry:
        return None
    profile_name, default_port = entry
    from tools.app_config import load as _load
    cfg = (_load("scraping").get("cdp_profiles") or {}).get(profile_name) or {}
    return int(cfg.get("port", default_port))


# 「ページは開けたが中身はログイン壁」の判別語。抜き出した本文にこれが混じって
# いたら JD ではない（そのまま保存すると gap 分析が半端な情報で走る）。
_LOGIN_WALL_MARKERS = (
    "詳細をご覧になる場合は会員登録",
    "会員登録（無料）が必要です",
    "ログインしてください",
    "Sign in to view",
    "Join LinkedIn to view",
    "Authenticating...",
)


def _is_login_wall(text: str) -> bool:
    return any(m in text for m in _LOGIN_WALL_MARKERS)


def _port_open(port: int) -> bool:
    """CDP port が既に開いているか。閉じていれば Chrome は起動しない。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _fetch_via_cdp(url: str, port: int, selector: str = "") -> tuple[str, str]:
    """既に開いている Chrome（CDP）で詳細ページを開き JD を抜く。"""
    from playwright.sync_api import sync_playwright

    LOGIN_INDICATORS = ("/login", "/members/login", "/uas/login", "/checkpoint", "/auth")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            if any(ind in page.url for ind in LOGIN_INDICATORS):
                return "", "needs_login"
            status = resp.status if resp else 0
            liveness = classify_liveness(status, page.url, page.content()[:6000], url).value
            if liveness == "expired":
                return "", "expired"
            text = page.evaluate(
                "(sel) => { const el = (sel && document.querySelector(sel)) || "
                "document.querySelector('article') || document.querySelector('main') || "
                "document.body; return el ? el.innerText.trim() : ''; }",
                selector,
            ) or ""
            text = text[:MAX_JD_CHARS]
            return (text, liveness) if text else ("", "no_content")
        finally:
            page.close()


def fetch_one(job: dict) -> tuple[str, str]:
    """1 件だけ JD を取り直す。戻り値 (jd_text, liveness)。DB へは書かない。

    liveness は取得できなかった理由も兼ねる:
    ``unsupported`` / ``needs_login`` / ``expired`` / ``no_content`` / ``error``。
    """
    url = (job.get("url") or "").strip()
    source = job.get("source") or ""
    if not url.startswith("http"):
        return "", "unsupported"

    try:
        if source in _API_FETCHERS:
            with httpx.Client(follow_redirects=True, timeout=10) as client:
                fetch = globals()[_API_FETCHERS[source]]
                text, liveness = fetch(url, client)
            return (text, liveness) if text else ("", liveness if liveness != "active" else "no_content")

        port = cdp_port_for(source)
        # 既ログイン Chrome が開いていればそちらが最優先（indeed は headless だと
        # bot 判定で login wall に飛ばされる）。
        if port is not None and _port_open(port):
            sel = INDEED_SELECTORS if source == "indeed_jp" else ""
            text, liveness = _fetch_via_cdp(url, port, sel)
            if text and _is_login_wall(text):
                text, liveness = "", "needs_login"
            if text or source in _LOGIN_REQUIRED:
                return text, liveness
        elif source in _LOGIN_REQUIRED:
            return "", "needs_login"

        if source == "indeed_jp":
            text, liveness = _fetch_indeed_batch([job], dry_run=True).get(
                job.get("id"), ("", "uncertain"))
            if text and _is_login_wall(text):
                text, liveness = "", "needs_login"
            if not text and liveness in ("uncertain", "error"):
                # headless は bot 判定で弾かれる。CDP profile を開けば通る。
                return "", "needs_login"
            return (text, liveness) if text else ("", liveness if liveness != "active" else "no_content")
    except Exception as e:
        print(f"  fetch_one error: {type(e).__name__}: {e!s:.80}")
        return "", "error"

    return "", "unsupported"


def run(source: str | None = None, dry_run: bool = False) -> None:
    jobs = _get_empty_jd_jobs(source)
    print(f"空 raw_jd 職缺: {len(jobs)} 筆")

    by_source: dict[str, list[dict]] = {}
    for j in jobs:
        by_source.setdefault(j["source"], []).append(j)

    for src, src_jobs in sorted(by_source.items(), key=lambda x: len(x[1]), reverse=True):
        print(f"\n=== {src} ({len(src_jobs)} 筆) ===")

        if src == "indeed_jp":
            results = _fetch_indeed_batch(src_jobs, dry_run)
            for job in src_jobs:
                jid = job["id"]
                text, liveness = results.get(jid, ("", "uncertain"))
                if not dry_run:
                    if text:
                        update_raw_jd(jid, text)
                        pt = classify_posting_type(job["company"], text)
                        update_posting_type(jid, pt)
                    update_liveness(jid, liveness)

        elif src in _API_FETCHERS:
            fetch = globals()[_API_FETCHERS[src]]
            with httpx.Client(follow_redirects=True, timeout=10) as client:
                for job in src_jobs:
                    jid = job["id"]
                    text, liveness = fetch(job["url"], client)
                    status_str = f"{len(text)}c" if text else "no_content"
                    print(f"  [{jid}] {job['company'][:25]} → {liveness} {status_str}")
                    if not dry_run:
                        if text:
                            update_raw_jd(jid, text)
                            pt = classify_posting_type(job["company"], text)
                            update_posting_type(jid, pt)
                        update_liveness(jid, liveness)
                    time.sleep(0.3)

        elif src == "bizreach":
            _fetch_bizreach_cdp(src_jobs, dry_run)

        else:
            # linkedin_jp：需要 cookie，只做 liveness HTTP check
            print(f"  {src} 需要 cookie，跳過 JD 重抓，僅做 HTTP liveness")
            with httpx.Client(follow_redirects=True, timeout=10) as client:
                for job in src_jobs:
                    jid = job["id"]
                    try:
                        resp = client.head(job["url"], timeout=8)
                        liveness = classify_liveness(resp.status_code, str(resp.url), "", job["url"]).value
                    except Exception:
                        liveness = "error"
                    print(f"  [{jid}] {job['company'][:25]} → {liveness}")
                    if not dry_run:
                        update_liveness(jid, liveness)
                    time.sleep(0.2)

    print("\n完成。")


def main() -> None:
    parser = argparse.ArgumentParser(description="空 raw_jd 重抓 + liveness 檢查")
    parser.add_argument("--source", help="只處理指定 source")
    parser.add_argument("--dry-run", action="store_true", help="預覽不寫 DB")
    args = parser.parse_args()
    run(source=args.source, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
