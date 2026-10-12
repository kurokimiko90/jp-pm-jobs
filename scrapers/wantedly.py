"""Wantedly scraper — 需要登入（cookie）才能拿到精準搜尋結果。"""

import re
from datetime import datetime
from urllib.parse import quote

from playwright.sync_api import Page

from analyzer.role_filter import is_engineering_only
from ._common import polite_sleep

PROVIDER_META = {
    "id": "wantedly",
    "name": "Wantedly",
    "requires_login": True,
    "base_url": "https://www.wantedly.com",
    "description": "Wantedly 求人・スタートアップ向け",
}

ENTRY_RE = re.compile(r"^\d+\s*エントリー$")
LOGIN_REDIRECT_INDICATORS = ("/sign_in", "/users/sign_in", "/login")


def _fetch_jd_body(page: Page, url: str) -> str:
    """進入 Wantedly 詳情頁抓 JD 全文。失敗回空字串。"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"    [detail] goto 失敗 {type(e).__name__}: {url[:80]}")
        return ""
    try:
        # Wantedly 詳情頁的內容主體：<main> 內含完整職缺說明
        page.wait_for_selector("main, article", timeout=10000)
    except Exception as e:
        print(f"    [detail] selector 失敗 {type(e).__name__}: {url[:80]}")
        return ""
    polite_sleep()
    try:
        text = page.evaluate(
            """
            () => {
                const main = document.querySelector('main') || document.querySelector('article');
                return main ? (main.innerText || '').trim() : '';
            }
            """
        )
        return (text or "")[:20000]
    except Exception:
        return ""


def _parse_card_text(text: str) -> tuple[str, str]:
    """從 card innerText 萃取 (title, company)。

    結構通常：
        [tag 如 '自社開発 / ポテンシャル採用']  ← 可選
        [N エントリー]                          ← 可選
        [實際 title]
        ↩ 空行
        [company]
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return "", ""
    title_idx = -1
    for i, ln in enumerate(lines):
        if ENTRY_RE.match(ln):
            continue
        if i == 0 and "/" in ln and len(ln) < 40:
            continue
        title_idx = i
        break
    if title_idx == -1:
        return lines[0][:120], ""
    title = lines[title_idx][:200]
    company = lines[title_idx + 1] if title_idx + 1 < len(lines) else ""
    return title, company[:120]


def scrape(page: Page, keyword: str, max_pages: int = 3) -> list[dict]:
    results: list[dict] = []
    for page_num in range(1, max_pages + 1):
        url = f"https://www.wantedly.com/projects?q={quote(keyword)}&page={page_num}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('a[href^="/projects/"]', timeout=10000)
        except Exception as e:
            print(f"  [wantedly] p{page_num} 載入失敗: {type(e).__name__}")
            continue

        # 偵測是否被踢回登入頁
        if any(ind in page.url for ind in LOGIN_REDIRECT_INDICATORS):
            print(f"  [wantedly] cookie 無效或過期 — 跳轉到登入頁 {page.url}")
            return results

        polite_sleep()

        cards_data = page.evaluate(
            """
            () => {
                const links = document.querySelectorAll('a[href^="/projects/"]');
                const seen = new Set();
                const out = [];
                for (const a of links) {
                    const m = a.getAttribute('href').match(/^\\/projects\\/(\\d+)/);
                    if (!m || seen.has(m[1])) continue;
                    seen.add(m[1]);
                    const card = a.closest('[class*="JobPostItem"]') || a.closest('article') || a;
                    const isFeatured = card.className && card.className.includes('Featured');
                    out.push({
                        id: m[1],
                        text: card.innerText || '',
                        featured: isFeatured,
                    });
                }
                return out;
            }
            """
        )

        page_count = 0
        skipped_eng = 0
        list_url = url
        for c in cards_data:
            if c["featured"]:
                continue
            title, company = _parse_card_text(c["text"])
            if not title:
                continue
            if is_engineering_only(title):
                skipped_eng += 1
                continue
            detail_url = f"https://www.wantedly.com/projects/{c['id']}"
            jd_body = _fetch_jd_body(page, detail_url)
            results.append({
                "source": "wantedly",
                "source_id": c["id"],
                "title": title,
                "company": company,
                "location": "",
                "url": detail_url,
                "raw_jd": jd_body,
                "keyword": keyword,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
            })
            page_count += 1
        # 回列表頁準備下一頁
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        print(f"  [wantedly] '{keyword}' p{page_num}: {page_count} 筆（已排除 featured）" + (f"（略過工程職 {skipped_eng}）" if skipped_eng else ""))
        if not cards_data:
            break
    return results
