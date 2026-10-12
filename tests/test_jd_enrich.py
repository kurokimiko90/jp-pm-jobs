"""analyzer.jd_enrich（mentions_ai）＋ jd_scorer.classify_role 規則測試。"""
from analyzer.jd_enrich import mentions_ai
from analyzer.jd_scorer import classify_role
from analyzer.sweet_spot import extract_employees


# ── classify_role ──────────────────────────────────────────────

def test_classify_pdm_titles():
    assert classify_role("プロダクトマネージャー") == "pdm"
    assert classify_role("Senior Product Manager") == "pdm"
    assert classify_role("AI PdM") == "pdm"
    assert classify_role("ＰＭ（自社SaaS）") == "pdm"  # 全角も半角化して判定


def test_classify_pjm_intercepts_before_pdm():
    assert classify_role("プロジェクトマネージャー") == "pjm"
    assert classify_role("Technical Program Manager") == "pjm"
    assert classify_role("PMO コンサルタント") == "pjm"  # demote 優先於 consulting
    assert classify_role("プログラムマネージャー, Quality Management") == "pjm"


def test_classify_katakana_variants():
    # マネージャ／マネジャー 等長音變體
    assert classify_role("アソシエイトプロダクトマネージャ") == "pdm"
    assert classify_role("プロダクトマネジャー") == "pdm"


def test_classify_consulting():
    assert classify_role("戦略コンサルタント") == "consulting"
    assert classify_role("DX Consultant") == "consulting"


def test_classify_mixed_pm_consul_is_pdm():
    # PM+コンサル混合職銜以 PM 為主軸（與 score_role_fit 的 70 分邏輯一致）
    assert classify_role("プロダクトマネージャー／コンサルタント") == "pdm"


def test_classify_other():
    assert classify_role("バックエンドエンジニア") == "other"
    assert classify_role("") == "other"
    assert classify_role(None) == "other"


# ── mentions_ai ────────────────────────────────────────────────

def test_ai_hit_in_jd():
    assert mentions_ai("営業", "生成AIを活用した業務改善") == 1
    assert mentions_ai("営業", "機械学習モデルの運用") == 1
    assert mentions_ai("PM", "We use LLM and ChatGPT daily") == 1
    assert mentions_ai("PM", "ＡＩプロダクト開発")  == 1  # 全角


def test_ai_hit_in_title_only():
    assert mentions_ai("AIプロダクトマネージャー", "") == 1


def test_ai_no_hit():
    assert mentions_ai("営業", "法人向けの提案営業です") == 0


def test_ai_word_boundary_no_false_positive():
    # Dubai / said / 600ml の部分文字列では誤中しない
    assert mentions_ai("PM", "Office in Dubai. He said hello. Bottle 600ml.") == 0


def test_ai_empty_jd_is_unknown():
    assert mentions_ai("営業", "") is None
    assert mentions_ai("営業", None) is None


# ── extract_employees（既存 sweet_spot 關鍵路徑煙霧測試） ──────

def test_extract_employees_formats():
    assert extract_employees("従業員数：1,250名") == 1250
    assert extract_employees("社員数 80名") == 80
    assert extract_employees("この求人に人数記載なし") is None
    assert extract_employees(None) is None
