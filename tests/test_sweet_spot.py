"""analyzer/sweet_spot.py 甜蜜點企業偵測測試。"""
import pytest

from analyzer.sweet_spot import evaluate, extract_employees


# ── 従業員數抽取 ─────────────────────────────────────────


def test_extract_tag_format():
    jd = "…本文…\n【企業データ】従業員数:230名 / 上場:非上場 / 業種:SaaS / 設立:2015年"
    assert extract_employees(jd) == 230


def test_extract_loose_format():
    assert extract_employees("当社は従業員数 350名のSaaS企業です") == 350


def test_extract_loose_with_comma():
    assert extract_employees("社員数：1,200名") == 1200


def test_extract_fullwidth_digits():
    assert extract_employees("従業員数：２５０名") == 250


def test_extract_none_when_absent():
    assert extract_employees("従業員が活躍する職場です") is None
    assert extract_employees("") is None
    assert extract_employees(None) is None


def test_extract_prefers_tag_over_loose():
    jd = "従業員数10,000名以上の大手企業向けSaaS\n【企業データ】従業員数:180名 / 上場:非上場"
    assert extract_employees(jd) == 180


# ── 三條件分別評分 ────────────────────────────────────────


def test_size_hit():
    r = evaluate("【企業データ】従業員数:300名 / 上場:非上場")
    assert r["size"] is True
    assert r["employees"] == 300
    assert r["bonus"] == 20


def test_size_no_upper_limit():
    assert evaluate("従業員数:100名")["size"] is True
    assert evaluate("従業員数:501名")["size"] is True
    assert evaluate("従業員数:10,000名")["size"] is True
    assert evaluate("従業員数:99名")["size"] is False


def test_size_unknown_penalized():
    r = evaluate("素敵な会社です")
    assert r["size"] is False
    assert r["employees"] is None
    assert r["bonus"] == -2  # 人數缺失 −2


def test_size_detected_below_min_no_penalty():
    r = evaluate("従業員数:50名")
    assert r["size"] is False
    assert r["bonus"] == 0  # 有人數但 <100：不加分也不扣


def test_maturity_keywords():
    r = evaluate("大手企業への導入実績多数、継続率99%のSaaSプロダクト")
    assert r["maturity"] is True
    assert r["bonus"] == 15 - 2  # maturity +15、人數缺失 −2


def test_maturity_not_triggered_by_mijoujou():
    # 「未上場」に含まれる「上場」で誤爆しないこと
    r = evaluate("当社は未上場のスタートアップです")
    assert r["maturity"] is False


def test_maturity_listed_company():
    assert evaluate("東証プライム上場企業です")["maturity"] is True


def test_ascii_keyword_word_boundary():
    # 「arr」が arrange 等の部分文字列で誤爆しないこと
    assert evaluate("We arrange meetings")["maturity"] is False
    assert evaluate("ARR 10億円を突破")["maturity"] is True


def test_ai_upgrade_keywords():
    r = evaluate("既存プロダクトへのAI導入を推進中")
    assert r["ai_upgrade"] is True
    assert r["bonus"] == 15 - 2


def test_ai_upgrade_legacy_replace():
    assert evaluate("レガシーシステムのリプレイスとDX推進")["ai_upgrade"] is True


def test_all_three_hit():
    jd = ("従業員数:250名。東証グロース上場、大手企業への導入実績500社。"
          "生成AI活用によるプロダクト刷新を推進中。")
    r = evaluate(jd)
    assert r["size"] and r["maturity"] and r["ai_upgrade"]
    assert r["bonus"] == 50


def test_no_hit():
    r = evaluate("普通の求人票です")
    assert r["bonus"] == -2  # 全不命中且無人數
    assert not r["size"] and not r["maturity"] and not r["ai_upgrade"]
