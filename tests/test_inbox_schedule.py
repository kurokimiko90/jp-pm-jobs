"""inbox.schedule（日程確定抽出→applications 反映）+ rule_classify 優先權測試。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from inbox import schedule
from inbox.rule_classify import classify_by_rules

R_AGENT_BODY = """＜本メールは日程調整システムから自動送信しております＞

胡様

下記企業の日程が確定しましたので、ご確認ください。

========================================================

【企業名】株式会社サンプル
【仕事の名称】【東京/マネージャー】ITコンサルタント/積極採用中
【確定日時】
　2026/07/08(水)　20:00スタート

----------------------------------------------------------
"""

TIMEREX_BODY = """胡 さん

TimeRexで日程調整いただきありがとうございました。
以下の通り、ビズフリ運営事務局さんとの日程調整が完了しました。

日時：2026年7月6日 (月) 13:00 - 13:30（Asia/Tokyo）
Web会議室：https://meet.google.com/xxx-xxxx-xxx
"""

# 実在の「選考情報変更のご連絡」メール（会社名は匿名化、構造・折返しは実物どおり）：
# 【企業名】に職務名が括弧書きで同居し、【仕事の名称】行は存在しない。
R_AGENT_INFO_CHANGE_BODY = """＜本メールは日程調整システムから自動送信しております＞

胡様

下記企業の面接URLが確定しましたので、選考情報をご確認ください。

===================================================================
【企業名】株式会社サンプル（【プロダクトマネージャー】新規事業の推進）
【確定日時】
　2026/08/05(水)　18:00スタート
【所要時間】
　約1時間0分

　訪問先：
　　Microsoft Teams 会議
　　参加する: https://teams.microsoft.com/meet/229100708597653?p=EXlrIGLzp98QbGY776

　　会議 ID: 229 100 708 597 653
"""

# Teamsの長いURLが本文で折返される実例（会社名匿名化、URL構造は実物どおり）
R_AGENT_INFO_CHANGE_WRAPPED_URL_BODY = """＜本メールは日程調整システムから自動送信しております＞

胡様

下記企業の面接URLが確定しましたので、選考情報をご確認ください。

===================================================================
【企業名】株式会社サンプル（【東京】ITコンサルタント）
【確定日時】
　2026/07/08(水)　20:00スタート

