"""proposal/trace.py — LLM 呼び出し履歴のテスト（LLM を呼ばない部分）。

trace の生命線は 2 つ:
  1. **閘門を落ちた応答も残る。** 通ったものだけ残すなら成果物 md で足りる。
     後から見たいのは「1 回目はなぜ落ちたか」なので、そこが消えたら無価値。
  2. **brain を推測で埋めない。** 同期呼び出しに落ちて実際の生成者が分からない
     ときは None のまま残す。要求 engine（codex）で埋めると、実際は別の brain が
     書いたものを codex が書いたことにしてしまう。
"""

from pathlib import Path

import pytest

from proposal import trace

JOB = {"id": 42, "company": "テスト株式会社", "title": "PdM"}


def _meta(brain="antigravity"):
    return {"engine_requested": "codex", "brain": brain,
            "brains": ["codex", brain] if brain else [],
            "llm_ms": 42628, "queued_ms": 351, "task_id": "t-1"}


# ----------------------------------------------------------------- 記録する

def test_落ちた応答も本文ごと残る(tmp_path: Path):
    trace.record(tmp_path, job=JOB, stage="main_case", attempt=1,
                 prompt="P1", output="落ちた本文", meta=_meta(),
                 gate="fail", errors=["[Gate A] 見出し不足"], elapsed=48.2,
                 prompt_version="v3.1")
    rows = trace.load(tmp_path)
    assert len(rows) == 1
    assert rows[0]["gate"] == "fail"
    body = (trace.trace_dir(tmp_path) / rows[0]["out_file"]).read_text(encoding="utf-8")
    assert body == "落ちた本文"


def test_通過と是正の両方が時系列で並ぶ(tmp_path: Path):
    trace.record(tmp_path, job=JOB, stage="main_case", attempt=1, prompt="P1",
                 output="一回目", meta=_meta(), gate="fail", errors=["[Gate B] 数字"])
    trace.record(tmp_path, job=JOB, stage="main_case", attempt=2, prompt="P2",
                 output="二回目", meta=_meta(), gate="pass", errors=[])
    rows = trace.load(tmp_path)
    assert [r["seq"] for r in rows] == [1, 2]
    assert [r["attempt"] for r in rows] == [1, 2]
    assert [r["gate"] for r in rows] == ["fail", "pass"]


def test_同一promptは内容尋址で一度だけ保存(tmp_path: Path):
    for i in (1, 2):
        trace.record(tmp_path, job=JOB, stage="cards", attempt=i,
                     prompt="まったく同じ prompt", output=f"出力{i}",
                     meta=_meta(), gate="fail")
    prompts_dir = trace.trace_dir(tmp_path) / "prompts"
    assert len(list(prompts_dir.glob("*.md"))) == 1
    # 応答は別々に残る（本文が違うので上書きしてはいけない）
    assert len(list((trace.trace_dir(tmp_path) / "out").glob("*.md"))) == 2


def test_brainと時刻とprompt版が索引に載る(tmp_path: Path):
    trace.record(tmp_path, job=JOB, stage="persona", attempt=1, prompt="P",
                 output="本文", meta=_meta("gemini1"), gate="pass",
                 prompt_version="v3.1")
    r = trace.load(tmp_path)[0]
    assert r["brain"] == "gemini1"
    assert r["brains"] == ["codex", "gemini1"]
    assert r["engine_requested"] == "codex"
    assert r["llm_ms"] == 42628
    assert r["prompt_version"] == "v3.1"
    assert r["at"] and len(r["at"]) == 19       # 秒まで（日付だけにしない）
    assert r["company"] == "テスト株式会社"


def test_brainが取れないときはNoneのまま_要求engineで埋めない(tmp_path: Path):
    meta = {"engine_requested": "codex", "brain": None, "brains": [],
            "meta_unavailable": "task API 非対応（HTTP 404）"}
    trace.record(tmp_path, job=JOB, stage="product", attempt=1, prompt="P",
                 output="本文", meta=meta, gate="pass")
    r = trace.load(tmp_path)[0]
    assert r["brain"] is None
    assert r["engine_requested"] == "codex"
    assert r["meta_unavailable"]


