"""通知冪等記錄 — 同一事件（kind + ref）只推一次。

存在 data/jobs.sqlite 的 notify_log 表，launchd 重跑 / callback 重送都不會重複打擾。
"""

from __future__ import annotations

from datetime import datetime

from tracker import db


def escalation_state(kind: str, prefix: str) -> tuple[int, datetime | None]:
    """同一事件多次升級提醒用：回傳 (已送次數, 最後一次送出時間)。

    ref 以 f"{prefix}:{n}"（n=1,2,3...）記錄每一次提醒，藉此複用 notify_log
    既有的一次性 dedupe 機制湊出「次數上限 + 間隔冷卻」，不用另開表。
    """
    with db.connect() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT sent_at FROM notify_log WHERE kind = ? AND ref LIKE ? "
            "ORDER BY sent_at DESC",
            (kind, f"{prefix}:%"),
        ).fetchall()
    if not rows:
        return 0, None
    return len(rows), datetime.fromisoformat(rows[0]["sent_at"])


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notify_log (
            kind TEXT NOT NULL,
            ref TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (kind, ref)
        )
    """)


def already_sent(kind: str, ref: str) -> bool:
    with db.connect() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT 1 FROM notify_log WHERE kind = ? AND ref = ?", (kind, str(ref))
        ).fetchone()
        return row is not None


def mark_sent(kind: str, ref: str) -> None:
    with db.connect() as conn:
        _ensure_table(conn)
        conn.execute(
            "INSERT OR IGNORE INTO notify_log (kind, ref, sent_at) VALUES (?, ?, ?)",
            (kind, str(ref), datetime.now().isoformat(timespec="seconds")),
        )
