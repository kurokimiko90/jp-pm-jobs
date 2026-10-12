"""inbox_mails 表：收到的招聘郵件 + 分類 + 草稿狀態。

System Layer 衍生資料（可重建）。寫入 data/jobs.sqlite，與 jobs 表（唯讀）分離。
重用 tracker.db.connect()（rw context manager）。job_id 可 NULL（對不到職缺）。
"""
from __future__ import annotations

import sqlite3

from tracker.db import connect

INBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_mails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_msg_id TEXT NOT NULL UNIQUE,   -- 去重
    thread_id TEXT,
    job_id INTEGER REFERENCES jobs(id),  -- 對應職缺，可 NULL
    sender TEXT,
    subject TEXT,
    received_at DATE,
    gmail_received_at INTEGER, -- Gmail internalDate（秒）；精確的滾動窗口用
    category TEXT,        -- interview_invite/scheduling/rejection/initial_contact/offer/other
    confidence REAL,
    summary TEXT,
    draft_id TEXT,        -- Gmail draft id
    draft_text TEXT,      -- 回覆草稿全文（手動貼上或未送 Gmail 時保存於此）
    draft_claimed_at TEXT,-- 草稿生成互斥鎖；避免重疊掃描重複建草稿
    body_raw TEXT,        -- 郵件原文（手動貼上時保存；fetch 來的可留空）
    status TEXT NOT NULL DEFAULT 'new',  -- new/classified/draft_ready/sent/skipped
    created_at DATE NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox_mails(status);
CREATE INDEX IF NOT EXISTS idx_inbox_job ON inbox_mails(job_id);
"""


def init_inbox_db() -> None:
    with connect() as conn:
        conn.executescript(INBOX_SCHEMA)
        # 冪等遷移：CREATE TABLE IF NOT EXISTS 不會替既有表補欄
        for col in ("draft_text TEXT", "body_raw TEXT", "draft_claimed_at TEXT",
                    "gmail_received_at INTEGER"):
            try:
                conn.execute(f"ALTER TABLE inbox_mails ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inbox_pending_window "
            "ON inbox_mails(status, gmail_received_at)"
        )


def msg_exists(gmail_msg_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM inbox_mails WHERE gmail_msg_id = ?", (gmail_msg_id,)
        ).fetchone()
        return row is not None


def upsert_mail(mail: dict) -> None:
    """插入或更新一封郵件（以 gmail_msg_id 去重）。不覆蓋已存在的 draft_id（除非新值非空）。"""
    init_inbox_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO inbox_mails
              (gmail_msg_id, thread_id, job_id, sender, subject, received_at, gmail_received_at,
               category, confidence, summary, draft_id, draft_text, body_raw,
               status, created_at)
            VALUES
              (:gmail_msg_id, :thread_id, :job_id, :sender, :subject, :received_at, :gmail_received_at,
               :category, :confidence, :summary, :draft_id, :draft_text, :body_raw,
               :status, date('now','localtime'))
            ON CONFLICT(gmail_msg_id) DO UPDATE SET
               job_id=excluded.job_id,
               category=excluded.category,
               confidence=excluded.confidence,
               summary=excluded.summary,
               gmail_received_at=COALESCE(excluded.gmail_received_at, gmail_received_at),
               draft_id=COALESCE(excluded.draft_id, draft_id),
               draft_text=COALESCE(excluded.draft_text, draft_text),
               body_raw=COALESCE(excluded.body_raw, body_raw),
               status=excluded.status
            """,
            {
                "gmail_msg_id": mail["gmail_msg_id"],
                "thread_id": mail.get("thread_id"),
                "job_id": mail.get("job_id"),
                "sender": mail.get("sender"),
                "subject": mail.get("subject"),
                "received_at": mail.get("received_at"),
                "gmail_received_at": mail.get("gmail_received_at"),
                "category": mail.get("category"),
                "confidence": mail.get("confidence"),
                "summary": mail.get("summary"),
                "draft_id": mail.get("draft_id"),
                "draft_text": mail.get("draft_text"),
                "body_raw": mail.get("body_raw"),
                "status": mail.get("status", "new"),
            },
        )


def set_draft(gmail_msg_id: str, draft_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE inbox_mails SET draft_id = ?, status = 'draft_ready', draft_claimed_at = NULL "
            "WHERE gmail_msg_id = ?",
            (draft_id, gmail_msg_id),
        )


def claim_draft(gmail_msg_id: str, stale_after_minutes: int = 60) -> bool:
    """Atomically reserve a message for draft creation.

    The scan runs every 30 minutes, and a slow LLM call can overlap the next
    run. A reservation lets only one process create the Gmail draft. Stale
    reservations are reclaimable after a crash.
    """
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE inbox_mails
            SET draft_claimed_at = datetime('now', 'localtime')
            WHERE gmail_msg_id = ?
              AND status = 'classified'
              AND draft_id IS NULL
              AND (draft_claimed_at IS NULL
                   OR draft_claimed_at < datetime('now', 'localtime', ?))
            """,
            (gmail_msg_id, f"-{stale_after_minutes} minutes"),
        )
        return cur.rowcount == 1


def release_draft_claim(gmail_msg_id: str) -> None:
    """Make a failed draft attempt eligible for a later retry."""
    with connect() as conn:
        conn.execute(
            "UPDATE inbox_mails SET draft_claimed_at = NULL "
            "WHERE gmail_msg_id = ? AND draft_id IS NULL",
            (gmail_msg_id,),
        )


def set_status(gmail_msg_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE inbox_mails SET status = ? WHERE gmail_msg_id = ?",
            (status, gmail_msg_id),
        )


def list_pending_drafts(
    days: int, no_draft_categories: tuple[str, ...], min_conf: float,
    hours: int | None = None,
) -> list[dict]:
    """已分類但草稿生成失敗（無 draft_id）的可回覆信 — 供下輪掃描補生（LLM 故障自癒）。

    分類排除清單與信心門檻由呼叫端（inbox.reply）傳入，維持單一定義來源。
    """
    with connect() as conn:
        placeholders = ",".join("?" for _ in no_draft_categories)
        query = (
            "SELECT * FROM inbox_mails WHERE status = 'classified' AND draft_id IS NULL "
            "AND (draft_claimed_at IS NULL "
            "     OR draft_claimed_at < datetime('now', 'localtime', '-60 minutes')) "
            f"AND category NOT IN ({placeholders}) "
            "AND COALESCE(confidence, 0) >= ? "
        )
        params: tuple[object, ...] = (*no_draft_categories, min_conf)
        if hours is not None:
            # Gmail internalDate is a Unix timestamp (UTC); do not apply SQLite's
            # localtime modifier before comparing it.
            query += "AND gmail_received_at >= strftime('%s', 'now', ?) "
            params += (f"-{hours} hours",)
        else:
            query += "AND received_at >= date('now', 'localtime', ?) "
            params += (f"-{days} day",)
        rows = conn.execute(query + "ORDER BY received_at DESC", params).fetchall()
        return [dict(r) for r in rows]


def list_mails(status: str | None = None) -> list[dict]:
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM inbox_mails WHERE status = ? ORDER BY received_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM inbox_mails ORDER BY received_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