　訪問先：
　　面接URL / 面接場所：会議URL:
　　https://teams.microsoft.com/l/meetup-
　　join/19%3ameeting_NzlkODkxMTMtZGQ5Ni00YWE3LWJmMjAtMGVjZmI5NmIwNjA4%40thread.v2/0?context=%7b%22Tid%22%3a%220defc166-5d6f-4b1b-bf1b-eef505d764a4%22%7d
　　ミーティングID: 4510474927450 パスコード: eq3G6gN3
"""


# ── 抽出 ──

def test_extract_r_agent():
    info = schedule.extract_schedule({"subject": "【確定】株式会社サンプル/日程確定のお知らせ", "body": R_AGENT_BODY})
    assert info == {
        "company": "株式会社サンプル",
        "title": "【東京/マネージャー】ITコンサルタント/積極採用中",
        "date": "2026/07/08",
        "weekday": "水",
        "time": "20:00",
        "duration_min": None,
        "meeting_url": None,  # r-agent形式はメール本文にURLが載らない（マイページ側）
        "format": "r_agent",
    }


def test_extract_r_agent_with_duration():
    body = R_AGENT_BODY.replace(
        "----------------------------------------------------------",
        "【所要時間】\n　約1時間0分\n----------------------------------------------------------",
    )
    info = schedule.extract_schedule({"subject": "", "body": body})
    assert info["duration_min"] == 60


def test_extract_timerex():
    info = schedule.extract_schedule({"subject": "ビズフリ運営事務局さんとの日程調整が完了しました", "body": TIMEREX_BODY})
    assert info["company"] == "ビズフリ運営事務局"
    assert info["date"] == "2026/07/06"
    assert info["weekday"] == "月"
    assert info["time"] == "13:00"
    assert info["duration_min"] == 30
    assert info["meeting_url"] == "https://meet.google.com/xxx-xxxx-xxx"
    assert info["format"] == "timerex"


def test_extract_none_for_ordinary_mail():
    assert schedule.extract_schedule({"subject": "面接のご案内", "body": "よろしくお願いします"}) is None


def test_extract_meeting_url_ignores_unrelated_link():
    """r-agentの【ご紹介URL】等、会議と無関係なリンクは拾わない。"""
    body = R_AGENT_BODY + "\n【ご紹介URL】\nhttps://mypage.r-agent.com/entry?entry_mode=refer\n"
    assert schedule._extract_meeting_url(body) is None


def test_extract_meeting_url_by_domain_without_label():
    """ラベルが無くても既知の会議系ドメインなら拾う。"""
    body = "オンライン面接となります。\nhttps://us02web.zoom.us/j/123456789\nよろしくお願いします。"
    assert schedule._extract_meeting_url(body) == "https://us02web.zoom.us/j/123456789"


def test_extract_meeting_url_wrapped_teams_link():
    """長いTeamsURLが本文で改行折返しされていても正しく1本に復元する。"""
    url = schedule._extract_meeting_url(R_AGENT_INFO_CHANGE_WRAPPED_URL_BODY)
    assert url == (
        "https://teams.microsoft.com/l/meetup-"
        "join/19%3ameeting_NzlkODkxMTMtZGQ5Ni00YWE3LWJmMjAtMGVjZmI5NmIwNjA4%40thread.v2/0"
        "?context=%7b%22Tid%22%3a%220defc166-5d6f-4b1b-bf1b-eef505d764a4%22%7d"
    )
    # 折返し後の「ミーティングID」行は呑み込まない
    assert "ミーティング" not in url


def test_extract_info_change_mail_splits_embedded_title():
    """【企業名】に括弧書きで同居する職務名を正しく分離し、URLも抽出する。"""
    info = schedule.extract_schedule(
        {"subject": "※ご確認ください※【株式会社サンプル】1次選考　選考情報変更のご連絡",
         "body": R_AGENT_INFO_CHANGE_BODY}
    )
    assert info["company"] == "株式会社サンプル"
    assert info["title"] == "【プロダクトマネージャー】新規事業の推進"
    assert info["date"] == "2026/08/05"
    assert info["time"] == "18:00"
    assert info["meeting_url"] == "https://teams.microsoft.com/meet/229100708597653?p=EXlrIGLzp98QbGY776"


def test_classify_info_change_mail_as_schedule_confirmed():
    """「選考情報変更のご連絡」は返信不要の確定通知——scheduling(要返信)に截走されない。"""
    mail = {
        "subject": "※ご確認ください※【株式会社サンプル】1次選考　選考情報変更のご連絡",
        "body": R_AGENT_INFO_CHANGE_BODY,
        "sender_domain": "r-agent.com",
        "sender_email": "staff01@r-agent.com",
    }
    assert classify_by_rules(mail)["category"] == "schedule_confirmed"


# ── DB 對應 + 寫入 ──

@pytest.fixture(autouse=True)
def _no_gcal(monkeypatch):
    """測試環境不打真實 Google Calendar API（避免依賴/污染真實帳號）。"""
    monkeypatch.setattr("inbox.calendar_sync.upsert_event", lambda *a, **k: None)


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT, source_id TEXT,
            url TEXT, title TEXT, company TEXT);
        CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES jobs(id), status TEXT NOT NULL,
            applied_at DATE NOT NULL, last_updated DATE NOT NULL,
            resume_version TEXT, notes TEXT, next_event TEXT,
            rejection_stage TEXT, rejection_reason TEXT, gcal_event_id TEXT,
            meeting_url TEXT,
            UNIQUE(job_id));
        INSERT INTO jobs VALUES
            (1,'s','1','u','人事部 / 中途採用メンバー','株式会社サンプル'),
            (2,'s','2','u','【東京/マネージャー】ITコンサルタント/積極採用中','株式会社サンプル'),
            (3,'s','3','u','【プロダクトマネージャー】プロダクト戦略の推進','株式会社デモテック');
        INSERT INTO applications (job_id,status,applied_at,last_updated,next_event) VALUES
            (2,'recruiter','2026-06-26','2026-07-02','1次選考 面接日程調整中'),
            (3,'recruiter','2026-06-29','2026-07-02','カジュアル面談 面接日程調整中');
    """)
    conn.commit()
    conn.close()
    import tracker.db
    monkeypatch.setattr(tracker.db, "DB_PATH", db)
    return db


def test_match_prefers_applied_job_with_title(temp_db):
    # job 1（未投遞）不在候選；job 2 靠職缺名比對命中
    jid = schedule.match_application(
        "株式会社サンプル",
        "【東京/マネージャー】ITコンサルタント/積極採用中",
    )
    assert jid == 2


def test_match_unknown_company(temp_db):
    assert schedule.match_application("ビズフリ運営事務局", "") is None


def test_apply_schedule_updates_next_event_and_preserves_stage(temp_db):
    mail = {"subject": "【確定】株式会社サンプル/日程確定のお知らせ", "body": R_AGENT_BODY}
    info = schedule.apply_schedule(mail)
    assert info["job_id"] == 2
    assert info["changed"] is True
    assert info["next_event"] == "1次選考 確定 2026/07/08(水) 20:00"

    conn = sqlite3.connect(temp_db)
    row = conn.execute("SELECT next_event FROM applications WHERE job_id=2").fetchone()
    conn.close()
    assert row[0] == "1次選考 確定 2026/07/08(水) 20:00"


def test_apply_schedule_idempotent(temp_db):
    mail = {"subject": "【確定】株式会社サンプル/日程確定のお知らせ", "body": R_AGENT_BODY}
    assert schedule.apply_schedule(mail)["changed"] is True
    # リマインド重送 → 同値、changed=False（不重複通知）
    assert schedule.apply_schedule(mail)["changed"] is False


