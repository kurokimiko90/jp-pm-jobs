"""inbox.salary_reply（送信済みの年収回答 → salary_replies）と
app_analytics.build_salary_ask（年収回答 × 書類通過）の測試。

社名は架空（実際の応募先を tests に残さない）。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from inbox import salary_reply as sr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
import app_analytics as aa  # noqa: E402


# ── 金額抽出 ──

@pytest.mark.parametrize("text, expected", [
    # r-agent 年収確認の定型（【】内に数字、単位は外）
    ("・最低希望年収：【800】万円\n・希望年収：【900】万円\n・現年収：【550】万円",
     {"desired": 900, "minimum": 800, "current": 550}),
    # 【】内に単位まで入る / 全角
    ("■希望年収：【８５０万】\n■最低確保したい年収：【８００万】",
     {"desired": 850, "minimum": 800, "current": None}),
    # 単位を書かずに【】で閉じた回答
    ("■現年収：【550】\n■希望年収：【900】", {"desired": 900, "minimum": None, "current": 550}),
    # 記入欄の残骸が残ったまま 1 行に並ぶ
    ("【現年収：【現年収を記入】約600万円 最低希望年収：【最低希望年収を記入】880万円 "
     "希望年収：【希望年収を記入】960万円】",
     {"desired": 960, "minimum": 880, "current": 600}),
    # 「辞退しない最低限の希望年収」は最低（希望として二重に拾わない）
    ("・現年収：【550】万円\n・辞退しない最低限の希望年収：【750】万円\n・希望年収：【800】万円",
     {"desired": 800, "minimum": 750, "current": 550}),
    # ラベルの次行に金額、括弧内の現年収
    ("2）希望年収\n800万円〜（現年収520万円）", {"desired": 800, "minimum": None, "current": 520}),
    # 4 桁・コロンなし
    ("希望年収1000万円、最低希望年収960万円", {"desired": 1000, "minimum": 960, "current": None}),
])
def test_parse_amounts(text, expected):
    assert sr.parse_amounts(text) == expected


def test_parse_ignores_unlabeled_numbers():
    # 希望/最低のラベルが無い金額（オファー交渉の地の文）は拾わない
    text = "提示条件は固定年収610万円＋賞与で、年収は約550万円です。希望年収その理由を説明します。"
    assert sr.parse_amounts(text) == {"desired": None, "minimum": None, "current": None}


def test_own_text_drops_quote():
    body = "希望年収：【900】万円\n\n2026年8月20日(木) 10:00 担当 <a@example.com>:\n> 希望年収：【1200】万円"
    assert sr.parse_amounts(sr.own_text(body))["desired"] == 900


# ── 件名 → 社名 ──

@pytest.mark.parametrize("subject, first", [
    ("Re: 【8/25(火)12:00〆　年収確認のお願い】 (株)サンプルテック/本人様 【リクルートエージェント】",
     "(株)サンプルテック"),
    ("Re: ≪9/16（水）正午〆：事前確認依頼≫ＤＥＭＯ　ＨＯＬＤＩＮＧＳ社", "DEMO HOLDINGS"),
    # 株式会社の「社」は落とさない
    ("Re: ≪07/24(金)12:00〆：事前確認依頼≫モック株式会社", "モック株式会社"),
])
def test_company_candidates(subject, first):
    assert sr.company_candidates(subject)[0] == first


def test_company_after_slash_is_candidate():
    # 断片は順に照合される（最初の断片が求人に当たらなければ次へ）
    cands = sr.company_candidates("Re: 【本人様、ご返信ください】最終選考へ向けてのご意向/ｅｘａｍｐｌｅ社")
    assert "example" in cands


def test_company_embedded_in_sentence():
    cands = sr.company_candidates(
        "Re: ★至急★【重要】面接終了後にご感想をお知らせください　株式会社ｓａｍｐｌｅ　面接日時：2026/08/31")
    assert "株式会社sample" in cands


# ── 段階判定 + 記録 ──

@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT, company TEXT, company_norm TEXT);
        CREATE TABLE application_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER, status TEXT, prev_status TEXT, changed_at TIMESTAMP);
        CREATE TABLE applications (job_id INTEGER, status TEXT, applied_at DATE);
        INSERT INTO jobs VALUES (1,'PdM','株式会社サンプルテック','サンプルテック'),
                                (2,'PdM','株式会社デモ','デモ'),
                                (3,'AI-PdM','株式会社モック','モック'),
                                (4,'DX推進PdM','株式会社モック','モック');
        INSERT INTO applications VALUES (4,'rejected','2026-07-14');
        INSERT INTO application_events (job_id,status,changed_at) VALUES
            (2,'applied','2026-07-01 10:00:00'), (2,'recruiter','2026-07-10 10:00:00');
    """)
    conn.commit()
    conn.close()
    import tracker.db
    monkeypatch.setattr(tracker.db, "DB_PATH", db)
    return db


