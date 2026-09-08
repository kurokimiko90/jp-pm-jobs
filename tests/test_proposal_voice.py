"""proposal/voice.py の素材抽出テスト（ブラウザを起動しない部分だけ）。

守るのは「何を ChatGPT へ渡すか」— ここが静かに壊れると、音声だけ内容が
抜け落ちたパックが出来上がり、面接前日まで気づけない。
"""

from pathlib import Path

import pytest

from proposal import prompts, voice

REDTEAM_MD = """# 紅隊レビュー

## 1. 致命的な弱点

### 1. 台帳の実装が、原因検証より先に固定されています

**指摘**：順序が逆です。

## 3. 想定質問と応答案

### 弱点1への質問

**質問**：「なぜ既存の管理システムを調べる前に、新しい台帳を作るのですか？」

**応答案**：最初の30日は新規実装を確約しません。既存画面で足りるか先に見ます。

### 弱点2への質問

**質問**：「収益構造も不明なのに、最大損失をどう決めるのですか？」

**応答案**：金額換算は算定式を承認した後に行います。

## 4. 修正すべき記述

直すべき箇所の一覧です。
"""

# 同じ意味の §3 でも、パックによって書き方が違う（prompt が形式を固定していない）。
# 実測 4752 はこちら — 見出しが無く、コロンが太字の内側で、応答案は太字ですらない。
# 4407 の形だけを拾う parser は、ここで**エラーを出さずに 0 件を返す**
REDTEAM_MD_FLAT = """# レッドチームレビュー

## 3. 想定質問と応答案

**質問：そもそも、お客様は画面で選びたいのでしょうか。営業に相談したいのでは？**

応答案：現時点では、自己選択型の導線とは断定していません。
まず商談の実態を確認し、営業主導なら社内向けの提案支援から試します。

**質問：目的選択画面を作ると、なぜロードマップの判断が良くなるのですか。**

応答案：目的の収集だけでは採否基準になりません。

## 4. 修正すべき記述

直すべき箇所の一覧です。
"""

CARDS_MD = """# 能力カード

_生成日: 2026-08-15_

### KPIを背負ってプロダクトを成長させる力
- **この会社での出番**: 充電器を一体の製品として成長させる場面で要ります。
- **具体的にやること**: ①KGIと指標を結ぶ ②損失を比べて対象を決める
- **本人の実績**: ポイントブランド統合をE2Eで主導しました。
- **主提案との関係**: KPIツリーに効きます。

### ステークホルダーとの合意形成力
- **この会社での出番**: 各部門が異なる前提を持つ場面で要ります。
- **具体的にやること**: 前提を並べ、受入基準へ変える。
- **本人の実績**: 直接の実績なし。近いのは部門横断の調整です。
- **主提案との関係**: 進め方に効きます。
"""

PITCH_MD = """# 5 分ピッチ

## 1. いま何が起きていると見ているか

外から見えているのは、設置台数を伸ばしている段階だということです。
確認できていないのは、利用側の継続率です。

## 2. だから何を課題として置くか

私が置く課題は、設置ではなく充電の成功率です。

## 5. なぜ私がやるのか

前職で同じ判断をしました。
"""


# ---------------------------------------------------------------- redteam QA

def test_extract_redteam_qa_pairs_question_with_answer():
    got = voice.extract_redteam_qa(REDTEAM_MD)
    assert len(got) == 2
    assert got[0].question.startswith("なぜ既存の管理システム")
    assert got[0].conclusion.startswith("最初の30日")


def test_extract_redteam_qa_strips_quote_brackets():
    """「」を残すと合成音が「かぎかっこ」と読む。"""
    assert not voice.extract_redteam_qa(REDTEAM_MD)[0].question.startswith("「")


def test_extract_redteam_qa_ignores_other_sections():
    """§1 の弱点や §4 の修正案を拾うと、音声が紅隊レビューの朗読になる。"""
    for seg in voice.extract_redteam_qa(REDTEAM_MD):
        assert "順序が逆" not in seg.conclusion
        assert "直すべき箇所" not in seg.conclusion


