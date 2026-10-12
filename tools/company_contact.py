"""官網應募窗口探測 — 純規則優先，LLM fallback（tools/direct_apply.py 呼叫）。

三層來源依序：
  1. JD 本文 regex 抽 email（saiyo@/recruit@ 高信心、info@ 降級）
  2. 職缺 URL 已是官網（非求人媒體域名）→ 直接爬同域採用頁
  3. DuckDuckGo HTML 搜「{公司} 中途採用」→ 官網域名 → 抓求人頁

輸出三態 apply_method: email / form / none。
所有網路請求 10s timeout + 每次 fetch 間隔 1s（禮貌性 rate limit）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
FETCH_TIMEOUT = 10
FETCH_INTERVAL = 1.0  # 秒，同一輪內連續 fetch 的間隔
MAX_PAGES_PER_JOB = 6

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}")

# local part → 信心分（採用専用 > 一般窗口 > 通知系排除）
_LOCAL_CONF = [
    (re.compile(r"^(saiyo|saiyou|recruit|recruiting|careers?|jobs?|jinji|hr|talent|entry|oubo)",
                re.I), 0.9),
    (re.compile(r"^(info|contact|inquiry|support|hello|office)", re.I), 0.5),
]
_EXCLUDE_LOCAL = re.compile(
    r"^(no-?reply|noreply|do-?not-?reply|unsubscribe|privacy|press|pr|ir|sales|"
    r"webmaster|postmaster|abuse|mailer-daemon|abc$|xyz$|test|demo|sample|dummy|hoge|"
    r"example|user(name)?$|your[-_.]?(name|mail|email))", re.I)
# 副檔名誤判（img@2x.png 之類）與範例/佔位域名（example.com / xxxxxx.co.jp / hoge.jp）
_EXCLUDE_DOMAIN = re.compile(
    r"(example\.(com|org|net)|\.(png|jpe?g|gif|svg|webp|css|js)$|sentry|wixpress"
    r"|(^|\.)x{2,}\.|(^|\.)(hoge|fuga|sample|dummy)\.)", re.I)

# 求人媒體 / SNS 域名 — 不可能是「公司官網」
JOBBOARD_DOMAINS = (
    "indeed.com", "jp.indeed.com", "green-japan.com", "linkedin.com", "bizreach.jp",
    "wantedly.com", "doda.jp", "mynavi.jp", "rikunabi.com", "en-japan.com",
    "type.jp", "openwork.jp", "vorkers.com", "glassdoor.com", "careercross.com",
    "r-agent.com", "mynavi-agent.jp", "pasonacareer.jp",
    "daijob.com", "japan-dev.com", "tokyodev.com", "hellowork.mhlw.go.jp",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
    "wikipedia.org", "prtimes.jp", "note.com", "qiita.com", "zenn.dev",
    "salesnavi", "hatena", "ameblo.jp", "duckduckgo.com",
)

# ATS / 應募フォーム服務域名（出現 = form 應募方式）
FORM_HOSTS = (
    "herp.careers", "hrmos.co", "open.talentio.com", "talentio.com",
    "en-gage.net", "engage.en-japan.com", "jobcan.jp", "recruiting.cloud",
    "docs.google.com/forms", "forms.gle", "greenhouse.io", "lever.co",
    "ashbyhq.com", "workable.com", "jobs.smartrecruiters.com", "recruitee.com",
)

# 採用頁連結關鍵字（anchor text 或 href）
_CAREERS_HINT = re.compile(
    r"(採用|求人|募集|recruit|careers?|jobs?|join[-_ ]?us|entry|中途)", re.I)
_MIDCAREER_HINT = re.compile(r"(中途|キャリア採用|経験者|career)", re.I)
_NEWGRAD_ONLY = re.compile(r"(新卒|newgrad|graduate)", re.I)

_CJK_RE = re.compile(r"[　-鿿＀-￯]")

# 日本勤務地判定（正向匹配；salary 字串 / 空值 fallback 到 JD CJK 比例）
_JP_LOC = re.compile(
    r"(japan|tokyo|osaka|kyoto|nagoya|fukuoka|yokohama|sapporo|sendai|okinawa|"
    r"日本|東京|大阪|京都|名古屋|福岡|横浜|札幌|仙台|沖縄|北海道|"
    r"リモート|在宅|[都道府県]|２３区|23区)", re.I)
_SALARY_LIKE = re.compile(r"[0-9０-９][0-9０-９,，]*\s*万")


def is_japan_job(job: dict) -> bool:
    """日本勤務地判定：location 有日本地名 → True；明示海外地名 → False；
    location 空 / 是薪資字串（部分來源髒資料）→ 看 JD+標題的 CJK 字元數。"""
    loc = (job.get("location") or "").strip()
    if loc and _JP_LOC.search(loc):
        return True
    if loc and not _SALARY_LIKE.search(loc):
        return False  # 非空、非薪資、無日本地名 → 視為海外（Berlin / Toronto / SF …）
    text = (job.get("raw_jd") or "") + (job.get("title") or "")
    return len(_CJK_RE.findall(text)) >= 20


@dataclass
class ContactResult:
    apply_method: str = "none"          # email / form / none
    emails: list[dict] = field(default_factory=list)  # [{"email","confidence","source"}]
    form_url: str = ""
    careers_url: str = ""
    official_domain: str = ""
    pages_fetched: list[str] = field(default_factory=list)
    notes: str = ""


def _fetch(url: str, state: dict) -> str:
    """取頁面文字（HTML）。超過單 job 頁數上限 / 失敗回空字串。"""
    if len(state.setdefault("fetched", [])) >= MAX_PAGES_PER_JOB:
        return ""
    if state["fetched"]:
        time.sleep(FETCH_INTERVAL)
    try:
        resp = requests.get(url, headers={"User-Agent": UA},
                            timeout=FETCH_TIMEOUT, allow_redirects=True)
        state["fetched"].append(url)
        if resp.status_code != 200:
            return ""
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text or ""
    except Exception:
        state["fetched"].append(url)
        return ""


def score_email(email: str) -> float | None:
    """email → 信心分；排除項回 None。"""
    local, _, domain = email.partition("@")
    if _EXCLUDE_LOCAL.match(local) or _EXCLUDE_DOMAIN.search(domain):
        return None
    for pat, conf in _LOCAL_CONF:
        if pat.match(local):
            return conf
    if re.match(r"^(gmail|yahoo|hotmail|outlook|icloud)\.", domain, re.I):
        return 0.45  # 免費信箱：小公司可能真用，但降級
    return 0.6


def extract_emails(text: str, source: str) -> list[dict]:
    """從任意文本抽 email 候選（去重、按信心排序）。"""
    seen: dict[str, dict] = {}
    for m in EMAIL_RE.finditer(text or ""):
        email = m.group(0).strip(".").lower()
        conf = score_email(email)
        if conf is None:
            continue
        if email not in seen or conf > seen[email]["confidence"]:
            seen[email] = {"email": email, "confidence": conf, "source": source}
    return sorted(seen.values(), key=lambda e: -e["confidence"])


def verify_email_domain(email: str) -> bool:
    """MX（缺 dnspython 時退化為 A record）驗證域名可收信。失敗不擋流程，僅降信心。"""
    domain = email.rpartition("@")[2]
    try:
        import dns.resolver
        return bool(dns.resolver.resolve(domain, "MX", lifetime=5))
    except ImportError:
        import socket
        try:
            socket.gethostbyname(domain)
            return True
        except OSError:
            return False
    except Exception:
        return False


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def is_jobboard(url: str) -> bool:
    d = _domain(url)
    return any(d == j or d.endswith("." + j.split("/")[0]) for j in JOBBOARD_DOMAINS)


_ASSET_EXT_RE = re.compile(r"\.(css|js|png|jpe?g|gif|svg|webp|woff2?|ico|map)(\?|#|$)", re.I)


def find_form_url(html: str, base_url: str) -> str:
    """頁面內的 ATS / 應募フォーム連結。只看 <a> 錨點，排除 CSS/JS 等靜態資源連結
    （en-gage.net 等 ATS 域名整站都在 FORM_HOSTS，若不限定 <a> 標籤，<head> 裡的
    stylesheet <link href="...css"> 會被誤判成應募表單連結）。"""
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\']', html or "", re.I):
        url = urljoin(base_url, m.group(1))
        low = url.lower()
        if _ASSET_EXT_RE.search(low):
            continue
        if any(h in low for h in FORM_HOSTS):
            return url
    # 同域 entry/apply 頁（href 同時帶 entry/apply/応募 與 form 字樣才算）
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\']', html or "", re.I):
        href = m.group(1)
        if _ASSET_EXT_RE.search(href.lower()):
            continue
        if re.search(r"(entry|apply|応募)", href, re.I) and re.search(r"form", href, re.I):
            return urljoin(base_url, href)
    return ""


def find_careers_links(html: str, base_url: str) -> list[str]:
    """頁面內的採用頁連結（中途優先、純新卒頁排除、同域優先）。"""
    base_domain = _domain(base_url)
    scored: list[tuple[float, str]] = []
    seen: set[str] = set()
    for m in re.finditer(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.{0,80}?)</a>',
            html or "", re.S | re.I):
        href, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        blob = f"{href} {text}"
        if not _CAREERS_HINT.search(blob):
            continue
        if _NEWGRAD_ONLY.search(blob) and not _MIDCAREER_HINT.search(blob):
            continue
        url = urljoin(base_url, href.split("#")[0])
        if not url.startswith("http") or url in seen or is_jobboard(url):
            continue
        seen.add(url)
        score = 1.0
        if _MIDCAREER_HINT.search(blob):
            score += 0.5
        if _domain(url) == base_domain:
            score += 0.3
        scored.append((score, url))
    return [u for _, u in sorted(scored, key=lambda t: -t[0])][:4]


def search_official_site(company: str, state: dict) -> str:
    """DuckDuckGo HTML 搜尋公司官網（回首個非求人媒體結果 URL）。"""
    company = re.sub(r"\s+", " ", company or "").strip()
    if not company:
        return ""
    q = f"{company} 採用"
    html = _fetch(f"https://html.duckduckgo.com/html/?q={requests.utils.quote(q)}", state)
    if not html:
        return ""
    for m in re.finditer(r'href=["\']([^"\']*duckduckgo\.com/l/\?[^"\']+)["\']', html):
        qs = parse_qs(urlparse(m.group(1)).query)
        target = (qs.get("uddg") or [""])[0]
        if target.startswith("http") and not is_jobboard(target):
            return target
    # 無 redirect 包裝的直連結果
    for m in re.finditer(r'class="result__a"[^>]+href=["\']([^"\']+)["\']', html):
        if m.group(1).startswith("http") and not is_jobboard(m.group(1)):
            return m.group(1)
    return ""


def _strip_tags(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", text)


def detect(job: dict) -> ContactResult:
    """主入口：對單一職缺跑三層探測（純規則、零 LLM）。"""
    state: dict = {"fetched": []}
    result = ContactResult()

    # ── 第 1 層：JD 本文
    jd = job.get("raw_jd") or ""
    result.emails = extract_emails(jd, "jd")

    # ── 第 2 層：職缺 URL 已是官網
    job_url = job.get("url") or ""
    pages: list[tuple[str, str]] = []  # (url, html)
    if job_url.startswith("http") and not is_jobboard(job_url):
        result.official_domain = _domain(job_url)
        html = _fetch(job_url, state)
        if html:
            pages.append((job_url, html))

    # ── 第 3 層：官網探索（搜尋 → 首頁 → 採用頁）
    if not pages:
        site = search_official_site(job.get("company") or "", state)
        if site:
            result.official_domain = _domain(site)
            html = _fetch(site, state)
            if html:
                pages.append((site, html))

    # 從已有頁面往下追採用頁
    for url, html in list(pages):
        for link in find_careers_links(html, url):
            if len(state["fetched"]) >= MAX_PAGES_PER_JOB:
                break
            sub = _fetch(link, state)
            if sub:
                pages.append((link, sub))
                if not result.careers_url:
                    result.careers_url = link

    # 彙整 email / form
    for url, html in pages:
        text = _strip_tags(html)
        for e in extract_emails(html + " " + text, url):
            if e["email"] not in {x["email"] for x in result.emails}:
                result.emails.append(e)
        if not result.form_url:
            result.form_url = find_form_url(html, url)
        if not result.careers_url and _CAREERS_HINT.search(url):
            result.careers_url = url

    result.emails.sort(key=lambda e: -e["confidence"])
    result.pages_fetched = state["fetched"]

    # MX 驗證最高分候選（失敗降 0.2）
    for e in result.emails[:3]:
        if not verify_email_domain(e["email"]):
            e["confidence"] = round(max(e["confidence"] - 0.2, 0.1), 2)
    result.emails.sort(key=lambda e: -e["confidence"])

    if result.emails:
        result.apply_method = "email"
    elif result.form_url:
        result.apply_method = "form"
    else:
        result.apply_method = "none"
    return result


def llm_extract(pages_text: str) -> dict:
    """LLM fallback：規則抽不到時，把採用頁文本丟 miko-ws 抽結構化窗口。
    回 {"application_email": "", "form_url": "", "careers_url": "", "notes": ""}。"""
    import json

    from tools import miko_llm
    if not miko_llm.is_available():
        return {}
    prompt = f"""以下は企業の採用関連ページのテキストです。中途採用の応募窓口を抽出してください。

出力は JSON のみ（説明不要）:
{{"application_email": "応募用メールアドレス（なければ空文字）",
  "form_url": "応募フォームの URL（なければ空文字）",
  "careers_url": "中途採用情報ページの URL（なければ空文字）",
  "notes": "補足（20字以内）"}}

注意: 実在する記載のみ抽出。推測でアドレスを作らない。新卒専用窓口は除外。

# ページテキスト
{pages_text[:8000]}
"""
    try:
        raw = miko_llm.text(prompt, timeout=120,
                            opts={"accept": {"includesAll": ["application_email"]}})
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}
