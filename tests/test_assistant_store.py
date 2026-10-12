"""assistant.store — assistant_turns 表的 CRUD 迴歸測試（獨立 practice.sqlite）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from assistant import store


@pytest.fixture
def temp_practice_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "practice.sqlite"
    monkeypatch.setattr(store, "PRACTICE_DB", db)
    return db


def test_log_turn_and_recent_turns_roundtrip(temp_practice_db):
    store.log_turn("web", "有哪些職缺要跟進？", "結論：無", ["job:1", "job:2"])
    store.log_turn("telegram", "今天有面試嗎？", "結論：無", [])

    turns = store.recent_turns(limit=10)
    assert [t["question"] for t in turns] == ["有哪些職缺要跟進？", "今天有面試嗎？"]
    assert turns[0]["channel"] == "web"
    assert turns[0]["citations"] == ["job:1", "job:2"]
    assert turns[1]["citations"] == []


def test_recent_turns_respects_limit_and_order(temp_practice_db):
    for i in range(5):
        store.log_turn("web", f"Q{i}", f"A{i}")

    turns = store.recent_turns(limit=2)
    # 最近 2 輪，時間正序（最舊在前，符合對話上下文閱讀順序）
    assert [t["question"] for t in turns] == ["Q3", "Q4"]


def test_turns_since_filters_by_date(temp_practice_db):
    store.log_turn("web", "old question", "old answer")
    turns = store.turns_since("2099-01-01")
    assert turns == []

    turns_all = store.turns_since("2000-01-01")
    assert len(turns_all) == 1
