"""dashboard/backend/files_api.open_jds — 一括オープンの件数上限と順序保持。

score 範囲だけを送っていた頃は DB 全件（数百タブ）が一度に開いた。
上限と ID 指定順の維持をここで固定する。
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND))

files_api = pytest.importorskip("files_api")


def _row(i: int, source: str = "green") -> dict:
    return {"id": i, "url": f"https://example.com/{i}", "source": source, "source_id": str(i)}


@pytest.fixture
def spy(monkeypatch):
    """query() を差し替え、Popen で開かれた URL を記録する。"""
    launched: list[list[str]] = []
    monkeypatch.setattr(files_api.subprocess, "Popen",
                        lambda cmd, **kw: launched.append(cmd))
    return launched


def test_caps_batch_at_default_limit(monkeypatch, spy):
    monkeypatch.setattr(files_api, "query", lambda *a: [_row(i) for i in range(200)])

    r = files_api.open_jds({"score_min": 60, "score_max": 100})

    assert r["opened"] == files_api.DEFAULT_OPEN_LIMIT
    assert r["matched"] == 200
    assert r["skipped_over_limit"] == 200 - files_api.DEFAULT_OPEN_LIMIT
    urls = [a for cmd in spy for a in cmd if a.startswith("http")]
    assert len(urls) == files_api.DEFAULT_OPEN_LIMIT


def test_request_limit_is_hard_capped(monkeypatch, spy):
    monkeypatch.setattr(files_api, "query", lambda *a: [_row(i) for i in range(200)])

    r = files_api.open_jds({"limit": 999})

    assert r["limit"] == files_api.MAX_OPEN_LIMIT
    assert r["opened"] == files_api.MAX_OPEN_LIMIT


def test_job_ids_keep_caller_order_and_dedupe(monkeypatch, spy):
    # DB は昇順で返すが、画面の並び（呼び出し順）を保つ
    monkeypatch.setattr(files_api, "query", lambda *a: [_row(3), _row(7), _row(9)])

    r = files_api.open_jds({"job_ids": [9, 3, 9, 7]})

    assert r["opened"] == 3
    assert r["matched"] == 3
    urls = [a for cmd in spy for a in cmd if a.startswith("http")]
    assert urls == ["https://example.com/9", "https://example.com/3", "https://example.com/7"]


def test_job_ids_over_limit_are_truncated(monkeypatch, spy):
    monkeypatch.setattr(files_api, "query", lambda *a: [_row(i) for i in range(100)])

    r = files_api.open_jds({"job_ids": list(range(100))})

    assert r["opened"] == files_api.DEFAULT_OPEN_LIMIT
    assert r["skipped_over_limit"] == 100 - files_api.DEFAULT_OPEN_LIMIT
