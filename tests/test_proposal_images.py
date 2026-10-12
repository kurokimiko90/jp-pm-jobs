"""proposal.images の回帰テスト — codex 呼び出しはモックし、配線だけを検証する。

実際の codex 生成の実測は output/proposal/{job_id}_{company}/01_company.png を参照
（2026-08-16、236s、事実/仮説の凡例付きインフォグラフィックとして正しく生成された）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from proposal import images, prompts
from proposal._llm import LLMUnavailable


def _job() -> dict:
    return {"id": 999, "company": "テスト株式会社", "title": "PdM"}


def _pdir_with_stage(tmp_path: Path, stage: str, content: str = "本文テスト") -> Path:
    pdir = tmp_path / "999_test"
    pdir.mkdir()
    (pdir / prompts.STAGES[stage]["file"]).write_text(content, encoding="utf-8")
    return pdir


def test_image_stages_match_research_and_thinking_layers():
    """対象は研究層＋思考層の 6 stage。面接層（cards 以降）は含まない。"""
    assert images.IMAGE_STAGES == list(prompts.LAYERS["research"]) + list(
        prompts.LAYERS["thinking"])
    assert "cards" not in images.IMAGE_STAGES
    assert "deck" not in images.IMAGE_STAGES


def test_build_prompt_includes_title_and_body():
    prompt = images.build_prompt("company", "会社のビジネスモデル本文")
    assert prompts.STAGES["company"]["title"] in prompt
    assert "会社のビジネスモデル本文" in prompt
    assert "日本語" in prompt


def test_build_prompt_includes_ja_it_style_rules():
    """他 stage の本文 prompt と同じ「地道な日本の IT 用語」規則を画像 prompt にも渡す。"""
    prompt = images.build_prompt("company", "本文")
    assert prompts.JA_IT_STYLE_RULES in prompt


def test_image_path_mirrors_md_basename():
    pdir = Path("/tmp/999_test")
    assert images.image_path(pdir, "main_case").name == "05_main_proposal.png"
    assert images.image_path(pdir, "plan90").name == "06_plan_90days.png"


def test_generate_raises_when_md_missing(tmp_path):
    pdir = tmp_path / "999_test"
    pdir.mkdir()
    with pytest.raises(FileNotFoundError):
        images.generate(_job(), pdir, "company")


def test_generate_calls_llm_image_and_writes_trace(tmp_path, monkeypatch):
    pdir = _pdir_with_stage(tmp_path, "company")
    calls = []

    def fake_image(prompt, output_path, timeout=560, engine=None):
        calls.append((prompt, output_path))
        Path(output_path).write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return str(output_path)

    monkeypatch.setattr(images, "llm_image", fake_image)
    out = images.generate(_job(), pdir, "company")

    assert out.exists()
    assert out.name == "01_company.png"
    assert len(calls) == 1
    assert "本文テスト" in calls[0][0]

    rows = (pdir / "_trace" / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    assert '"stage": "image_company"' in rows[0]
    assert '"gate": "pass"' in rows[0]


def test_generate_skips_when_already_exists(tmp_path, monkeypatch):
    pdir = _pdir_with_stage(tmp_path, "company")
    calls = []
    monkeypatch.setattr(images, "llm_image",
                        lambda p, o, timeout=560, engine=None: calls.append(1))

    out1 = images.generate(_job(), pdir, "company")  # 1 回目: 実際に呼ぶ
    out1.write_bytes(b"already-there")
    out2 = images.generate(_job(), pdir, "company")  # force=False 既定 → 2 回目はスキップ

    assert out2 == out1
    assert calls == [1]  # 2 回目は呼ばれていない（1 回目の分だけ）


def test_generate_all_skips_missing_stages_without_aborting(tmp_path, monkeypatch):
    pdir = _pdir_with_stage(tmp_path, "company")
    # persona の md は用意しない → スキップされるが例外で全体は止まらない

    def fake_image(prompt, output_path, timeout=560, engine=None):
        Path(output_path).write_bytes(b"fake")
        return str(output_path)

    monkeypatch.setattr(images, "llm_image", fake_image)
    results = images.generate_all(_job(), pdir, stages=["company", "persona"])

    assert results["company"].startswith("✓")
    assert results["persona"].startswith("—")


def test_generate_all_propagates_llm_unavailable(tmp_path, monkeypatch):
    pdir = _pdir_with_stage(tmp_path, "company")

    def raise_unavailable(prompt, output_path, timeout=560, engine=None):
        raise LLMUnavailable("指揮中心が落ちている")

    monkeypatch.setattr(images, "llm_image", raise_unavailable)
    with pytest.raises(LLMUnavailable):
        images.generate_all(_job(), pdir, stages=["company"])


def test_generate_dry_run_writes_prompt_without_calling_codex(tmp_path, monkeypatch):
    """--images-dry-run: codex を呼ばず prompt だけ _prompts/ に落とす。"""
    pdir = _pdir_with_stage(tmp_path, "company")
    calls = []
    monkeypatch.setattr(images, "llm_image", lambda *a, **k: calls.append(1))

    out = images.generate(_job(), pdir, "company", no_llm=True)

    assert calls == []  # codex は呼ばれていない
    assert out == pdir / "_prompts" / "company.image.prompt.md"
    assert out.exists()
    assert "本文テスト" in out.read_text(encoding="utf-8")
    assert not images.image_path(pdir, "company").exists()  # PNG は作られない


def test_generate_defaults_to_codex_engine(tmp_path, monkeypatch):
    """既定は codex。agy は速いが小さい日本語が崩れるので既定にはしない。"""
    pdir = _pdir_with_stage(tmp_path, "company")
    seen = {}

    def fake_image(prompt, output_path, timeout=560, engine=None):
        seen["engine"] = engine
        Path(output_path).write_bytes(b"fake")
        return str(output_path)

    monkeypatch.setattr(images, "llm_image", fake_image)
    images.generate(_job(), pdir, "company")

    assert images.DEFAULT_ENGINE == "codex"
    assert seen["engine"] == "codex"


def test_generate_engine_override_and_trace(tmp_path, monkeypatch):
    """--image-engine agy で上書きでき、どのエンジンで撮ったかが trace に残る。"""
    pdir = _pdir_with_stage(tmp_path, "company")
    seen = {}

    def fake_image(prompt, output_path, timeout=560, engine=None):
        seen["engine"] = engine
        Path(output_path).write_bytes(b"fake")
        return str(output_path)

    monkeypatch.setattr(images, "llm_image", fake_image)
    images.generate(_job(), pdir, "company", engine="agy")

    assert seen["engine"] == "agy"
    row = json.loads((pdir / "_trace" / "index.jsonl").read_text(encoding="utf-8"))
    assert row["engine_requested"] == "agy"


def test_generate_all_passes_engine_through(tmp_path, monkeypatch):
    pdir = _pdir_with_stage(tmp_path, "company")
    engines = []

    def fake_image(prompt, output_path, timeout=560, engine=None):
        engines.append(engine)
        Path(output_path).write_bytes(b"fake")
        return str(output_path)

    monkeypatch.setattr(images, "llm_image", fake_image)
    images.generate_all(_job(), pdir, stages=["company"], engine="gemini1")

    assert engines == ["gemini1"]


def test_generate_dry_run_ignores_existing_png(tmp_path, monkeypatch):
    """dry run は force に関係なく常に prompt を書き直す（既存 PNG の有無を見ない）。"""
    pdir = _pdir_with_stage(tmp_path, "company")
    images.image_path(pdir, "company").write_bytes(b"already-there")
    calls = []
    monkeypatch.setattr(images, "llm_image", lambda *a, **k: calls.append(1))

    out = images.generate(_job(), pdir, "company", no_llm=True)

    assert calls == []
    assert out == pdir / "_prompts" / "company.image.prompt.md"
