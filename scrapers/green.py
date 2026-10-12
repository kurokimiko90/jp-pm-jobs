"""Green（green-japan.com）scraper — 公開，不需登入。

搜尋結果與職缺詳情都內嵌在 Next.js SSR 頁面的 `__NEXT_DATA__` JSON 裡
（`props.pageProps.defaultSearchJobOfferData.jobOffers` / `props.pageProps.jobOffer`），
直接解析 JSON 比靠 DOM class 選擇器更穩定，不受前端樣式改版影響。

搜尋 URL: https://www.green-japan.com/search?keyword=KEYWORD&page=N
"""

import json
import re
from datetime import datetime
from urllib.parse import quote

from playwright.sync_api import Page

from analyzer.role_filter import is_engineering_only
from ._common import polite_sleep

PROVIDER_META = {
    "id": "green",
    "name": "Green",
    "requires_login": False,
    "base_url": "https://www.green-japan.com",
    "description": "Green（グリーン）IT/Web業界求人",
}

_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

_DETAIL_SECTIONS = (
    ("", "title"),
    ("【仕事概要】", "summary"),
    ("【仕事内容】", "detail"),
    ("【応募要件】", "qualification"),
    ("【入社後のキャリア】", "acquisition"),
    ("【募集背景】", "background"),
    ("【給与】", "salaryDetail"),
    ("【勤務地】", "address"),
    ("【福利厚生】", "welfare"),
    ("【休日休暇】", "holiday"),
    ("【選考プロセス】", "process"),
    ("【部署】", "department"),
    ("【会社概要】", "abstract"),
)


def _extract_next_data(page: Page) -> dict | None:
    html = page.content()
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _company_profile_tag(client: dict) -> str:
    """把 Green 的企業結構化資料（員工數/上場狀態/業種/設立年）組成一行標籤。

    這是真實可查證的公開資料（Green 自己的企業頁），不是猜測；
    附在 raw_jd 尾端供 tools.jd_tier_classifier 的規模啟發式規則使用
    （白名單之外，命中知名品牌名失敗時的備援判斷依據）。
    """
    if not client:
        return ""
    employees = client.get("employees") or 0
    stock_name = (client.get("stock") or {}).get("name") or "非上場"
    industries = ", ".join(
        i.get("name", "") for i in (client.get("industryTypes") or []) if i.get("name")
    )
    est_ts = client.get("establishTimestamp") or 0
    est_year = datetime.fromtimestamp(est_ts).year if est_ts else None
    parts = [f"従業員数:{employees}名" if employees else None,
             f"上場:{stock_name}",
             f"業種:{industries}" if industries else None,
             f"設立:{est_year}年" if est_year else None]
    return "【企業データ】" + " / ".join(p for p in parts if p)


def _fetch_jd_detail(page: Page, url: str) -> tuple[str, int | None, int | None]:
    """進入詳情頁，從 __NEXT_DATA__.jobOffer 組出 JD 全文 + 結構化年収（萬円）。

    minSalary/maxSalary 是 Green 自己的結構化欄位，比從 raw_jd 用 regex
    猜測（tools.salary_parser）更準——很多職缺的 salaryDetail 文字只描述
    計算方式（如「基本給＋賞与」）沒有乾淨的數字區間，但 minSalary/maxSalary
    仍然有值。失敗回 ("", None, None)。
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"    [green-detail] goto 失敗 {type(e).__name__}: {url[:80]}")
        return "", None, None
    polite_sleep(2, 4)
    data = _extract_next_data(page)
    if not data:
        return "", None, None
    pp = data.get("props", {}).get("pageProps", {}) or {}
    jo = pp.get("jobOffer") or {}
    parts = []
    for label, key in _DETAIL_SECTIONS:
        text = jo.get(key)
        if text:
            parts.append(f"{label}\n{text}" if label else text)
    profile_tag = _company_profile_tag(pp.get("client") or {})
    if profile_tag:
        parts.append(profile_tag)
    raw_jd = "\n\n".join(parts)[:20000]
    return raw_jd, jo.get("minSalary"), jo.get("maxSalary")


def scrape(page: Page, keyword: str, max_pages: int = 3) -> list[dict]:
    results: list[dict] = []
    for page_num in range(1, max_pages + 1):
        url = f"https://www.green-japan.com/search?keyword={quote(keyword)}&page={page_num}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"  [green] p{page_num} 載入失敗: {type(e).__name__}")
            continue

        data = _extract_next_data(page)
        if not data:
            print(f"  [green] p{page_num} 找不到 __NEXT_DATA__，可能被擋或改版")
            break

        pp = data.get("props", {}).get("pageProps", {}) or {}
        job_offers = (pp.get("defaultSearchJobOfferData") or {}).get("jobOffers") or []
        polite_sleep(2, 4)

        page_count = 0
        skipped_eng = 0
        for jo in job_offers:
            job_id = jo.get("id")
            rel_url = jo.get("jobOfferUrl") or ""
            title = (jo.get("name") or jo.get("title") or "")[:200]
            if not job_id or not rel_url:
                continue
            if is_engineering_only(title):
                skipped_eng += 1
                continue
            detail_url = "https://www.green-japan.com" + rel_url
            raw_jd, salary_min, salary_max = _fetch_jd_detail(page, detail_url)
            if raw_jd:
                print(f"    [green-detail] JD 取得 {len(raw_jd)} 字: {detail_url[:60]}")
            else:
                print(f"    [green-detail] JD 空白: {detail_url[:60]}")

            results.append({
                "source": "green",
                "source_id": str(job_id),
                "title": title,
                "company": ((jo.get("company") or {}).get("name") or "")[:120],
                "location": (jo.get("areaName") or "")[:80],
                "url": detail_url,
                "raw_jd": raw_jd,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "keyword": keyword,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
            })
            page_count += 1
        print(f"  [green] '{keyword}' p{page_num}: {page_count} 筆" + (f"（略過工程職 {skipped_eng}）" if skipped_eng else ""))
        if not job_offers:
            break
    return results
