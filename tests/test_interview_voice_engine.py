"""tools/interview_voice.py のエンジン選択と受け皿のテスト。

面接パックの音声は「無音で終わらない」ことが最優先。GPT 読み上げが取れなかったとき
黙って音檔ゼロで終わると、面接前日に気づくことになる。
"""

from pathlib import Path

import pytest

from tools import interview_voice as iv


@pytest.fixture()
def calls(monkeypatch) -> dict:
    log = {"gpt": 0, "theater": 0}

    def fake_gpt(pack_dir, out_path, limit=None, force=False):
        log["gpt"] += 1
        return log.get("gpt_ok", True)

    def fake_theater(pack_dir, out_path, job=None):
        log["theater"] += 1
        return True

    monkeypatch.setattr(iv, "generate_gpt", fake_gpt)
    monkeypatch.setattr(iv, "generate_theater", fake_theater)
    return log


def test_engine_gpt_does_not_touch_theater(calls, tmp_path: Path):
    assert iv.generate(tmp_path, tmp_path / "out.mp3", engine="gpt") is True
    assert calls == {"gpt": 1, "theater": 0}


def test_engine_theater_skips_gpt(calls, tmp_path: Path):
    assert iv.generate(tmp_path, tmp_path / "out.mp3", engine="theater") is True
    assert calls == {"gpt": 0, "theater": 1}


def test_gpt_failure_falls_back_to_theater(calls, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(iv, "generate_gpt", lambda *a, **kw: False)
    assert iv.generate(tmp_path, tmp_path / "out.mp3", engine="gpt") is True
    assert calls["theater"] == 1


def test_engine_defaults_to_config(calls, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("tools.app_config.get",
                        lambda name, key, default=None: "theater")
    iv.generate(tmp_path, tmp_path / "out.mp3")
    assert calls == {"gpt": 0, "theater": 1}
