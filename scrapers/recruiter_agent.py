"""Recruiter Agent (リクルートエージェント) — Gmail 郵件職缺來源。

從担当キャリアアドバイザー的推薦郵件中提取職缺。三種郵件格式：
  1. 求人一覧（plain text）— 多職缺，▼ 分隔
  2. 新着おすすめ求人（HTML）— 多職缺，▼ 開頭
  3. オファー（HTML）— 單一職缺 offer

不走 Playwright，直接用 Gmail API 讀郵件。
"""

from __future__ import annotations

import base64
import re
import unicodedata
from datetime import datetime
from html.parser import HTMLParser

from inbox.auth import get_service
from tracker.db import connect, update_raw_jd

PROVIDER_META = {
    "id": "recruiter_agent",
    "name": "リクルートエージェント",
    "requires_login": False,
    "base_url": "https://mypage.r-agent.com",
    "description": "担当キャリアアドバイザーからの推薦求人（Gmail）",
}

# 担当 CA のメールアドレスは config/scraping.yaml の recruiter_agent.sender_query
# で設定（例: "from:xxx@r-agent.com"）。未設定なら本 provider は自動 skip。
from tools.app_config import load as _load_app_config

_RA_CFG = _load_app_config("scraping").get("recruiter_agent") or {}
SENDER_QUERY = _RA_CFG.get("sender_query", "")
URL_RE = re.compile(r"https://mypage\.r-agent\.com/joboffers/(\d+)\S*")
SALARY_RE = re.compile(r"(\d{3,4})万円[～〜~](\d{3,4})万円")

# /joboffers/{id} は裸 URL だと必ず「エラーが発生しました」。ただし必要なのは
# 求人ごとのトークンではなく **流入元タグ** で、`job_referral=jobSearch`（r-agent
# 自身の求人検索からの遷移タグ）は全求人で通る（2026-08 実測 16/16）。
#
# 以前は推薦メールの URL（job_referral=recommendPost / aiScout / i2aJob*）を
# Gmail から毎回引いていたが、(a) 1 回 70 秒以上かかる (b) メール由来でない求人
# （求人検索・気になる経由、全体の約 6 割）は開けない (c) スカウト系タグは
# /onboarding/scout_acceptance へ飛ばされることがある、の 3 点で破綻していた。
JOBOFFER_REFERRAL = _RA_CFG.get("joboffer_referral", "jobSearch")


def joboffer_url(source_id: str | int) -> str:
    """r-agent の求人詳細 URL（ログイン済みブラウザで開ける形）を返す。"""
    return (f"https://mypage.r-agent.com/joboffers/{source_id}"
            f"?job_referral={JOBOFFER_REFERRAL}")

# Footer markers — stop collecting JD content when any of these appear.
# 担当 CA の署名（氏名など）は config の footer_markers で追加する。
_FOOTER_MARKERS = tuple(
    [
        "マイページログイン画面",
        "株式会社インディードリクルートパートナーズ",
        "配信停止については",
        "■本メールについて",
        "面接調整センター",
        "ご転職を検討中のお知り合い",
        "Tel: 050-",
        "■Tel:",
    ]
    + list(_RA_CFG.get("footer_markers") or [])
)


def _trim_footer(text: str) -> str:
    """Remove email signature / footer from JD text."""
    lines = text.splitlines()
    result: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("--") and len(s) >= 5:
            break
        if any(marker in s for marker in _FOOTER_MARKERS):
            break
        result.append(line)
    return "\n".join(result).strip()


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


class _TextExtractor(HTMLParser):
    """Extract visible text + href from HTML."""

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._hrefs: list[str] = []

    def handle_data(self, data: str):
        self._chunks.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self._hrefs.append(v)

    @property
    def text(self) -> str:
        return "\n".join(self._chunks)

    @property
    def hrefs(self) -> list[str]:
        return self._hrefs


def _extract_html(html: str) -> tuple[str, list[str]]:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text, parser.hrefs


