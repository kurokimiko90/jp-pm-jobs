"""練習/元氣資料庫（data/practice.sqlite，可寫）。jobs.sqlite 維持唯讀不動。"""
import sqlite3

from paths import PROJECT_ROOT

PRACTICE_DB = PROJECT_ROOT / "data" / "practice.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prep_dir TEXT NOT NULL,
    company TEXT NOT NULL,
    section TEXT,
    qno TEXT,
    question TEXT NOT NULL,
    answer TEXT,
    UNIQUE(prep_dir, qno, question)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES qa_cards(id),
    grade TEXT NOT NULL CHECK(grade IN ('o','d','x')),  -- ○ △ ×
    mode TEXT NOT NULL,                                  -- flash / mock
    duration_sec INTEGER,
    reviewed_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS checkins (
    day TEXT PRIMARY KEY,           -- YYYY-MM-DD，一日一筆
    level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 5),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS wins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(PRACTICE_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()