def test_extract_redteam_qa_without_the_section_is_empty_not_an_error():
    assert voice.extract_redteam_qa("# 紅隊\n\n## 1. 弱点\n\n本文") == []


def test_extract_redteam_qa_handles_the_flat_format():
    """見出し無し・コロンが太字の内側・応答案は太字なし（実測 4752）。"""
    got = voice.extract_redteam_qa(REDTEAM_MD_FLAT)
    assert len(got) == 2
    assert got[0].question.startswith("そもそも、お客様は")
    assert not got[0].question.endswith("**")


def test_flat_format_answer_keeps_the_wrapped_lines():
    """応答案は行末 2 スペースで折り返す。2 行目を落とすと結論だけになる。"""
    seg = voice.extract_redteam_qa(REDTEAM_MD_FLAT)[0]
    assert "断定していません" in seg.conclusion
    assert "商談の実態を確認" in seg.conclusion
    assert "  " not in seg.conclusion


def test_flat_format_stops_at_the_next_section():
    for seg in voice.extract_redteam_qa(REDTEAM_MD_FLAT):
        assert "直すべき箇所" not in seg.conclusion


# ---------------------------------------------------------------- cards

def test_extract_cards_one_segment_per_capability():
    got = voice.extract_cards(CARDS_MD)
    assert len(got) == 2
    assert got[0].question.startswith("KPIを背負って")


def test_extract_cards_carries_scene_and_evidence():
    seg = voice.extract_cards(CARDS_MD)[0]
    assert "一体の製品として成長" in seg.conclusion
    assert any("ポイントブランド統合" in p for p in seg.points)


def test_extract_cards_drops_the_link_to_the_main_proposal():
    """提案との接続は pitch の担当。両方に入れるとどちらも印象に残らない。"""
    for seg in voice.extract_cards(CARDS_MD):
        assert "KPIツリーに効きます" not in " ".join([seg.conclusion, *seg.points])


def test_circled_numbers_become_speakable_japanese():
    """①は合成音が読めない。順番が耳で分かる日本語に開く。"""
    seg = voice.extract_cards(CARDS_MD)[0]
    joined = " ".join(seg.points)
    assert "①" not in joined and "②" not in joined
    assert "1つ目に、" in joined


def test_extract_cards_keeps_honest_gaps():
    """「直接の実績なし」は誤魔化しではなく正直な記述 — 落としてはいけない。"""
    seg = voice.extract_cards(CARDS_MD)[1]
    assert any("直接の実績なし" in p for p in seg.points)


# ---------------------------------------------------------------- pitch

def test_extract_pitch_one_segment_per_section():
    got = voice.extract_pitch(PITCH_MD)
    assert len(got) == 3
    assert got[0].question.startswith("1. いま何が")


def test_extract_pitch_keeps_the_whole_paragraph():
    seg = voice.extract_pitch(PITCH_MD)[0]
    assert "設置台数" in seg.conclusion and "継続率" in seg.conclusion


def test_extract_pitch_ignores_the_document_title():
    assert all("5 分ピッチ" not in s.question for s in voice.extract_pitch(PITCH_MD))


# ---------------------------------------------------------------- track 配線

def test_every_track_points_at_a_real_stage_file():
    """track の素材はファイル名を直書きせず STAGES から引く（番号ずれ防止）。"""
    for meta in voice.TRACKS.values():
        assert meta["source"] in prompts.STAGES


def test_source_path_follows_the_stage_definition(tmp_path: Path):
    assert voice.source_path(tmp_path, "pitch").name == prompts.STAGES["pitch"]["file"]
    assert voice.source_path(tmp_path, "qa").name == prompts.STAGES["redteam"]["file"]


def test_audio_paths_are_distinct_per_track(tmp_path: Path):
    paths = {voice.audio_path(tmp_path, t) for t in voice.TRACKS}
    assert len(paths) == len(voice.TRACKS)


def test_segments_for_missing_source_names_the_stage_to_run(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="--stage redteam"):
        voice.segments_for(tmp_path, "qa")


