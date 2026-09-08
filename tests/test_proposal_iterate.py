"""proposal/iterate.py と deck の role 閘門のテスト（LLM・Telegram を呼ばない部分）。

評分駆動の迭代は「全版が残る」「採点と版の対応が消えない」が生命線。
snapshot が上書きし始めたり、role 閘門が黙って消えたりすると、
90 点に到達したときにどの版が合格したのか分からなくなる。
"""

from pathlib import Path

from proposal import deck, iterate, prompts

JOB = {"id": 1, "company": "テスト株式会社", "title": "PdM", "raw_jd": "求人本文"}


def _full_role_slides() -> list[dict]:
    """必須 role すべてを備えた最小構成。"""
    return [
        {"layout": "cover", "title": "表紙"},
        {"layout": "statement", "role": "reframe", "title": "課題の再定義です",
         "note": "話す"},
        {"layout": "cards", "role": "moves", "title": "打ち手です", "note": "話す",
         "cards": [{"heading": "打ち手", "body": "内容", "meta": "実現: 体験層"}]},
        {"layout": "flow", "role": "design_detail", "title": "設計です", "note": "話す",
         "steps": [{"label": "入力", "caption": "予約ログ"}],
         "lanes": [{"name": "最小実装", "cells": [{"heading": "台帳", "body": "結合"}]}]},
        {"layout": "arch", "role": "architecture", "title": "構造です", "note": "話す",
         "layers": [{"name": "体験層", "items": ["予約"]}]},
        {"layout": "tree", "role": "kpi_tree", "title": "測り方です", "note": "話す",
         "kgi": "継続率を上げる", "drivers": [{"name": "初回成功", "leading": "到達"}]},
        {"layout": "table", "role": "metrics_spec", "title": "定義です", "note": "話す",
         "table": {"columns": ["指標", "定義式", "取得元", "頻度"],
                   "rows": [["到達率", "到達 / 全体", "予約ログ", "週次"]]}},
        {"layout": "table", "role": "jd_map", "title": "対応です", "note": "話す",
         "table": {"columns": ["要件", "打ち手"], "rows": [["a", "b"]]}},
        {"layout": "cards", "role": "data_case", "title": "実例です", "note": "話す",
         "cards": [{"heading": "基準へ", "body": "数値に置いた", "meta": "合格率85%"},
                   {"heading": "削った", "body": "比較した", "meta": "12回→1回"}]},
        {"layout": "cards", "role": "why_me", "title": "なぜ私かです", "note": "話す",
         "cards": [{"heading": "点", "body": "根拠", "meta": "案件名"}]},
        {"layout": "timeline", "role": "roadmap", "title": "進め方です", "note": "話す",
         "phases": [{"period": "0-30", "title": "検証", "items": ["聞く"]}]},
        {"layout": "qa", "title": "想定問答です", "note": "話す",
         "qa": [{"q": "Q", "a": "A"}]},
        {"layout": "closing", "title": "締め", "note": "話す"},
    ]


# ------------------------------------------------------- deck role 閘門（Deck C）

def test_deck_missing_roles_are_rejected():
    fields = {"slides": [{"layout": "cover", "title": "表紙"},
                         {"layout": "closing", "title": "締め", "note": "話す"}]}
    errs = deck.check(fields, JOB, corpus="")
    missing = [e for e in errs if "role のスライドが無い" in e]
    # 1 件にまとめたうえで、全 role の名前が入っている（role が増えても
    # 是正指示が role の話だけで埋まらないように 1 件へ集約している）
    assert len(missing) == 1
    assert all(role in missing[0] for role in deck.REQUIRED_ROLES)


def test_deck_full_roles_pass_role_gate():
    errs = deck.check({"slides": _full_role_slides()}, JOB, corpus="")
    assert not [e for e in errs if e.startswith("[Deck C]")]


def test_deck_unknown_role_is_rejected():
    slides = _full_role_slides()
    _by_role(slides, "reframe")["role"] = "reframing"          # 語彙外
    errs = deck.check({"slides": slides}, JOB, corpus="")
    assert any("未知の role" in e for e in errs)


