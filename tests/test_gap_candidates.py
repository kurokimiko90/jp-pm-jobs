"""Gap 常駐掃描の選件 — 「PdM かつ JD に AI 言及」は分数に関係なく対象、JD 未取得は除外。

- 分数ゲート（min_score）だけだと、AI PdM でも評分 50 前後の求人が永久に gap されない
- 郵件摘要（60 字前後）やログイン壁の本文で gap を回すと半端な情報で判断してしまう
"""
import sqlite3

import pytest

from tools.jd_quality import MIN_JD_CHARS, is_login_wall, jd_ready
from tracker import db

FULL_JD = "仕事内容\n" + "AIプロダクトのロードマップ策定と要件定義。" * 40


def test_jd_ready_rejects_email_summary():
    assert not jd_ready("【PdM】AI×SaaS/年収900万~")


def test_jd_ready_rejects_empty_and_none():
    assert not jd_ready("")
    assert not jd_ready(None)


def test_jd_ready_rejects_login_wall_even_if_long():
    text = FULL_JD + "\n詳細をご覧になる場合は会員登録（無料）が必要です"
    assert len(text) > MIN_JD_CHARS
    assert is_login_wall(text)
    assert not jd_ready(text)


def test_jd_ready_accepts_full_jd():
    assert jd_ready(FULL_JD)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    path = tmp_path / "jobs.sqlite"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with sqlite3.connect(path) as conn:  # blacklisted は init_db 外の遷移で足される列
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "blacklisted" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN blacklisted INTEGER DEFAULT 0")
    return path


def _insert(path, **kw):
    row = {
        "source": "recruiter_agent", "source_id": str(kw["id"]), "url": "u",
        "title": "t", "first_seen": "2026-10-01", "last_seen": "2026-10-01",
        "score": 40, "job_type": "pdm", "mentions_ai": 1, "blacklisted": 0,
        "gap_analysis": None, "raw_jd": FULL_JD,
    }
    row.update(kw)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with sqlite3.connect(path) as conn:
        conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({marks})", list(row.values()))


def test_pdm_ai_candidates_filters_type_ai_and_window(tmp_db):
    _insert(tmp_db, id=1)                                   # 対象
    _insert(tmp_db, id=2, job_type="pjm")                   # PjM は対象外
    _insert(tmp_db, id=3, mentions_ai=0)                    # AI 言及なし
    _insert(tmp_db, id=4, blacklisted=1)                    # ブラックリスト
    _insert(tmp_db, id=5, first_seen="2020-01-01")          # 窓の外

    ids = {r["id"] for r in db.pdm_ai_candidates(days=17, today="2026-10-04")}
    assert ids == {1}


def test_pdm_ai_candidates_zero_days_disables(tmp_db):
    _insert(tmp_db, id=1)
    assert db.pdm_ai_candidates(days=0, today="2026-10-04") == []