def _parse_plain_block(block: str) -> dict | None:
    """Parse a ▼ block from plain-text 求人一覧."""
    lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
    if not lines:
        return None

    header = _norm(lines[0].lstrip("▼").strip())
    company = header.split("/")[0].strip() if "/" in header else header
    title = "/".join(header.split("/")[1:]).strip() if "/" in header else ""

    salary_raw = ""
    location = ""
    url = ""
    jd_lines = []

    for line in lines[1:]:
        line_n = _norm(line)
        # Stop at footer markers
        if line_n.startswith("--") and len(line_n) >= 5:
            break
        if any(marker in line_n for marker in _FOOTER_MARKERS):
            break
        if line_n.startswith("［仕事の内容］") or line_n.startswith("[仕事の内容]"):
            jd_lines.append(line_n.replace("［仕事の内容］", "").replace("[仕事の内容]", "").strip())
        elif line_n.startswith("［想定年収］") or line_n.startswith("[想定年収]"):
            salary_raw = line_n.replace("［想定年収］", "").replace("[想定年収]", "").strip()
        elif line_n.startswith("［勤務地］") or line_n.startswith("[勤務地]"):
            location = line_n.replace("［勤務地］", "").replace("[勤務地]", "").strip()
        elif URL_RE.search(line_n):
            url = URL_RE.search(line_n).group(0)
        elif not line_n.startswith("[応募状況]") and not line_n.startswith("検討中"):
            jd_lines.append(line_n)

    if not url:
        return None

    source_id = URL_RE.search(url).group(1)
    raw_jd = f"{title}\n{salary_raw}\n{location}\n" + "\n".join(jd_lines)

    return {
        "source": "recruiter_agent",
        "source_id": source_id,
        "title": title or header,
        "company": company,
        "location": location,
        "url": url.split("?")[0],
        "raw_jd": raw_jd.strip(),
        "keyword": "recruiter_recommend",
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }


def _parse_html_jobs(html: str) -> list[dict]:
    """Parse ▼ blocks from HTML emails (新着おすすめ / オファー).

    Strategy: split HTML at ▼, then within each chunk find the joboffers URL.
    This keeps URL-to-block association correct.
    """
    html_n = unicodedata.normalize("NFKC", html)
    chunks = re.split(r"▼", html_n)
    results = []
    seen: set[str] = set()

    for chunk in chunks[1:]:
        url_match = URL_RE.search(chunk)
        if not url_match:
            continue
        sid = url_match.group(1)
        if sid in seen:
            continue
        seen.add(sid)

        parser = _TextExtractor()
        parser.feed(chunk.split("▼")[0])
        text = parser.text
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            continue

        company = lines[0].strip()
        title = ""
        salary_raw = ""
        location = ""
        desc_lines: list[str] = []
        skip_words = ("求人を見る", "ご紹介URL", "LINE", "マイページ",
                      "配信停止", "他の求人", "ログイン", "友だち追加")

        field_re = re.compile(r"^\[?(勤務地|仕事の内容|想定年収|応募状況)")
        for line in lines[1:]:
            if any(marker in line for marker in _FOOTER_MARKERS):
                break
            if any(w in line for w in skip_words):
                continue
            if "万円" in line and ("～" in line or "~" in line):
                salary_raw = line.strip()
            elif field_re.match(line.lstrip("［[")):
                if "勤務地" in line:
                    location = re.sub(r"^[\[［]?勤務地[\]］]?\s*", "", line).strip()
                elif "仕事の内容" in line:
                    desc_lines.append(re.sub(r"^[\[［]?仕事の内容[\]］]?\s*", "", line).strip())
            elif not title:
                title = line.strip()
            elif title and line.strip() and "mypage.r-agent.com" not in line:
                s = line.strip()
                if s.startswith("--") and len(s) >= 5:
                    break
                desc_lines.append(s)

        clean_url = url_match.group(0).split("?")[0]
        desc = "\n".join(desc_lines[:5])  # 最多 5 行描述
        raw_jd = f"{company} {title}\n{salary_raw}\n{location}\n{desc}".strip()

        results.append({
            "source": "recruiter_agent",
            "source_id": sid,
            "title": title or company,
            "company": company,
            "location": location,
            "url": clean_url,
            "raw_jd": raw_jd.strip(),
            "keyword": "recruiter_recommend",
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
        })

    return results


