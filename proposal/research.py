"""研究層 — 会社を**実際に取りに行く**（LLM に会社を想像させない）。

提案パックで一番事故るのは「会社を知らないまま知った風に書く」こと。`--facts` を
人が用意しなければ全部が仮説になる、という設計だと実際には誰も用意しないので、
ここで機械的に取りに行く。

  1. 官網を特定（求人 URL が官網ならそれ、違えば検索）
  2. **同一組織ドメイン**のリンクを種別で採点し、深さ 2 まで巡回
  3. ニュース一覧に当たったら、その場で**個別記事の本文**を開く
  4. 自社発信の外部面（技術ブログ・note）も取る
  5. 本文を抜き出して `_research_raw.md` に**原文断片 + URL + 取得日**で落とす

**LLM はこの段階では一切呼ばない。** ここで取れた原文が、後段（company / product /
hypotheses）の引用照合（Gate F）の照合先になる。取れなければ「取れなかった」と書く
だけで、埋め合わせに想像を混ぜない。

⚠ **提案の質の上限はここで決まる。** 実測（ある応募先 / PdM 職）: コーポレートサイト 4 頁
しか取れなかったとき、紅隊の採点は 事業理解 4/10・根拠 4/10 で、出てきた提案は
どの B2B SaaS にも当てはまる計測論だった。取れていなかったのは外から普通に読める
事実ばかり — 別サブドメインのプロダクトサイト、MBO による独立、価格改定。
**prompt を直しても事業理解は上がらない。素材が無いだけ。**

⚠ **日本企業のコーポレートサイトは JS 描画が多い。** requests だけだと `<title>` しか
取れず（実測: 660 バイト）、研究層が丸ごと空振りする。本文が薄いページはその場で
headless Chromium へ切り替える（`Renderer`。公開ページなのでログインは不要）。

⚠ **採用サイトと本体サイトは別。** 会社名で検索すると `recruit.example.com` が先に
当たることが多いが、そこには事業の説明が無い。サブドメインを剥がした本体
（`example.com`）を優先して取りに行く。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from tools.company_contact import (
    UA, _domain, _strip_tags, is_jobboard, search_official_site,
)
from tools.redact import redact

FETCH_TIMEOUT = 12
FETCH_INTERVAL = 1.0        # 礼儀的 rate limit（同一ドメインへ連投しない）
MAX_PAGES = 16              # `_research_raw.md` に残すページ数の上限
# 取得**試行**の上限。空振り（404・本文が薄いまま・巡回対象外と分かった頁）も
# 数えるので、残す頁数より広く取らないと、失敗が続いたときに後半の種別
# （ニュース個別記事・外部面）が予算切れで一度も試されない
MAX_FETCHES = 26
PAGE_CHARS = 4000           # 1 頁から残す本文の長さ
MIN_BODY_CHARS = 400        # これ未満 = JS 描画。headless で取り直す
RENDER_TIMEOUT = 15000      # ms
RAW_FILE = "_research_raw.md"

# ニュース一覧から本文を開く本数。**ここが提案の事業理解を決める。**
# 一覧ページに載るのは切り詰められた見出しだけで（実測: 「新規事業サービス
# 「〇〇 P...」）、資本構成の変化・価格改定・提携といった、外から取れる中で
# 一番効く事実が全部そこに畳まれている
NEWS_ARTICLES = 6
# 自社発信の外部面（技術ブログ・note）。何を作っているか / どんな技術判断を
# しているかは、コーポレートサイトより開発者向けの発信の方に出る
MAX_OFFSITE = 3
# トップからの巡回深さ。事業ページの下に個別プロダクトのページがぶら下がる
MAX_DEPTH = 2

# 採用・IR 専用サブドメイン。ここが当たったら本体ドメインも見に行く
_SUBDOMAIN_PREFIX = re.compile(
    r"^(recruit|recruiting|careers?|saiyo|saiyou|job|jobs|hrmos|herp|engage|"
    r"ir|corp|corporate|about|info|www)\.")

# 属性型 JP ドメインの第 2 レベル（`example.co.jp` を 1 組織として見るため）
_JP_SLD = frozenset(("co", "or", "ne", "ac", "go", "gr", "ed", "lg", "ad"))

# ページ種別 — (kind, パターン, 基礎点)。点が高い順に巡回する。
# `pricing` が最上位なのは、PdM 提案で一番効く外部事実が「誰が何にいくら払うか」
# だから。事業内容ページは「何をしているか」までしか書いていない
_KINDS: list[tuple[str, re.Pattern, float]] = [
    ("pricing", re.compile(r"(pricing|price|plan(s)?|料金|価格|プラン|費用)", re.I), 3.5),
    ("about", re.compile(r"(company|about|corporate|会社概要|企業情報|私たち|philosophy|mission)", re.I), 3.0),
    ("service", re.compile(r"(service|product|solution|business|事業|製品|サービス|プロダクト)", re.I), 3.0),
    ("case", re.compile(r"(case|works|jirei|事例|導入事例|実績|お客様)", re.I), 2.5),
    ("news", re.compile(r"(news|press|release|topics|ニュース|お知らせ|プレス)", re.I), 2.0),
    ("ir", re.compile(r"(^|/)(ir|investor|株主|決算|有価証券)", re.I), 2.5),
    ("careers", re.compile(r"(recruit|career|採用|求人|中途)", re.I), 1.5),
]
# 自社が発信している外部面。ここは同域ではないが「会社が書いた原文」なので
# Gate F の照合先として同じ資格がある
_OFFSITE_HOST = re.compile(
    r"^(zenn\.dev|note\.com|qiita\.com|speakerdeck\.com|medium\.com|"
    r"[\w-]+\.notion\.site|[\w-]+\.substack\.com)$", re.I)
# 巡回しないもの（法務・問い合わせ・ログイン等、事業理解に効かない頁）。
# faq は外した — 「使えないケース」「制限事項」は製品の制約が一番はっきり出る場所で、
# 提案の前提条件を書くときに効く
_SKIP = re.compile(
    r"(privacy|policy|terms|law|tokutei|特定商取引|個人情報|cookie|sitemap|"
    r"login|signin|signup|mypage|cart|contact|お問い合わせ|blog/tag|/tag/|"
    r"\.(pdf|zip|jpe?g|png|gif|svg|css|js)(\?|#|$))", re.I)
# ニュース一覧の中の「一覧の別の切り口」— 本文ではないので開かない
_NEWS_INDEXISH = re.compile(r"/(category|categories|tag|tags|page|archives?)(/|$)", re.I)
# 日付を含むパス = 個別記事。`classify` に任せると本文の語（「サービス」等）で
# service 判定され、事業ページの取得枠を記事が食う（実測で 1 枠奪われた）
_DATED_PATH = re.compile(r"/(19|20)\d{2}[-/]?(0[1-9]|1[0-2])[-/]?([0-3]\d)?")
# 会社としては告知だが、事業の理解には効かない記事。後ろへ回す（捨てはしない）
_NEWS_NOISE = re.compile(
    r"(休業|移転|偲ぶ|訃報|逝去|年末年始|インターン|1day|仕事体験|会社説明会|"
    r"登壇|出展|selected|選出|受賞|アンバサダー|ウェビナー開催|セミナー)")
# 事業の前提が変わった記事。JD の語と重ならなくても必ず上位へ。
# 実測: JD 語だけで並べると「MBO実施によるグループからの独立」が枠から落ちた —
# 資本構成が変わった直後の会社に「この事業をどう伸ばすか」を提案するとき、
# それを知らずに書いた提案は前提から外れる
_NEWS_SIGNAL = re.compile(
    r"(MBO|EBO|TOB|買収|統合|合併|子会社|資本|出資|資金調達|上場|独立|分社|"
    r"提携|パートナー|価格|料金|改定|値上げ|刷新|リニューアル|正式リリース|"
    r"提供開始|新サービス|新プロダクト|代表取締役|社長交代|事業譲渡)", re.I)
# JD からキーワードを拾うときの雑音（どの求人にも出るので識別に効かない）
_KW_STOP = frozenset((
    "the", "and", "for", "with", "you", "our", "are", "have", "who", "will",
    "job", "work", "team", "http", "https", "www", "com", "株式会社",
    "ポジション", "メンバー", "サービス", "プロダクト", "マネージャー",
    "エンジニア", "ビジネス", "チーム", "スキル", "キャリア", "リモート"))
_KW_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]{2,}|[ァ-ヴー]{3,}")
# 同一種別を何頁まで取るか。事業・料金は複数ページに割れていることが多い
_PER_KIND_LIMIT = {"service": 3, "about": 2, "pricing": 2, "case": 2,
                   "news": 1, "ir": 1, "careers": 1, "other": 1}
# トップ以外でも、この種別のページからは更に下へ辿る（製品の個別ページを取る）
_EXPAND_KINDS = ("top", "service", "about", "pricing")


@dataclass
class Page:
    url: str
    kind: str
    title: str
    text: str


@dataclass
class ResearchResult:
    official: str = ""
    pages: list[Page] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.pages) >= 2


def _can_fetch(url: str, state: dict, budget: int = MAX_FETCHES) -> bool:
    fetched = state.setdefault("fetched", [])
    return len(fetched) < budget and url not in fetched


def _get(url: str, state: dict) -> str:
    if not _can_fetch(url, state):
        return ""
    if state["fetched"]:
        time.sleep(FETCH_INTERVAL)
    state["fetched"].append(url)
    try:
        resp = requests.get(url, headers={"User-Agent": UA},
                            timeout=FETCH_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return ""
        resp.encoding = resp.apparent_encoding or resp.encoding
        return resp.text or ""
    except Exception:
        return ""


class Renderer:
    """必要になった時点で開き、巡回が終わるまで使い回す headless Chromium。

    以前は「requests で全部取ってから、薄かったページをまとめて描画し直す」
    順序だった。これだと**巡回そのものが素の HTML で行われる**ので、JS 描画の
    サイトではリンクが 1 本も見つからず、下位ページへ辿れない。実測: ニュース
    一覧を requests で取ると本文も記事リンクも空で、個別記事へ入れなかった。

    リンクを辿る前に描画しておく必要があるので、取得のその場で切り替える。
    ブラウザは 1 回だけ起動して使い回す（1 頁ごとに起動すると巡回が数分になる）。
    playwright が無い環境では静かに諦め、素の HTML のまま進む。
    """

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._page = None
        self._failed = False

    def _ensure(self) -> bool:
        if self._page is not None:
            return True
        if self._failed:
            return False
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
            self._page = self._browser.new_page(user_agent=UA)
            return True
        except Exception:
            self._failed = True
            self.close()
            return False

    def html(self, url: str) -> str:
        if not self._ensure():
            return ""
        try:
            self._page.goto(url, timeout=RENDER_TIMEOUT,
                            wait_until="domcontentloaded")
            self._page.wait_for_timeout(1200)     # 主要な描画を待つ
            return self._page.content() or ""
        except Exception:
            return ""

    @property
    def used(self) -> bool:
        return self._page is not None

    def close(self) -> None:
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            try:
                if obj is not None:
                    getattr(obj, meth)()
            except Exception:
                pass
        self._pw = self._browser = self._page = None


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return re.sub(r"\s+", " ", _strip_tags(m.group(1))).strip()[:80] if m else ""


def _body(html: str) -> str:
    """本文だけ。ナビ・フッタは落としきれないので、短い行を捨てて薄める。"""
    text = _strip_tags(html)
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    keep = [l for l in lines if len(l) >= 12]
    return "\n".join(keep)[:PAGE_CHARS]


def classify(url: str, anchor: str = "") -> tuple[str, float]:
    """URL とアンカー文字から種別と巡回優先度を決める。"""
    blob = f"{url} {anchor}"
    best, score = "other", 0.0
    for kind, pat, base in _KINDS:
        if pat.search(blob):
            bonus = 0.5 if pat.search(anchor or "") else 0.0
            if base + bonus > score:
                best, score = kind, base + bonus
    return best, score


def org_of(url: str) -> str:
    """登録可能ドメイン。`podb.example.co.jp` も `www.example.co.jp` も同じ組織。

    以前は `_domain()` の完全一致で同域判定していたため、**同じ会社が別の
    サブドメインに置いたプロダクトサイトを一頁も取れなかった**。コーポレート
    サイトに書いてあるのは「何をしている会社か」までで、「そのプロダクトが
    どう使われるか」は製品側のドメインにしか無い。
    """
    host = _domain(url)
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    if parts[-1] == "jp" and parts[-2] in _JP_SLD and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


_A_TAG = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                    re.S | re.I)


def _anchors(html: str, base_url: str):
    """(url, anchor) を document 順で返す（重複除去つき）。

    ⚠ アンカー本文の量指定子に上限を付けてはいけない。以前は `(.{0,60}?)</a>`
    だったため、**中に入れ子の markup があるリンクは 1 本も拾えなかった**。
    カード状のニュース一覧（`<a>` の中に日付・カテゴリ・見出しの div が入る）が
    まさにそれで、記事へのリンクが全滅していた。取れていたのはナビの短い
    テキストリンクだけ — 巡回できる範囲がコーポレートサイトの骨組みに限られた
    のはこれが原因。
    """
    seen: set[str] = set()
    for m in _A_TAG.finditer(html or ""):
        href = m.group(1)
        anchor = re.sub(r"\s+", " ",
                        re.sub(r"<[^>]+>", " ", m.group(2))).strip()[:200]
        url = urljoin(base_url, href.split("#")[0]).rstrip("/")
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        yield url, anchor


def _links(html: str, base_url: str, org: str = "") -> list[tuple[float, str, str]]:
    """同一組織の巡回候補を (点, url, kind) で返す。"""
    org = org or org_of(base_url)
    out: list[tuple[float, str, str]] = []
    for url, anchor in _anchors(html, base_url):
        if _SKIP.search(url) or is_jobboard(url) or org_of(url) != org:
            continue
        # 日付付きパスは個別記事。ニュース収集側が拾うのでここでは通さない
        if _DATED_PATH.search(urlparse(url).path):
            continue
        kind, score = classify(url, anchor)
        if score <= 0:
            continue
        # 別サブドメイン（プロダクトサイト等）は本体より優先度をわずかに上げる
        # — コーポレートサイトの下位ページより、製品そのものの説明が欲しい
        bonus = 0.5 if _domain(url) != _domain(base_url) else 0.0
        # 階層が浅いほど会社全体の話（/company より /company/about/2024/... は弱い）
        depth = url.count("/") - 2
        out.append((score + bonus - 0.2 * max(0, depth - 1), url, kind))
    return sorted(out, key=lambda t: -t[0])


def _offsite_links(html: str, base_url: str) -> list[str]:
    """自社が発信している外部面（技術ブログ・note 等）。"""
    out = []
    for url, _anchor in _anchors(html, base_url):
        if _OFFSITE_HOST.match(_domain(url)) and not _SKIP.search(url):
            out.append(url)
    return out


def jd_keywords(job: dict, limit: int = 24) -> set[str]:
    """JD から、この求人を他と区別する語を拾う（製品名・技術名・領域名）。

    完全に決定論的 — LLM は呼ばない。ニュース記事の選別にだけ使う。
    """
    blob = f"{job.get('title') or ''}\n{job.get('raw_jd') or ''}"
    counts: dict[str, int] = {}
    for tok in _KW_TOKEN.findall(blob):
        key = tok.lower()
        if key in _KW_STOP or len(key) < 3:
            continue
        counts[key] = counts.get(key, 0) + 1
    return {k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]}


def _news_articles(html: str, index_url: str, org: str,
                   keywords: set[str] | None = None) -> list[str]:
    """ニュース一覧から**個別記事**の URL を、この求人に効く順で拾う。

    一覧の下位（`/news/20260401`）で、切り口違いの一覧（`/news/category/...`）
    ではないもの。ドメインは `www` 有無で揺れるので path で判定する。

    並びを新しい順のままにすると「オフィス移転」「年末年始休業」が上位の枠を
    食い、価格改定やプロダクトの刷新といった**提案の土台になる記事**が
    こぼれる（実測: 上位 5 本のうち 1 本が移転告知だった）。JD に出てくる語と
    重なる記事を先に、告知系を後ろに回す。同点なら一覧の並び順（＝新しい順）。
    """
    base_path = urlparse(index_url).path.rstrip("/")
    keywords = keywords or set()
    scored: list[tuple[float, int, str]] = []
    for i, (url, anchor) in enumerate(_anchors(html, index_url)):
        if org_of(url) != org or _SKIP.search(url) or _NEWS_INDEXISH.search(url):
            continue
        path = urlparse(url).path.rstrip("/")
        if not (base_path and path.startswith(base_path + "/") and path != base_path):
            continue
        blob = f"{url} {anchor}".lower()
        score = sum(1.0 for kw in keywords if kw in blob)
        if _NEWS_SIGNAL.search(anchor or url):
            score += 2.5
        if _NEWS_NOISE.search(anchor or url):
            score -= 2.0
        scored.append((-score, i, url))
    return [u for _s, _i, u in sorted(scored)]


def _apex(url: str) -> str:
    """`recruit.example.co.jp` → `https://example.co.jp`（本体サイト候補）。"""
    host = _domain(url)
    stripped = _SUBDOMAIN_PREFIX.sub("", host)
    if stripped == host or stripped.count(".") < 1:
        return ""
    return f"https://{stripped}/"


def _fetch_page(url: str, state: dict, renderer: Renderer) -> str:
    """requests で取り、本文が薄ければ**その場で**描画して取り直す。

    「後でまとめて」ではないのが要点 — このページから次のリンクを辿るのは
    この直後なので、ここで描画しておかないと巡回が止まる。
    """
    if not _can_fetch(url, state):
        return ""
    html = _get(url, state)
    if html and len(_body(html)) >= MIN_BODY_CHARS:
        return html
    rendered = renderer.html(url)
    if rendered and len(_body(rendered)) > len(_body(html)):
        state.setdefault("rendered", []).append(url)
        return rendered
    return html


def _origins(job: dict, state: dict, res: ResearchResult,
             sites: list[str] | None = None) -> list[str]:
    """巡回の起点候補（本体サイトを採用サイトより優先）。

    `sites` が指定されたらそれだけを使う。会社名での検索は求人媒体を引きやすく
    （媒体ドメインは足し続けても追いつかない）、人が官網を知っているなら
    検索に賭ける理由がないため。
    """
    if sites:
        given = [s for s in (s.strip() for s in sites) if s.startswith("http")]
        for s in given:
            res.notes.append(f"官網を明示指定: {s}")
        return list(dict.fromkeys(given))

    cands: list[str] = []
    job_url = (job.get("url") or "").strip()
    if job_url.startswith("http") and not is_jobboard(job_url):
        cands.append(job_url)
        res.notes.append(f"求人 URL が官網ドメイン（{_domain(job_url)}）")
    found = search_official_site(job.get("company") or "", state)
    if found:
        cands.append(found)
        res.notes.append(f"検索で候補を特定: {found}")
    # サブドメインが採用/IR 用なら本体も候補に入れ、そちらを先に見る
    apexes = [a for a in (_apex(c) for c in cands) if a]
    for a in apexes:
        if a not in cands:
            res.notes.append(f"採用/IR サブドメインだったので本体も見る: {a}")
    ordered = apexes + [c for c in cands if c not in apexes]
    return list(dict.fromkeys(ordered))


def _crawl(start: str, html: str, res: ResearchResult, state: dict,
           renderer: Renderer, budget: int,
           keywords: set[str] | None = None) -> None:
    """1 つの起点から巡回して res.pages に足す。

    深さ 2 まで辿るのは、事業ページの下にプロダクト個別ページがぶら下がる形が
    多いから。トップ直下だけを見ていると「データ活用を支援する会社です」で
    止まり、提案の対象になる製品そのものの説明に一度も届かない。
    """
    org = org_of(start)
    res.pages.append(Page(start, "top", _title(html), _body(html)))
    limit_total = len(res.pages) + budget - 1
    per_kind: dict[str, int] = {}
    seen = {start}
    counts = {"news": 0, "offsite": 0}

    def take(url: str, kind: str) -> str:
        """1 頁取って res へ足す。取れなければ空文字。"""
        if url in seen or len(res.pages) >= limit_total:
            return ""
        seen.add(url)
        sub = _fetch_page(url, state, renderer)
        if not sub:
            return ""
        res.pages.append(Page(url, kind, _title(sub), _body(sub)))
        return sub

    # (url, html, kind, depth) — 深さ順に処理する
    frontier: list[tuple[str, str, str, int]] = [(start, html, "top", 0)]
    while frontier and len(res.pages) < limit_total:
        page_url, page_html, page_kind, depth = frontier.pop(0)
        if page_kind not in _EXPAND_KINDS or depth >= MAX_DEPTH:
            continue

        for _score, url, kind in _links(page_html, page_url, org):
            if len(res.pages) >= limit_total:
                break
            if per_kind.get(kind, 0) >= _PER_KIND_LIMIT.get(kind, 1):
                continue
            sub = take(url, kind)
            if not sub:
                continue
            per_kind[kind] = per_kind.get(kind, 0) + 1
            frontier.append((url, sub, kind, depth + 1))
            # ニュース一覧は**その場で**個別記事まで開く。frontier に積んで
            # 後回しにすると、事業ページの深さ 2 展開が先に予算を食い切り、
            # 一番効く事実（資本構成の変化・価格改定）に一度も届かない（実測）
            if kind == "news":
                for art in _news_articles(sub, url, org, keywords):
                    if counts["news"] >= NEWS_ARTICLES:
                        break
                    if take(art, "news_article"):
                        counts["news"] += 1

        # 自社発信の外部面は最後（同域の事業説明を取り切ってから）
        for url in _offsite_links(page_html, page_url):
            if counts["offsite"] >= MAX_OFFSITE:
                break
            if take(url, "offsite"):
                counts["offsite"] += 1

    if counts["news"]:
        res.notes.append(f"ニュース個別記事を {counts['news']} 本開いた"
                         f"（一覧の見出しは切り詰められていて使えない）")
    if counts["offsite"]:
        res.notes.append(f"自社発信の外部面を {counts['offsite']} 件取得した")


def collect(job: dict, *, max_pages: int = MAX_PAGES,
            sites: list[str] | None = None) -> ResearchResult:
    """官網を特定して巡回する。ネットワークのみ、LLM は呼ばない。"""
    state: dict = {"fetched": []}
    res = ResearchResult()
    renderer = Renderer()
    keywords = jd_keywords(job)

    try:
        origins = _origins(job, state, res, sites)
        if not origins:
            res.notes.append("官網を特定できなかった（会社名での検索が空振り）")
            return res

        # 明示指定のときは全部の起点を巡回する（子会社が施工、親会社がプロダクト、
        # のようにドメインが分かれていることがある）。自動特定のときは従来どおり
        # 最初に取れた 1 つだけ。
        starts = origins if sites else origins[:1] or origins
        budget = max(3, max_pages // max(1, len(starts))) if sites else max_pages

        start = ""
        for cand in (starts if sites else origins):
            got = _fetch_page(cand, state, renderer)
            if not got:
                continue
            if not start:
                start = cand
                res.official = _domain(cand)
            _crawl(cand, got, res, state, renderer, budget, keywords)
            if not sites:
                break
        if not start:
            res.notes.append(f"トップページの取得に失敗: {origins[0]}")
            return res
    finally:
        renderer.close()

    n_rendered = len(state.get("rendered") or [])
    if n_rendered:
        res.notes.append(f"JS 描画のため {n_rendered} ページを headless で取り直した")

    res.pages = [p for p in res.pages if len(p.text) >= 200 or p.kind == "top"]
    if len(res.pages) < 2:
        res.notes.append("下位ページを取れなかった（描画後も本文が見つからない）")
    return res


def render(job: dict, res: ResearchResult) -> str:
    """`_research_raw.md` の本体。原文断片をそのまま残す（要約しない）。"""
    lines = [
        f"# 会社研究 生素材 — {job.get('company') or '（会社名不明）'}",
        "",
        f"_取得日: {date.today().isoformat()} / 官網: {res.official or '特定できず'} / "
        f"取得ページ数: {len(res.pages)}_",
        "",
        "> このファイルは**取得した原文の断片**であり、要約や解釈は入っていない。",
        "> 後段の分析はここに書かれていることだけを「事実」として扱う。",
        "",
    ]
    for note in res.notes:
        lines.append(f"- 備考: {note}")
    if res.notes:
        lines.append("")
    for i, p in enumerate(res.pages, 1):
        cleaned, _ = redact(p.text)
        lines += [
            f"## {i}. [{p.kind}] {p.title or p.url}",
            "",
            f"出典: {p.url}",
            "",
            "```text",
            cleaned,
            "```",
            "",
        ]
    if not res.pages:
        lines += [
            "## 取得結果: 0 ページ",
            "",
            "官網から情報を取得できなかった。以降の分析では会社の内部事情を"
            "**すべて仮説**として扱うこと。",
            "",
        ]
    return "\n".join(lines)


def raw_path(pdir: Path) -> Path:
    return pdir / RAW_FILE


def load(pdir: Path) -> str:
    p = raw_path(pdir)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def quote_corpus(pdir: Path) -> str:
    """Gate F の引用照合先 — 取得原文のコードブロック部分だけ。

    見出しや備考まで含めると「取得日」等の自前の文言に引用が当たってしまう。
    """
    return "\n".join(quote_blocks(pdir))


def quote_blocks(pdir: Path) -> list[str]:
    """取得原文を**ページ単位のまま**返す。

    prompt に載せるときは全文が入らない。先頭から切ると後ろのページ（事業・
    料金・導入事例のように後から巡回したページ）が丸ごと消え、素材を増やしても
    prompt が変わらないという事故になる。ページ単位で渡して呼び出し側が均等に
    間引けるようにする。
    """
    return [text for _kind, text in quote_pages(pdir)]


_PAGE_HEAD = re.compile(r"^## \d+\. \[(\w+)\]", re.M)


def quote_pages(pdir: Path) -> list[tuple[str, str]]:
    """取得原文を `(kind, 本文)` で返す。

    kind は prompt へ載せる分量を配る側（`pipeline.research_block`）が使う。
    全ページを等分すると、事業ページも「オフィス移転のお知らせ」も同じ枠に
    なる。どの種別が効くかは分かっているので、そこは均さない。
    """
    text = load(pdir)
    kinds = _PAGE_HEAD.findall(text)
    blocks = re.findall(r"```text\n(.*?)\n```", text, re.S)
    if len(kinds) != len(blocks):
        return [("other", b) for b in blocks]
    return list(zip(kinds, blocks))


def run(job: dict, pdir: Path, *, force: bool = False,
        max_pages: int = MAX_PAGES,
        sites: list[str] | None = None) -> tuple[str, ResearchResult]:
    """収集して `_research_raw.md` を書く。既存があれば再取得しない。"""
    path = raw_path(pdir)
    if path.exists() and not force:
        return "cached", ResearchResult(pages=[], notes=["既存の生素材を使用"])
    res = collect(job, max_pages=max_pages, sites=sites)
    pdir.mkdir(parents=True, exist_ok=True)
    path.write_text(render(job, res), encoding="utf-8")
    return ("ok" if res.ok else "degraded"), res
