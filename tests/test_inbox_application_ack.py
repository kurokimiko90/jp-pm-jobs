"""inbox.application_ack（応募受付メール → applications 自動記録）測試。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from inbox import application_ack
from inbox.rule_classify import classify_by_rules

# 実メールと同じ体裁（社名は全角、注意書きに「お見送り」を含む）
ACK_BODY = """※本メールは、応募手続きをしていただいたお客様へ
　自動返信をさせていただいております。

―――――――――――――――――――――――――――――――

ご応募いただいた求人について、応募手続きを承りました。

※ご応募いただいた求人の選考結果はマイページでご確認ください。
　担当者からは募集終了や書類選考お見送りのご連絡はメールでは通知されません。

▼応募手続き求人
【企業名】　株式会社ｓａｍｐｌｅ
【仕事の名称】　【プロダクトマネージャー】リモート可/フレックス
【求人No】　K20260322-112-01-027
"""

ACK_MAIL = {
    "subject": "応募手続きを承りました【リクルートエージェント】",
    "body": ACK_BODY,
    "received_at": "2026-07-25",
    "sender_email": "pdt_support@r-agent.com",
    "sender_domain": "r-agent.com",
}


# ── 抽出 ──

def test_extract_ack():
    assert application_ack.extract_ack(ACK_MAIL) == {
        "company": "株式会社sample",           # 全角 → NFKC 半角化
        "title": "【プロダクトマネージャー】リモート可/フレックス",
        "job_no": "K20260322-112-01-027",
    }


def test_extract_none_for_other_mail():
    # 日程確定メールも【企業名】を持つが、マーカーが無いので対象外
    mail = {"subject": "日程確定のお知らせ", "body": "【企業名】株式会社sample\n【確定日時】"}
    assert application_ack.extract_ack(mail) is None


# ── 分類優先權 ──

def test_classify_ack_beats_rejection():
    """本文の「お見送り」注意書きで rejection に誤判されないこと。"""
    assert classify_by_rules(ACK_MAIL)["category"] == "application_ack"


# ── DB 對應 + 寫入 ──

@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT, source_id TEXT,
            url TEXT, title TEXT, company TEXT, company_norm TEXT);
        CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id), status TEXT NOT NULL,
            applied_at DATE NOT NULL, last_updated DATE NOT NULL,
            resume_version TEXT, notes TEXT, next_event TEXT,
            rejection_stage TEXT, rejection_reason TEXT, gcal_event_id TEXT,
            channel TEXT, UNIQUE(job_id));
        INSERT INTO jobs VALUES
            (1,'indeed_jp','1','u','【プロダクトマネージャー】リモート可/フレックス',
                '株式会社sample','sample'),
            (2,'indeed_jp','2','u','データアナリスト','株式会社sample','sample'),
            (3,'recruiter_agent','3','u','PdM','株式会社デモテック','デモテック');
        INSERT INTO applications (job_id,status,applied_at,last_updated) VALUES
            (3,'rejected','2026-06-29','2026-07-02');
    """)
    conn.commit()
    conn.close()
    import tracker.db
    monkeypatch.setattr(tracker.db, "DB_PATH", db)
    return db


def test_match_job_picks_by_title(temp_db):
    # 同社 2 件 → 職務名類似度で job 1
    assert application_ack.match_job(
        "株式会社sample", "【プロダクトマネージャー】リモート可/フレックス"
    ) == 1


def test_match_job_unknown_company(temp_db):
    assert application_ack.match_job("株式会社存在しない", "PdM") is None


def test_apply_ack_records_application(temp_db):
    info = application_ack.apply_ack(ACK_MAIL)
    assert info["job_id"] == 1
    assert info["recorded"] is True
    assert info["reason"] == "recorded"

    conn = sqlite3.connect(temp_db)
    row = conn.execute(
        "SELECT status, applied_at, channel, notes FROM applications WHERE job_id=1"
    ).fetchone()
    conn.close()
    assert row[0] == "applied"
    assert row[1] == "2026-07-25"          # 応募日はメール受信日
    assert row[2] == "r-agent"             # 求人の初出が indeed でも経路は r-agent
    assert "K20260322-112-01-027" in row[3]


def test_apply_ack_idempotent(temp_db):
    assert application_ack.apply_ack(ACK_MAIL)["recorded"] is True
    again = application_ack.apply_ack(ACK_MAIL)
    assert again["recorded"] is False
    assert again["reason"] == "already"


def test_apply_ack_never_downgrades_existing_status(temp_db):
    """既に rejected の応募を applied に巻き戻さない。"""
    mail = {**ACK_MAIL, "body": ACK_BODY.replace("株式会社ｓａｍｐｌｅ", "株式会社デモテック")}
    info = application_ack.apply_ack(mail)
    assert info["job_id"] == 3
    assert info["recorded"] is False
    assert info["status"] == "rejected"

    conn = sqlite3.connect(temp_db)
    status = conn.execute("SELECT status FROM applications WHERE job_id=3").fetchone()[0]
    conn.close()
    assert status == "rejected"


def test_apply_ack_no_job_does_not_write(temp_db):
    mail = {**ACK_MAIL, "body": ACK_BODY.replace("株式会社ｓａｍｐｌｅ", "株式会社未知企業")}
    info = application_ack.apply_ack(mail)
    assert info["job_id"] is None
    assert info["reason"] == "no_job"

    conn = sqlite3.connect(temp_db)
    n = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
    conn.close()
    assert n == 1  # 既存の 1 件のみ、捏造なし


def test_apply_ack_returns_none_for_non_ack_mail(temp_db):
    assert application_ack.apply_ack({"subject": "面接のご案内", "body": "よろしく"}) is None
