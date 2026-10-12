"""LinkedIn 回覆草稿生成。**永不自動送出** — LinkedIn 無官方草稿 API，草稿只存在
DB（draft_text）與 output/linkedin_drafts/{conversation_id}.txt，供人工複製貼上到
LinkedIn 網頁後手動按送出。

- prompt 帶 tools.deid.build_deid_profile()（候選人畫像去識別化，內部已含品牌名遮蔽）
- 對方訊息正文先過 _redact_contact_pii（email/電話/URL）與 tools.redact.redact()
  （取引先ブランド名，NDA 相當——避免草稿裡意外帶出候選人現職接觸過的品牌名）
- 生成後的草稿文字再過 tools.redact.scan() 做殘留檢查，命中則印警告（不阻擋，
  草稿本來就要人工複審才會手動貼上發送，這是最後一道機械提示）
- 零編造：面試時段／目前年收／離職日等候選人沒有的具體數字一律留佔位符
"""
from __future__ import annotations

import re
from pathlib import Path

from interview._llm import call
from tools.deid import build_deid_profile
from tools.redact import redact, scan
from linkedin_inbox.store import claim_draft, release_draft_claim, set_draft

ROOT = Path(__file__).parent.parent
DRAFTS_DIR = ROOT / "output" / "linkedin_drafts"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\b0\d{1,4}[-(]?\d{1,4}[-)]?\d{3,4}\b")
_URL = re.compile(r"https?://\S+")

_SYSTEM = (
    "あなたは求職者本人として、LinkedIn 上で受信した採用関連メッセージへの丁寧な返信を書く"
    "アシスタント。相手のメッセージと同じ言語で書くこと（日本語なら敬語、英語ならプロフェッ"
    "ショナルなビジネス英語）。簡潔に（150〜250字程度）。"
    "重要: 候補者が持っていない具体情報（面接の空き日時・現在の年収・退職可能日など）は"
    "絶対に捏造せず、【...】(または[TBD])のプレースホルダを残すこと。"
    "署名は書かないこと（人が貼り付ける際に自分で付与する）。"
    "本文のみを出力し、前置きや説明は書かないこと。"
)


def _redact_contact_pii(text: str) -> str:
    """對方訊息中的個資（email/電話/URL）→ 佔位符（候選人自身 PII 由 build_deid_profile 處理）。"""
    text = _EMAIL.sub("[EMAIL]", text or "")
    text = _PHONE.sub("[PHONE]", text)
    return _URL.sub("[URL]", text)


def _build_prompt(conv: dict) -> str:
    deid = build_deid_profile(compact=True)
    body, _hits = redact(_redact_contact_pii(conv.get("body_raw", ""))[:2000])
    sender = conv.get("sender_name") or "相手"
    headline = conv.get("sender_headline") or ""
    return (
        f"{_SYSTEM}\n\n"
        f"# 候補者プロフィール（去識別化済み・参考用）\n{deid}\n\n"
        f"# 送信者\n{sender}（{headline}）\n\n"
        f"# 受信メッセージ\n{body}\n\n"
        f"# 出力\n返信本文のみ。"
    )


def draft_text(conv: dict, model: str = "haiku") -> str:
    return call(_build_prompt(conv), model=model).strip()


def draft_for(conv: dict, model: str = "haiku") -> str | None:
    """生成草稿並存檔（DB + txt）。回傳草稿文字（失敗 None）。永不送出。

    先取 DB claim，避免重疊掃描（30 分鐘 launchd 輪詢）對同一對話重複生成。
    """
    conversation_id = conv["conversation_id"]
    if not claim_draft(conversation_id):
        return None
    try:
        text = draft_text(conv, model=model)
        if not text:
            return None
        residual = scan(text)
        if residual:
            print(f"  ⚠ [linkedin_inbox] 草稿殘留禁止語（人工複審時留意）: {residual}")
        set_draft(conversation_id, text)
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", conversation_id)[:100]
        header = f"# 宛先: {conv.get('sender_name', '')}（{conv.get('profile_url', '')}）\n"
        header += "# ⚠ 草稿は未送信。LinkedIn を開いて内容を確認のうえ手動で貼り付けて送信すること。\n\n"
        (DRAFTS_DIR / f"{safe_id}.txt").write_text(header + text, encoding="utf-8")
        return text
    finally:
        release_draft_claim(conversation_id)
