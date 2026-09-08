"""tts/gpt_voice.py の機械層テスト（ブラウザを起動しない部分だけ）。

ブラウザ操作は ChatGPT の UI に依存するので自動テストの対象外。ここで守るのは
「外部へ何を送るか」「同じ問いを二度録らないか」という、静かに壊れると
PII 漏れや無駄な再生成になる部分。
"""

import re
from pathlib import Path

import pytest

from tts import gpt_voice as gv

QUESTION = "これまでのご経歴を教えてください"
CONCLUSION = "エンジニアを起点に約9年プロダクト開発を担ってきました。"
POINTS = ["要件定義から本番リリースまで一貫して担当しています。",
          "顧客課題の特定から開発順序まで決めてきました。"]


# ---------------------------------------------------------------- prompt

def test_prompt_contains_question_and_all_points():
    prompt = gv.build_prompt(QUESTION, CONCLUSION, POINTS)
    assert QUESTION in prompt
    assert CONCLUSION in prompt
    for p in POINTS:
        assert p in prompt


def test_prompt_without_conclusion_still_lists_points():
    prompt = gv.build_prompt(QUESTION, "", POINTS)
    assert prompt.count("- ") == len(POINTS)


def test_primer_forbids_unverifiable_claims():
    """「裏を取れないことは書くな」が消えると、音声だけ捏造が混ざる。"""
    assert "裏を取れない" in gv.SYSTEM_PRIMER
    assert "骨子" in gv.SYSTEM_PRIMER


# ---------------------------------------------------------------- 錨定閘門

SOURCE = gv.build_prompt(QUESTION, CONCLUSION, POINTS)


def test_unanchored_flags_invented_terms():
    bad = gv.unanchored_terms("NASAでBlockchainの案件を担当しました。", SOURCE, corpus="")
    assert "NASA" in bad and "Blockchain" in bad


def test_unanchored_flags_invented_numbers():
    assert "42" in gv.unanchored_terms("42件の実績があります。", SOURCE, corpus="")


def test_unanchored_accepts_terms_from_the_source():
    assert gv.unanchored_terms(CONCLUSION, SOURCE, corpus="") == []


def test_unanchored_accepts_terms_backed_by_profile():
    """骨子に無くても本人の経歴ファイルにあれば事実 — 弾いてはいけない。"""
    corpus = "IoT（STB／エレベーター監視）と上場 Fintech の決済 PdM。"
    assert gv.unanchored_terms("上場FintechとIoTの経験があります。", SOURCE, corpus=corpus) == []


def test_unanchored_ignores_generic_job_terms():
    assert gv.unanchored_terms("PdMとしてAPIとUATを担当しました。", SOURCE, corpus="") == []


def test_rewrite_prompt_lists_the_bad_terms():
    prompt = gv.rewrite_prompt(["NASA", "42"])
    assert "NASA" in prompt and "42" in prompt


# ---------------------------------------------------------------- PII 閘門

def test_scrub_replaces_real_name(monkeypatch):
    monkeypatch.setattr("tools.pii_gate._terms", lambda: (("山田太郎", "本人"),))
    clean, findings = gv.scrub("山田太郎です。よろしくお願いします。")
    assert "山田太郎" not in clean
    assert "本人" in clean
    assert findings


def test_scrub_returns_no_findings_for_clean_text():
    clean, findings = gv.scrub("本人はプロダクトマネージャーです。")
    assert clean == "本人はプロダクトマネージャーです。"
    assert findings == []


# ---------------------------------------------------------------- キャッシュ鍵

def test_hash_is_stable_for_same_input():
    assert gv.item_hash(QUESTION, CONCLUSION, POINTS) == gv.item_hash(QUESTION, CONCLUSION, POINTS)


def test_hash_changes_when_answer_changes():
    other = gv.item_hash(QUESTION, CONCLUSION, POINTS + ["追加の要点"])
    assert other != gv.item_hash(QUESTION, CONCLUSION, POINTS)


