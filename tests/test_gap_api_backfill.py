"""gap_api.gap_single — raw_jd が空でも自動で JD を取り直してから分析する。

以前は「raw_jd 無し → 400」で行き止まり（画面には "400" だけが出る）。
自動補抓と、取れなかった時に理由が伝わることをここで固定する。
"""
import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND))

gap_api = pytest.importorskip("gap_api")
from fastapi import HTTPException  # noqa: E402


@pytest.fixture
def fake_analyzer(monkeypatch):
    """analyzer.gap_analyzer をダミーに差し替え、渡された row を記録する。"""
    seen = {}
    mod = types.ModuleType("analyzer.gap_analyzer")
    mod.load_profile_summary = lambda: "profile"

    def _analyze_one(row, profile_yaml):
        seen["row"] = row
        return {"verdict": "go"}

    mod.analyze_one = _analyze_one
    monkeypatch.setitem(sys.modules, "analyzer.gap_analyzer", mod)
    return seen


def _rows(monkeypatch, first: dict, after: dict | None = None):
    """query_one を 1 回目 first、2 回目以降 after で応答させる。"""
    calls = {"n": 0}

    def _query_one(sql, params=()):
        calls["n"] += 1
        return first if calls["n"] == 1 else (after or first)

    monkeypatch.setattr(gap_api, "query_one", _query_one)


def test_backfills_missing_jd_then_analyzes(monkeypatch, fake_analyzer):
    row = {"id": 7, "source": "indeed_jp", "url": "https://x/j", "company": "C", "raw_jd": ""}
    filled = dict(row, raw_jd="取得できた JD")
    _rows(monkeypatch, row, filled)
    monkeypatch.setattr(gap_api, "fetch_one", lambda job: ("取得できた JD", "active"))
    written = {}
    monkeypatch.setattr(gap_api, "update_raw_jd", lambda jid, txt: written.update(id=jid, jd=txt))
    monkeypatch.setattr(gap_api, "update_liveness", lambda jid, st: None)

    out = gap_api.gap_single(7)

    assert written == {"id": 7, "jd": "取得できた JD"}
    assert fake_analyzer["row"]["raw_jd"] == "取得できた JD"
    assert out["gap_analysis"] == {"verdict": "go"}


def test_reports_reason_when_backfill_fails(monkeypatch, fake_analyzer):
    row = {"id": 7, "source": "bizreach", "url": "https://x/j", "company": "C", "raw_jd": ""}
    _rows(monkeypatch, row)
    monkeypatch.setattr(gap_api, "fetch_one", lambda job: ("", "needs_login"))

    with pytest.raises(HTTPException) as ei:
        gap_api.gap_single(7)

    assert ei.value.status_code == 400
    assert "ログイン" in ei.value.detail or "Chrome" in ei.value.detail
    assert "gap" not in fake_analyzer  # 分析は走らない


def test_expired_job_says_so(monkeypatch, fake_analyzer):
    row = {"id": 7, "source": "indeed_jp", "url": "https://x/j", "company": "C", "raw_jd": ""}
    _rows(monkeypatch, row)
    monkeypatch.setattr(gap_api, "fetch_one", lambda job: ("", "expired"))
    marked = {}
    monkeypatch.setattr(gap_api, "update_liveness", lambda jid, st: marked.update(id=jid, st=st))

    with pytest.raises(HTTPException) as ei:
        gap_api.gap_single(7)

    assert ei.value.status_code == 400
    assert marked == {"id": 7, "st": "expired"}


def test_existing_jd_skips_backfill(monkeypatch, fake_analyzer):
    row = {"id": 7, "source": "indeed_jp", "url": "https://x/j", "company": "C", "raw_jd": "既にある JD"}
    _rows(monkeypatch, row)
    monkeypatch.setattr(gap_api, "fetch_one",
                        lambda job: pytest.fail("既に raw_jd があるのに補抓した"))

    gap_api.gap_single(7)
    assert fake_analyzer["row"]["raw_jd"] == "既にある JD"
