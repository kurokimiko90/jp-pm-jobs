"""BizReach scraper — 需登入。

公開頁面只能拿到通用清單；登入後 `/jobs/search?q=KEYWORD&srt=salary_high` 才能精準搜尋
並看到實際公司名（公開頁顯示「企業名は会員のみ表示されます」）。
"""

from datetime import datetime
from urllib.parse import quote

from playwright.sync_api import Page

from analyzer.role_filter import is_engineering_only
from ._common import polite_sleep

JD_SELECTORS = (
    ".pg-job-detail-body, "
    ".pg-job-detail-content, "
    ".pg-job-detail__body, "
    "[class*='jobDetail'], "
    "[class*='job-detail'], "
    "article"
)

PROVIDER_META = {
    "id": "bizreach",
    "name": "BizReach",
    "requires_login": True,
    "base_url": "https://www.bizreach.jp",
    "description": "ビズリーチ ハイクラス転職",
}

LOGIN_INDICATORS = ("/login", "/members/login", "/auth")


def _fetch_jd_detail(page: Page, url: str) -> str:
    """進入詳情頁抓 JD 全文。失敗回空字串。"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"    [bizreach-detail] goto 失敗 {type(e).__name__}: {url[:80]}")
        return ""
    try:
        page.wait_for_selector(JD_SELECTORS, timeout=10000)
    except Exception:
        pass
    polite_sleep(2, 4)
    try:
        text = page.evaluate(
            """
            () => {
                const sel = document.querySelector(
                    '.pg-job-detail-body, .pg-job-detail-content, .pg-job-detail__body, '
                    + "[class*='jobDetail'], [class*='job-detail'], article"
                );
                if (sel) return (sel.innerText || '').trim();
                // fallback: grab all section content
                const sections = document.querySelectorAll('section, .pg-job-detail-section');
                if (sections.length) {
                    return Array.from(sections).map(s => (s.innerText || '').trim()).join('\\n\\n');
                }
                return '';
            }
            """
        )
        return (text or "")[:20000]
    except Exception:
        return ""


def _extract_id_from_href(href: str) -> str:
    """從 /job/view/12345/ 或類似格式抽出 ID。"""
    parts = [p for p in href.split("?")[0].rstrip("/").split("/") if p.isdigit()]
    return parts[-1] if parts else href


def scrape(page: Page, keyword: str, max_pages: int = 3) -> list[dict]:
    results: list[dict] = []
    for page_num in range(1, max_pages + 1):
        url = (
            f"https://www.bizreach.jp/job/j/?qk={quote(keyword)}"
            f"&p={page_num}&srt=hd"
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector(".pg-job-area-jobcassette", timeout=15000)
        except Exception as e:
            print(f"  [bizreach] p{page_num} 載入失敗: {type(e).__name__}")
            continue

        if any(ind in page.url for ind in LOGIN_INDICATORS):
            print(f"  [bizreach] cookie 無效或過期 — 跳轉到 {page.url}")
            return results

        polite_sleep(5, 10)

        cards_data = page.evaluate(
            """
            () => {
                const cassettes = document.querySelectorAll('.pg-job-area-jobcassette');
                const out = [];
                for (const c of cassettes) {
                    const linkEl = c.querySelector(
                        '.pg-job-area-jobcassette-overall-link a, a.pg-job-area-jobcassette-overall-link, a[href*="/job/view/"]'
                    );
                    const nameEl = c.querySelector('.pg-job-name');
                    const companyEl = c.querySelector('.pg-job-area-jobcassette-company-name');
                    const industryEl = c.querySelector('.pg-job-area-jobcassette-company-industry');
                    const incomeEl = c.querySelector('.pg-job-annual-income');
                    out.push({
                        title: nameEl ? nameEl.innerText.trim() : '',
                        href: linkEl ? linkEl.getAttribute('href') : '',
                        company: companyEl ? companyEl.innerText.trim() : '',
                        industry: industryEl ? industryEl.innerText.trim().substring(0, 80) : '',
                        income: incomeEl ? incomeEl.innerText.trim() : '',
                    });
                }
                return out;
            }
            """
        )

        page_count = 0
        skipped_eng = 0
        for c in cards_data:
            if not c["title"] or not c["href"]:
                continue
            if is_engineering_only(c["title"]):
                skipped_eng += 1
                continue
            full_url = c["href"]
            if not full_url.startswith("http"):
                full_url = "https://www.bizreach.jp" + full_url
            source_id = _extract_id_from_href(full_url)
            company = c["company"]
            if "会員" in company:
                company = c["industry"] or ""

            detail_url = full_url.split("?")[0]
            raw_jd = _fetch_jd_detail(page, detail_url)
            if raw_jd:
                print(f"    [bizreach-detail] JD 取得 {len(raw_jd)} 字: {detail_url[:60]}")
            else:
                print(f"    [bizreach-detail] JD 空白: {detail_url[:60]}")

            results.append({
                "source": "bizreach",
                "source_id": source_id,
                "title": c["title"][:200],
                "company": company[:120],
                "location": c["income"][:80],
                "url": detail_url,
                "raw_jd": raw_jd,
                "keyword": keyword,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
            })
            page_count += 1
        print(f"  [bizreach] '{keyword}' p{page_num}: {page_count} 筆" + (f"（略過工程職 {skipped_eng}）" if skipped_eng else ""))
        if not cards_data:
            break
    return results
