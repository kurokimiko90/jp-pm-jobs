"""tools/jd_tier_classifier.classify — 規則式企業三類分流。"""
from tools.jd_tier_classifier import classify


class TestCompanyPatterns:
    def test_mega_venture_hit(self):
        r = classify("株式会社メルカリ", None, None)
        assert (r.tier, r.conf) == ("mega_venture", 0.95)

    def test_traditional_sier_hit(self):
        r = classify("NTT DATA", None, None)
        assert (r.tier, r.conf) == ("traditional_sier", 0.95)

    def test_ai_startup_hit(self):
        r = classify("Preferred Networks", None, None)
        assert (r.tier, r.conf) == ("ai_startup", 0.95)


class TestJdHints:
    def test_ai_hint_in_jd_medium_conf(self):
        r = classify("無名株式会社", "PM", "RLHFの経験歓迎")
        assert (r.tier, r.conf) == ("ai_startup", 0.60)

    def test_client_mention_in_jd_does_not_trigger_company_match(self):
        # 公司名只比對 company 欄位：JD 內文提到的客戶案例不可誤判成公司本身
        r = classify("小さい会社", None, "顧客にはメルカリ、リクルートを含む")
        assert r.tier == "unknown"


class TestScaleHeuristic:
    def test_listed_large_company_is_mega_venture(self):
        r = classify(None, None,
                     "【企業データ】従業員数:800名 / 上場:東証グロース / 業種:インターネット / 設立:2015年")
        assert (r.tier, r.conf) == ("mega_venture", 0.5)

    def test_sier_industry_large_company(self):
        r = classify(None, None,
                     "【企業データ】従業員数:500名 / 上場:東証プライム / 業種:システムインテグレータ / 設立:2000年")
        assert (r.tier, r.conf) == ("traditional_sier", 0.5)

    def test_sier_industry_small_but_old_company(self):
        # 従業員 <300 但設立 ≥15 年 → is_old 分支
        r = classify(None, None,
                     "【企業データ】従業員数:100名 / 上場:非上場 / 業種:ITコンサルティング / 設立:1990年")
        assert (r.tier, r.conf) == ("traditional_sier", 0.5)

    def test_missing_industry_part_still_matches(self):
        # 業種部件缺（green.py 可選部件）：上場+規模規則仍可命中
        r = classify(None, None,
                     "【企業データ】従業員数:400名 / 上場:東証プライム / 設立:2001年")
        assert (r.tier, r.conf) == ("mega_venture", 0.5)

    def test_small_young_unlisted_is_unknown(self):
        r = classify(None, None,
                     "【企業データ】従業員数:50名 / 上場:非上場 / 業種:インターネット / 設立:2020年")
        assert r.tier == "unknown"


class TestUnknown:
    def test_no_signal_is_unknown(self):
        r = classify("ABC商事", None, None)
        assert (r.tier, r.conf, r.reason) == ("unknown", 0.0, "no_match")
