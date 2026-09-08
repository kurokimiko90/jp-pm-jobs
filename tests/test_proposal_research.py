"""研究層の巡回範囲の回帰テスト（ネットワークには出ない）。

発端は実測。ある応募先の求人で研究層が取れていたのはコーポレートサイト 4 頁だけで、
紅隊の採点は 事業理解 4/10・根拠 4/10 だった。提案は「利用量ではなく意思決定を
測ろう」という、どの B2B SaaS にも当てはまる計測論になっていた。

取れていなかったのは、外から普通に読める事実ばかり:

- プロダクトサイト本体（別サブドメイン）… 同域の完全一致で弾いていた
- MBO によるグループからの独立 / 価格改定（5 万円からの小口購入）… ニュース
  一覧の見出しだけを取り、個別記事を 1 本も開いていなかった
- 技術ブログ・note … 外部ドメインなので全部落としていた

修正後は同じ求人で 15 頁。ここで押さえるのは、その 15 頁を成立させている
4 つの判定（入れ子アンカー / 組織ドメイン / 記事の選別 / 分量配分）。
"""

from pathlib import Path

from proposal import pipeline, research


# ── 入れ子 markup のあるリンク ──────────────────────────

def test_anchor_with_nested_markup_is_found():
    """カード状のリンクを拾えないと、ニュース記事へ 1 本も辿れない。"""
    html = ('<a href="/news/20260401">'
            '<div class="card"><span class="cat">お知らせ</span>'
            '<time>2026.4.1</time>'
            '<h3>MBO実施によるグループからの独立に関するお知らせ</h3>'
            '<p>この度、当社はマネジメント・バイアウトを実施しました。</p>'
            '</div></a>')
    found = dict(research._anchors(html, "https://example.co.jp/news"))
    assert "https://example.co.jp/news/20260401" in found
    assert "MBO実施" in found["https://example.co.jp/news/20260401"]


# ── 組織ドメイン ────────────────────────────────

def test_org_treats_subdomains_as_one_company():
    org = research.org_of("https://example.co.jp/")
    assert org == "example.co.jp"
    for url in ("https://zoob.example.co.jp/", "https://www.example.co.jp/news",
                "https://recruit.example.co.jp"):
        assert research.org_of(url) == org, url


def test_org_separates_different_companies():
    assert research.org_of("https://other.com/") != research.org_of("https://example.com/")


def test_product_subdomain_is_crawlable():
    """プロダクトサイトが別サブドメインにある形は珍しくない。"""
    html = ('<a href="https://prepper.example.co.jp">プロダクトはこちら</a>'
            '<a href="https://someoneelse.com/service">外部のサービス</a>')
    urls = [u for _s, u, _k in research._links(html, "https://example.co.jp/")]
    assert "https://prepper.example.co.jp" in urls
    assert not any("someoneelse.com" in u for u in urls)


def test_dated_paths_do_not_consume_section_quota():
    """日付パスは個別記事。事業ページの枠を奪わせない。"""
    html = ('<a href="/news/20260406">Snowflakeサービスパートナーに認定</a>'
            '<a href="/service">事業内容</a>')
    kinds = {u: k for _s, u, k in research._links(html, "https://example.co.jp/")}
    assert "https://example.co.jp/service" in kinds
    assert "https://example.co.jp/news/20260406" not in kinds


# ── ニュース記事の選別 ──────────────────────────────

_NEWS_INDEX = """
<a href="/news/20260216"><h3>オフィス移転のお知らせ</h3></a>
<a href="/news/20260401"><h3>MBO実施によるグループからの独立に関するお知らせ</h3></a>
<a href="/news/20260202"><h3>データ提供サービス「ZOOB Plus」を刷新 5万円からの小口購入に対応</h3></a>
<a href="/news/category/info"><h3>お知らせ一覧</h3></a>
"""


def _articles(keywords=None):
    return research._news_articles(_NEWS_INDEX, "https://example.co.jp/news",
                                   "example.co.jp", keywords)


def test_news_index_variants_are_not_opened():
    assert "https://example.co.jp/news/category/info" not in _articles()


def test_structural_news_outranks_announcements():
    """資本構成の変化は、JD の語と重ならなくても最優先で読む。"""
    got = _articles()
    assert got[-1].endswith("/20260216")            # 移転告知は最後
    assert "https://example.co.jp/news/20260401" in got[:2]


def test_jd_keywords_pull_product_news_up():
    got = _articles({"zoob"})
    assert got[0].endswith("/20260202")


def test_jd_keywords_skip_boilerplate():
    kws = research.jd_keywords(
        {"title": "プロダクトマネージャー",
         "raw_jd": "ZOOB の価値設計。Snowflake 上でのデータ提供。リモート可。"})
    assert "zoob" in kws and "snowflake" in kws
    assert "プロダクト" not in kws        # どの求人にもある語は識別に使えない


# ── prompt へ配る分量 ──────────────────────────────

def _write_raw(tmp_path: Path, pages: list[tuple[str, str]]) -> Path:
    body = "# 会社研究 生素材\n\n"
    for i, (kind, text) in enumerate(pages, 1):
        body += f"## {i}. [{kind}] タイトル\n\n出典: https://x/{i}\n\n```text\n{text}\n```\n\n"
    (tmp_path / research.RAW_FILE).write_text(body, encoding="utf-8")
    return tmp_path


def test_quote_pages_keeps_kind_with_text(tmp_path):
    _write_raw(tmp_path, [("top", "トップ"), ("news_article", "記事")])
    assert research.quote_pages(tmp_path) == [("top", "トップ"),
                                              ("news_article", "記事")]


def test_service_page_gets_more_room_than_careers(tmp_path):
    _write_raw(tmp_path, [("service", "サ" * 4000), ("careers", "採" * 4000)])
    out = pipeline.research_block(tmp_path, max_chars=3000)
    assert out.count("サ") > out.count("採")


def test_long_page_keeps_its_tail(tmp_path):
    """会社概要の「従業員数」は頁の末尾にある。頭から切ると毎回消える。"""
    page = "あ" * 3000 + "従業員数 79名"
    _write_raw(tmp_path, [("about", page)])
    out = pipeline.research_block(tmp_path, max_chars=1200)
    assert "従業員数 79名" in out
    assert out.startswith("あ")


def test_every_page_appears_in_the_prompt(tmp_path):
    _write_raw(tmp_path, [(k, f"{k}の本文" + "x" * 3000)
                          for k in ("top", "about", "service", "news_article",
                                    "offsite", "careers")])
    out = pipeline.research_block(tmp_path, max_chars=6000)
    for kind in ("top", "about", "service", "news_article", "offsite", "careers"):
        assert f"{kind}の本文" in out, kind
