"""scrapers/jac_recruitment — 純規則パース（Gmail API を叩かない部分）。"""

from scrapers.jac_recruitment import (
    JOB_NO_RE,
    _build_raw_jd,
    _html_to_text,
    _parse_fields,
    _parse_salary,
    _split_pdf,
)

# 実メールの骨格（宛名＝本人氏名、フッター＝担当者氏名を含む）
SAMPLE_HTML = """<html><body>
<p>山田 花子 　様</p>
<p>いつもお世話になっております。</p>
<p>-----------------------------------------------------------</p>
<p>●● 求人No. NJB2384628 ●●</p>
<p>【社名】 テスト株式会社</p>
<p>【事業内容】</p>
<p>デジタルバンク事業</p>
<p>【職種】 プロダクトマネージャー / リード</p>
<p>【仕事内容】</p>
<p>■私たちについて</p>
<p>【業務内容】</p>
<p>・プロダクト戦略の立案</p>
<p>【勤務地】東京都　</p>
<p>東京都品川区西五反田8-4-13</p>
<p>【雇用形態】 無期雇用</p>
<p>【給与条件】 年俸制</p>
<p>【年収（想定額）】8,500,000円 ～13,500,000円</p>
<p>上記求人の詳細内容に関しましては、小職までご連絡くださいますようお願い致します。</p>
<p>担当者： 佐藤 次郎</p>
</body></html>"""


class TestParseFields:
    def _fields(self):
        return _parse_fields(_html_to_text(SAMPLE_HTML))

    def test_job_no(self):
        assert JOB_NO_RE.search(SAMPLE_HTML).group(1) == "NJB2384628"

    def test_company_and_title(self):
        f = self._fields()
        assert f["社名"] == "テスト株式会社"
        assert f["職種"] == "プロダクトマネージャー / リード"

    def test_greeting_dropped(self):
        """最初の【社名】より前（宛名＝本人氏名）は取り込まない。"""
        assert "山田 花子" not in "\n".join(self._fields().values())

    def test_footer_dropped(self):
        """フッター（担当者氏名）以降は捨てる。"""
        assert "佐藤 次郎" not in "\n".join(self._fields().values())

    def test_nested_label_stays_in_description(self):
        """仕事内容の中の【業務内容】は欄位ではなく本文の一部。"""
        f = self._fields()
        assert "【業務内容】" in f["仕事内容"]
        assert "業務内容" not in f  # トップレベル欄位にはならない

    def test_location_multiline(self):
        assert self._fields()["勤務地"].splitlines()[0] == "東京都"


class TestParseSalary:
    def test_yen_range_to_man(self):
        assert _parse_salary("8,500,000円 ～13,500,000円") == (850, 1350)

    def test_yen_single(self):
        assert _parse_salary("9,000,000円") == (900, None)

    def test_trailing_note_ignored(self):
        assert _parse_salary("6,420,000円 ～10,450,000円\n※当社規定に基づく") == (642, 1045)

    def test_empty(self):
        assert _parse_salary("") == (None, None)


PDF_TEXT = """会社概要
No.NJB2384628
テスト株式会社
従業員数： 89 名 ( 2025年8月現在 )
求人内容には非公開情報が含まれるため、第三者への提供は禁止させて頂いております。 担当者： 佐藤 次郎
求人要項
職種 プロダクトマネージャー
■■ 応募条件 ■■
■必須要件
・BtoC領域におけるプロダクト経験
■■ 勤務条件 ■■
給与条件 年収： 850 万円 ～ 1,350 万円
"""


class TestSplitPdf:
    def test_overview_and_conditions(self):
        overview, conditions = _split_pdf(PDF_TEXT)
        assert "従業員数" in overview
        assert "求人要項" not in overview       # 概要は「求人要項」の手前まで
        assert "■必須要件" in conditions
        assert "勤務条件" in conditions

    def test_boilerplate_and_consultant_name_stripped(self):
        overview, conditions = _split_pdf(PDF_TEXT)
        assert "佐藤 次郎" not in overview + conditions
        assert "非公開情報" not in overview + conditions

    def test_empty_pdf(self):
        assert _split_pdf("") == ("", "")

    def test_no_marker_falls_back_to_full_text(self):
        overview, conditions = _split_pdf("レイアウト想定外のテキスト")
        assert overview == "レイアウト想定外のテキスト"
        assert conditions == ""


class TestBuildRawJd:
    def _jd(self):
        fields = _parse_fields(_html_to_text(SAMPLE_HTML))
        return _build_raw_jd(fields, fields.get("年収", ""), PDF_TEXT)

    def test_salary_line_parseable_by_salary_parser(self):
        """salary_parser の「年収 X万円〜Y万円」正則が拾える形を併記する。"""
        from tools.salary_parser import parse_salary

        assert parse_salary(self._jd()) == (850, 1350)

    def test_requirements_before_company_overview(self):
        """gap 分析は 6000 字で切るので応募条件を会社概要より前に置く。"""
        jd = self._jd()
        assert jd.index("応募条件") < jd.index("会社概要")

    def test_employee_count_extractable(self):
        from analyzer.sweet_spot import extract_employees

        assert extract_employees(self._jd()) == 89

    def test_no_third_party_name(self):
        assert "佐藤 次郎" not in self._jd()
