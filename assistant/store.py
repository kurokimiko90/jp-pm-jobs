"""AI 職涯助手對話紀錄 — data/practice.sqlite 的 assistant_turns 表（可寫）。

跟 jobs.sqlite 分開：jobs.sqlite 是 dashboard 唯讀資料源，assistant 需要寫入
每輪問答才能支援每日/每週總結，因此比照 dashboard/backend/practice_db.py 的
既有慣例，落在同一顆可寫的 practice.sqlite（獨立連線，不跨 import dashboard/backend）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
PRACTICE_DB = ROOT / "data" / "practice.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_turns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel       TEXT NOT NULL,              -- web / telegram
    question      TEXT NOT NULL,
    answer        TEXT NOT NULL,
    citations_json TEXT,                      -- ["job:123", ...]
    question_at   TEXT,                       -- 提問時間；NULL = 舊資料，顯示時 fallback 用 created_at
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))  -- 回覆落地時間
);
CREATE INDEX IF NOT EXISTS idx_assistant_turns_created ON assistant_turns(created_at);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(assistant_turns)")}
    if "question_at" not in cols:
        conn.execute("ALTER TABLE assistant_turns ADD COLUMN question_at TEXT")
        conn.execute("UPDATE assistant_turns SET question_at = created_at WHERE question_at IS NULL")
        conn.commit()


def get_conn() -> sqlite3.Connection:
    PRACTICE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PRACTICE_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def log_turn(
    channel: str,
    question: str,
    answer: str,
    citations: list[str] | None = None,
    question_at: str | None = None,
) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO assistant_turns (channel, question, answer, citations_json, question_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (channel, question, answer, json.dumps(citations or [], ensure_ascii=False), question_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["citations"] = json.loads(d.pop("citations_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["citations"] = []
    d["question_at"] = d.get("question_at") or d["created_at"]
    return d


def recent_turns(limit: int = 20) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM assistant_turns ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows][::-1]


def turns_since(since_iso: str) -> list[dict]:
    """since_iso: 'YYYY-MM-DD' 或完整 datetime 字串（含）之後的所有輪次。"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM assistant_turns WHERE created_at >= ? ORDER BY id", (since_iso,)
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]
