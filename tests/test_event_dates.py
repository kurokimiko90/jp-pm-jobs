"""選考歴程の日付：過去メールの取り込みでも事件は「実際の日」で残る。"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest

import tracker.db as db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO jobs (id, source, source_id, url, title, company, first_seen, last_seen) "
                 "VALUES (1, 'indeed_jp', 'x', 'u', 'PdM', '株式会社サンプル', '2026-07-01', '2026-07-01')")
    conn.commit()
    conn.close()
    return path


def _events(path):
    conn = sqlite3.connect(path)
    return conn.execute("SELECT status, changed_at FROM application_events ORDER BY id").fetchall()


def test_backfilled_mails_keep_mail_dates(temp_db):
    db.record_application(1, "applied", applied_at="2026-07-20")
    db.record_application(1, "rejected", last_updated="2026-07-28", rejection_stage="shorui")
    assert _events(temp_db) == [("applied", "2026-07-20"), ("rejected", "2026-07-28")]


def test_live_record_keeps_write_time(temp_db):
    db.record_application(1, "applied")  # 日付の明示なし＝今その場の記録
    (status, changed_at), = _events(temp_db)
    assert changed_at.startswith(date.today().isoformat()) and len(changed_at) > 10
