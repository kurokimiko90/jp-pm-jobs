"""面接 QA upgrade の決定論的 safety gate。"""

import pytest

from interview.qa_quality import (
    QAItem,
    drill_oral_lints,
    oral_lints,
    parse_qa,
    render_qa,
    unsupported_numbers,
)
from interview.qa_upgrade import _local_numeric_sources, _replace_comp_tokens, _validate_review


SAMPLE = """# 想定問答

## 定番

### Q. 自己紹介してください。

結論です。

1. 一つ目です。
2. 二つ目です。

#### 深掘り①：なぜですか？

旧回答です。

---

### Q. 志望動機を教えてください。

御社を志望します。
1. 理由です。
2. 貢献します。
"""


def test_parse_qa_separates_old_drills():
    items = parse_qa(SAMPLE)
    assert len(items) == 2
    assert items[0].qid == 1
    assert items[0].section == "定番"
    assert items[0].conclusion == "結論です。"
    assert items[0].points == ["一つ目です。", "二つ目です。"]
    assert "旧回答" not in items[0].answer_text
    assert "旧回答" in items[0].old_drills


def test_render_is_compatible_and_preserves_question_count():
    items = parse_qa(SAMPLE)
    rendered = render_qa("会社", "PdM", "2026-07-21", items)
    reparsed = parse_qa(rendered)
    assert len(reparsed) == len(items)
    assert [x.question for x in reparsed] == [x.question for x in items]


def test_unsupported_numbers_uses_authoritative_sources_only():
    sources = "PdM経験は7.5年。8ブランドを担当。"
    generated = "PdM経験は7.5年で、15ブランドを担当しました。"
    assert unsupported_numbers(generated, sources) == ["15"]


def test_oral_lints_detect_written_and_overused_phrasing():
    item = QAItem(
        qid=1,
        section="",
        question="質問",
        conclusion="貴社を志望するのである。",
        points=[
            "理由なんですけれども、背景なんですけれども、そうなんですけれども。",
            "以上です。",
        ],
    )
    findings = "\n".join(oral_lints([item]))
    assert "貴社" in findings
    assert "常体" in findings
    assert "ですけれども" in findings


def test_oral_lints_detect_stiff_written_business_japanese():
    item = QAItem(
        qid=1,
        section="",
        question="質問",
        conclusion="当該プロジェクトにおいて成果へ寄与しました。",
        points=["責任分界と精緻化条件は下記の通りです。", "チーム成果を自分へ帰属させません。"],
    )
    findings = "\n".join(oral_lints([item]))
    assert "硬い書面語" in findings
    assert "責任分界" in findings
    assert "精緻化条件" in findings


def test_oral_lints_detect_coach_meta_language():
    item = QAItem(
        qid=1,
        section="",
        question="成果を教えてください",
        conclusion="資料上では改善しました。",
        points=["面接では大幅に改善したと答えます。", "御社でも生かします。"],
    )
    findings = "\n".join(oral_lints([item]))
    assert "資料上では" in findings
    assert "面接では" in findings
    assert "と答えます" in findings


def test_drill_oral_lints_detect_editorial_meta_language():
    drills = {
        1: [{
            "question": "数字の定義は何ですか？",
            "tag": "HR",
            "answer": "現時点の資料では確認できません。成果を自分へ帰属させません。",
        }]
    }
    findings = "\n".join(drill_oral_lints(drills))
    assert "Q1 D1" in findings
    assert "硬い書面語" in findings


def test_compensation_values_are_inserted_only_after_llm():
    safe = "現年収は<CURRENT_ANNUAL>万円、希望は<DESIRED_ANNUAL>万円です。"
    profile = {"compensation": {"current_annual": 500, "desired_annual": 900}}
    assert _replace_comp_tokens(safe, profile) == "現年収は500万円、希望は900万円です。"


def test_local_numeric_sources_include_resume_without_adding_it_to_prompts(tmp_path):
    resume = tmp_path / "data.yaml"
    resume.write_text("achievement: 使用率180倍\n", encoding="utf-8")
    profile = {"compensation": {"current_annual": 500, "desired_annual": 900}}
    sources = _local_numeric_sources(profile, resume)
    assert "180" in sources
    assert "500" in sources


def test_review_requires_hiring_fit_signal_for_every_answer():
    row = {
        "id": 1,
        "score_before": 50,
        "risk": 40,
        "decision": "REWRITE",
        "issues": [],
        "evidence_refs": ["experience"],
        "fit_refs": ["顧客業務を技術要件へ変換する"],
        "conclusion": "顧客業務から要件を整理します。",
        "points": ["業務フローを確認します。", "例外と受入条件を決めます。"],
    }
    _validate_review({"items": [row]}, {1})

    row_without_fit = dict(row)
    row_without_fit.pop("fit_refs")
    with pytest.raises(ValueError, match="fit_refs"):
        _validate_review({"items": [row_without_fit]}, {1})