def _mail(msg_id, subject, body, date="2026-07-20"):
    return {"gmail_msg_id": msg_id, "subject": subject, "body": body, "received_at": date}


def test_record_stage_and_idempotent(temp_db):
    mails = [
        _mail("m1", "Re: 【年収確認のお願い】(株)サンプルテック/本人様", "・希望年収：【900】万円"),
        # 回答日より前に面接段階へ進んでいた → interview
        _mail("m2", "Re: ≪事前確認依頼≫株式会社デモ", "・希望年収：【960】万円"),
        _mail("m3", "Re: 【内定のご連絡】株式会社デモ", "希望年収：960万円"),
        _mail("m4", "Re: 面談のお願い", "今日はありがとうございました。"),  # 金額なし → 対象外
    ]
    infos = [i for i in (sr.extract(m) for m in mails) if i]
    assert [i["gmail_msg_id"] for i in infos] == ["m1", "m2", "m3"]
    sr.record(infos)
    assert [(i["job_id"], i["stage"], i["new"]) for i in infos] == [
        (1, "shorui", True), (2, "interview", True), (2, "offer", True)]

    again = sr.record([sr.extract(mails[0])])
    assert again[0]["new"] is False
    conn = sqlite3.connect(temp_db)
    assert conn.execute("SELECT COUNT(*) FROM salary_replies").fetchone()[0] == 3


def test_match_prefers_applied_job_of_same_company(temp_db):
    # 同社に求人が 2 件（先頭は未応募）。件名に職種が無くても応募した方へ付ける
    info = sr.extract(_mail("m5", "Re: 【年収確認のお願い】(株)モック/本人様", "・希望年収：【1000】万円"))
    assert info["job_id"] == 4


# ── 統計 ──

def _ann(job_id, outcome, applied_at="2026-07-01", company="テスト株式会社"):
    return {"job_id": job_id, "company": company, "outcome": outcome, "status": "rejected",
            "rejection_stage": None, "applied_at": applied_at}


def _sal(job_id, stage="shorui", desired=900, minimum=800, replied_at="2026-07-01"):
    return {"job_id": job_id, "stage": stage, "desired_man": desired, "minimum_man": minimum,
            "replied_at": replied_at, "company": "テスト株式会社"}


def test_salary_ask_uses_first_shorui_reply_only():
    annotated = [_ann(1, "pass"), _ann(2, "fail"), _ann(3, "fail"),
                 _ann(4, "pass", applied_at="2026-07-05"), _ann(5, "fail", applied_at="2026-06-01")]
    rows = [
        _sal(1, desired=960), _sal(1, stage="interview", desired=800),  # 面接段階の回答は使わない
        _sal(2, desired=960), _sal(3, desired=800),
        _sal(9, desired=1000),  # 応募記録なし
    ]
    out = aa.build_salary_ask(annotated, rows)
    assert out["summary"]["passed"] == 1 and out["summary"]["decided"] == 3
    assert out["summary"]["no_application"] == 1
    assert {b["key"]: b["decided"] for b in out["by_desired"]} == {"960": 2, "800": 1}
    # 対照は「最初の回答日以降に応募・回答なし」だけ（6/01 の応募は期間外）
    assert out["control"]["jobs"] == 1 and out["control"]["passed"] == 1


def test_salary_ask_empty():
    out = aa.build_salary_ask([_ann(1, "fail")], [])
    assert out["summary"]["jobs"] == 0 and out["summary"]["since"] is None
    assert out["control"]["jobs"] == 0
