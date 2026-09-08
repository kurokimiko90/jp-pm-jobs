"""assistant.context — 白名單資料檢索的迴歸測試（獨立 jobs.sqlite）。"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from assistant import context


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY, source TEXT, source_id TEXT, url TEXT,
            title TEXT, company TEXT, first_seen DATE, score INTEGER, tier TEXT,
            blacklisted INTEGER DEFAULT 0, gap_batch_id INTEGER, recommend_score INTEGER,
            gap_analysis TEXT, company_norm TEXT
        );
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id),
            status TEXT NOT NULL, applied_at DATE NOT NULL, last_updated DATE NOT NULL,
            resume_version TEXT, notes TEXT, next_event TEXT,
            rejection_stage TEXT, rejection_reason TEXT, gcal_event_id TEXT,
            channel TEXT, UNIQUE(job_id)
        );
        CREATE TABLE followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id),
            logged_at DATE NOT NULL, note TEXT NOT NULL, method TEXT DEFAULT 'email'
        );
        CREATE TABLE gap_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
            source_filter TEXT, min_score INTEGER, job_count INTEGER DEFAULT 0,
            summary_json TEXT
        );
    """)

    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    old_date = (today - timedelta(days=20)).isoformat()
    long_ago = (today - timedelta(days=90)).isoformat()

    conn.executescript(f"""
        INSERT INTO jobs VALUES
            (1,'s','1','u','面接控制のPdM','株式会社サンプル','{today.isoformat()}',85,'ai_startup',0,NULL,NULL,NULL,'サンプル'),
            (2,'s','2','u','逾期跟進職缺','株式会社デモテック','{old_date}',70,'traditional_sier',0,NULL,NULL,
             '{{"reason": "決済ドメインは合致するが英語要件が不明"}}','デモテック'),
            (3,'s','3','u','高分未投遞','株式会社Unread','{old_date}',90,'mega_venture',0,NULL,NULL,NULL,'unread'),
            (4,'s','4','u','已封殺職缺','株式会社Blacklisted','{today.isoformat()}',95,'ai_startup',1,NULL,NULL,NULL,'blacklisted'),
            (5,'s','5','u','Gap高分職缺','株式会社GapTop','{old_date}',60,'ai_startup',0,1,88,NULL,'gaptop'),
            (6,'s','6','u','古い不採用','株式会社オールド','{long_ago}',65,'ai_startup',0,NULL,NULL,NULL,'オールド'),
            (7,'s','7','u','短名 ASCII 誤命中確認','find','{old_date}',50,'ai_startup',0,NULL,NULL,NULL,'find');

        INSERT INTO applications (job_id, status, applied_at, last_updated, next_event) VALUES
            (1, 'tech', '2026-07-20', '2026-07-25', '{tomorrow.replace('-', '/')} 15:00 面接'),
            (2, 'recruiter', '{old_date}', '{old_date}', NULL),
            (5, 'applied', '{old_date}', '{old_date}', NULL),
            (6, 'rejected', '{long_ago}', '{long_ago}', NULL);

        INSERT INTO followups (job_id, logged_at, note) VALUES (1, '{today.isoformat()}', '初回跟進');

        INSERT INTO gap_batches (id, created_at, job_count) VALUES (1, '2026-07-30', 1);
    """)
    conn.commit()
    conn.close()

    import tracker.db
    monkeypatch.setattr(tracker.db, "DB_PATH", db)
    return db


def test_today_new_jobs_excludes_blacklisted(temp_db):
    rows = context.today_new_jobs()
    ids = [r["id"] for r in rows]
    assert 1 in ids
    assert 4 not in ids  # blacklisted


def test_overdue_followups_flags_job_2(temp_db):
    overdue = context.overdue_followups()
    job_ids = [o["job_id"] for o in overdue]
    assert 2 in job_ids
    assert 1 not in job_ids  # 剛跟進過，未到下次時程


def test_upcoming_interviews_within_window(temp_db):
    upcoming = context.upcoming_interviews(days=3)
    assert len(upcoming) == 1
    assert upcoming[0]["job_id"] == 1


def test_high_score_unread_jobs_excludes_applied_and_blacklisted(temp_db):
    unread = context.high_score_unread_jobs(threshold=80)
    ids = [j["id"] for j in unread]
    assert 3 in ids       # 高分未投遞
    assert 1 not in ids   # 已投遞（在 applications 裡）
    assert 4 not in ids   # blacklisted


