"""pipeline.run_stage() が md 生成の直後に画像生成を呼ぶことの配線テスト。

実際の codex 呼び出しはモックし、`context.build()` 等の重い依存も
`pipeline.build_prompt` の差し替えで避ける — ここで見たいのは
「どのタイミングで・どの stage に対して・どの force 値で」画像生成が
呼ばれるかの配線であって、生成内容そのものではない
（内容の実測は output/proposal/4407_*/ のドライラン・
output/proposal/4752_*/ の実生成を参照）。
"""

from __future__ import annotations

import hashlib
import json

from proposal import pipeline, prompts


def _job() -> dict:
    return {"id": 1, "company": "テスト株式会社", "title": "PdM", "raw_jd": "x"}


def _seed_cached_stage(pdir, stage: str, prompt_text: str) -> None:
    """`run_stage` がキャッシュ命中経路を通るように md + digest 一致のキャッシュを仕込む。"""
    meta = prompts.STAGES[stage]
    (pdir / meta["file"]).write_text("既存の本文", encoding="utf-8")
    digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
    cache_path = pdir / "_cache" / f"{stage}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "digest": digest, "status": "ok", "attempts": 1, "errors": [],
    }), encoding="utf-8")


def test_cached_stage_triggers_image_when_with_images(tmp_path, monkeypatch):
    pdir = tmp_path / "1_test"
    pdir.mkdir()
    monkeypatch.setattr(pipeline, "build_prompt", lambda *a, **k: "FIXED PROMPT")
    _seed_cached_stage(pdir, "company", "FIXED PROMPT")

    calls = []
    monkeypatch.setattr(
        pipeline.images, "generate",
        lambda job, pdir, stage, **kw: calls.append((stage, kw)) or (pdir / f"{stage}.png"))

    result = pipeline.run_stage(_job(), "company", pdir, facts="", with_images=True)

    assert result.status == "cached"
    assert calls == [("company", {"force": False, "no_llm": False,
                                   "engine": None})]


def test_cached_stage_does_not_trigger_image_by_default(tmp_path, monkeypatch):
    """with_images=False（既定）なら今まで通り画像生成に触らない — 回帰の防止線。"""
    pdir = tmp_path / "1_test"
    pdir.mkdir()
    monkeypatch.setattr(pipeline, "build_prompt", lambda *a, **k: "FIXED PROMPT")
    _seed_cached_stage(pdir, "company", "FIXED PROMPT")

    calls = []
    monkeypatch.setattr(pipeline.images, "generate",
                        lambda *a, **k: calls.append(1))

    result = pipeline.run_stage(_job(), "company", pdir, facts="")

    assert result.status == "cached"
    assert calls == []


def test_non_image_stage_never_triggers_image(tmp_path, monkeypatch):
    """redteam 等の面接層 stage は IMAGE_STAGES 対象外 — with_images=True でも呼ばれない。"""
    pdir = tmp_path / "1_test"
    pdir.mkdir()
    monkeypatch.setattr(pipeline, "build_prompt", lambda *a, **k: "FIXED PROMPT")
    _seed_cached_stage(pdir, "redteam", "FIXED PROMPT")

    calls = []
    monkeypatch.setattr(pipeline.images, "generate",
                        lambda *a, **k: calls.append(1))

    result = pipeline.run_stage(_job(), "redteam", pdir, facts="", with_images=True)

    assert result.status == "cached"
    assert calls == []


def test_images_dry_run_flag_forwarded_to_generate(tmp_path, monkeypatch):
    pdir = tmp_path / "1_test"
    pdir.mkdir()
    monkeypatch.setattr(pipeline, "build_prompt", lambda *a, **k: "FIXED PROMPT")
    _seed_cached_stage(pdir, "plan90", "FIXED PROMPT")

    calls = []
    monkeypatch.setattr(
        pipeline.images, "generate",
        lambda job, pdir, stage, **kw: calls.append(kw) or (pdir / f"{stage}.png"))

    pipeline.run_stage(_job(), "plan90", pdir, facts="", with_images=True,
                       images_dry_run=True)

    assert calls == [{"force": False, "no_llm": True, "engine": None}]


def test_image_generation_failure_does_not_abort_pipeline(tmp_path, monkeypatch, capsys):
    """画像生成が失敗しても本文の cached 結果は正常に返す（本流を止めない）。"""
    pdir = tmp_path / "1_test"
    pdir.mkdir()
    monkeypatch.setattr(pipeline, "build_prompt", lambda *a, **k: "FIXED PROMPT")
    _seed_cached_stage(pdir, "company", "FIXED PROMPT")

    def boom(*a, **k):
        raise RuntimeError("codex がタイムアウトした")

    monkeypatch.setattr(pipeline.images, "generate", boom)

    result = pipeline.run_stage(_job(), "company", pdir, facts="", with_images=True)

    assert result.status == "cached"
    assert "画像生成失敗" in capsys.readouterr().out
