"""linkedin_inbox.rule_classify（零 LLM 規則分類）測試。"""
from __future__ import annotations

from linkedin_inbox.rule_classify import classify


def test_recruiter_headline_plus_body_keyword_is_high_confidence():
    conv = {
        "sender_headline": "Technical Recruiter at Example Corp",
        "body_raw": "We have an exciting Product Manager opportunity that might interest you.",
    }
    result = classify(conv)
    assert result["category"] == "recruiting"
    assert result["confidence"] >= 0.85


def test_japanese_recruiter_headline_is_recruiting():
    conv = {
        "sender_headline": "株式会社サンプル 採用担当",
        "body_raw": "貴殿のご経歴を拝見し、ぜひ一度カジュアル面談でお話しできればと思います。",
    }
    assert classify(conv)["category"] == "recruiting"


def test_headline_alone_is_mid_confidence():
    conv = {"sender_headline": "Talent Acquisition Partner", "body_raw": "Hi, how are you?"}
    result = classify(conv)
    assert result["category"] == "recruiting"
    assert result["confidence"] < 0.85


def test_multiple_body_keywords_without_headline_is_recruiting():
    conv = {
        "sender_headline": "Software Engineer",
        "body_raw": "We're hiring for a new role, great career opportunity with strong salary.",
    }
    assert classify(conv)["category"] == "recruiting"


def test_generic_connection_invite_is_not_recruiting():
    conv = {
        "sender_headline": "Marketing enthusiast",
        "body_raw": "Hi! Let's connect and grow my network together.",
    }
    result = classify(conv)
    assert result["category"] == "other"


def test_no_signal_is_not_recruiting():
    conv = {"sender_headline": "Photographer", "body_raw": "Great photo, love your profile!"}
    assert classify(conv)["category"] == "other"


def test_missing_fields_do_not_crash():
    assert classify({})["category"] == "other"
