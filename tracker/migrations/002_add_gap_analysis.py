"""Add gap_analysis 欄位（LLM「我 vs JD 要求」差距分析結果）。

Adds 1 column to `jobs`:
- gap_analysis  TEXT  (JSON: requirements/matched/gaps/recommend_score/recommend_reason)

Idempotent: safe to re-run.

Usage:
    python3 -m tracker.migrations.002_add_gap_analysis
"""

from __future__ import annotations

import sqlite3

from tracker.db import DB_PATH

NEW_COLUMNS = [
    ("gap_analysis", "TEXT"),
]


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def migrate() -> None:
    if not DB_PATH.exists():
        print(f"[migration 002] DB 不存在於 {DB_PATH}，跳過（init_db 會自帶新欄位）。")
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

        if added:
            print(f"[migration 002] 新增欄位：{', '.join(added)}")
        else:
            print("[migration 002] 全部欄位已存在，無需變更。")

        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        print(f"[migration 002] jobs 表共 {total} 筆。")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
