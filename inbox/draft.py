"""生成日文回覆草稿 + 建 Gmail 草稿。**永不 send** — 只呼叫 drafts().create()。

- prompt 帶 tools.deid.build_deid_profile()（候選人畫像去識別化）
- 零編造：空き日時 / 希望年収 / 前職數字一律留【】placeholder
- 對方郵件正文經 _redact 遮罩 PII
"""
from __future__ import annotations

import base64
from email.mime.text import MIMEText

from interview._llm import call
from tools.deid import build_deid_profile
from inbox._redact import redact_pii
from inbox.auth import get_service
from inbox.store import claim_draft, release_draft_claim, set_draft

# 各分類的回覆指引（不生成 other / rejection 草稿，由 reply 過濾）
_GUIDE = {
    "interview_invite": "面接の招待への返信。感謝＋出席意思。日時は【希望日時を記入】を残す。",
    "scheduling": "日程調整への返信。候補日時は【候補日時を記入】のプレースホルダ。柔軟な姿勢。",
    "initial_contact": "エージェント/採用担当の初回接触への返信。興味を示し、職務内容・条件を質問。",
    "offer": "内定通知への返信。感謝＋確認事項（条件書面・回答期限）を丁寧に確認。",
}

# 對外署名（本人指定）— 全回覆草稿末尾自動付與。
SIGNATURE = "コ"

_SYSTEM = (
    "あなたは求職者本人として、採用関連メールへの丁寧な日本語返信を書くアシスタント。"
    "ビジネス日本語・敬語、簡潔（200字前後）。"
    "重要: 候補者が持っていない具体情報（空き日時・希望年収・前職の数字）は絶対に捏造せず、"
    "【...】のプレースホルダを残すこと。"
    "署名は書かないこと（システムが自動で付与する）。"
    "宛名（相手の担当者名・会社名）はメールから読み取れる場合のみ記載してよい。"
)


def _build_prompt(mail: dict) -> str:
    guide = _GUIDE.get(mail.get("category", ""), "丁寧に返信する。")
    deid = build_deid_profile()
    body = redact_pii(mail.get("body", ""))[:3000]
    return (
        f"{_SYSTEM}\n\n"
        f"# 候補者プロフィール（去識別化済み・参考用）\n{deid}\n\n"
        f"# 返信方針\n{guide}\n\n"
        f"# 受信メール\n件名: {mail.get('subject', '')}\n本文:\n{body}\n\n"
        f"# 出力\n返信本文のみ（プレースホルダは【】で）。"
    )


def draft_text(mail: dict, model: str = "haiku") -> str:
    return call(_build_prompt(mail), model=model).strip()


def _reply_subject(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def draft_for(mail: dict, model: str = "haiku") -> str | None:
    """生成草稿 + 建 Gmail 草稿，回傳 draft_id（失敗 None）。永不 send。

    A DB claim is taken before the LLM call so overlapping scans cannot create
    two drafts for the same Gmail message.
    """
    msg_id = mail["gmail_msg_id"]
    if not claim_draft(msg_id):
        return None
    try:
        text = draft_text(mail, model=model)
        if not text:
            return None
        text = f"{text}\n\n{SIGNATURE}"
        svc = get_service()
        mime = MIMEText(text, "plain", "utf-8")
        mime["To"] = mail.get("sender_email") or mail.get("sender", "")
        mime["Subject"] = _reply_subject(mail.get("subject", ""))
        message = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}
        if mail.get("thread_id"):
            message["threadId"] = mail["thread_id"]
        draft = svc.users().drafts().create(userId="me", body={"message": message}).execute()
        draft_id = draft.get("id")
        if draft_id:
            set_draft(msg_id, draft_id)
        return draft_id
    finally:
        # On success set_draft() has already cleared it; on any failure this
        # makes the message eligible for the bounded retry in a later scan.
        release_draft_claim(msg_id)
