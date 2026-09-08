#!/usr/bin/env python3
"""日本 IT PM 職缺爬蟲 — 多站 dispatcher。

用法:
    python3 scrape.py                       # 全部來源, 預設關鍵字
    python3 scrape.py --source indeed       # 只跑 Indeed（不需 cookie）
    python3 scrape.py --source linkedin wantedly  # 多選
    python3 scrape.py --keywords "Product Manager" "PdM" --max-pages 3
    python3 scrape.py --headed              # debug 顯示瀏覽器

來源:
    indeed   — Indeed Japan (公開, 不需 cookie)
    wantedly — Wantedly     (需 cookie，見 auth/README.md)
    linkedin — LinkedIn JP  (需 cookie)
    bizreach — BizReach     (需 cookie)
    green    — Green        (公開, 不需 cookie；職缺資料內嵌在 __NEXT_DATA__ JSON)

CDP 專用來源（登入済み Chrome に接続）:
    ragent_search    — r-agent マイページの保存条件から求人一覧＋JD を直接取得
                       （config/scraping.yaml の ragent_search.search_urls）
    ragent_interests — r-agent マイページ「気になる」登録求人を直接取得

郵件來源（Gmail API、不需瀏覽器）:
    recruiter_agent  — リクルートエージェント担当 CA の推薦メール
    jac_recruitment  — JAC Recruitment「求人情報のご案内」メール（添付求人票 PDF も解析）
"""

import argparse
import json
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()
except ImportError:
    _STEALTH = None

from auth.load_cookies import COOKIES_DIR, load_into_context
from scrapers._common import USER_AGENT
from scrapers.provider import run_provider, discover_providers
from tracker.db import connect, init_db, new_jobs_today, stats_by_source, upsert_job

OUTPUT_DIR = Path(__file__).parent / "output"

# 2026 日本 AI PM 市場對標關鍵字組
KEYWORD_PRESETS: dict[str, list[str]] = {
    "core_pm": [
        "プロダクトマネージャー",
        "Product Manager",
        "PdM",
    ],
    "ai_pm_2026": [
        "AI プロダクトマネージャー",
        "AI Product Manager",
        "LLM Product Manager",
        "Product Architect",
        "Technical Program Manager",
        "TPM",
        "AI エージェント PM",
        "生成AI プロダクト",
    ],
    "senior_track": [
        "Senior Product Manager",
        "VP of Product",
        "Head of Product",
    ],
}
DEFAULT_KEYWORDS = KEYWORD_PRESETS["core_pm"] + KEYWORD_PRESETS["ai_pm_2026"]
ALL_SOURCES = ["indeed_cdp", "wantedly", "linkedin", "linkedin_cdp", "bizreach_cdp", "green", "recruiter_agent", "recruiter_agent_cdp", "ragent_search", "ragent_interests", "jac_recruitment"]

# Gmail 郵件來源（Playwright / cookie 不要）: source → (module, 既定の遡り日数)
EMAIL_SOURCES: dict[str, tuple[str, int]] = {
    "recruiter_agent": ("scrapers.recruiter_agent", 7),
    "jac_recruitment": ("scrapers.jac_recruitment", 30),
}
# "bizreach" is auto-promoted to "bizreach_cdp" (see _resolve_source)
# Chrome 執行檔與 CDP profile 皆可由 config/scraping.yaml 覆蓋
# （範本: config/scraping.yaml.example；缺檔 = 用以下預設）。
from tools.app_config import load as _load_app_config

