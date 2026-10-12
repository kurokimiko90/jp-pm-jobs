"""linkedin_inbox.store（linkedin_messages 表 CRUD）測試。"""
from __future__ import annotations

from pathlib import Path

import pytest

from linkedin_inbox import store


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "jobs.sqlite"
    import tracker.db
    monkeypatch.setattr(tracker.db, "DB_PATH", db)
    store.init_linkedin_db()
    return db


def _conv(conversation_id="conv-1", **overrides):
    base = {
        "conversation_id": conversation_id,
        "sender_name": "山田太郎",
        "sender_headline": "Recruiter at ExampleCorp",
        "profile_url": "https://www.linkedin.com/in/example",
        "last_message_at": "",
        "category": "recruiting",
        "confidence": 0.85,
        "body_raw": "興味があればぜひご連絡ください。",
        "draft_text": None,
        "status": "classified",
    }
    base.update(overrides)
    return base


def test_upsert_and_exists(temp_db):
    assert not store.conversation_exists("conv-1")
    store.upsert_conversation(_conv())
    assert store.conversation_exists("conv-1")


def test_upsert_is_idempotent_and_updates_fields(temp_db):
    store.upsert_conversation(_conv())
    store.upsert_conversation(_conv(sender_name="山田太郎（更新）", confidence=0.9))
    rows = store.list_messages()
    assert len(rows) == 1
    assert rows[0]["sender_name"] == "山田太郎（更新）"
    assert rows[0]["confidence"] == 0.9


def test_upsert_does_not_clobber_existing_draft(temp_db):
    store.upsert_conversation(_conv())
    store.set_draft("conv-1", "既存の草稿")
    # 後続の再分類 upsert に draft_text が無くても既存草稿は保持される（status は
    # 呼び出し側が渡した値で上書きされるが、draft_text が非 NULL の限り claim_draft /
    # list_pending_drafts はこの対話を再取得しない）
    store.upsert_conversation(_conv())
    rows = store.list_messages()
    assert rows[0]["draft_text"] == "既存の草稿"
    assert store.claim_draft("conv-1") is False


def test_claim_draft_is_exclusive(temp_db):
    store.upsert_conversation(_conv())
    assert store.claim_draft("conv-1") is True
    # 直後の再クレームは失敗（誰かが処理中）
    assert store.claim_draft("conv-1") is False


def test_release_draft_claim_allows_retry(temp_db):
    store.upsert_conversation(_conv())
    store.claim_draft("conv-1")
    store.release_draft_claim("conv-1")
    assert store.claim_draft("conv-1") is True


def test_set_draft_marks_ready(temp_db):
    store.upsert_conversation(_conv())
    store.set_draft("conv-1", "こちらが返信の草稿です。")
    rows = store.list_messages(status="draft_ready")
    assert len(rows) == 1
    assert rows[0]["draft_text"] == "こちらが返信の草稿です。"


def test_list_pending_drafts_only_recruiting_without_draft(temp_db):
    store.upsert_conversation(_conv("conv-1", category="recruiting", confidence=0.9))
    store.upsert_conversation(_conv("conv-2", category="other", confidence=0.9, status="skipped"))
    store.upsert_conversation(_conv("conv-3", category="recruiting", confidence=0.2))
    pending = store.list_pending_drafts(min_conf=0.5)
    ids = {r["conversation_id"] for r in pending}
    assert ids == {"conv-1"}
