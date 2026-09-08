"""Add liveness tracking fields to jobs table.

新增：
- 欄 `jobs.liveness_status`      TEXT  (active / expired / uncertain / error)
- 欄 `jobs.liveness_checked_at`  DATE  (最後一次 liveness 檢查日期)

Idempotent: safe to re-run.

Usage:
    python3 -m tracker.migrations.004_add_liveness_fields
"""

from __future__ import annotations

import sqlite3

from tracker.db import DB_PATH

NEW_COLUMNS = [
    ("liveness_status", "TEXT"),
    ("liveness_checked_at", "DATE"),
]


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate() -> None:
    if not DB_PATH.exists():
        print(f"[migration 004] DB 不存在於 {DB_PATH}，跳過。")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        present = existing_columns(conn, "jobs")
        added = []
        for col, col_type in NEW_COLUMNS:
            if col not in present:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
                added.append(col)
        conn.commit()
        print(f"[migration 004] liveness 欄位"
              + (f"新增：{', '.join(added)}" if added else "已存在"))
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