_SCRAPING_CFG = _load_app_config("scraping")
CHROME_BIN = _SCRAPING_CFG.get(
    "chrome_bin", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# CDP profile registry: name → (port, user_data_dir, start_url)
_DEFAULT_CDP_PROFILES: dict[str, tuple[int, str, str]] = {
    "linkedin_cdp": (9253, str(Path.home() / ".chrome-linkedin"), "https://www.linkedin.com/jobs"),
    "bizreach_cdp": (9270, str(Path.home() / ".chrome-bizreach"), "https://www.bizreach.jp/"),
    "indeed_cdp": (9280, str(Path.home() / ".chrome-indeed"), "https://jp.indeed.com/"),
    "recruiter_agent_cdp": (9270, str(Path.home() / ".chrome-bizreach"), "https://mypage.r-agent.com/"),
    "ragent_search": (9270, str(Path.home() / ".chrome-bizreach"), "https://mypage.r-agent.com/"),
    "ragent_interests": (9270, str(Path.home() / ".chrome-bizreach"), "https://mypage.r-agent.com/"),
}

def _merge_cdp_profiles() -> dict[str, tuple[int, str, str]]:
    merged = dict(_DEFAULT_CDP_PROFILES)
    for name, v in (_SCRAPING_CFG.get("cdp_profiles") or {}).items():
        base = merged.get(name, (9270, str(Path.home() / f".chrome-{name}"), ""))
        merged[name] = (
            int(v.get("port", base[0])),
            str(Path(v.get("user_data_dir", base[1])).expanduser()),
            v.get("start_url", base[2]),
        )
    return merged

CDP_PROFILES = _merge_cdp_profiles()

SCRAPER_MAP = {
    "indeed": ("indeed_jp", "indeed_jp", False),     # (provider_id, cookie_site, needs_cookie)
    "wantedly": ("wantedly", "wantedly", True),
    "linkedin": ("linkedin_jp", "linkedin", True),
    "linkedin_cdp": ("linkedin_jp", None, False),    # CDP connect — no cookie file needed
    "bizreach_cdp": ("bizreach", None, False),        # CDP launch Profile 2 on demand
    "indeed_cdp": ("indeed_jp", None, False),          # CDP — avoid headless ban
    "green": ("green", None, False),                    # 公開，JD 內嵌在 __NEXT_DATA__，不需 cookie/CDP
    "recruiter_agent_cdp": ("recruiter_agent", None, False),  # CDP — fetch full JD from r-agent
    # 求人検索（マイページの保存条件）— 書き込み source はメール由来と同じ
    # 'recruiter_agent'（同じ /joboffers/{id} 空間なので UNIQUE で自動マージ）
    "ragent_search": ("recruiter_agent", None, False),
    # 「気になる」登録求人一覧 — 同上、書き込み source は 'recruiter_agent'
    "ragent_interests": ("recruiter_agent", None, False),
}

# CLI accepts short names → auto-promote to CDP
_SOURCE_ALIASES = {"bizreach": "bizreach_cdp", "indeed": "indeed_cdp"}


def _resolve_source(source: str) -> str:
    resolved = _SOURCE_ALIASES.get(source, source)
    if resolved != source:
        print(f"  [{source}] → 自動升級為 {resolved}（CDP profile）")
    return resolved


def _find_free_port(start: int = 9270) -> int:
    for port in range(start, start + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) != 0:
                return port
    raise RuntimeError("空き CDP ポートが見つかりません")


_LOGIN_CHECKS: dict[str, tuple[str, list[str]]] = {
    "bizreach_cdp": (
        "https://www.bizreach.jp/mypage/",
        ["/login", "/members/login", "/auth"],
    ),
    "linkedin_cdp": (
        "https://www.linkedin.com/feed/",
        ["/login", "/authwall", "/uas/login"],
    ),
    "recruiter_agent_cdp": (
        "https://mypage.r-agent.com/mypage/",
        ["/rikupon/login", "/login"],
    ),
    "ragent_search": (
        "https://mypage.r-agent.com/mypage/",
        ["/rikupon/login", "/login"],
    ),
    "ragent_interests": (
        "https://mypage.r-agent.com/mypage/",
        ["/rikupon/login", "/login"],
    ),
}


def _ensure_cdp(port: int, user_data_dir: str, start_url: str) -> subprocess.Popen | None:
    """Reuse running Chrome on port, or launch one. Returns Popen if we launched it (caller should terminate)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("localhost", port)) == 0:
            print(f"  既存 Chrome 再利用 (port={port})")
            return None
    chrome = shutil.which("google-chrome") or CHROME_BIN
    print(f"  Chrome 起動中 (port={port}, profile={user_data_dir})...")
    proc = subprocess.Popen([
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=IsolateOrigins,site-per-process",
        start_url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        time.sleep(0.5)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) == 0:
                return proc
    proc.terminate()
    raise RuntimeError(f"Chrome CDP port={port} が起動しませんでした")


def _verify_cdp_login(page, source: str) -> bool:
    """Navigate to a login-required page and check if we're still logged in."""
    check = _LOGIN_CHECKS.get(source)
    if not check:
        return True
    test_url, redirect_indicators = check
    try:
        page.goto(test_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
        final_url = page.url
        if any(ind in final_url for ind in redirect_indicators):
            print(f"  ⚠ [{source}] 未登入 — 被跳轉到 {final_url}")
            return False
        print(f"  ✓ [{source}] 登入狀態確認 OK")
        return True
    except Exception as e:
        print(f"  ⚠ [{source}] 登入驗證失敗: {type(e).__name__}: {e}")
        return False


def run_source(source: str, keywords: list[str], max_pages: int, headed: bool) -> int:
    """Run one source for all keywords, write to DB. Returns count of NEW jobs."""
    source = _resolve_source(source)
    provider_id, cookie_site, needs_cookie = SCRAPER_MAP[source]

    if needs_cookie and not (COOKIES_DIR / f"{cookie_site}.json").exists():
        print(f"\n[{source}] 略過：缺少 cookie 檔 auth/cookies/{cookie_site}.json（見 auth/README.md）")
        return 0

    new_count = 0
    total_count = 0

    # 在庫已存在的 source_id 預載為 seen → 跨 keyword / 跨 run 去重：命中者只更新
    # last_seen，不重複開詳情頁（爬取階段昂貴的 detail fetch 只對真正的新職缺執行）。
    with connect() as _c:
        seen_ids = {
            r["source_id"]
            for r in _c.execute(
                "SELECT source_id FROM jobs WHERE source = ?", (provider_id,)
            ).fetchall()
        }

    if source in CDP_PROFILES:
        port, user_data_dir, start_url = CDP_PROFILES[source]
        print(f"\n[{source}]")
        proc = _ensure_cdp(port, user_data_dir, start_url)
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
                ctx = browser.contexts[0]
                page = ctx.new_page()
                if not _verify_cdp_login(page, source):
                    print(f"  [{source}] 中止：未登入，請手動登入 CDP profile 後重試")
                    page.close()
                    browser.close()
                    return 0

                # ragent_search: config の保存条件 URL から一覧＋JD を直接取得
                # （keyword ループではなく 1 回だけ呼ぶ）
                if source == "ragent_search":
                    from scrapers.ragent_search import search_all
                    jobs = search_all(page, seen_ids=seen_ids)
                    for job in jobs:
                        _, is_new = upsert_job(job)
                        total_count += 1
                        if is_new:
                            new_count += 1
                    page.close()
                    browser.close()
                    print(f"[{source}] 完成：scraped {total_count}, 新增 {new_count}")
                    return new_count

                # ragent_interests: 「気になる」登録求人一覧を直接取得（1 回だけ呼ぶ）
                if source == "ragent_interests":
                    from scrapers.ragent_interests import fetch_all
                    jobs = fetch_all(page, seen_ids=seen_ids)
                    for job in jobs:
                        # 星付き＝本人が選んだ求人。同公司が他サイト経由で在庫に
                        # あっても併入せず独立 row にする（r-agent の URL/JD を残す）
                        _, is_new = upsert_job(job, allow_company_dup=True)
                        total_count += 1
                        if is_new:
                            new_count += 1
                    page.close()
                    browser.close()
                    print(f"[{source}] 完成：scraped {total_count}, 新增 {new_count}")
                    return new_count

                # recruiter_agent_cdp: enrich existing short JDs instead of keyword search
                if source == "recruiter_agent_cdp":
                    from scrapers.recruiter_agent import enrich_jds
                    enriched = enrich_jds(page)
                    page.close()
                    browser.close()
                    print(f"[{source}] 完成：enriched {enriched} 筆 JD")
                    return enriched

                for kw in keywords:
                    print(f"  搜尋: {kw}")
                    jobs = run_provider(provider_id, page, kw, max_pages=max_pages, seen_ids=seen_ids)
                    for job in jobs:
                        _, is_new = upsert_job(job)
                        total_count += 1
                        if is_new:
                            new_count += 1
                page.close()
                browser.close()
        finally:
            if proc:
                proc.terminate()
        print(f"[{source}] 完成：scraped {total_count}, 新增 {new_count}")
        return new_count

    pw_ctx = _STEALTH.use_sync(sync_playwright()) if _STEALTH else sync_playwright()
    with pw_ctx as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        if needs_cookie:
            loaded = load_into_context(ctx, cookie_site)
            print(f"\n[{source}] cookie 已載入" if loaded else f"\n[{source}] 警告：cookie 載入失敗")
        else:
            print(f"\n[{source}] 公開爬取（不需 cookie）")

        page = ctx.new_page()
        for kw in keywords:
            print(f"  搜尋: {kw}")
            jobs = run_provider(provider_id, page, kw, max_pages=max_pages, seen_ids=seen_ids)
            for job in jobs:
                _, is_new = upsert_job(job)
                total_count += 1
                if is_new:
                    new_count += 1
        browser.close()

    print(f"[{source}] 完成：scraped {total_count}, 新增 {new_count}")
    return new_count


def run_email_source(source: str, days: int | None = None) -> int:
    """Gmail 郵件來源（Playwright 不要）を 1 本走らせる。Returns count of NEW jobs.

    去重は各 provider（run 内の求人No.）＋ upsert_job（UNIQUE(source, source_id)
    と company_norm マージ）に任せるので、何度走らせても冪等。
    """
    import importlib

    module_name, default_days = EMAIL_SOURCES[source]
    fetch_from_gmail = importlib.import_module(module_name).fetch_from_gmail

    print(f"\n[{source}]")
    jobs = fetch_from_gmail(days=days or default_days)
    new_count = 0
    for job in jobs:
        _, is_new = upsert_job(job)
        if is_new:
            new_count += 1
    print(f"[{source}] 完成：fetched {len(jobs)}, 新增 {new_count}")
    return new_count


def save_today_report() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    rows = new_jobs_today()
    jobs = [dict(r) for r in rows]

    json_path = OUTPUT_DIR / f"new-{today}.json"
    json_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    md_path = OUTPUT_DIR / f"new-{today}.md"
    by_source: dict[str, list[dict]] = {}
    for j in jobs:
        by_source.setdefault(j["source"], []).append(j)

    lines = [f"# 新 PM 職缺 — {today}", "", f"**今日新增 {len(jobs)} 筆**", ""]
    for src in sorted(by_source.keys()):
        items = by_source[src]
        lines.append(f"## {src} ({len(items)})")
        lines.append("")
        for j in items:
            company = j.get("company") or "—"
            loc = j.get("location") or ""
            loc_str = f" — {loc}" if loc else ""
            lines.append(f"- **{j['title']}** | {company}{loc_str}")
            lines.append(f"  - {j['url']}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nDaily report:\n  {json_path}\n  {md_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="日本 IT PM 職缺爬蟲")
    parser.add_argument(
        "--source", nargs="+", choices=ALL_SOURCES + list(_SOURCE_ALIASES.keys()) + ["all"], default=["all"]
    )
    parser.add_argument(
        "--preset",
        choices=list(KEYWORD_PRESETS.keys()) + ["all"],
        help="使用預設關鍵字組（覆蓋 --keywords）。'all' 表示合併所有 preset。",
    )
    parser.add_argument("--keywords", nargs="+", default=DEFAULT_KEYWORDS)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--no-postprocess",
        action="store_true",
        help="跳過抓取後的分類 / 評分 / 履歷 tailor 後處理（debug 用）",
    )
    args = parser.parse_args()

    if args.preset:
        if args.preset == "all":
            keywords = [kw for kws in KEYWORD_PRESETS.values() for kw in kws]
        else:
            keywords = KEYWORD_PRESETS[args.preset]
    else:
        keywords = args.keywords

    init_db()
    sources = ALL_SOURCES if "all" in args.source else args.source

    total_new = 0
    for src in sources:
        try:
            if src in EMAIL_SOURCES:
                total_new += run_email_source(src)
            else:
                total_new += run_source(src, keywords, args.max_pages, args.headed)
        except Exception as e:
            print(f"\n[{src}] 異常: {type(e).__name__}: {e}")
            continue

    save_today_report()
    print(f"\n========")
    print(f"今日新增 {total_new} 筆")
    print(f"DB 累計：{stats_by_source()}")

    if not args.no_postprocess:
        run_postprocess()
        from pipeline import report_new_batch
        report_new_batch()


def run_postprocess() -> None:
    """抓取後處理：分類 → 評分 → 高分職缺 tailor 履歷。"""
    print("\n========\n[後處理] 分類 / 評分 / 履歷 tailor")
    try:
        from tools.jd_tier_classifier import classify_all
        from tools.salary_parser import parse_all as parse_all_salaries
        from analyzer.jd_scorer import score_all, write_report
        from tools.resume_tailor import tailor_top
        from tracker.db import dedup_jobs, dedup_fuzzy

        dedup_jobs()
        dedup_fuzzy()
        from tools.blacklist import flag_blacklisted
        flag_blacklisted()
        classify_all()
        from tools.jd_tier_classifier import classify_posting_type_all
        classify_posting_type_all()
        from analyzer.jd_enrich import enrich_all
        enrich_all()
        parse_all_salaries()
        score_all()
        write_report()
        tailor_top(min_score=70, limit=10)
        from notify.events import notify_new_high_scores
        n = notify_new_high_scores()
        if n:
            print(f"[後處理] 高分職缺通知 {n} 筆")
    except Exception as e:
        print(f"[後處理] 警告: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
