"""Indeed Japan scraper — 不需登入。"""

import re
from datetime import datetime
from urllib.parse import quote

from playwright.sync_api import Page

from analyzer.role_filter import is_engineering_only
from ._common import polite_sleep

PROVIDER_META = {
    "id": "indeed_jp",
    "name": "Indeed Japan",
    "requires_login": False,
    "base_url": "https://jp.indeed.com",
    "description": "Indeed 日本求人検索",
}


JD_SELECTORS = (
    "#viewJobSSRRoot, #jobDescriptionText, "
    "div.jobsearch-JobComponent-description"
)


# --- raw_jd の構造化 -------------------------------------------------------
# Indeed は集約サイトなので JD の書式が掲載元ごとに違う。共通するのは
# (1) 詳細ページ側が付ける大区分見出し（「仕事内容」「求める人材」…）
# (2) 掲載元が本文に書く 【…】/■ の小見出し
# の 2 層で、どちらも独立行に現れる。この 2 つを `## `/`**…**` に昇格させれば
# `JdViewer` がそのまま目次付きで描ける（意味解析はしない・情報の欠落なし）。
# ラベル定義は `scrapers/recruiter_agent.py` の同名定数と役割が同じだが、
# Indeed 側のテンプレート（リクルートエージェント転載分を含む）は語彙が違うため
# 共有せず別に持つ。

_SECTION_LABELS = frozenset([
    "仕事内容", "仕事の内容", "職務内容詳細", "職務内容", "業務内容",
    "求める人材", "応募資格", "応募資格について", "必要な経験・能力等", "求める人物像",
    "アピールポイント", "企業・求人の特色",
    "待遇面(給与・福利厚生など)", "待遇面（給与・福利厚生など）", "待遇・福利厚生",
    "勤務条件", "募集要項", "働き方",
    "選考プロセス", "選考内容", "選考について",
    "会社概要", "企業概要", "会社情報", "企業情報",
    "採用企業情報・求人取扱いエージェント", "求人エントリーにあたって",
])

_FIELD_LABELS = frozenset([
    "勤務地", "勤務地所在地", "勤務地備考", "勤務地詳細", "配属先情報", "アクセス", "最寄駅",
    "就業時間", "勤務時間", "勤務時間・曜日", "勤務形態", "残業", "休日", "休日休暇", "休日・休暇",
    "給与", "年収", "想定年収", "賃金形態", "給与補足", "一律手当", "諸手当",
    "雇用形態", "試用期間", "契約期間", "変更の範囲",
    "福利厚生", "社会保険", "その他制度", "受動喫煙対策", "喫煙環境", "退職金", "寮・社宅",
    "学歴・資格", "学歴", "資格", "語学力", "採用人数", "面接回数", "募集職種", "職種",
    "企業名", "求人名", "代表者名", "本社所在地", "業種", "事業内容", "設立", "資本金",
    "従業員数", "株式公開", "主な株主", "決算情報", "代表電話番号", "企業URL",
    "募集背景", "具体的には", "その他", "備考",
])

# ヘッダー検索窓（キーワード/勤務地/求人検索）と、本文でないボタン・バッジ行。
_NOISE_LINES = frozenset([
    "キーワード", "求人検索", "応募画面に進む", "応募先へ進む", "問題を報告する",
    "&nbsp;", "この求人に応募する", "保存", "共有", "求人を報告",
])
_NOISE_RE = re.compile(
    r"^(?:\d+件のクチコミ|slide\d+ of \d+|\d+ / \d+|\d+\+?日前|新着|_{4,}|[=\-─━]{4,})$"
)
# 掲載元が本文に書く小見出し。「■…：値」は見出しではなく「ラベル：値」の本文行、
# 「■…を担当。」は普通の箇条書きなので、句読点とコロンを含む行は昇格させない。
_BRACKET_RE = re.compile(r"^【([^】]{1,30})】$")
_SYMBOL_RE = re.compile(
    r"^[■◆▼◇◎●]+\s*([^\s。、！？：:][^。、！？：:]{0,29}?)\s*[■◆▼◇◎●]*$"
)