def _by_role(slides: list[dict], role: str) -> dict:
    """role で引く。index 直指定にすると role を 1 つ足すたびにずれる。"""
    return next(s for s in slides if s.get("role") == role)


def test_deck_architecture_without_layers_is_rejected():
    slides = _full_role_slides()
    _by_role(slides, "architecture").pop("layers")
    errs = deck.check({"slides": slides}, JOB, corpus="")
    assert any("layers" in e for e in errs)


def test_deck_kpi_tree_without_drivers_is_rejected():
    slides = _full_role_slides()
    _by_role(slides, "kpi_tree").pop("drivers")
    errs = deck.check({"slides": slides}, JOB, corpus="")
    assert any("kgi / drivers" in e for e in errs)


# ------------------------------------------------------------- 版快照と採点履歴

def test_snapshot_increments_and_never_overwrites(tmp_path: Path):
    main_file = prompts.STAGES["main_case"]["file"]
    (tmp_path / main_file).write_text("v1 の内容", encoding="utf-8")
    assert iterate.snapshot(tmp_path) == 1
    (tmp_path / main_file).write_text("v2 の内容", encoding="utf-8")
    assert iterate.snapshot(tmp_path) == 2
    # 前の版は書き換わっていない（歴史版が残るのが要件）
    assert (tmp_path / "versions/v1" / main_file).read_text(
        encoding="utf-8") == "v1 の内容"
    assert (tmp_path / "versions/v2" / main_file).read_text(
        encoding="utf-8") == "v2 の内容"


def test_snapshot_skips_cache_and_raw(tmp_path: Path):
    (tmp_path / "_cache").mkdir()
    (tmp_path / "_cache" / "x.json").write_text("{}", encoding="utf-8")
    (tmp_path / "_research_raw.md").write_text("原文", encoding="utf-8")
    (tmp_path / "10_deck.html").write_text("<html>", encoding="utf-8")
    iterate.snapshot(tmp_path)
    v1 = tmp_path / "versions/v1"
    assert (v1 / "10_deck.html").exists()
    assert not (v1 / "_cache").exists()
    assert not (v1 / "_research_raw.md").exists()


def test_best_score_tracks_max(tmp_path: Path):
    iterate.record(tmp_path, kind="score", version=1, score=72, note="浅い")
    iterate.record(tmp_path, kind="score", version=2, score=88, note="")
    iterate.record(tmp_path, kind="version", version=3, redteam=50)  # 採点ではない
    assert iterate.best_score(tmp_path) == (88, 2)


def test_best_score_empty(tmp_path: Path):
    assert iterate.best_score(tmp_path) == (None, None)


# ------------------------------------------------------------------ scope 判定

def test_scope_auto_deck_words_only_rebuild_deck():
    assert iterate._resolve_scope("auto", "スライドに構造図が無い") == "deck"
    assert iterate._resolve_scope("auto", "PPT の密度が高すぎる") == "deck"


def test_scope_auto_content_note_rebuilds_full():
    assert iterate._resolve_scope("auto", "KGI ツリーが浅い") == "full"
    assert iterate._resolve_scope("auto", "") == "full"


def test_scope_explicit_wins():
    assert iterate._resolve_scope("full", "スライドが丑い") == "full"
    assert iterate._resolve_scope("deck", "提案が弱い") == "deck"
    assert iterate._resolve_scope("thinking", "スライドが丑い") == "thinking"


def test_scope_thinking_starts_from_hypotheses():
    stages = iterate._SCOPE_STAGES["thinking"]
    assert stages[0] == "hypotheses"
    assert set(iterate._FULL_STAGES) < set(stages)


def test_rewrite_regenerates_the_spoken_script_last():
    """pitch は main_case / mapping の下流。先に回すと古い主張を口に出す原稿が残る。"""
    stages = iterate._FULL_STAGES
    assert stages[-1] == "pitch"
    for upstream in ("main_case", "plan90", "mapping"):
        assert stages.index(upstream) < stages.index("pitch")
