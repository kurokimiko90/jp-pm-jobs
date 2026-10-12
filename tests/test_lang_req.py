"""analyzer.lang_req（外語要求標籤）規則測試。"""
from analyzer.lang_req import detect_lang_env, is_english_jd

_EN_BODY = (
    "We are looking for a Product Manager to lead our platform team. "
    "You will work with engineers and designers across Tokyo and Singapore. " * 4
)


def test_empty_jd_is_unknown():
    assert detect_lang_env("PM", "") is None
    assert detect_lang_env("PM", None) is None


def test_japanese_only():
    assert detect_lang_env("PM", "■必須要件\n・PdM経験3年以上") == ""


def test_english_jd_means_english_env():
    assert is_english_jd(_EN_BODY)
    assert detect_lang_env("Product Manager", _EN_BODY) == "en:req"


def test_required_vs_preferred_by_section():
    jd = "■必須要件\n・英語力（TOEIC 800点以上）\n■歓迎要件\n・中国語での業務コミュニケーション"
    assert detect_lang_env("PM", jd) == "en:req,zh:pref"


def test_preferred_by_line_wording():
    assert detect_lang_env("PM", "・ビジネス英語力があれば尚可") == "en:pref"
    assert detect_lang_env("PM", "英語や中国語を活かしながら活躍") == "en:pref,zh:pref"


def test_req_wins_over_pref_for_same_language():
    jd = "■歓迎要件\n・英語力\n■必須要件\n・ビジネスレベルの英語"
    assert detect_lang_env("PM", jd) == "en:req"


def test_negation_and_benefits_ignored():
    assert detect_lang_env("PM", "英語力 不問") == ""
    assert detect_lang_env("PM", "■福利厚生\n英会話レッスン補助") == ""


def test_bare_mention_ignored():
    # 言語切替メニュー等、能力を問わない裸の言及
    assert detect_lang_env("PM", "English (English)\n한국어 (Korean)") == ""
