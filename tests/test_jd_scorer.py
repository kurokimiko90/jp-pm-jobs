"""analyzer/jd_scorer 純函數 — 測機制不測個人配置。

關鍵字清單 / 目標年收來自 config/scoring.yaml（因人而異），
測試一律 monkeypatch 成已知值，只驗證計分機制本身。
"""
import pytest

import analyzer.jd_scorer as js


class TestNormalize:
    def test_fullwidth_to_halfwidth(self):
        assert js._normalize("ＰＭ（ＡＩ）！Ａ１") == "PM(AI)!A1"

    def test_plain_ascii_untouched(self):
        assert js._normalize("Product Manager") == "Product Manager"


class TestMarketKeywords:
    def test_hit_ratio_scales_to_cap(self, monkeypatch):
        monkeypatch.setattr(js, "MARKET_KEYWORDS", ["llm", "agent", "rag", "evals", "mlops"])
        bd = js.ScoreBreakdown()
        assert js.score_market_keywords("llm と agent の経験", bd) == 50.0  # 2/4
        assert bd.matched_keywords == ["llm", "agent"]

    def test_four_or_more_hits_is_full_score(self, monkeypatch):
        monkeypatch.setattr(js, "MARKET_KEYWORDS", ["llm", "agent", "rag", "evals", "mlops"])
        bd = js.ScoreBreakdown()
        assert js.score_market_keywords("llm agent rag evals mlops", bd) == 100.0


class TestTechOverlap:
    def test_no_candidate_data_is_neutral_50(self, monkeypatch):
        monkeypatch.setattr(js, "CANDIDATE_TECH", set())
        assert js.score_tech_overlap("anything", js.ScoreBreakdown()) == 50.0

    def test_five_hits_is_full_score(self, monkeypatch):
        monkeypatch.setattr(js, "CANDIDATE_TECH", {"python", "fastapi", "react", "sqlite", "playwright"})
        blob = "python fastapi react sqlite playwright"
        assert js.score_tech_overlap(blob, js.ScoreBreakdown()) == 100.0

    def test_single_hit_is_20(self, monkeypatch):
        monkeypatch.setattr(js, "CANDIDATE_TECH", {"python", "fastapi", "react", "sqlite", "playwright"})
        assert js.score_tech_overlap("python だけ", js.ScoreBreakdown()) == 20.0


class TestTierPreference:
    def test_configured_tier(self, monkeypatch):
        monkeypatch.setattr(js, "TIER_PREFERENCE", {"ai_startup": 100})
        assert js.score_tier_preference("ai_startup", js.ScoreBreakdown()) == 100.0

    def test_missing_tier_defaults_40(self, monkeypatch):
        monkeypatch.setattr(js, "TIER_PREFERENCE", {})
        assert js.score_tier_preference(None, js.ScoreBreakdown()) == 40.0


class TestSalaryFit:
    @pytest.fixture(autouse=True)
    def _targets(self, monkeypatch):
        monkeypatch.setattr(js, "TARGET_SALARY_MIN", 900)
        monkeypatch.setattr(js, "TARGET_SALARY_MAX", 1800)

    def test_at_or_above_target_max_is_100(self):
        row = {"salary_min": None, "salary_max": 1800, "raw_jd": ""}
        assert js.score_salary_fit(row, js.ScoreBreakdown()) == 100.0

    def test_at_target_min_is_55(self):
        row = {"salary_min": None, "salary_max": 900, "raw_jd": ""}
        assert js.score_salary_fit(row, js.ScoreBreakdown()) == 55.0

    def test_below_soft_floor_is_25(self):
        # soft floor = TARGET_MIN * 0.8 = 720
        row = {"salary_min": None, "salary_max": 700, "raw_jd": ""}
        assert js.score_salary_fit(row, js.ScoreBreakdown()) == 25.0

    def test_missing_salary_is_neutral_50_and_flagged(self):
        bd = js.ScoreBreakdown()
        row = {"salary_min": None, "salary_max": None, "raw_jd": ""}
        assert js.score_salary_fit(row, bd) == 50.0
        assert bd.salary_missing is True

    def test_imputed_from_raw_jd_with_low_min_penalty(self):
        bd = js.ScoreBreakdown()
        row = {"salary_min": None, "salary_max": None, "raw_jd": "年収600万〜900万円"}
        got = js.score_salary_fit(row, bd)
        assert bd.salary_imputed is True
        # s=900 → base 55；smin=600 < 900 → ×(600/900)
        assert got == pytest.approx(55.0 * 600 / 900)