def test_hash_changes_when_prompt_version_changes(monkeypatch):
    before = gv.item_hash(QUESTION, CONCLUSION, POINTS)
    monkeypatch.setattr(gv, "PROMPT_VERSION", gv.PROMPT_VERSION + 1)
    assert gv.item_hash(QUESTION, CONCLUSION, POINTS) != before


# ---------------------------------------------------------------- 音檔まわり

@pytest.mark.parametrize("mime,expected", [
    ("audio/aac", "aac"),
    ("audio/mp4; codecs=\"mp4a.40.2\"", "m4a"),
    ("audio/mpeg", "mp3"),
    ("", "aac"),
])
def test_ext_for(mime, expected):
    assert gv.ext_for(mime) == expected


def test_manifest_roundtrip(tmp_path: Path):
    pack = tmp_path / "123_サンプル社"
    gv.voice_dir(pack).mkdir(parents=True)
    gv.save_manifest(pack, {"version": 1, "items": [{"hash": "abc", "ordinal": 1}]})
    assert gv.load_manifest(pack)["items"][0]["hash"] == "abc"


def test_load_manifest_missing_returns_empty(tmp_path: Path):
    assert gv.load_manifest(tmp_path / "nope")["items"] == []


# ---------------------------------------------------------------- バッチ分割

def test_batch_prompt_numbers_every_question():
    prompt = gv.build_batch_prompt([(1, "質問A"), (2, "質問B")])
    assert "第1問。" in prompt and "第2問。" in prompt
    assert "質問A" in prompt and "質問B" in prompt


def test_parse_batch_reply_splits_by_number():
    reply = "第1問。原稿A です。\n\n第2問。原稿B です。"
    assert gv.parse_batch_reply(reply, [1, 2]) == {1: "原稿A です。", 2: "原稿B です。"}


def test_parse_batch_reply_tolerates_fullwidth_numbers():
    assert gv.parse_batch_reply("第１問。原稿。", [1]) == {1: "原稿。"}


def test_parse_batch_reply_drops_unknown_numbers():
    """モデルが勝手な番号を振っても、頼んでいない問は拾わない。"""
    assert gv.parse_batch_reply("第9問。よその原稿。", [1, 2]) == {}


def test_parse_batch_reply_missing_question_is_a_gap_not_a_crash():
    got = gv.parse_batch_reply("第1問。原稿A。", [1, 2])
    assert got == {1: "原稿A。"}


def test_batch_hash_changes_with_membership():
    assert gv.batch_hash(["a", "b"]) != gv.batch_hash(["a", "c"])
    assert gv.batch_hash(["a", "b"]) == gv.batch_hash(["a", "b"])


# ---------------------------------------------------------------- 生成フロー

class _FakeSession:
    """ChatGPT の代役。まとめ依頼に「第N問。」形式で返す。"""

    def __init__(self, *a, **kw):
        self.asked: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _bodies(self, prompt: str) -> str:
        nums = re.findall(r"第(\d+)問。", prompt)
        if not nums:                       # primer
            return "了解しました。"
        return "\n\n".join(f"第{n}問。話し言葉に整えた原稿です。" for n in nums)

    def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self._bodies(prompt)

    def read_aloud(self):
        return b"\xff\xf1fake-aac-bytes", "audio/aac"


MD = f"""# 想定問答 — サンプル社

## 経歴

### Q. {QUESTION}

{CONCLUSION}
1. {POINTS[0]}
2. {POINTS[1]}

### Q. 転職理由を教えてください

顧客価値に責任を持てる環境へ移りたいからです。
1. 担当変更が多く継続的な検証まで担いにくい状況です。
"""


