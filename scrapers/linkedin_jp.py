"""LinkedIn Japan scraper — 需登入。

主搜尋 URL（24h 內新發布）:
    /jobs/search?keywords=Product+Manager&location=Japan&f_TPR=r86400
"""

from datetime import datetime
from urllib.parse import quote_plus

from playwright.sync_api import Page

from analyzer.role_filter import is_engineering_only
from ._common import polite_sleep

PROVIDER_META = {
    "id": "linkedin_jp",
    "name": "LinkedIn Japan",
    "requires_login": True,
    "base_url": "https://www.linkedin.com",
    "description": "LinkedIn 日本求人",
}

LOGIN_INDICATORS = ("/login", "/uas/login", "/checkpoint")

# LinkedIn 每頁滿量 25 筆；未滿一頁即代表沒有下一頁。
_PAGE_SIZE = 25
_CARD_SELECTOR = (
    "div.job-card-container, li[data-occludable-job-id], a.job-card-container__link"
)

def _fetch_jd_detail(page: Page, url: str) -> tuple[str, str]:
    """Open a LinkedIn job detail page, return (jd_text, salary_text)."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"    [detail] goto 失敗 {type(e).__name__}: {url[:80]}")
        return "", ""
    polite_sleep(2, 4)
    result = page.evaluate(r"""() => {
        // force-expand all "show more" buttons via dispatchEvent
        document.querySelectorAll('button').forEach(b => {
            if (/more|show|展開|詳細/i.test(b.innerText)) {
                try {
                    b.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                } catch(e) {}
            }
        });
        // also try the expandable-text-box's own expand mechanism
        document.querySelectorAll('[data-testid="expandable-text-box"] button, footer button').forEach(b => {
            try { b.dispatchEvent(new MouseEvent('click', {bubbles: true})); } catch(e) {}
        });
        return null;
    }""")
    polite_sleep(1, 2)
    result = page.evaluate(r"""() => {
        const box = document.querySelector('[data-testid="expandable-text-box"]');
        const jd = box ? box.innerText.trim() : '';
        const pageText = document.body.innerText;
        const salMatch = pageText.match(/年収[：:]?\s*[\d,]+万?.*?[\d,]+万?/);
        return { jd: jd, salary: salMatch ? salMatch[0] : '' };
    }""")
    return result.get("jd", ""), result.get("salary", "")


def scrape(
    page: Page,
    keyword: str,
    max_pages: int = 3,
    recent_only: bool = True,
    seen_ids: set | None = None,
) -> list[dict]:
    # seen_ids: 本次 scrape session 已抓過 + 在庫已存在的 job id（跨 keyword / 跨 run
    # 共享）。命中者不重複開詳情頁——多個 PM 類 keyword 會搜出大量重疊的熱門職缺，
    # 若不去重，靠前的熱門職缺會被每個 keyword 各開一次詳情頁，擠佔靠後職缺的抓取時間。
    if seen_ids is None:
        seen_ids = set()
    results: list[dict] = []
    time_filter = "&f_TPR=r604800"  # 7 days; 改成 r86400 = 24h
    if not recent_only:
        time_filter = ""

    for page_num in range(1, max_pages + 1):
        start = (page_num - 1) * 25
        url = (
            f"https://www.linkedin.com/jobs/search/"
            f"?keywords={quote_plus(keyword)}&location=Japan{time_filter}&start={start}"
        )
        # goto：網路抖動時重試 1 次
        loaded = False
        for attempt in range(2):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                loaded = True
                break
            except Exception as e:
                print(f"  [linkedin] p{page_num} goto {type(e).__name__}"
                      + ("，重試" if attempt == 0 else "，放棄後續頁"))
                if attempt == 0:
                    polite_sleep(2, 4)
        if not loaded:
            break  # 連續分頁，一頁載不動則後續頁多半也不通

        # 等職缺卡片：等不到 = 該頁無結果（keyword 結果不足一頁，翻過頭了）→ 停止翻頁，
        # 不再對後續空頁做 goto + 白等（原本每個空頁浪費 ~15s 且徒增反爬觸發）。
        try:
            page.wait_for_selector(_CARD_SELECTOR, timeout=12000)
        except Exception:
            msg = "無職缺" if page_num == 1 else f"p{page_num}: 已到底（無更多職缺）"
            print(f"  [linkedin] '{keyword}' {msg}")
            break

        if any(ind in page.url for ind in LOGIN_INDICATORS):
            print(f"  [linkedin] cookie 無效或過期 — 跳轉到 {page.url}")
            return results

        polite_sleep(5, 10)

        # 滾動觸發 lazy-load 全部 25 筆
        page.evaluate(
            """
            async () => {
                const container = document.querySelector('.jobs-search-results-list, main');
                if (!container) return;
                for (let i = 0; i < 10; i++) {
                    container.scrollBy(0, 600);
                    await new Promise(r => setTimeout(r, 400));
                }
            }
            """
        )
        polite_sleep(2, 4)

        # LinkedIn 2026 版 DOM：每頁 25 筆 li[data-occludable-job-id]。
        # id 從 li 屬性拿（全部都有），url 用 id 拼接（不依賴 lazy-render 的 a[href]），
        # title 用 a[aria-label]（去掉 " with verification" 後綴），company 用 subtitle。
        cards_data = page.evaluate(
            """
            () => {
                const cards = document.querySelectorAll('li[data-occludable-job-id]');
                const out = [];
                const seen = new Set();
                for (const c of cards) {
                    const id = c.getAttribute('data-occludable-job-id');
                    if (!id || seen.has(id)) continue;

                    const link = c.querySelector('a[aria-label]');
                    let title = link ? (link.getAttribute('aria-label') || '').trim() : '';
                    title = title.replace(/\\s+with verification$/i, '').trim();
                    if (!title) {
                        const tEl = c.querySelector('.artdeco-entity-lockup__title');
                        title = tEl ? tEl.innerText.trim().split('\\n')[0] : '';
                    }

                    const companyEl = c.querySelector(
                        '.artdeco-entity-lockup__subtitle, .job-card-container__primary-description'
                    );
                    const locationEl = c.querySelector(
                        '.artdeco-entity-lockup__caption, .job-card-container__metadata-item'
                    );

                    seen.add(id);
                    out.push({
                        id,
                        title,
                        company: companyEl ? companyEl.innerText.trim() : '',
                        location: locationEl ? locationEl.innerText.trim() : '',
                        url: 'https://www.linkedin.com/jobs/view/' + id + '/',
                    });
                }
                return out;
            }
            """
        )

        page_count = 0
        skipped_eng = 0
        skipped_dup = 0
        for c in cards_data:
            if not c["title"]:
                continue
            if is_engineering_only(c["title"]):
                skipped_eng += 1
                continue
            jid = c["id"]
            base = {
                "source": "linkedin_jp",
                "source_id": jid,
                "title": c["title"][:200],
                "company": c["company"][:120],
                "location": c["location"][:80],
                "url": c["url"],
                "keyword": keyword,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
            }
            if jid in seen_ids:
                # 已抓過（前面的 keyword 或在庫既有）→ 不重開詳情頁，僅回傳輕量記錄
                # 更新 last_seen（不帶 raw_jd，upsert 的 COALESCE 會保留既有 JD）。
                results.append(base)
                skipped_dup += 1
                continue
            seen_ids.add(jid)
            jd_body, salary_hint = _fetch_jd_detail(page, c["url"])
            raw_jd = jd_body
            if salary_hint and salary_hint not in jd_body:
                raw_jd = f"【年収】{salary_hint}\n\n{jd_body}"
            results.append({**base, "raw_jd": raw_jd})
            page_count += 1
        _extra = []
        if skipped_eng:
            _extra.append(f"略過工程職 {skipped_eng}")
        if skipped_dup:
            _extra.append(f"略過重複 {skipped_dup}")
        print(f"  [linkedin] '{keyword}' p{page_num}: {page_count} 筆"
              + (f"（{'、'.join(_extra)}）" if _extra else ""))
        # 未滿一頁 → 沒有下一頁，提早結束（多數 keyword 結果 < 25，只需跑 1 頁；
        # 這是消掉 p2/p3 空頁 TimeoutError 的主力優化）。
        if len(cards_data) < _PAGE_SIZE:
            break
    return results