def _get_body(payload: dict) -> tuple[str, str]:
    """Extract (text, html) from Gmail message payload."""
    text_body = ""
    html_body = ""

    body_data = payload.get("body", {}).get("data", "")
    mime = payload.get("mimeType", "")

    if body_data:
        decoded = base64.urlsafe_b64decode(body_data).decode("utf-8")
        if "html" in mime:
            html_body = decoded
        else:
            text_body = decoded
        return text_body, html_body

    for part in payload.get("parts", []):
        part_data = part.get("body", {}).get("data", "")
        if not part_data:
            continue
        decoded = base64.urlsafe_b64decode(part_data).decode("utf-8")
        if part["mimeType"] == "text/plain":
            text_body = decoded
        elif part["mimeType"] == "text/html":
            html_body = decoded

    return text_body, html_body


def fetch_from_gmail(days: int = 7, max_results: int = 50) -> list[dict]:
    """Fetch job listings from recruiter emails.

    Args:
        days: Look back N days.
        max_results: Max emails to scan.

    Returns:
        List of job dicts compatible with upsert_job().
    """
    if not SENDER_QUERY:
        print("  [recruiter_agent] 未設定 sender_query（config/scraping.yaml），跳過")
        return []
    svc = get_service()
    query = f"{SENDER_QUERY} newer_than:{days}d"
    resp = svc.users().messages().list(
        userId="me", maxResults=max_results, q=query
    ).execute()
    messages = resp.get("messages", [])

    if not messages:
        print(f"  [recruiter_agent] 無郵件 (query: {query})")
        return []

    print(f"  [recruiter_agent] 掃描 {len(messages)} 封郵件...")
    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    for m in messages:
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="full"
        ).execute()
        hdrs = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        subject = hdrs.get("Subject", "")

        if "ご状況伺い" in subject or "面談" in subject:
            continue

        text_body, html_body = _get_body(msg["payload"])

        jobs: list[dict] = []

        if text_body and "▼" in text_body:
            blocks = re.split(r"▼", text_body)
            for block in blocks[1:]:
                job = _parse_plain_block("▼" + block)
                if job:
                    jobs.append(job)

        if html_body and "▼" in html_body and not jobs:
            jobs = _parse_html_jobs(html_body)

        if html_body and not jobs and URL_RE.search(html_body):
            text, hrefs = _extract_html(html_body)
            text_n = _norm(text)
            for href in hrefs:
                match = URL_RE.search(href)
                if match:
                    sid = match.group(1)
                    if sid in seen_ids:
                        continue
                    title_candidates = re.findall(r"【(.+?)】", text_n)
                    title = next(
                        (t for t in title_candidates if "URL" not in t and "LINE" not in t),
                        subject,
                    )
                    company_match = re.search(r"▼(.+?)[\n\r]", text_n)
                    salary_match = SALARY_RE.search(text_n)
                    jobs.append({
                        "source": "recruiter_agent",
                        "source_id": sid,
                        "title": title,
                        "company": company_match.group(1).strip() if company_match else "",
                        "location": "",
                        "url": href.split("?")[0],
                        "raw_jd": _trim_footer(text_n)[:1000],
                        "keyword": "recruiter_recommend",
                        "scraped_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    break

        for job in jobs:
            if job["source_id"] not in seen_ids:
                seen_ids.add(job["source_id"])
                all_jobs.append(job)

    print(f"  [recruiter_agent] 提取 {len(all_jobs)} 筆職缺（{len(messages)} 封郵件）")
    return all_jobs


# JD content selectors to try in order
_JD_SELECTORS = [
    ".jobDetail__body",
    ".job-detail__description",
    ".detail-description",
    "#job-description",
    "[data-testid='job-description']",
    ".jobDetailText",
    ".offer-detail",
    ".offerDetail",
    "article",
    "main",
]

_NAV_WORDS = frozenset([
    "ホーム", "求人ポスト", "求人検索", "気になる", "メッセージ",
    "アドバイザー紹介", "応募する", "興味なし", "募集要項",
    "選考・企業概要", "職務内容", "企業・求人の特色", "年収", "勤務地",
])


def _normalize_fw(s: str) -> str:
    """全角英数字・全角スペースを半角に変換（会社名の表記揺れ対策）。"""
    result = []
    for c in s:
        cp = ord(c)
        if 0xFF01 <= cp <= 0xFF5E:
            result.append(chr(cp - 0xFEE0))
        elif cp == 0x3000:  # 全角スペース → 半角スペース
            result.append(" ")
        else:
            result.append(c)
    return " ".join("".join(result).split()).strip()  # 連続スペースも正規化


def _extract_title_from_jd(jd_text: str, company: str) -> str:
    """JD 本文から職銜を抽出。company の次の非ナビ行を返す。"""
    company_norm = _normalize_fw(company)
    lines = [l.strip() for l in jd_text.splitlines() if l.strip() and l.strip() not in _NAV_WORDS]
    for i, line in enumerate(lines):
        line_norm = _normalize_fw(line)
        # 完全一致 or DB側に略称サフィックス（例: (NSSOL)）がある場合も許容
        if (line_norm == company_norm or company_norm.startswith(line_norm)) and i + 1 < len(lines):
            candidate = lines[i + 1]
            if candidate not in _NAV_WORDS and "万" not in candidate:
                return candidate
    return ""


# Text to strip from fetched JD (login prompts, nav, etc.)
_JD_NOISE = re.compile(
    r"(ログイン|マイページ|ご登録|Cookie|プライバシー|利用規約|求人一覧へ戻る"
    r"|ページトップへ|このページを印刷|お気に入り|応募する|気になる)",
)

# r-agent 詳細ページは固定テンプレートの dt/dd（ラベル行→値行）が innerText で
# フラットに並ぶ。大区分は「## 」、個別項目は「**label**」で境界を入れて可読にする。
_JD_SECTION_LABELS = frozenset([
    "職務内容", "勤務条件", "勤務地", "制度・福利厚生", "選考・企業概要", "選考内容", "企業概要",
])
_JD_FIELD_LABELS = frozenset([
    "年収", "休日",
    "企業・求人の特色", "仕事内容", "求める能力・経験", "資格", "学歴",
    "雇用形態", "試用期間", "給与", "通勤手当", "勤務時間", "残業", "残業手当",
    "休日・休暇", "有給休暇", "その他", "社会保険", "備考",
    "配属先", "転勤", "住所", "最寄駅", "喫煙環境",
    "制度", "寮・社宅", "退職金", "その他制度", "制度備考",
    "採用人数", "面接回数", "選考",
    "企業名", "代表取締役", "設立", "従業員数", "資本金", "平均年齢",
    "本社所在地", "本社以外の事務所", "事業内容・商品・販売先等", "関連会社",
    "株式公開", "主な株主", "企業URL",
])


# 詳細本文の開始位置。これより前は一覧カード由来の要約（年収/勤務地/休日）なので、
# 同名ラベルでも大区分に昇格させない（「勤務地」が 2 回 ## になるのを防ぐ）。
_JD_BODY_START = "職務内容"

# 詳細ページ innerText に必ず混ざるナビ / ボタン / ステータスバッジ行。JD 本文では
# ないので落とす。`ragent_search._clean_detail` も同じ集合を使う（定義はここが唯一）。
# 「選考・企業概要」はタブ見出しであると同時に `_fetch_detail` が入れる区分マーカー
# （【選考・企業概要】）でもあるため、ここでは落とさず空セクションとして扱う。
NAV_LINES = frozenset([
    "ホーム", "求人ポスト", "求人検索", "気になる", "メッセージ", "アドバイザー紹介",
    "応募する", "興味なし", "２タップで完了!", "2タップで完了!", "募集要項",
    "この求人を見た人はこんな求人も見ています", "求人一覧へ戻る", "ページトップへ",
    "TOP画面へ", "ヘルプ・お問い合わせ", "リクルートID規約", "利用規約",
    "プライバシーポリシー", "新着", "おすすめ企業オファー", "受付終了", "応募済み",
])


def _delabel(line: str) -> str:
    """既に付いている見出しマーカーを外す（再実行を冪等にするため）。"""
    bare = line.strip()
    if bare.startswith("## "):
        bare = bare[3:].strip()
    elif len(bare) > 4 and bare.startswith("**") and bare.endswith("**"):
        bare = bare[2:-2].strip()
    return bare.strip("【】")


def structure_jd_lines(lines: list[str]) -> str:
    """r-agent の dt/dd フラット行列を見出し付き Markdown へ整形する。

    ラベル行（"給与" 等）は常に単独行で直後が値というテンプレート前提で、
    既知ラベルを境界に分割するだけ（意味解析ではないので新ラベルの追加は
    ``_JD_SECTION_LABELS`` / ``_JD_FIELD_LABELS`` に足すだけでよい）。
    整形済みテキストを再度通しても結果は変わらない（冪等）。
    """
    out: list[str] = []
    buf: list[str] = []
    in_body = False

    def flush() -> None:
        if buf:
            out.append("\n".join(buf).strip())
            buf.clear()

    for raw in lines:
        line = raw.strip()
        if not line or line in NAV_LINES:
            continue
        label = _delabel(line)
        if label == _JD_BODY_START:
            in_body = True
        if in_body and label in _JD_SECTION_LABELS:
            flush()
            out.append(f"\n## {label}")
        elif label in _JD_FIELD_LABELS or label in _JD_SECTION_LABELS:
            # 本文開始前の同名ラベルはカード要約の項目 → 大区分にしない
            flush()
            out.append(f"\n**{label}**")
        else:
            buf.append(line)
    flush()
    return "\n".join(out).strip()


def _extract_jd_text(page) -> str:
    """Extract JD text from an r-agent joboffers page."""
    import time
    time.sleep(2)

    # r-agent first renders a compact recommendation card, then hydrates the
    # detailed sections asynchronously.  Reading immediately after
    # ``domcontentloaded`` can therefore return only ~200 chars even though
    # the full JD is already available a few seconds later.
    try:
        page.wait_for_function(
            """() => {
                const text = document.body?.innerText || '';
                // The tab labels are present before the detail panel is
                // hydrated, so checking only for "仕事内容" is too early.
                return text.includes('求める能力・経験') &&
                       (text.includes('勤務条件') || text.includes('給与'));
            }""",
            timeout=15000,
        )
        time.sleep(1)
    except Exception:
        # Some older/expired offers do not expose the detailed section; keep
        # the existing selector and body-text fallbacks for those pages.
        pass

    candidates: list[str] = []
    for selector in _JD_SELECTORS:
        try:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text()
                if text and len(text.strip()) > 200:
                    candidates.append(text.strip())
        except Exception:
            continue

    # Fallback: get all visible text from body, filter noise
    try:
        text = page.inner_text("body")
        lines = [l.strip() for l in text.splitlines() if l.strip() and not _JD_NOISE.search(l)]
        body_text = "\n".join(lines[:150])
        if len(body_text) > 200:
            candidates.append(body_text)
    except Exception:
        pass

    # The first matching element can be only the compact recommendation card
    # while the hydrated detail sections live elsewhere in the page.  Prefer
    # the longest candidate so we retain the actual JD instead of the card.
    if not candidates:
        return ""
    return structure_jd_lines(max(candidates, key=len).splitlines())


def _collect_full_urls(days: int = 90, max_results: int = 200) -> dict[str, str]:
    """Re-fetch emails to get full URLs (with query params) keyed by source_id."""
    if not SENDER_QUERY:
        return {}
    svc = get_service()
    query = f"{SENDER_QUERY} newer_than:{days}d"
    resp = svc.users().messages().list(userId="me", maxResults=max_results, q=query).execute()
    messages = resp.get("messages", [])

    full_urls: dict[str, str] = {}
    for m in messages:
        msg = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        text_body, html_body = _get_body(msg["payload"])

        # HTML: extract from href attributes
        for href in re.findall(r'href="(https://mypage\.r-agent\.com/joboffers/[^"]+)"', html_body or ""):
            match = URL_RE.search(href)
            if match:
                sid = match.group(1)
                if sid not in full_urls:
                    full_urls[sid] = href

        # Plain text: extract bare URLs (仲介メールは text-only が多い)
        for url in re.findall(r'https://mypage\.r-agent\.com/joboffers/\S+', text_body or ""):
            url = url.rstrip(')')  # strip trailing punctuation
            match = URL_RE.search(url)
            if match:
                sid = match.group(1)
                if sid not in full_urls:
                    full_urls[sid] = url

    return full_urls


def enrich_jds(page, min_jd_len: int = 300, batch_size: int = 50) -> int:
    """Fetch full JD from mypage.r-agent.com for jobs with short raw_jd.
    Uses ``joboffer_url()`` — bare /joboffers/{id} always errors on r-agent.

    Args:
        page: Playwright Page (must be logged into r-agent as 胡様's account).
        min_jd_len: Jobs with raw_jd shorter than this will be enriched.
        batch_size: Max jobs to process per run.

    Returns:
        Number of jobs enriched.
    """
    import time

    # Get jobs needing enrichment
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, source_id, title, company FROM jobs "
            "WHERE source = 'recruiter_agent' AND length(COALESCE(raw_jd,'')) < ? "
            "ORDER BY id DESC LIMIT ?",
            (min_jd_len, batch_size),
        ).fetchall()

    if not rows:
        print("  [recruiter_agent_cdp] 補全対象なし")
        return 0

    print(f"  [recruiter_agent_cdp] {len(rows)} 筆短 JD 待補全")

    # Reopen a fresh page — the login-check page may have been closed by r-agent's JS
    try:
        ctx = page.context
        page.close()
        page = ctx.new_page()
    except Exception:
        pass  # if page was already closed, ctx.new_page() still works

    enriched = 0
    for row in rows:
        job_id = row["id"]
        sid = row["source_id"]
        company = row["company"]
        if not sid:
            print(f"  ✗ [{job_id}] {company} — source_id なし")
            continue
        url = joboffer_url(sid)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            jd_text = _extract_jd_text(page)
            if jd_text and len(jd_text) > 200:
                update_raw_jd(job_id, jd_text[:5000])
                enriched += 1
                # title == company の場合、JD から正しい職銜を抽出して上書き
                if row["title"] == company:
                    extracted = _extract_title_from_jd(jd_text, company)
                    if extracted:
                        with connect() as c:
                            c.execute("UPDATE jobs SET title=? WHERE id=?", (extracted, job_id))
                        print(f"  ✓ [{job_id}] {company} title fixed → {extracted[:40]}")
                    else:
                        print(f"  ✓ [{job_id}] {company} ({len(jd_text)} chars)")
                else:
                    print(f"  ✓ [{job_id}] {company} ({len(jd_text)} chars)")
            else:
                print(f"  ✗ [{job_id}] {company} — JD 不足 ({len(jd_text)} chars, url={url[:60]})")
        except Exception as e:
            print(f"  ✗ [{job_id}] {company} — {type(e).__name__}: {e}")
        time.sleep(1.5)

    print(f"  [recruiter_agent_cdp] 完成：enriched {enriched}/{len(rows)}")
    return enriched