def test_gap_batch_highlights_returns_latest_batch(temp_db):
    gap = context.gap_batch_highlights()
    assert gap is not None
    assert gap["batch_id"] == 1
    assert gap["top"][0]["id"] == 5


def test_build_context_cites_job_ids(temp_db):
    text = context.build_context()
    assert "job:2" in text  # 逾期跟進
    assert "job:3" in text  # 高分未投遞
    assert "job:1" in text  # 近期面試


def test_findings_prioritizes_interviews_first(temp_db):
    finds = context.findings()
    assert finds[0]["level"] == "P0"
    assert finds[0]["job_id"] == 1


# ── 現況檢索（缺這些區塊時 LLM 會退回去讀 [對話紀錄] 的舊職缺） ──────────


def test_active_pipeline_excludes_applied_and_rejected(temp_db):
    ids = [a["job_id"] for a in context.active_pipeline()]
    assert ids == [2, 1]  # recruiter / tech，按 last_updated DESC
    assert 5 not in ids  # applied = 尚未回覆，不算進行中
    assert 6 not in ids  # rejected = 已結束


def test_awaiting_reply_only_applied(temp_db):
    assert [w["job_id"] for w in context.awaiting_reply()] == [5]


def test_recent_applications_excludes_out_of_window(temp_db):
    ids = [a["job_id"] for a in context.recent_applications(days=21)]
    assert 2 in ids and 5 in ids
    assert 6 not in ids  # 90 天前，超出視窗
    assert 1 not in ids  # 2026-07-20 固定日期，同樣超窗


def test_build_context_includes_pipeline_and_recent_applications(temp_db):
    text = context.build_context()
    assert "[進行中的選考 共 2 條]" in text
    assert "[已投遞待回覆 共 1 筆]" in text
    assert "[近 21 天投遞]" in text
    assert "[資料截止]" in text


def test_build_context_appends_company_history(temp_db):
    text = context.build_context("デモテック 怎麼樣了？")
    assert "[提問提到的企業（應募歷史）]" in text
    assert "應募過" in text
    assert "決済ドメイン" in text


def test_build_context_states_not_applied_for_known_company(temp_db):
    """庫內有該公司但沒投遞過 → 必須明說「未應募過」，不能只是不提。"""
    text = context.build_context("株式会社Unread に応募した？")
    assert "未應募過" in text


def test_build_context_says_no_record_when_intent_but_no_match(temp_db):
    """「投過沒有」意圖 + 查無 → 明確否定。缺這句 LLM 會拿別家紀錄硬套。"""
    text = context.build_context("ズンドコ株式会社に応募したことある？")
    assert "[提問提到的企業（應募歷史）] 查無" in text


def test_build_context_skips_lookup_block_without_intent_or_match(temp_db):
    text = context.build_context("最近很累")
    assert "應募歷史" not in text


def test_build_context_appends_explicit_job_id(temp_db):
    text = context.build_context("job:2 現在到哪一關了？")
    assert "[提問指名的職缺（當前狀態）]" in text


# ── next_event 的日期解析（漏掉 = 該公司的日程整條看不到） ────────────


def test_event_note_accepts_dash_format(temp_db):
    """実データに `2026-09-03 11:00 面接` 形式が存在する。`/` だけ見ると丸ごと漏れる。"""
    future = (date.today() + timedelta(days=2)).isoformat()
    assert "2天後" in context._event_note(f"{future} 11:00 面接")


def test_event_note_flags_past_event(temp_db):
    past = (date.today() - timedelta(days=5)).isoformat()
    note = context._event_note(f"{past} 11:00 面接")
    assert "已過期5天" in note


def test_event_note_keeps_unparsable_text(temp_db):
    assert "調整中" in context._event_note("日程調整中")


def test_upcoming_interviews_accepts_dash_format(temp_db, tmp_path):
    import sqlite3 as _sq
    conn = _sq.connect(temp_db)
    future = (date.today() + timedelta(days=1)).isoformat()
    conn.execute(
        "INSERT INTO applications (job_id, status, applied_at, last_updated, next_event) "
        "VALUES (3, 'recruiter', ?, ?, ?)",
        (future, future, f"{future} 11:00 面接"),
    )
    conn.commit()
    conn.close()
    assert 3 in [i["job_id"] for i in context.upcoming_interviews(days=3)]
