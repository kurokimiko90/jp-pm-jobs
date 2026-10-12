"""職位詳情頁 JD 全文補抓。

LinkedIn / BizReach 的搜尋卡片只有標題+公司，無 JD 全文，導致評分被壓低。
此模組點進每個 job 詳情頁，抓 `main` 區塊的純文字當 raw_jd。

兩站詳情頁的 class 名都是 hash 化的 CSS module（如 `_0b3200fc`、
`HiringCompany_xxx`），不可靠；改抓語意穩定的 `<main>` innerText。
"""

from playwright.sync_api import Page

from ._common import polite_sleep

# 跳轉到這些路徑代表 cookie 失效
LOGIN_INDICATORS = ("/login", "/uas/login", "/checkpoint", "/members/login", "/auth")

# JD 上限字數（評分器只需關鍵詞密度，過長純屬雜訊）
MAX_JD_CHARS = 8000
# 低於此長度視為抓取失敗（login wall / 空頁）
MIN_JD_CHARS = 200


def fetch_jd(page: Page, url: str) -> str | None:
    """點進詳情頁抓 JD 全文。失敗回傳 None（不覆蓋既有資料）。"""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"    載入失敗 {url}: {type(e).__name__}")
        return None

    if any(ind in page.url for ind in LOGIN_INDICATORS):
        print(f"    cookie 失效 — 跳轉到 {page.url[:60]}")
        return None

    polite_sleep(3, 6)

    try:
        text = page.evaluate(
            "() => { const m = document.querySelector('main'); "
            "return m ? m.innerText : (document.body ? document.body.innerText : ''); }"
        )
    except Exception as e:
        print(f"    抽取失敗 {url}: {type(e).__name__}")
        return None

    if not text:
        return None
    text = text.strip()
    if len(text) < MIN_JD_CHARS:
        return None
    return text[:MAX_JD_CHARS]
