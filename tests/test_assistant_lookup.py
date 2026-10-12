"""assistant.lookup — 提問裡的企業／職缺模糊比對。

「XX 投過沒有」答錯的代價高（會讓人重複投遞或漏掉跟進），且誤命中與漏命中
的判斷全在幾條門檻上，所以把每條門檻的成立與不成立都固定成回歸測試。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from assistant import lookup


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY, source TEXT, source_id TEXT, url TEXT,
            title TEXT, company TEXT, company_norm TEXT, first_seen DATE,
            score INTEGER, recommend_score INTEGER, gap_analysis TEXT
        );
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL REFERENCES jobs(id),
            status TEXT NOT NULL, applied_at DATE NOT NULL, last_updated DATE NOT NULL,
            next_event TEXT, rejection_stage TEXT, rejection_reason TEXT,
            channel TEXT, UNIQUE(job_id)
        );

        INSERT INTO jobs VALUES
            -- 略称で引かれる想定（サンプル → サンプルロボティクス）。同社 2 求人
            (1,'s','1','u','PdM','株式会社サンプルロボティクス','サンプルロボティクス',
             '2026-08-01',59,77,'{"reason": "ドローン運用の知見は無いが基盤設計は合致"}'),
            (2,'s','2','u','PdM(PF)','株式会社 サンプルロボティクス','サンプルロボティクス',
             '2026-07-01',70,66,NULL),
            -- 空白入り ASCII 社名。DB 側は空白が潰れている
            (3,'s','3','u','PdM','株式会社SAMPLE STUDIO','samplestudio','2026-06-01',70,76,NULL),
            -- 語尾だけ共通する別会社（〜スタジオ／〜キャスト）
            (4,'s','4','u','PdM','株式会社テストスタジオ','テストスタジオ','2026-06-01',50,NULL,NULL),
            (5,'s','5','u','PdM','株式会社アドキャスト','アドキャスト','2026-06-01',50,NULL,NULL),
            (6,'s','6','u','PdM','株式会社ミライキャスト','ミライキャスト','2026-06-01',65,70,NULL),
            -- 日常英語と衝突する短い ASCII 社名
            (7,'s','7','u','PdM','find','find','2026-06-01',50,NULL,NULL),
            -- 単語境界の確認用（unread が unreadable に釣られない）
            (8,'s','8','u','PdM','Unread株式会社','unread','2026-06-01',90,NULL,NULL),
            -- 庫內にあるが未応募
            (9,'s','9','u','PdM','トヨタ自動車株式会社','トヨタ自動車','2026-06-01',60,NULL,NULL);

        INSERT INTO applications
            (job_id, status, applied_at, last_updated, rejection_stage, rejection_reason)
        VALUES
            (1, 'recruiter', '2026-08-23', '2026-08-31', NULL, NULL),
            (3, 'rejected', '2026-06-29', '2026-07-05', 'shorui', 'experience'),
            (6, 'applied', '2026-08-23', '2026-08-23', NULL, NULL);
    """)
    conn.commit()
    conn.close()

    import tracker.db
    monkeypatch.setattr(tracker.db, "DB_PATH", db)
    return db


# ── 命中すべきケース ────────────────────────────────────────────


def test_matches_full_name(temp_db):
    hits = lookup.find_companies("株式会社ミライキャストはどうなった？")
    assert [h["company_norm"] for h in hits] == ["ミライキャスト"]
    assert hits[0]["applied"] is True


def test_matches_abbreviation_by_prefix(temp_db):
    """略称は頭を取る。サンプル → サンプルロボティクス。"""
    hits = lookup.find_companies("サンプルに応募したっけ？")
    assert hits[0]["company_norm"] == "サンプルロボティクス"
    assert hits[0]["matched_text"] == "サンプル"


def test_groups_multiple_jobs_of_same_company(temp_db):
    """同じ会社で 2 求人ある。1 件だけ見て「未応募」と答えないよう企業単位で束ねる。"""
    hits = lookup.find_companies("サンプルロボティクス")
    assert hits[0]["job_count"] == 2
    assert len(hits[0]["applications"]) == 1
    assert len(hits[0]["other_jobs"]) == 1


def test_matches_ascii_name_written_with_space(temp_db):
    """質問文は `SAMPLE STUDIO`、DB は `samplestudio`。空白を潰さないと当たらない。"""
    hits = lookup.find_companies("SAMPLE STUDIO 這家我投過嗎？")
    assert [h["company_norm"] for h in hits] == ["samplestudio"]


def test_carries_rejection_detail(temp_db):
    hits = lookup.find_companies("SAMPLE STUDIO は？")
    app = hits[0]["applications"][0]
    assert app["status"] == "rejected"
    assert app["rejection_stage"] == "shorui"
    assert app["rejection_reason"] == "experience"


def test_reports_known_company_without_application(temp_db):
    hits = lookup.find_companies("トヨタ自動車に応募した？")
    assert hits[0]["applied"] is False
    assert hits[0]["applications"] == []


def test_carries_gap_reason(temp_db):
    hits = lookup.find_companies("サンプルロボティクス")
    assert "ドローン運用" in hits[0]["applications"][0]["gap_reason"]


# ── 命中してはいけないケース ──────────────────────────────────


def test_suffix_only_match_is_rejected(temp_db):
    """〜キャスト／〜スタジオ の語尾一致で同業他社を総なめにしない。"""
    names = {h["company_norm"] for h in lookup.find_companies("ミライキャストは？")}
    assert names == {"ミライキャスト"}
    assert "アドキャスト" not in names


def test_short_ascii_name_ignored(temp_db):
    """`find` のような日常英語と同形の短い社名は比對対象から外す。"""
    assert lookup.find_companies("我想 find 一份新工作") == []


def test_ascii_needs_word_boundary(temp_db):
    assert lookup.find_companies("unreadable な JD ばかり") == []
    assert [h["company_norm"] for h in lookup.find_companies("unread の求人は？")] == ["unread"]


def test_no_company_mentioned(temp_db):
    assert lookup.find_companies("最近很累") == []


def test_generic_token_never_matches(temp_db):
    assert lookup.match_score("システム", "システム開発の求人ある？") is None


# ── 意図判定と job:ID 直取り ──────────────────────────────────


@pytest.mark.parametrize("q", [
    "この会社に応募した？", "投過這家嗎", "有沒有應聘過", "選考は進んでる？",
    "did I apply to them?",
])
def test_apply_intent_detected(q):
    assert lookup.has_apply_intent(q)


def test_apply_intent_absent(temp_db):
    assert not lookup.has_apply_intent("今日は疲れた")


def test_find_jobs_by_id(temp_db):
    hits = lookup.find_jobs_by_id("job:1 と #6 の状況は？")
    assert sorted(h["id"] for h in hits) == [1, 6]


def test_find_jobs_by_id_empty(temp_db):
    assert lookup.find_jobs_by_id("番号は書いてない") == []
