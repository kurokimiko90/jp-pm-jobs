"""tools/salary_parser.parse_salary — 純規則薪資抽取（単位：万円）。"""
import pytest

from tools.salary_parser import parse_salary


class TestAnnual:
    def test_annual_range(self):
        assert parse_salary("年収600万〜900万円") == (600, 900)

    def test_annual_single(self):
        assert parse_salary("年収700万") == (700, None)

    def test_annual_with_colon_gap(self):
        # LinkedIn 格式：年収:700万（_GAP 允許冒號）
        assert parse_salary("年収：700万") == (700, None)


class TestMonthly:
    def test_monthly_man_range_x12(self):
        assert parse_salary("月給30万〜50万円") == (360, 600)

    def test_monthly_yen_single_x12(self):
        assert parse_salary("月給 300,000円") == (360, None)

    def test_monthly_yen_below_floor_rejected(self):
        # 諸手当類誤命中防護：低於 MIN_REASONABLE_MONTHLY_YEN 不採用
        assert parse_salary("月給 5,000円") == (None, None)


class TestBareRange:
    def test_bare_range_within_sanity(self):
        assert parse_salary("600万円〜800万円") == (600, 800)

    def test_bare_range_below_sanity_rejected(self):
        # 30-50 万低於 BARE_ANNUAL_MIN=200，視為月給範圍不當年収
        assert parse_salary("30万円〜50万円") == (None, None)


class TestNoMatch:
    @pytest.mark.parametrize("jd", ["", "経験3年以上", None])
    def test_unparseable(self, jd):
        assert parse_salary(jd) == (None, None)


class TestStructuredJd:
    """構造化済み JD（`## `/`**…**` 付き）でもラベルと値が繋がること。

    見出しマーカーを `_GAP` が跨げないと、年収ラベルを見失って月給からの逆算に
    落ち、実際より低い年収で登録される（`scrapers/indeed_jp.py` 参照）。
    """

    def test_field_marker_between_label_and_value(self):
        assert parse_salary("**想定年収**\n724万円～920万円") == (724, 920)

    def test_bracket_label_and_value(self):
        assert parse_salary("【想定年収】\n268万円～460万円") == (268, 460)

    def test_section_marker(self):
        assert parse_salary("## 年収\n600万円～800万円") == (600, 800)

    def test_monthly_fallback_not_preferred_over_annual(self):
        jd = "月給 18万円 ~ 32万円 - 正社員\n\n**想定年収**\n268万円～460万円"
        assert parse_salary(jd) == (268, 460)
