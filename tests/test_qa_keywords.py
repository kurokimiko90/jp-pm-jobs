"""見出しキーワード（`### Q. [キーワード] 質問文`）の付与と剥がしのテスト。"""

from interview import qa_quality
from interview.qa import generate, keywords, taxonomy
from tts import theater

QA_MD = """\
# 想定問答 — テスト株式会社

## 経歴・転職・条件

### Q. [経歴] これまでのご経歴を教えてください

エンジニア出身です。
1. 要点です。
"""


# ---------------------------------------------------------------- 基本

def test_split_strips_prefix():
    assert keywords.split("[権限設計] 権限の境界をどう定義しますか") == (
        "権限設計", "権限の境界をどう定義しますか")


def test_split_returns_empty_keyword_when_absent():
    assert keywords.split("権限の境界をどう定義しますか") == (
        "", "権限の境界をどう定義しますか")


def test_decorate_omits_empty_brackets():
    assert keywords.decorate("", "質問文") == "質問文"
    assert keywords.decorate("経歴", "質問文") == "[経歴] 質問文"


def test_trim_caps_length():
    assert len(keywords.trim("非常に長いキーワードです")) == keywords.MAX_LEN


def test_fallback_never_exceeds_max_len():
    questions = [
        "最後に、何か質問はありますか",
        "AI エージェントの権限をどう設計しますか",
        "この事業でまず着手すべきことは何だと思いますか",
    ]
    for question in questions:
        assert 0 < len(keywords.fallback(question)) <= keywords.MAX_LEN


def test_core_keywords_are_defined_and_short():
    for item in taxonomy.CORE:
        assert item.keyword, f"{item.qid} にキーワードが無い"
        assert len(item.keyword) <= keywords.MAX_LEN, item.qid


# -------------------------------------------------------------- 生成側

def test_generate_parse_separates_keyword():
    text = "### Q. [多言語] 多言語対応で意識していることは何ですか\n結論です。\n1. 要点です。"
    qid, keyword, question, conclusion, points = generate._parse(text)[0]

    assert qid is None
    assert keyword == "多言語"
    assert question == "多言語対応で意識していることは何ですか"
    assert conclusion == "結論です。"
    assert points == ["要点です。"]


def test_generate_fills_keyword_when_llm_omits_it():
    text = "### Q. 希望年収を教えてください\n結論です。\n1. 要点です。"
    _, keyword, question, conclusion, points = generate._parse(text)[0]
    item = generate._qa(keyword, question, conclusion, points, "jd")

    assert item.keyword and len(item.keyword) <= keywords.MAX_LEN


# -------------------------------------------------------------- 下流側

def test_tts_parser_drops_keyword_from_spoken_question():
    items = theater.parse_standard_qa(QA_MD)

    assert len(items) == 1
    assert items[0].question == "これまでのご経歴を教えてください"
    assert "[" not in items[0].question


def test_qa_quality_roundtrip_keeps_keyword():
    items = qa_quality.parse_qa(QA_MD)

    assert items[0].keyword == "経歴"
    assert items[0].question == "これまでのご経歴を教えてください"

    rendered = qa_quality.render_qa("テスト株式会社", "面接", "2026-08-06", items)
    assert "### Q. [経歴] これまでのご経歴を教えてください" in rendered