@pytest.fixture()
def pack(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "999_サンプル社"
    d.mkdir()
    (d / "01_interview_qa.md").write_text(MD, encoding="utf-8")
    monkeypatch.setattr(gv, "ChatGPTVoice", _FakeSession)
    monkeypatch.setattr(gv, "to_wav", lambda src, dst: dst.write_bytes(b"RIFFfake"))
    # 実行主体はテスト環境（miko-ws が起動しているか）で変わってはいけない
    monkeypatch.setattr(gv, "pick_backend", lambda *a, **kw: "local")
    return d


def test_default_batches_all_questions_into_one_recording(pack: Path):
    res = gv.generate(pack, log=lambda *_: None)
    assert res.total == 2
    assert res.batches == 1        # 既定 batch_size=20 なので 2 問は 1 メッセージ
    assert res.done == 1
    assert res.failed == 0
    assert len(res.wav_files) == 1
    assert res.wav_files[0].exists()


def test_batch_size_one_records_each_question_separately(pack: Path):
    res = gv.generate(pack, batch_size=1, log=lambda *_: None)
    assert res.batches == 2
    assert len(res.wav_files) == 2


def test_one_message_per_batch(pack: Path, monkeypatch):
    """往復回数が問数に比例して増えないこと（バッチ化の目的そのもの）。"""
    session = _FakeSession()
    monkeypatch.setattr(gv, "ChatGPTVoice", lambda *a, **kw: session)
    gv.generate(pack, log=lambda *_: None)
    assert len(session.asked) == 2   # primer + 1 バッチ


def test_generate_second_run_uses_cache(pack: Path):
    gv.generate(pack, log=lambda *_: None)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.done == 0
    assert res.cached == 1


def test_changing_batch_size_invalidates_the_cache(pack: Path):
    """束ね方が変われば音檔の中身も変わる — 使い回してはいけない。"""
    gv.generate(pack, log=lambda *_: None)
    res = gv.generate(pack, batch_size=1, log=lambda *_: None)
    assert res.done == 2


def test_generate_force_rerecords(pack: Path):
    gv.generate(pack, log=lambda *_: None)
    res = gv.generate(pack, force=True, log=lambda *_: None)
    assert res.done == 1


def test_generate_limit_takes_first_n(pack: Path):
    res = gv.generate(pack, limit=1, log=lambda *_: None)
    assert res.total == 1
    assert len(res.wav_files) == 1


def test_generate_records_every_spoken_text(pack: Path):
    gv.generate(pack, log=lambda *_: None)
    item = gv.load_manifest(pack)["items"][0]
    assert item["ordinals"] == [1, 2]
    assert item["spoken"]["1"] == "話し言葉に整えた原稿です。"
    assert item["spoken"]["2"] == "話し言葉に整えた原稿です。"


def test_stale_recordings_are_pruned(pack: Path):
    """QA を録り直すたびに旧音檔が溜まると、どれが最新か分からなくなる。"""
    gv.generate(pack, log=lambda *_: None)
    stale = gv.voice_dir(pack) / "099-deadbeef1234.wav"
    stale.write_bytes(b"old")
    gv.generate(pack, log=lambda *_: None)
    assert not stale.exists()


def test_generate_rejects_pack_without_qa(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        gv.generate(tmp_path, log=lambda *_: None)


def test_generate_writes_audit_file(pack: Path):
    gv.generate(pack, log=lambda *_: None)
    audit = (pack / "06_voice_audit.md").read_text(encoding="utf-8")
    assert QUESTION in audit
    assert "話し言葉に整えた原稿です。" in audit


class _Inventing(_FakeSession):
    """書き直しを求めても裏の取れない語を出し続けるモデル。"""

    def _bodies(self, prompt: str) -> str:
        nums = re.findall(r"第(\d+)問。", prompt) or ["1"]
        return "\n\n".join(f"第{n}問。NASAの案件を担当しました。" for n in nums)


def test_unverifiable_terms_are_retried_once_then_reported(pack: Path, monkeypatch):
    monkeypatch.setattr(gv, "ChatGPTVoice", _Inventing)
    res = gv.generate(pack, limit=1, log=lambda *_: None)
    assert res.done == 1          # 音声は作る（黙って捨てる方が危ない）
    assert res.degraded == 1      # が、要確認として数える
    assert "NASA" in gv.load_manifest(pack)["items"][0]["unanchored"]["1"]
    assert "⚠" in (pack / "06_voice_audit.md").read_text(encoding="utf-8")


def test_retry_asks_the_model_to_drop_the_terms(pack: Path, monkeypatch):
    session = _Inventing()
    monkeypatch.setattr(gv, "ChatGPTVoice", lambda *a, **kw: session)
    gv.generate(pack, limit=1, log=lambda *_: None)
    assert any("NASA" in p and "削除" in p for p in session.asked)


def test_one_failed_batch_does_not_abort_the_rest(pack: Path, monkeypatch):
    class _FirstBatchBroken(_FakeSession):
        """第1問だけは何度録り直しても駄目なモデル（再試行でも復活しない）。"""

        def read_aloud(self):
            if "第1問。" in self.asked[-1]:
                raise RuntimeError("朗讀が始まらなかった")
            return b"\xff\xf1ok", "audio/aac"

    monkeypatch.setattr(gv, "ChatGPTVoice", _FirstBatchBroken)
    res = gv.generate(pack, batch_size=1, log=lambda *_: None)
    assert res.failed == 1
    assert res.done == 1


# ---------------------------------------------------------------- 失敗の残り方

def test_transient_failure_is_retried_and_recovers(pack: Path, monkeypatch):
    """1 バッチ = 20 問。瞬断で 20 問まとめて失う方が高くつく。"""
    class _FlakyOnce(_FakeSession):
        fails = 1

        def read_aloud(self):
            if _FlakyOnce.fails:
                _FlakyOnce.fails -= 1
                raise RuntimeError("朗讀が始まらなかった")
            return b"\xff\xf1ok", "audio/aac"

    monkeypatch.setattr(gv, "ChatGPTVoice", _FlakyOnce)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.done == 1
    assert res.failed == 0


def test_retries_can_be_switched_off(pack: Path, monkeypatch):
    class _Broken(_FakeSession):
        def read_aloud(self):
            raise RuntimeError("朗讀が始まらなかった")

    monkeypatch.setattr(gv, "ChatGPTVoice", _Broken)
    monkeypatch.setattr(gv, "config", lambda: {**gv.DEFAULTS, "retries": 0})
    res = gv.generate(pack, log=lambda *_: None)
    assert res.failed == 1
    assert res.errors[0]["attempts"] == 1


def test_failed_batches_are_written_to_the_manifest(pack: Path, monkeypatch):
    """成功しか書かないと、空の manifest を見ても原因が分からない。"""
    class _Broken(_FakeSession):
        def read_aloud(self):
            raise RuntimeError("朗讀が始まらなかった")

    monkeypatch.setattr(gv, "ChatGPTVoice", _Broken)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.failed == 1
    fail = gv.load_manifest(pack)["failures"][0]
    assert fail["ordinals"] == [1, 2]
    assert "朗讀が始まらなかった" in fail["error"]
    assert fail["backend"] == "local"
    assert res.errors == [fail]


def test_error_message_keeps_the_exception_type(pack: Path, monkeypatch):
    """playwright / requests の例外は str() が空になることがある。"""
    class _Silent(_FakeSession):
        def read_aloud(self):
            raise TimeoutError()

    monkeypatch.setattr(gv, "ChatGPTVoice", _Silent)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.errors[0]["error"] == "TimeoutError"


def test_failures_are_listed_in_the_audit_file(pack: Path, monkeypatch):
    class _Broken(_FakeSession):
        def read_aloud(self):
            raise RuntimeError("朗讀が始まらなかった")

    monkeypatch.setattr(gv, "ChatGPTVoice", _Broken)
    gv.generate(pack, log=lambda *_: None)
    audit = (pack / "06_voice_audit.md").read_text(encoding="utf-8")
    assert "録れなかったバッチ" in audit
    assert "朗讀が始まらなかった" in audit


def test_session_that_never_opens_is_reported_not_raised(pack: Path, monkeypatch):
    """ここで raise すると prep.py の voice stage が落ち、theater 備援も動かない。"""
    class _NoChrome:
        def __init__(self, *a, **kw):
            raise RuntimeError("Chrome の CDP port 9261 が開きません")

    monkeypatch.setattr(gv, "ChatGPTVoice", _NoChrome)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.done == 0
    assert res.failed == 1
    assert res.errors[0]["stage"] == "session"
    assert "CDP port" in res.errors[0]["error"]


def test_rewrite_failure_keeps_the_first_draft(pack: Path, monkeypatch):
    """書き直しの失敗で 20 問ぶんの原稿を捨てない。"""
    class _RewriteDies(_Inventing):
        def ask(self, prompt: str) -> str:
            if "削除" in prompt:
                raise TimeoutError("書き直しが返らない")
            return super().ask(prompt)

    monkeypatch.setattr(gv, "ChatGPTVoice", _RewriteDies)
    res = gv.generate(pack, limit=1, log=lambda *_: None)
    assert res.done == 1
    assert res.degraded == 1     # 初稿のまま録り、未錨定語は監査に出す


# ---------------------------------------------------------------- miko-ws backend

class _FakeMiko:
    """miko-ws 指揮中心の代役。voice() は wav を書いて spoken を返す。"""

    def __init__(self, spoken=None, fail_times=0):
        self.calls: list[dict] = []
        self._spoken = spoken
        self._fail_times = fail_times

    def voice(self, text, output_path, instruction=None, **kw):
        self.calls.append({"text": text, "path": str(output_path),
                           "instruction": instruction})
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("chatgpt1 未ログイン")
        nums = re.findall(r"第(\d+)問。", text) or ["1"]
        body = self._spoken or "\n\n".join(
            f"第{n}問。話し言葉に整えた原稿です。" for n in nums)
        Path(output_path).write_bytes(b"RIFFfake")
        return {"path": str(output_path), "raw": str(output_path).replace(".wav", ".aac"),
                "mime": "audio/aac", "bytes": 8, "spoken": body,
                "engine": "gpt", "account": "chatgpt2"}


@pytest.fixture()
def miko(monkeypatch):
    fake = _FakeMiko()
    monkeypatch.setattr(gv, "pick_backend", lambda *a, **kw: "miko")
    monkeypatch.setattr("tools.miko_llm.voice", fake.voice)
    return fake


def test_miko_backend_records_without_touching_local_chrome(pack: Path, miko, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("指揮中心を使う設定なのに本機 Chrome を起動した")

    monkeypatch.setattr(gv, "ChatGPTVoice", _boom)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.done == 1
    assert res.failed == 0
    assert len(miko.calls) == 1


def test_miko_backend_sends_the_primer_as_instruction(pack: Path, miko):
    """書き直し依頼として送る＝指揮中心側で合成音へ落ちない、が担保される。"""
    gv.generate(pack, log=lambda *_: None)
    assert "話し言葉" in miko.calls[0]["instruction"]
    assert miko.calls[0]["path"].endswith(".wav")
    assert Path(miko.calls[0]["path"]).is_absolute()


def test_miko_backend_keeps_the_anchor_gate(pack: Path, monkeypatch):
    fake = _FakeMiko(spoken="第1問。NASAの案件を担当しました。")
    monkeypatch.setattr(gv, "pick_backend", lambda *a, **kw: "miko")
    monkeypatch.setattr("tools.miko_llm.voice", fake.voice)
    res = gv.generate(pack, limit=1, log=lambda *_: None)
    assert res.degraded == 1                       # 裏が取れない語は指揮中心経由でも数える
    assert len(fake.calls) == 2                    # 1 回だけ書き直させる
    assert "削除" in fake.calls[1]["instruction"]


def test_miko_backend_records_which_account_spoke(pack: Path, miko):
    gv.generate(pack, log=lambda *_: None)
    item = gv.load_manifest(pack)["items"][0]
    assert item["account"] == "chatgpt2"
    assert item["engine"] == "gpt"


def test_old_runtime_without_voice_api_falls_back_to_local_chrome(pack: Path, monkeypatch):
    from tools.miko_llm import VoiceAPIUnsupported

    def _no_voice(*a, **kw):
        raise VoiceAPIUnsupported("voice API 非対応（HTTP 404）")

    monkeypatch.setattr(gv, "pick_backend", lambda *a, **kw: "miko")
    monkeypatch.setattr("tools.miko_llm.voice", _no_voice)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.done == 1        # 本機 Chrome（_FakeSession）で録れている
    assert res.failed == 0


def test_miko_consecutive_failures_fall_back_to_local(pack: Path, monkeypatch):
    """gateway ごと落ちていると、残りも 1 バッチずつ HTTP timeout を待って全滅する。"""
    fake = _FakeMiko(fail_times=99)
    monkeypatch.setattr(gv, "pick_backend", lambda *a, **kw: "miko")
    monkeypatch.setattr("tools.miko_llm.voice", fake.voice)
    monkeypatch.setattr(gv, "config", lambda: {**gv.DEFAULTS, "backend": "auto",
                                               "retries": 0, "miko_failover_after": 1})
    res = gv.generate(pack, batch_size=1, log=lambda *_: None)
    assert res.done == 2          # 本機 Chrome（_FakeSession）で 2 バッチとも録れた
    assert res.failed == 0        # 録り直せた失敗は記録から消える
    assert len(fake.calls) == 1   # 指揮中心は 1 回空振りしただけで見切る


def test_explicit_miko_backend_never_falls_back_to_local(pack: Path, monkeypatch):
    """backend: miko は「指揮中心のみ」— 黙って単一アカウント経路へ落とさない。"""
    def _boom(*a, **kw):
        raise AssertionError("backend: miko なのに本機 Chrome を起動した")

    fake = _FakeMiko(fail_times=99)
    monkeypatch.setattr(gv, "pick_backend", lambda *a, **kw: "miko")
    monkeypatch.setattr("tools.miko_llm.voice", fake.voice)
    monkeypatch.setattr(gv, "ChatGPTVoice", _boom)
    monkeypatch.setattr(gv, "config", lambda: {**gv.DEFAULTS, "backend": "miko",
                                               "retries": 0, "miko_failover_after": 1})
    res = gv.generate(pack, batch_size=1, log=lambda *_: None)
    assert res.failed == 2
    assert all(e["backend"] == "miko" for e in res.errors)


def test_pick_backend_respects_explicit_local(monkeypatch):
    monkeypatch.setattr(gv, "config", lambda: {**gv.DEFAULTS, "backend": "local"})
    monkeypatch.setattr(gv, "_miko_ready", lambda *a, **kw: True)
    assert gv.pick_backend(log=lambda *_: None) == "local"


def test_pick_backend_auto_falls_back_when_center_is_down(monkeypatch):
    monkeypatch.setattr(gv, "config", lambda: {**gv.DEFAULTS, "backend": "auto"})
    monkeypatch.setattr(gv, "_miko_ready", lambda *a, **kw: False)
    assert gv.pick_backend(log=lambda *_: None) == "local"


# ---------------------------------------------------------------- 挨拶だけ返された時

def test_merge_primer_drops_the_ack_instruction():
    """1 通目に混ぜるときに残すと、ChatGPT は挨拶だけ返して原稿を書かない（実測）。"""
    merged = gv.merge_primer(gv.SYSTEM_PRIMER)
    assert "最初のメッセージには" not in merged
    assert "話し言葉" in merged          # 本体の指示は残っている


def test_miko_backend_rejects_a_reply_without_any_script(pack: Path, monkeypatch):
    """挨拶だけの 1 秒音檔をキャッシュに載せない — 載ると二度と録り直されない。"""
    fake = _FakeMiko(spoken="了解しました。")
    monkeypatch.setattr(gv, "pick_backend", lambda *a, **kw: "miko")
    monkeypatch.setattr("tools.miko_llm.voice", fake.voice)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.done == 0
    assert res.failed == 1
    assert gv.load_manifest(pack)["items"] == []


def test_local_backend_rejects_a_reply_without_any_script(pack: Path, monkeypatch):
    class _OnlyGreets(_FakeSession):
        def _bodies(self, prompt: str) -> str:
            return "了解しました。"

    monkeypatch.setattr(gv, "ChatGPTVoice", _OnlyGreets)
    res = gv.generate(pack, log=lambda *_: None)
    assert res.done == 0
    assert res.failed == 1
