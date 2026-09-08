"""assistant.chat — prompt 組裝 / PII 去識別化 / 引用抽取的迴歸測試（LLM 呼叫全部 mock）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from assistant import chat, store


@pytest.fixture
def temp_practice_db(tmp_path: Path, monkeypatch):
    db = tmp_path / "practice.sqlite"
    monkeypatch.setattr(store, "PRACTICE_DB", db)
    return db


@pytest.fixture(autouse=True)
def _stub_context_and_history(monkeypatch, temp_practice_db):
    monkeypatch.setattr(
        chat.context, "build_context",
        lambda question=None: "[逾期跟進]\njob:2 株式会社デモテック 逾期 3 天未跟進",
    )


def test_answer_includes_facts_and_question_in_prompt(monkeypatch):
    captured = {}

    def fake_call(prompt, timeout=120, model=""):
        captured["prompt"] = prompt
        return "結論：job:2 逾期 3 天，建議今天跟進。\n依據：job:2\n未知：無\n建議：發跟進信"

    monkeypatch.setattr(chat, "call", fake_call)
    monkeypatch.setattr(chat, "build_deid_profile", lambda compact=True: "positioning: PdM")

    result = chat.answer("有哪些職缺要跟進？", channel="web")

    assert "job:2" in captured["prompt"]
    assert "有哪些職缺要跟進？" in captured["prompt"]
    assert "positioning: PdM" in captured["prompt"]
    assert result["citations"] == ["job:2"]
    assert "job:2" in result["answer"]


def test_answer_skips_profile_when_missing(monkeypatch):
    monkeypatch.setattr(chat, "call", lambda prompt, timeout=120, model="": "結論：無\n依據：無\n未知：無\n建議：無")

    def raise_missing(compact=True):
        raise SystemExit("找不到 candidate_profile.yaml")

    monkeypatch.setattr(chat, "build_deid_profile", raise_missing)

    result = chat.answer("測試問題", channel="web")
    assert result["answer"].startswith("結論")


def test_answer_logs_turn_to_store(monkeypatch, temp_practice_db):
    monkeypatch.setattr(chat, "call", lambda prompt, timeout=120, model="": "結論：job:2 要跟進\n依據：job:2\n未知：無\n建議：無")
    monkeypatch.setattr(chat, "build_deid_profile", lambda compact=True: "")

    chat.answer("有哪些職缺要跟進？", channel="telegram")

    turns = store.recent_turns(limit=5)
    assert len(turns) == 1
    assert turns[0]["channel"] == "telegram"
    assert turns[0]["citations"] == ["job:2"]


def test_answer_empty_question_short_circuits(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(chat, "call", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "unused")

    result = chat.answer("   ", channel="web")
    assert called["n"] == 0
    assert result["citations"] == []
