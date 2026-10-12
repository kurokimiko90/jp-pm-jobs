"""room3d_api — 面接パック／提案パックの成果物を部屋の展示物へ束ねる。"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "dashboard" / "backend"
sys.path.insert(0, str(BACKEND))

room3d_api = pytest.importorskip("room3d_api")
from fastapi import HTTPException  # noqa: E402


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    prep = tmp_path / "prep"
    proposal = tmp_path / "proposal"
    (prep / "1_テスト" / "theater").mkdir(parents=True)
    (proposal / "1_テスト").mkdir(parents=True)
    monkeypatch.setattr(room3d_api, "PREP_DIR", prep)
    monkeypatch.setattr(room3d_api, "PROPOSAL_DIR", proposal)
    return prep / "1_テスト", proposal / "1_テスト"


def _station(content, sid):
    return next(s for s in content["stations"] if s["id"] == sid)


def test_stations_map_files_and_mark_missing(dirs):
    prep, proposal = dirs
    (prep / "02_checklist.md").write_text("# 持ち物", encoding="utf-8")
    (proposal / "01_company.md").write_text("# 会社", encoding="utf-8")
    content = room3d_api.build_content("1_テスト")
    assert content["company"] == "テスト"
    assert _station(content, "checklist")["body"] == "# 持ち物"
    assert _station(content, "checklist")["room"] == "waiting"
    assert _station(content, "company")["source"] == "proposal/01_company.md"
    assert _station(content, "jikoshoukai")["available"] is False
    assert {s["id"] for s in content["stations"]} == {s[0] for s in room3d_api.STATIONS}


def test_hire_audit_picks_latest(dirs):
    prep, _ = dirs
    (prep / "hire_audit_20260801.md").write_text("old", encoding="utf-8")
    (prep / "hire_audit_20260902.md").write_text("new", encoding="utf-8")
    assert _station(room3d_api.build_content("1_テスト"), "hire_audit")["body"] == "new"


def test_proposal_missing_is_not_an_error(dirs, monkeypatch, tmp_path):
    monkeypatch.setattr(room3d_api, "PROPOSAL_DIR", tmp_path / "nothing")
    content = room3d_api.build_content("1_テスト")
    assert content["slides"] == []
    assert _station(content, "company")["available"] is False


def test_slides_sorted_and_long_body_truncated(dirs):
    prep, proposal = dirs
    for name in ("02_product.png", "01_company.png", "deck_cover.png"):
        (proposal / name).write_bytes(b"png")
    (prep / "05_qa_audit.md").write_text("あ" * (room3d_api.MAX_BODY_CHARS + 10), encoding="utf-8")
    content = room3d_api.build_content("1_テスト")
    assert [s.rsplit("/", 1)[1] for s in content["slides"]] == ["01_company.png", "02_product.png"]
    assert _station(content, "qa_audit")["body"].endswith("（以下省略）")


@pytest.mark.parametrize("bad", ["..", "_archive", "../prep", "a/b", ""])
def test_rejects_traversal(dirs, bad):
    with pytest.raises(HTTPException) as exc:
        room3d_api.build_content(bad)
    assert exc.value.status_code in (403, 404)


def test_unknown_pack_404(dirs):
    with pytest.raises(HTTPException) as exc:
        room3d_api.build_content("9_none")
    assert exc.value.status_code == 404


def test_slide_endpoint_only_serves_numbered_png(dirs):
    _, proposal = dirs
    (proposal / "01_company.png").write_bytes(b"png")
    (proposal / "10_deck.pdf").write_bytes(b"pdf")
    assert str(room3d_api.room3d_slide("1_テスト", "01_company.png").path).endswith("01_company.png")
    with pytest.raises(HTTPException):
        room3d_api.room3d_slide("1_テスト", "10_deck.pdf")
    with pytest.raises(HTTPException):
        room3d_api.room3d_slide("1_テスト", "../../prep/1_テスト/02_checklist.md")
