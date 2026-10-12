"""Add gap 批次 支援（每次跑 gap_analyzer = 一批次）。

新增：
- 表 `gap_batches`：id, created_at, source_filter, min_score, job_count, summary_json
- 欄 `jobs.gap_batch_id`  INTEGER  (該筆 gap 屬於哪一批)

Idempotent: safe to re-run.

Usage:
    python3 -m tracker.migrations.003_add_gap_batches
"""

from __future__ import annotations

import sqlite3

from tracker.db import DB_PATH

CREATE_BATCHES = """
CREATE TABLE IF NOT EXISTS gap_batches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    source_filter TEXT,
    min_score     INTEGER,
    job_count     INTEGER DEFAULT 0,
    summary_json  TEXT
);
"""

NEW_COLUMNS = [("gap_batch_id", "INTEGER")]


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate() -> None:
    if not DB_PATH.exists():
        print(f"[migration 003] DB 不存在於 {DB_PATH}，跳過。")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(CREATE_BATCHES)
        present = existing_columns(conn, "jobs")
        added = []
        for col, col_type in NEW_COLUMNS:
            if col not in present:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
                added.append(col)
        conn.commit()
        print(f"[migration 003] gap_batches 表就緒"
              + (f"；新增欄位：{', '.join(added)}" if added else "；jobs 欄位已存在"))
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
