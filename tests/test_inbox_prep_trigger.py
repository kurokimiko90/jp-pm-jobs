"""inbox.prep_trigger — 日程確定済みの面接 → 面接パック自動生成のトリガ。

固定したい仕様：
  - 過去の日程・rejected・日付を読めない next_event は対象外
  - 既存パックは**再生成しない**（手編集した 01_interview_qa.md が消えるため）
  - 同じ日程で二度走らない（成功後も、既存パック通知後も）
  - 失敗は上限回数まで再試行し、上限に達したら黙る
"""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from inbox import prep_trigger


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, company TEXT, title TEXT);
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            status TEXT NOT NULL, next_event TEXT,
            UNIQUE(job_id));
    """)
    conn.commit()
    conn.close()
    import tracker.db
    monkeypatch.setattr(tracker.db, "DB_PATH", db)
    return db


@pytest.fixture
def prep_dir(tmp_path: Path, monkeypatch):
    d = tmp_path / "output" / "prep"
    d.mkdir(parents=True)
    monkeypatch.setattr(prep_trigger, "PREP_DIR", d)
    monkeypatch.setattr(prep_trigger, "ROOT", tmp_path)
    return d


@pytest.fixture
def sent(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        prep_trigger, "send",
        lambda msg, title="", **k: (calls.append((title, msg)), True)[1],
    )
    return calls


@pytest.fixture
def no_config(monkeypatch, tmp_path: Path):
    """config/prep.yaml が実環境にあってもテストは既定値で回す。"""
    from tools import app_config
    app_config.load.cache_clear()
    monkeypatch.setattr(app_config, "CONFIG_DIR", tmp_path / "empty-config")
    yield
    app_config.load.cache_clear()


def _add(temp_db, job_id: int, next_event: str, status: str = "recruiter",
         company: str = "株式会社サンプル") -> None:
    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT OR IGNORE INTO jobs VALUES (?, ?, ?)", (job_id, company, "PM"))
    conn.execute(
        "INSERT INTO applications (job_id, status, next_event) VALUES (?, ?, ?)",
        (job_id, status, next_event),
    )
    conn.commit()
    conn.close()


def _event(days_ahead: int) -> str:
    d = date.today() + timedelta(days=days_ahead)
    return f"1次選考 確定 {d.year}/{d.month:02d}/{d.day:02d}(水) 18:00"


def _make_pack(prep_dir: Path, job_id: int) -> Path:
    pack = prep_dir / f"{job_id}_サンプル"
    pack.mkdir()
    (pack / "01_interview_qa.md").write_text("# 想定問答", encoding="utf-8")
    return pack


# ── pending() の絞り込み ──

def test_pending_picks_future_confirmed_interview(temp_db, prep_dir):
    _add(temp_db, 1, _event(3))
    items = prep_trigger.pending()
    assert [i["job_id"] for i in items] == [1]
    assert items[0]["has_pack"] is False


def test_pending_skips_past_interview(temp_db, prep_dir):
    _add(temp_db, 1, _event(-1))
    assert prep_trigger.pending() == []


def test_pending_skips_rejected(temp_db, prep_dir):
    _add(temp_db, 1, _event(3), status="rejected")
    assert prep_trigger.pending() == []


def test_pending_skips_unparseable_date(temp_db, prep_dir):
    _add(temp_db, 1, "日程調整中")
    assert prep_trigger.pending() == []


def test_pending_sorted_by_event_date(temp_db, prep_dir):
    _add(temp_db, 1, _event(10))
    _add(temp_db, 2, _event(2))
    assert [i["job_id"] for i in prep_trigger.pending()] == [2, 1]


def test_pending_flags_existing_pack(temp_db, prep_dir):
    _add(temp_db, 7, _event(3))
    _make_pack(prep_dir, 7)
    assert prep_trigger.pending()[0]["has_pack"] is True


# ── run()：生成 ──

def test_run_generates_pack_and_notifies(temp_db, prep_dir, sent, no_config, monkeypatch):
    _add(temp_db, 1, _event(3))
    calls: list[list[str]] = []

    def fake_run(job_id, stages, timeout):
        calls.append(stages)
        _make_pack(prep_dir, job_id)
        return True, ""

    monkeypatch.setattr(prep_trigger, "run_pack", fake_run)
    assert prep_trigger.run() == ["生成完了 #1"]
    assert calls == [["qa", "jikoshoukai", "checklist", "script"]]
    assert "面接パック生成完了" in sent[0][0]


def test_run_does_not_regenerate_second_time(temp_db, prep_dir, sent, no_config, monkeypatch):
    _add(temp_db, 1, _event(3))
    monkeypatch.setattr(
        prep_trigger, "run_pack",
        lambda job_id, stages, timeout: (_make_pack(prep_dir, job_id), (True, ""))[1],
    )
    prep_trigger.run()
    assert prep_trigger.run() == []          # 2 回目は完全に no-op
    assert len(sent) == 1                    # 通知も増えない


def test_run_skips_existing_pack_and_notifies_once(temp_db, prep_dir, sent, no_config, monkeypatch):
    _add(temp_db, 9, _event(3))
    _make_pack(prep_dir, 9)
    monkeypatch.setattr(prep_trigger, "run_pack",
                        lambda *a, **k: pytest.fail("既存パックを再生成してはいけない"))
    assert prep_trigger.run() == ["既存パックあり・通知のみ #9"]
    assert "面接パック既存" in sent[0][0]
    assert prep_trigger.run() == []          # 通知は 1 回きり


def test_run_respects_max_per_run(temp_db, prep_dir, sent, no_config, monkeypatch):
    _add(temp_db, 1, _event(2))
    _add(temp_db, 2, _event(3))
    monkeypatch.setattr(
        prep_trigger, "run_pack",
        lambda job_id, stages, timeout: (_make_pack(prep_dir, job_id), (True, ""))[1],
    )
    assert prep_trigger.run() == ["生成完了 #1"]   # 近い方から 1 本だけ


# ── run()：失敗 ──

def test_failure_retries_until_max_attempts(temp_db, prep_dir, sent, no_config, monkeypatch):
    _add(temp_db, 1, _event(3))
    monkeypatch.setattr(prep_trigger, "run_pack",
                        lambda *a, **k: (False, "指揮中心不可用"))
    assert prep_trigger.run() == ["生成失敗 #1"]
    assert prep_trigger.run() == ["生成失敗 #1"]
    assert prep_trigger.run() == []          # 既定 2 回で打ち止め
    assert all("生成失敗" in t for t, _ in sent)


def test_timeout_is_reported_not_raised(temp_db, prep_dir, sent, no_config, monkeypatch):
    _add(temp_db, 1, _event(3))

    def boom(job_id, stages, timeout):
        raise subprocess.TimeoutExpired(cmd="prep.py", timeout=timeout)

    monkeypatch.setattr(prep_trigger, "run_pack", boom)
    assert prep_trigger.run() == ["生成失敗 #1"]
    assert "timeout" in sent[0][1]


# ── dry-run / 設定 ──

def test_dry_run_does_not_execute_or_notify(temp_db, prep_dir, sent, no_config, monkeypatch):
    _add(temp_db, 1, _event(3))
    monkeypatch.setattr(prep_trigger, "run_pack",
                        lambda *a, **k: pytest.fail("dry-run で実行してはいけない"))
    out = prep_trigger.run(dry_run=True)
    assert out and out[0].startswith("[生成対象] #1")
    assert sent == []
    assert prep_trigger.run(dry_run=True)    # dry-run は状態を消費しない


def test_disabled_by_config(temp_db, prep_dir, sent, monkeypatch, tmp_path):
    _add(temp_db, 1, _event(3))
    from tools import app_config
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "prep.yaml").write_text("auto_interview_pack: false\n", encoding="utf-8")
    app_config.load.cache_clear()
    monkeypatch.setattr(app_config, "CONFIG_DIR", cfg_dir)
    try:
        assert prep_trigger.run() == []
        assert sent == []
    finally:
        app_config.load.cache_clear()


def test_lock_blocks_overlapping_round(tmp_path, monkeypatch):
    monkeypatch.setattr(prep_trigger, "LOCK_DIR", tmp_path / "logs" / ".lock")
    with prep_trigger._lock() as first:
        assert first is True
        with prep_trigger._lock() as second:
            assert second is False
    with prep_trigger._lock() as again:
        assert again is True     # 解放されている
