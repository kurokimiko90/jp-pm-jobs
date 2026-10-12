"""正式推薦度公式（weighted_v2）測試。"""

from analyzer.gap_analyzer import FORMULA_VERSION, compute_score


def _result(matched=None, gaps=None, reqs=None, assessments=None):
    return {
        "requirements": reqs or ["要件A", "要件B", "要件C", "要件D"],
        "matched": matched if matched is not None else ["LLM 導入を主導した実績", "SaaS PM経験", "要件Cの設計経験"],
        "gaps": gaps or [],
        "requirement_assessments": assessments or [],
    }


SWEET_JD = (
    "従業員数:250名。東証グロース上場、導入実績500社。"
    "生成AI活用によるプロダクト刷新を推進中。"
)


def _score(result, **kwargs):
    defaults = {
        "salary_min": 900,
        "salary_max": 1800,
        "title": "Product Manager",
        "raw_jd": SWEET_JD,
    }
    defaults.update(kwargs)
    return compute_score(
        result,
        defaults["salary_max"],
        defaults["raw_jd"],
        salary_min=defaults["salary_min"],
        title=defaults["title"],
    )


def test_breakdown_has_all_weighted_dimensions():
    score, verdict, breakdown, raw = _score(_result())
    assert 0 <= score <= 100
    assert verdict == "go"
    assert score == round(raw)
    assert breakdown["formula_version"] == FORMULA_VERSION
    assert sum(breakdown["weights"].values()) == 100
    assert set(breakdown["dimensions"]) == {
        "salary", "role_fit", "company_product_stage", "requirements",
        "domain", "evidence", "work_conditions", "culture_risk",
    }


def test_salary_is_continuous_and_uses_both_ends_of_range():
    low, _, low_breakdown, _ = _score(_result(), salary_min=800, salary_max=1200)
    high, _, high_breakdown, _ = _score(_result(), salary_min=1200, salary_max=1800)
    assert high > low
    assert high_breakdown["dimensions"]["salary"]["score"] > low_breakdown["dimensions"]["salary"]["score"]


def test_company_stage_is_a_weighted_dimension_not_a_large_bonus():
    plain, _, plain_breakdown, _ = _score(_result(), raw_jd="普通の求人票")
    staged, _, staged_breakdown, _ = _score(_result(), raw_jd=SWEET_JD)
    assert staged > plain
    # 公司／產品階段只佔 15%，不能再像舊版 +50 分那樣主宰結論。
    assert staged - plain <= 15
    assert staged_breakdown["dimensions"]["company_product_stage"]["score"] == 100
    assert plain_breakdown["dimensions"]["company_product_stage"]["score"] == 12.5


def test_requirement_assessments_use_importance_weights():
    result = _result(
        reqs=["必須", "歓迎"],
        matched=["必須を満たす実績"],
        assessments=[
            {"index": 1, "importance": "must", "status": "matched"},
            {"index": 2, "importance": "preferred", "status": "gap"},
        ],
    )
    _, _, breakdown, _ = _score(result)
    req = breakdown["dimensions"]["requirements"]
    assert req["method"] == "weighted_assessments"
    assert req["score"] == 75.0


def test_hard_stop_forces_skip_even_when_total_is_high():
    score, verdict, breakdown, _ = _score(_result(gaps=["重複応募のため採用対象外"] ))
    assert score >= 75
    assert verdict == "skip"
    assert set(breakdown["hard_blockers"]) == {"採用対象外", "重複応募"}