# 記号も既知ラベルも使わない JD（英文の自由記述が多い）向けの最後の手当て。
# 「空行の直後に来る、句点で終わらない短い行」＝見出し、という体裁だけの判定。
_PLAIN_TAIL_RE = re.compile(r"[.,:;、。!?？！]$")
_PLAIN_HEAD_RE = re.compile(r"^[・\-*#\d●■◆▼◇○（(【<※=_]")
_PLAIN_MAX_LEN = 40
_PLAIN_MIN_HITS = 2


def _plain_headings(text: str) -> set[str]:
    """体裁だけで見出しらしい行を拾う。2 本以上見つかったときだけ採用する。"""
    lines = [line.strip() for line in text.split("\n")]
    hits = {
        line for i, line in enumerate(lines)
        if line and len(line) <= _PLAIN_MAX_LEN
        and (i == 0 or not lines[i - 1])
        and not _PLAIN_TAIL_RE.search(line)
        and not _PLAIN_HEAD_RE.match(line)
        and "。" not in line
    }
    return hits if len(hits) >= _PLAIN_MIN_HITS else set()


def _delabel(line: str) -> tuple[str, str | None]:
    """付いている見出しマーカーを外し、(素の文字列, 既存の階層) を返す。

    既に整形済みの行は付いていた階層のまま素通しさせる。これが
    `structure_indeed_jd()` の冪等性を担保する — `■課題解決` は昇格後に元の
    パターンへ一致しなくなり、`【仕事内容】` は逆に素の文字列だけ見ると大区分に
    昇格してしまう（本来は掲載元が本文に書いた小見出し）ため。
    """
    bare = line.strip()
    if bare.startswith("## "):
        return bare[3:].strip(), "section"
    if len(bare) > 4 and bare.startswith("**") and bare.endswith("**"):
        return bare[2:-2].strip(), "field"
    return bare, None


def structure_indeed_jd(text: str) -> str:
    """Indeed の JD 本文を見出し付き Markdown へ整形する（冪等）。

    既知の大区分は `## `、【…】/■ の小見出しと既知の項目ラベルは `**…**` に
    昇格させ、検索窓・ボタン・「30+日前」等のノイズ行を落とす。未知の書式は
    そのまま素通しするので、どの掲載元でも壊れない。

    `plain_fallback` は記号も既知ラベルも 1 つも見つからなかったときだけ内部で
    立てる再試行フラグ（体裁だけの見出し判定）。呼び出し側は指定しない。
    """
    return _structure(text) or ""


def _structure(text: str, plain_fallback: bool = False) -> str:
    out: list[str] = []
    buf: list[str] = []
    plain = _plain_headings(text) if plain_fallback else set()

    def flush() -> None:
        if buf:
            out.append("\n".join(buf).strip())
            buf.clear()

    lines = text.split("\n")
    # 先頭の検索窓ブロック（キーワード→勤務地→求人検索）を丸ごと捨てる。
    # 残すと本文の「勤務地」より前に同名の項目が立って目次が二重になる。
    for i, raw in enumerate(lines[:6]):
        if raw.strip() == "求人検索":
            lines = lines[i + 1:]
            break

    for raw in lines:
        line = raw.strip()
        if not line or line in _NOISE_LINES or _NOISE_RE.match(line):
            continue
        bare, kind = _delabel(line)
        # 既に階層が付いている行は、その階層のまま素通し（＝冪等）。
        if kind == "section":
            flush()
            out.append(f"\n## {bare}")
            continue
        if kind == "field":
            flush()
            out.append(f"\n**{bare}**")
            continue
        match = _BRACKET_RE.match(bare) or _SYMBOL_RE.match(bare)
        if match:
            flush()
            out.append(f"\n**{match.group(1).strip()}**")
        elif bare in _SECTION_LABELS:
            flush()
            out.append(f"\n## {bare}")
        elif bare in _FIELD_LABELS or bare in plain:
            flush()
            out.append(f"\n**{bare}**")
        else:
            buf.append(line)
    flush()
    result = "\n".join(out).strip()
    if not plain_fallback and "## " not in result and "**" not in result:
        return _structure(text, plain_fallback=True)
    return result


