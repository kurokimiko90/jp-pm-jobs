"""notify.dedupe.escalation_state + notify.events.notify_meeting_url_missing の回帰テスト。"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from notify import dedupe, events


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, company TEXT, title TEXT);
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            status TEXT NOT NULL, next_event TEXT, meeting_url TEXT,
            UNIQUE(job_id));
    """)
    conn.commit()
    conn.close()
    import tracker.db
    monkeypatch.setattr(tracker.db, "DB_PATH", db)
    return db


@pytest.fixture
def sent_messages(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(events, "send", lambda msg, **k: (calls.append(msg), True)[1])
    return calls


def _insert(temp_db, job_id, company, next_event, meeting_url=None, status="recruiter"):
    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?, ?)", (job_id, company, "title"))
    conn.execute(
        "INSERT INTO applications (job_id, status, next_event, meeting_url) VALUES (?, ?, ?, ?)",
        (job_id, status, next_event, meeting_url),
    )
    conn.commit()
    conn.close()


# ── escalation_state ──

def test_escalation_state_starts_empty(temp_db):
    assert dedupe.escalation_state("k", "ref") == (0, None)


def test_escalation_state_counts_and_returns_last(temp_db):
    dedupe.mark_sent("k", "ref:1")
    dedupe.mark_sent("k", "ref:2")
    count, last_at = dedupe.escalation_state("k", "ref")
    assert count == 2
    assert last_at is not None


def test_escalation_state_prefix_does_not_leak_across_refs(temp_db):
    dedupe.mark_sent("k", "5:2026-08-05:1")
    dedupe.mark_sent("k", "50:2026-08-05:1")  # 前綴不同事件，不該混進 5:2026-08-05 的計數
    count, _ = dedupe.escalation_state("k", "5:2026-08-05")
    assert count == 1


# ── notify_meeting_url_missing ──

def _future_date_str() -> str:
    return (date.today() + timedelta(days=1)).strftime("%Y/%m/%d")


def test_sends_when_meeting_url_missing(temp_db, sent_messages):
    _insert(temp_db, 1, "株式会社A", f"1次選考 確定 {_future_date_str()}(木) 14:00")
    n = events.notify_meeting_url_missing()
    assert n == 1
    assert "尚未取得會議連結" in sent_messages[0]


def test_skips_when_meeting_url_present(temp_db, sent_messages):
    _insert(temp_db, 1, "株式会社A", f"1次選考 確定 {_future_date_str()}(木) 14:00",
            meeting_url="https://meet.google.com/xxx")
    assert events.notify_meeting_url_missing() == 0
    assert sent_messages == []


def test_skips_past_event(temp_db, sent_messages):
    past = (date.today() - timedelta(days=1)).strftime("%Y/%m/%d")
    _insert(temp_db, 1, "株式会社A", f"1次選考 確定 {past}(木) 14:00")
    assert events.notify_meeting_url_missing() == 0


def test_skips_rejected(temp_db, sent_messages):
    _insert(temp_db, 1, "株式会社A", f"1次選考 確定 {_future_date_str()}(木) 14:00",
            status="rejected")
    assert events.notify_meeting_url_missing() == 0


def test_does_not_resend_within_interval(temp_db, sent_messages):
    _insert(temp_db, 1, "株式会社A", f"1次選考 確定 {_future_date_str()}(木) 14:00")
    assert events.notify_meeting_url_missing() == 1
    assert events.notify_meeting_url_missing() == 0  # 1 小時內第二輪不重推
    assert len(sent_messages) == 1


def test_stops_after_max_attempts(temp_db, sent_messages, monkeypatch):
    _insert(temp_db, 1, "株式会社A", f"1次選考 確定 {_future_date_str()}(木) 14:00")
    event_date = date.today() + timedelta(days=1)
    prefix = f"1:{event_date.isoformat()}"
    for n in range(1, 6):
        dedupe.mark_sent(events._MEETING_URL_KIND, f"{prefix}:{n}")
    assert events.notify_meeting_url_missing() == 0
    assert sent_messages == []
