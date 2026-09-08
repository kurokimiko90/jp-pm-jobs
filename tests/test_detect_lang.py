"""prep._detect_lang — 投遞包語言自動偵測（CJK 比例 + ATS 網域 fallback）。"""
from prep import _detect_lang


class TestCjkRatio:
    def test_english_jd_is_en(self):
        jd = "We are hiring a Senior Product Manager for our AI platform team."
        assert _detect_lang({"raw_jd": jd}) == "en"

    def test_japanese_jd_is_jp(self):
        jd = "AIプロダクトマネージャーを募集しています。要件定義から実装まで。"
        assert _detect_lang({"raw_jd": jd}) == "jp"

    def test_mixed_at_threshold_is_jp(self):
        # CJK 比例恰為 20%（75/375）：不小於閾值 → jp（英日混合型 JD）
        mixed = "日本語" * 25 + "a" * 300
        assert _detect_lang({"raw_jd": mixed}) == "jp"


class TestEmptyJdFallback:
    def test_en_ats_domain_is_en(self):
        assert _detect_lang({"raw_jd": "", "url": "https://boards.greenhouse.io/x/jobs/1"}) == "en"

    def test_unknown_domain_defaults_jp(self):
        assert _detect_lang({"raw_jd": "", "url": "https://example.co.jp/jobs/1"}) == "jp"
