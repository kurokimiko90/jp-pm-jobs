"""tts/theater — QA md parser（風險集中點）+ tools/pii_gate 閘門。"""
import subprocess

from tts.theater import _probe_duration_ms, build_script, parse_standard_qa
from tools import pii_gate

STANDARD_MD = """\
# 想定問答 — テスト株式会社

生成日: 2026-07-19

---

## 定番質問（共通底稿）

→ 参照のみ。ここに ### Q は無い。

---

## A. 会社接続（2問）

### Q. 転職理由を教えてください
AI を本業にしたいからです。
1. 現職では **AI は業務の中心ではありません**。
2. 個人ではマルチ LLM 基盤を実運用しています。

### Q. 当社を志望する理由を教えてください
御社だからです。
1. 環境が合っています。

## B. この JD 特化（1問）

### Q. 品質管理はどのように行いますか
基準を数値で固定します。
1. テスト 250 項目以上を体系化しました（{{ここに自分の事例}}）。
2. `品質ゲート` を実装しています。
"""

OLD_FORMAT_MD = """\
# 想定問答

A. 会社接続（4問）
1. 転職理由の末尾接続

「これまで培った経験を…」

Q1. 顧客の課題をどう翻訳しますか？

狙い: 技術的翻訳力の検証。

回答骨子:

S/T: 大手向け SaaS 開発。
"""


class TestParseStandardQA:
    def test_parses_questions_sections_points(self):
        items = parse_standard_qa(STANDARD_MD)
        assert len(items) == 3
        assert items[0].section == "A. 会社接続（2問）"
        assert items[0].question == "転職理由を教えてください"
        assert items[0].conclusion == "AI を本業にしたいからです。"
        assert len(items[0].points) == 2
        assert items[2].section == "B. この JD 特化（1問）"

    def test_markdown_syntax_stripped(self):
        items = parse_standard_qa(STANDARD_MD)
        assert "**" not in items[0].points[0]
        assert "`" not in items[2].points[1]

    def test_placeholders_removed(self):
        items = parse_standard_qa(STANDARD_MD)
        assert "{{" not in items[2].points[0]
        assert "ここに自分の事例" not in items[2].points[0]

    def test_old_format_yields_empty(self):
        # 舊格式（狙い/回答骨子型）不支援 — 回空列，呼叫端要求重生成
        assert parse_standard_qa(OLD_FORMAT_MD) == []


class TestBuildScript:
    def test_script_shape(self):
        items = parse_standard_qa(STANDARD_MD)
        script = build_script(FakePath("3763_テスト"), items,
                              {"company": "テスト株式会社", "title": "PM"})
        assert script["noFavor"] is True
        assert script["sample"] is False
        assert len(script["acts"]) == 2  # A / B 兩幕（定番質問 section 無 ### Q → 無幕）
        act1 = script["acts"][0]
        # Q → interviewer、結論+要点 → candidate 逐句
        assert act1["lines"][0]["speaker"] == "interviewer"
        assert act1["lines"][1]["speaker"] == "candidate"
        assert act1["lines"][1]["ja"] == "AI を本業にしたいからです。"
        # 一切不編造演出
        assert all("favorDelta" not in l for a in script["acts"] for l in a["lines"])
        assert script["report"]["aspects"] == []

    def test_line_ids_unique(self):
        items = parse_standard_qa(STANDARD_MD)
        script = build_script(FakePath("x"), items, None)
        ids = [l["id"] for a in script["acts"] for l in a["lines"]]
        assert len(ids) == len(set(ids))


class FakePath:
    def __init__(self, name: str):
        self.name = name


class TestProbeDuration:
    def test_parses_ffprobe_seconds_to_ms(self, monkeypatch, tmp_path):
        def fake_run(*a, **kw):
            return subprocess.CompletedProcess(a, 0, stdout="3.456000\n", stderr="")
        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _probe_duration_ms(tmp_path / "x.mp3") == 3456

    def test_missing_ffprobe_returns_none(self, monkeypatch, tmp_path):
        def fake_run(*a, **kw):
            raise FileNotFoundError("ffprobe not found")
        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _probe_duration_ms(tmp_path / "x.mp3") is None

    def test_nonzero_exit_returns_none(self, monkeypatch, tmp_path):
        def fake_run(*a, **kw):
            raise subprocess.CalledProcessError(1, "ffprobe")
        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _probe_duration_ms(tmp_path / "x.mp3") is None


class TestPIIGate:
    def test_scrub_replaces_terms(self, monkeypatch):
        monkeypatch.setattr(pii_gate, "_terms",
                            lambda: (("山田太郎", "本人"), ("taro@example.com", "***")))
        clean, findings = pii_gate.scrub_for_external("私は山田太郎です。連絡は taro@example.com へ。")
        assert "山田太郎" not in clean
        assert "taro@example.com" not in clean
        assert "本人" in clean
        assert len(findings) == 2

    def test_clean_text_untouched(self, monkeypatch):
        monkeypatch.setattr(pii_gate, "_terms", lambda: (("山田太郎", "本人"),))
        clean, findings = pii_gate.scrub_for_external("AI を本業にしたいからです。")
        assert clean == "AI を本業にしたいからです。"
        assert findings == []