def test_run_track_skips_when_source_is_missing(tmp_path: Path):
    """素材が無いのは失敗ではない（その stage をまだ回していないだけ）。"""
    res = voice.run_track(tmp_path, "qa", log=lambda *_: None)
    assert res.status == "skipped"


# ---------------------------------------------------------------- 録音の配線

class _FakeVoiceResult:
    def __init__(self, wavs, degraded=0, failed=0, errors=None):
        self.wav_files, self.degraded = wavs, degraded
        self.failed, self.errors = failed, errors or []
        self.done, self.cached = len(wavs), 0
        self.batches = len(wavs) + failed


@pytest.fixture()
def pack(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "4407_サンプル社"
    d.mkdir()
    (d / prompts.STAGES["redteam"]["file"]).write_text(REDTEAM_MD, encoding="utf-8")
    (d / prompts.STAGES["cards"]["file"]).write_text(CARDS_MD, encoding="utf-8")
    (d / prompts.STAGES["pitch"]["file"]).write_text(PITCH_MD, encoding="utf-8")

    def fake_record(pdir, track, segments, *, force, limit, log=print):
        wav = pdir / voice.AUDIT_DIR / f"{track}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"RIFFfake")
        (pdir / voice.AUDIT_DIR / f"{track}.md").write_text(
            f"# 監査\n\n{track} の原稿", encoding="utf-8")
        return _FakeVoiceResult([wav])

    monkeypatch.setattr(voice, "_record", fake_record)
    monkeypatch.setattr(voice, "_concat",
                        lambda files, out: out.write_bytes(b"MP3fake"))
    return d


def test_run_produces_one_audio_per_track(pack: Path):
    results = voice.run({"id": 4407}, pack, send=False, log=lambda *_: None)
    assert [r.status for r in results] == ["ok", "ok", "ok"]
    for r in results:
        assert r.path.exists()


def test_run_writes_a_single_audit_file(pack: Path):
    voice.run({"id": 4407}, pack, send=False, log=lambda *_: None)
    audit = (pack / voice.AUDIT_FILE).read_text(encoding="utf-8")
    for track in voice.DEFAULT_TRACKS:
        assert f"{track} の原稿" in audit


@pytest.mark.parametrize("track,primer_attr", [
    ("pitch", "SCRIPT_PRIMER"),   # 原稿は閘門通過済み。作り変えさせてはいけない
    ("qa", "SYSTEM_PRIMER"),      # 一問一答は話し言葉へ書き直させる
    ("cards", "SYSTEM_PRIMER"),
])
def test_track_passes_the_right_primer(tmp_path: Path, monkeypatch,
                                       track, primer_attr):
    from tts import gpt_voice

    pack = tmp_path / "4407_サンプル社"
    pack.mkdir()
    for stage, md in (("pitch", PITCH_MD), ("redteam", REDTEAM_MD),
                      ("cards", CARDS_MD)):
        (pack / prompts.STAGES[stage]["file"]).write_text(md, encoding="utf-8")
    (pack / "x.wav").write_bytes(b"RIFF")

    seen: dict = {}
    monkeypatch.setattr(gpt_voice, "generate_items",
                        lambda pdir, segs, **kw: seen.update(kw) or
                        _FakeVoiceResult([pdir / "x.wav"]))
    monkeypatch.setattr(voice, "_concat",
                        lambda files, out: out.write_bytes(b"MP3fake"))
    voice.run_track(pack, track, log=lambda *_: None)
    assert seen["primer"] == getattr(gpt_voice, primer_attr)
    assert seen["track"] == track


def test_tracks_do_not_share_a_recording_directory(pack: Path):
    """同じ部屋に置くと、別 track の音檔を「古い音檔」として消し合う。"""
    from tts import gpt_voice

    dirs = {gpt_voice.voice_dir(pack, t) for t in voice.TRACKS}
    assert len(dirs) == len(voice.TRACKS)
    assert gpt_voice.voice_dir(pack) not in dirs