def test_apply_schedule_backfills_meeting_url_even_when_datetime_unchanged(temp_db):
    """先收到「日程確定のお知らせ」(無URL) → 後收到「選考情報変更のご連絡」(同日時+URL)，
    next_event 字串沒變也要視為 changed，把 URL 補進去（不能因為冪等檢查被吃掉）。"""
    first = schedule.apply_schedule(
        {"subject": "【確定】株式会社サンプル/日程確定のお知らせ", "body": R_AGENT_BODY}
    )
    assert first["changed"] is True
    assert first["meeting_url"] is None

    second = schedule.apply_schedule(
        {"subject": "※ご確認ください※【株式会社サンプル】1次選考　選考情報変更のご連絡",
         "body": R_AGENT_INFO_CHANGE_WRAPPED_URL_BODY}
    )
    assert second["job_id"] == 2
    assert second["changed"] is True
    assert second["meeting_url"].startswith("https://teams.microsoft.com/l/meetup-join/")

    conn = sqlite3.connect(temp_db)
    row = conn.execute("SELECT meeting_url FROM applications WHERE job_id=2").fetchone()
    conn.close()
    assert row[0] == second["meeting_url"]


def test_apply_schedule_unmatched_returns_info(temp_db):
    mail = {"subject": "ビズフリ運営事務局さんとの日程調整が完了しました", "body": TIMEREX_BODY}
    info = schedule.apply_schedule(mail)
    assert info["job_id"] is None
    assert info["changed"] is False


# ── 草稿補生（LLM 故障自癒）──

def test_list_pending_drafts(temp_db):
    from inbox.store import claim_draft, init_inbox_db, list_pending_drafts, release_draft_claim, upsert_mail

    from datetime import date

    init_inbox_db()
    base = {"thread_id": "t", "sender": "s", "received_at": date.today().isoformat(),
            "confidence": 0.6, "summary": "", "status": "classified"}
    upsert_mail({**base, "gmail_msg_id": "m1", "subject": "要補生",
                 "category": "scheduling", "body_raw": "本文"})
    upsert_mail({**base, "gmail_msg_id": "m2", "subject": "已有草稿",
                 "category": "scheduling", "draft_id": "d1"})
    upsert_mail({**base, "gmail_msg_id": "m3", "subject": "日程確定不需回",
                 "category": "schedule_confirmed"})
    upsert_mail({**base, "gmail_msg_id": "m4", "subject": "低信心",
                 "category": "initial_contact", "confidence": 0.4})

    pending = list_pending_drafts(7, ("other", "rejection", "schedule_confirmed"), 0.5)
    assert [p["gmail_msg_id"] for p in pending] == ["m1"]
    assert pending[0]["body_raw"] == "本文"
    assert claim_draft("m1") is True
    assert claim_draft("m1") is False  # 重疊掃描不可重複生成草稿
    release_draft_claim("m1")
    assert claim_draft("m1") is True


def test_list_pending_drafts_can_use_exact_hour_window(temp_db):
    from inbox.store import init_inbox_db, list_pending_drafts, upsert_mail
    from datetime import datetime

    init_inbox_db()
    now = int(datetime.now().timestamp())
    base = {"thread_id": "t", "sender": "s", "received_at": "2026-07-12",
            "confidence": 0.6, "summary": "", "status": "classified", "category": "scheduling"}
    upsert_mail({**base, "gmail_msg_id": "recent", "subject": "recent",
                 "gmail_received_at": now - 14 * 60 * 60})
    upsert_mail({**base, "gmail_msg_id": "old", "subject": "old",
                 "gmail_received_at": now - 16 * 60 * 60})

    pending = list_pending_drafts(7, ("other", "rejection", "schedule_confirmed"), 0.5, hours=15)
    assert [p["gmail_msg_id"] for p in pending] == ["recent"]


# ── 分類優先權 ──

def test_classify_schedule_confirmed_beats_scheduling():
    mail = {
        "subject": "【確定】株式会社デモテック/日程確定のお知らせ",
        "body": R_AGENT_BODY,
        "sender_domain": "r-agent.com",
        "sender_email": "staff01@r-agent.com",
    }
    assert classify_by_rules(mail)["category"] == "schedule_confirmed"


def test_classify_timerex_schedule_confirmed():
    mail = {
        "subject": "ビズフリ運営事務局さんとの日程調整が完了しました",
        "body": TIMEREX_BODY,
        "sender_domain": "timerex.net",
        "sender_email": "notifications@timerex.net",
    }
    assert classify_by_rules(mail)["category"] == "schedule_confirmed"


def test_classify_password_mail_excluded():
    mail = {
        "subject": "【ご対応願】株式会社ｐｉｎｅａｌ：1次選考 選考日程調整のご連絡 のパスワード通知メール",
        "body": "",
        "sender_domain": "r-agent.com",
        "sender_email": "staff01@r-agent.com",
    }
    assert classify_by_rules(mail)["category"] == "other"