def test_LLM呼び出し失敗も残る_応答ファイルは無い(tmp_path: Path):
    trace.record(tmp_path, job=JOB, stage="plan90", attempt=1, prompt="P",
                 gate="error", errors=["[LLM] 呼び出し失敗: timeout"])
    r = trace.load(tmp_path)[0]
    assert r["gate"] == "error"
    assert r["out_file"] == ""
    assert r["out_chars"] == 0


def test_記録の失敗は本流を止めない(tmp_path: Path):
    """trace は副作用。書けなくても生成は続けさせる（seq 0 を返すだけ）。"""
    blocked = tmp_path / "_trace"
    blocked.write_text("これはディレクトリではないのでmkdirが失敗する")
    assert trace.record(tmp_path, job=JOB, stage="cards", attempt=1,
                        prompt="P", output="本文") == 0


def test_count_は版との対応付けに使える(tmp_path: Path):
    assert trace.count(tmp_path) == 0
    for i in range(3):
        trace.record(tmp_path, job=JOB, stage="cards", attempt=1,
                     prompt=f"P{i}", output="x", meta=_meta())
    assert trace.count(tmp_path) == 3


def test_表示用ゲート抽出はDeck_DとEも記録する():
    ids = trace._gate_ids([
        "[Deck D] 個人情報が残っている",
        "[Deck E] IT 日本語として不自然な表現がある",
        "[Deck E] 重複は一度だけ表示する",
    ])
    assert ids == ["Deck D", "Deck E"]


# ------------------------------------------------------------------- prune

def test_pruneは本文だけ消して索引は全件残す(tmp_path: Path):
    for i in range(5):
        trace.record(tmp_path, job=JOB, stage="cards", attempt=1,
                     prompt=f"prompt{i}", output=f"out{i}", meta=_meta())
    n_out, n_prompt = trace.prune(tmp_path, keep=2)
    assert n_out == 3 and n_prompt == 3
    assert len(trace.load(tmp_path)) == 5           # 索引は削らない
    assert len(list((trace.trace_dir(tmp_path) / "out").glob("*.md"))) == 2


# ------------------------------------------------- meta の取り出し（gateway）

def test_text_with_meta_が採用brainと試行順を取り出す(monkeypatch):
    from tools import miko_llm

    task = {"status": "done", "result": "本文",
            "attempts": [{"brain": "codex", "ok": False, "ms": 1200},
                         {"brain": "antigravity", "ok": True, "ms": 42628}],
            "created_at": 1000, "started_at": 1351}
    monkeypatch.setattr(miko_llm, "submit", lambda *a, **k: "task-1")
    monkeypatch.setattr(miko_llm, "get_task", lambda *a, **k: task)

    text, meta = miko_llm.text_with_meta("P", timeout=10, poll=0)
    assert text == "本文"
    assert meta["brain"] == "antigravity"           # 採用されたのは最後の ok
    assert meta["brains"] == ["codex", "antigravity"]
    assert meta["llm_ms"] == 43828                  # 試行の合計
    assert meta["queued_ms"] == 351


def test_task_APIが無ければ専用例外_同期へ落とせる(monkeypatch):
    import requests
    from tools import miko_llm

    resp = requests.Response()
    resp.status_code = 404

    def _submit(*a, **k):
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr(miko_llm, "submit", _submit)
    with pytest.raises(miko_llm.TaskAPIUnsupported):
        miko_llm.text_with_meta("P", timeout=10)


def test_task失敗はそのまま伝える_同期へ逃がさない(monkeypatch):
    """生成が失敗したのに同期で再実行すると、二重課金して失敗も隠れる。"""
    from tools import miko_llm

    monkeypatch.setattr(miko_llm, "submit", lambda *a, **k: "task-1")
    monkeypatch.setattr(miko_llm, "get_task", lambda *a, **k: {
        "status": "failed", "error": "全 brain が accept を満たさなかった"})
    with pytest.raises(RuntimeError, match="accept"):
        miko_llm.text_with_meta("P", timeout=10, poll=0)
