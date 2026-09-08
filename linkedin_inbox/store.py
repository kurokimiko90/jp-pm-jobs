"""linkedin_messages 表：LinkedIn Messaging 收到的對話 + 分類 + 草稿狀態。

System Layer 衍生資料（可重建）。寫入 data/jobs.sqlite，重用 tracker.db.connect()。
架構比照 inbox/store.py（Gmail 版），差異：LinkedIn 沒有官方草稿 API，draft_text
是唯一的草稿保存位置（不像 Gmail 有 draft_id 對應真正的草稿匣）。
"""
from __future__ import annotations

from tracker.db import connect

LINKEDIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS linkedin_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL UNIQUE,  -- 去重
    sender_name TEXT,
    sender_headline TEXT,
    profile_url TEXT,
    last_message_at TEXT,      -- LinkedIn 顯示的相對時間文字（DOM 不提供精確時間戳）
    category TEXT,             -- recruiting/other
    confidence REAL,
    body_raw TEXT,             -- 對方最新訊息原文（人工核對用，未經遮罩）
    draft_text TEXT,           -- 回覆草稿全文（人工複製貼上到 LinkedIn 發送，永不自動送出）
    draft_claimed_at TEXT,     -- 草稿生成互斥鎖；避免重疊掃描重複生成
    status TEXT NOT NULL DEFAULT 'new',  -- new/classified/draft_ready/skipped
    created_at DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_linkedin_status ON linkedin_messages(status);
"""


def init_linkedin_db() -> None:
    with connect() as conn:
        conn.executescript(LINKEDIN_SCHEMA)


def conversation_exists(conversation_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM linkedin_messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        return row is not None


def upsert_conversation(conv: dict) -> None:
    """插入或更新一段對話（以 conversation_id 去重）。不覆蓋已存在的 draft_text（除非新值非空）。"""
    init_linkedin_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO linkedin_messages
              (conversation_id, sender_name, sender_headline, profile_url, last_message_at,
               category, confidence, body_raw, draft_text, status, created_at)
            VALUES
              (:conversation_id, :sender_name, :sender_headline, :profile_url, :last_message_at,
               :category, :confidence, :body_raw, :draft_text, :status, date('now','localtime'))
            ON CONFLICT(conversation_id) DO UPDATE SET
               sender_name=excluded.sender_name,
               sender_headline=excluded.sender_headline,
               last_message_at=excluded.last_message_at,
               category=excluded.category,
               confidence=excluded.confidence,
               body_raw=excluded.body_raw,
               draft_text=COALESCE(excluded.draft_text, draft_text),
               status=excluded.status
            """,
            {
                "conversation_id": conv["conversation_id"],
                "sender_name": conv.get("sender_name"),
                "sender_headline": conv.get("sender_headline"),
                "profile_url": conv.get("profile_url"),
                "last_message_at": conv.get("last_message_at"),
                "category": conv.get("category"),
                "confidence": conv.get("confidence"),
                "body_raw": conv.get("body_raw"),
                "draft_text": conv.get("draft_text"),
                "status": conv.get("status", "new"),
            },
        )


def set_draft(conversation_id: str, draft_text: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE linkedin_messages SET draft_text = ?, status = 'draft_ready', "
            "draft_claimed_at = NULL WHERE conversation_id = ?",
            (draft_text, conversation_id),
        )


def claim_draft(conversation_id: str, stale_after_minutes: int = 60) -> bool:
    """Atomically reserve a conversation for draft generation（避免重疊掃描重複生成）。"""
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE linkedin_messages
            SET draft_claimed_at = datetime('now', 'localtime')
            WHERE conversation_id = ?
              AND status = 'classified'
              AND draft_text IS NULL
              AND (draft_claimed_at IS NULL
                   OR draft_claimed_at < datetime('now', 'localtime', ?))
            """,
            (conversation_id, f"-{stale_after_minutes} minutes"),
        )
        return cur.rowcount == 1


def release_draft_claim(conversation_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE linkedin_messages SET draft_claimed_at = NULL "
            "WHERE conversation_id = ? AND draft_text IS NULL",
            (conversation_id,),
        )


def list_pending_drafts(min_conf: float) -> list[dict]:
    """已分類為招聘相關但草稿生成失敗的對話 — 供下輪掃描補生（LLM 故障自癒）。"""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM linkedin_messages
            WHERE status = 'classified' AND draft_text IS NULL
              AND (draft_claimed_at IS NULL
                   OR draft_claimed_at < datetime('now', 'localtime', '-60 minutes'))
              AND category = 'recruiting'
              AND COALESCE(confidence, 0) >= ?
            ORDER BY created_at DESC
            """,
            (min_conf,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_messages(status: str | None = None) -> list[dict]:
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM linkedin_messages WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM linkedin_messages ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
