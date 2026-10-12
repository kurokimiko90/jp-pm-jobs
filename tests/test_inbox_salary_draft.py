"""inbox.salary_draft（年収確認メール → 数字入りテンプレート草稿、零 LLM）測試。

本文はエージェント定型の体裁だけを写した架空のもの（実在の応募先・担当者名なし）。
"""
from __future__ import annotations

from inbox import salary_draft as sd

ANSWER = {"current": 600, "desired": 900, "minimum": 850, "name": "山田 太郎",
          "note": "いずれも税引前の金額です。"}

# 「年収確認のお願い」型：空欄（＿）を埋める 3 項目。説明文にも「最低年収」が出る
BLANK_FORM = """山田様

お世話になっております。リクルートエージェント　面接調整センターです。

以下、空欄(__)の箇所をご回答お願いいたします。
―――――――――――
・最低希望年収:＿万円
※「最低年収」は、この金額を下回ると辞退せざるを得ない金額というイメージにてお答えください。
・希望年収:＿万円
・現年収:＿万円
―――――――――――

何卒よろしくお願いいたします。
----------------------------------------
株式会社インディードリクルートパートナーズ
キャリアアドバイザー
・選考日程の調整などについては面接調整センター担当よりご連絡させていただきます
"""

# 「事前確認依頼」型：年収以外の質問が混ざり、区切り線が依頼ブロック内にもある
MIXED = """山田様

【株式会社サンプル】担当者より、以下についての確認依頼がございます。
=========================================
■企業からのご回答依頼（事前対応）
-----------------------------------
1）他社選考状況
2）希望年収
3）希望入社時期
-----------------------------------
=========================================
※（優先順位・志望順位や併願企業をご質問させて頂いた場合）
     併願企業名はご記載いただいても企業にはお伝えしません。
２）現状はハイブリッド勤務です。
　　こちらご志向として問題ないでしょうか。

どうぞよろしくお願いいたします。
"""

# エージェントの受領メール：本人の回答（年収の語）は > 引用の中だけ
ACK = """山田様

ご連絡いただき、ありがとうございました。企業担当にお伝えいたします。

今後とも、どうぞよろしくお願いいたします。

> ・最低希望年収：【850】万円
> ・希望年収：【900】万円
"""

# リマインド：自身の定型（・直近でのご対応が…）は項目にしない。依頼は前回引用側
REMINDER = """お世話になっております。

【ご確認のお願い】
・直近でのご対応が難しい場合は、理由をお知らせください。

どうぞよろしくお願いいたします。

*****（以下前回お送りしたメールです）*****

■現年収と希望年収をお知らせください。
■入社可能時期のご確認をお願いいたします。

何卒よろしくお願いいたします。
"""


def test_asked_fields_in_mail_order():
    assert sd.asked_fields(BLANK_FORM) == ["minimum", "desired", "current"]
    assert sd.asked_fields(MIXED) == ["desired"]
    assert sd.asked_fields(REMINDER) == ["current", "desired"]


def test_ack_mail_is_not_an_inquiry():
    assert sd.asked_fields(ACK) == []
    assert sd.salary_reply_text({"body": ACK}, ANSWER) is None


def test_other_items_skip_headers_notes_and_footer():
    assert sd.other_items(BLANK_FORM) == []
    assert sd.other_items(MIXED) == [
        "他社選考状況", "希望入社時期",
        "現状はハイブリッド勤務です。こちらご志向として問題ないでしょうか。",
    ]
    assert sd.other_items(REMINDER) == ["入社可能時期のご確認をお願いいたします。"]


def test_reply_fills_only_asked_numbers():
    text = sd.salary_reply_text({"body": BLANK_FORM}, ANSWER)
    assert text.splitlines()[0] == "リクルートエージェント 面接調整センター ご担当者様"
    assert "お世話になっております。山田 太郎です。" in text
    assert "・最低希望年収：850万円\n・希望年収：900万円\n・現年収：600万円" in text
    assert "いずれも税引前の金額です。" in text
    assert "【要記入】" not in text

    mixed = sd.salary_reply_text({"body": MIXED}, ANSWER)
    assert "・希望年収：900万円" in mixed and "現年収" not in mixed  # 聞かれていない項目は書かない
    assert "・希望入社時期：【要記入】" in mixed


def test_no_config_falls_back(monkeypatch):
    monkeypatch.setattr(sd.app_config, "load", lambda name: {})
    assert sd.salary_reply_text({"body": BLANK_FORM}) is None


def test_draft_text_uses_template_without_llm(monkeypatch):
    from inbox import draft

    monkeypatch.setattr(sd, "load_answer", lambda: ANSWER)

    def boom(*a, **k):
        raise AssertionError("年収確認メールで LLM を呼んではいけない（現年収を外部送信しない）")

    monkeypatch.setattr(draft, "call", boom)
    assert "・現年収：600万円" in draft.draft_text({"body": BLANK_FORM, "subject": "年収確認のお願い"})