def _fetch_jd_body(page: Page, url: str) -> str:
    """進入詳情頁抓 JD 全文。失敗回空字串。"""
    try:
        page.goto(url, wait_until="load", timeout=30000)
    except Exception as e:
        print(f"    [detail] goto 失敗 {type(e).__name__}: {url[:80]}")
        return ""
    try:
        page.wait_for_selector(JD_SELECTORS, timeout=10000)
    except Exception as e:
        try:
            title = page.title()[:60]
            cur = page.url[:80]
        except Exception:
            title, cur = "?", "?"
        print(f"    [detail] selector 失敗 {type(e).__name__} | title='{title}' url={cur}")
        return ""
    polite_sleep()
    try:
        text = page.evaluate(
            """
            () => {
                const el = document.querySelector('#jobDescriptionText')
                    || document.querySelector('div.jobsearch-JobComponent-description')
                    || document.querySelector('#viewJobSSRRoot');
                return el ? (el.innerText || '').trim() : '';
            }
            """
        )
        return structure_indeed_jd((text or "")[:20000])
    except Exception:
        return ""


def scrape(page: Page, keyword: str, max_pages: int = 5) -> list[dict]:
    results: list[dict] = []
    for page_num in range(1, max_pages + 1):
        start = (page_num - 1) * 10
        url = f"https://jp.indeed.com/jobs?q={quote(keyword)}&start={start}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector(
                "div.job_seen_beacon, td.resultContent, h2.jobTitle", timeout=15000
            )
        except Exception as e:
            print(f"  [indeed] p{page_num} 載入失敗: {type(e).__name__}")
            continue
        polite_sleep()

        cards_data = page.evaluate(
            """
            () => {
                const cards = document.querySelectorAll('div.job_seen_beacon, div.cardOutline, td.resultContent');
                const out = [];
                const seen = new Set();
                for (const c of cards) {
                    const link = c.querySelector('h2.jobTitle a, a.jcs-JobTitle');
                    const titleEl = c.querySelector('h2.jobTitle a, h2.jobTitle span[title], a.jcs-JobTitle');
                    const companyEl = c.querySelector('[data-testid="company-name"], span.companyName');
                    const locationEl = c.querySelector('[data-testid="text-location"], div.companyLocation');
                    const href = link ? link.getAttribute('href') : '';
                    const jk = link ? link.getAttribute('data-jk') : '';
                    const key = jk || href;
                    if (!key || seen.has(key)) continue;
                    seen.add(key);
                    out.push({
                        jk,
                        title: titleEl ? (titleEl.innerText || titleEl.getAttribute('title') || '').trim() : '',
                        company: companyEl ? companyEl.innerText.trim() : '',
                        location: locationEl ? locationEl.innerText.trim() : '',
                        href: href.startsWith('http') ? href : ('https://jp.indeed.com' + href),
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
            if not c["title"]:
                continue
            if is_engineering_only(c["title"]):
                skipped_eng += 1
                continue
            source_id = c["jk"] or c["href"].split("?")[0].split("/")[-1]
            # jk 優先 → 用標準 viewjob URL；否則保留原 href 完整查詢字串
            if c["jk"]:
                detail_url = f"https://jp.indeed.com/viewjob?jk={c['jk']}"
            else:
                detail_url = c["href"]
            jd_body = _fetch_jd_body(page, detail_url)
            results.append({
                "source": "indeed_jp",
                "source_id": source_id,
                "title": c["title"][:200],
                "company": c["company"][:120],
                "location": c["location"][:80],
                "url": detail_url,
                "raw_jd": jd_body,
                "keyword": keyword,
                "scraped_at": datetime.now().isoformat(timespec="seconds"),
            })
            page_count += 1
        # 回列表頁繼續下一頁
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        print(f"  [indeed] '{keyword}' p{page_num}: {page_count} 筆" + (f"（略過工程職 {skipped_eng}）" if skipped_eng else ""))
        if not cards_data:
            break  # 這頁完全沒卡片才提早停（純工程職被過濾不算沒結果）
    return results
