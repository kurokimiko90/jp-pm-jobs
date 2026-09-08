"""interview/theater_script.py の機械ゲート検証 — 故意の違反 fixture がすべて弾かれること。"""
import json

import pytest

from interview import theater_script as ts
from tts.theater import QAItem

SPEC3 = next(s for s in ts.SKELETON if s.no == 3)   # chain_required=True
SPEC4 = next(s for s in ts.SKELETON if s.no == 4)   # chain_required=False


def mat(digits_text: str = "実績は700万円、工数30%削減、7.5年") -> ts.Materials:
    m = ts.Materials(company="X社", title="PdM", qa_md=digits_text, shibou="",
                     brief_intro="", brief_jd="", brief_gyaku="", unconfirmed=[],
                     raw_jd="", numbers="")
    m.corpus_digits = ts._digits_of(digits_text)
    return m


def line(speaker="candidate", type_="talk", ja="よろしくお願いいたします。", **kw) -> dict:
    return {"speaker": speaker, "type": type_, "ja": ja, "emotion": "neutral", **kw}


# ---------------------------------------------------------------- Gate A 構造

class TestGateStructure:
    def test_valid_chain_passes(self):
        lines = [
            line("candidate", ja="差し支えなければ、募集背景を伺ってもよろしいでしょうか。"),
            line("interviewer", ja="新しい機能立ち上げに伴う増員です。"),
            line("candidate", ja="恐れ入りますが、その立ち上げの範囲を伺えますか。",
                 quoteFrom="立ち上げ"),
            line("interviewer", ja="企画から検証までお任せします。"),
        ]
        assert ts.gate_structure(lines, SPEC3) == []

    def test_fake_quotefrom_rejected(self):
        lines = [
            line("interviewer", ja="増員です。"),
            line("candidate", ja="御社の文化を伺えますか。", quoteFrom="存在しない語句"),
        ]
        assert any("quoteFrom" in v for v in ts.gate_structure(lines, SPEC4))

    def test_chain_required_missing_rejected(self):
        lines = [line("interviewer", ja="どうぞ。"), line("candidate")]
        assert any("チェーン" in v for v in ts.gate_structure(lines, SPEC3))

    def test_bad_speaker_rejected(self):
        assert any("speaker" in v for v in ts.gate_structure([line("narrator")], SPEC4))

    def test_empty_act_rejected(self):
        assert ts.gate_structure([], SPEC4) == ["幕が空"]


# ---------------------------------------------------------------- Gate B 事実錨定

class TestGateFacts:
    def test_corpus_digit_allowed(self):
        lines = [line(ja="前職では年収700万円でした。")]
        assert ts.gate_facts(lines, mat().corpus_digits) == []

    def test_fabricated_digit_rejected(self):
        lines = [line(ja="工数を45%削減しました。")]   # 45 は素材にない
        assert any("45" in v for v in ts.gate_facts(lines, mat().corpus_digits))

    def test_enumerator_allowed(self):
        lines = [line(ja="理由は3点ございます。")]
        assert ts.gate_facts(lines, set()) == []

    def test_interviewer_exempt(self):
        lines = [line("interviewer", ja="当社は設立99年です。")]
        assert ts.gate_facts(lines, set()) == []

    def test_decimal_and_comma_normalized(self):
        lines = [line(ja="経験は7.5年です。")]
        assert ts.gate_facts(lines, mat().corpus_digits) == []


class TestCorpusDigitsCoverage:
    """brief_intro / brief_gyaku 由来の実数字が corpus に入る（Gate B 誤爆の回帰防止）。"""

    def test_brief_intro_and_gyaku_digits_included(self, monkeypatch, tmp_path):
        pack = tmp_path / "1_会社"
        pack.mkdir()
        (pack / "03_interview_qa.md").write_text("### Q. x\n結論\n1. 要点", encoding="utf-8")
        (pack / "01_company_brief.md").write_text(
            "# brief\n従業員数は120名です。\n\n## 11. 逆質問リスト\n離職率は3%です。\n",
            encoding="utf-8")
        m = ts.collect_materials(pack, {"company": "会社", "title": "PdM", "raw_jd": ""})
        assert {"120", "3"} <= m.corpus_digits


# ---------------------------------------------------------------- Gate C 口語敬語

class TestGateSpeech:
    def test_plain_form_rejected(self):
        assert any("常体" in v for v in ts.gate_speech([line(ja="これが強みだ。")]))

    def test_bare_question_rejected(self):
        assert any("クッション" in v
                   for v in ts.gate_speech([line(ja="募集背景は何ですか。")]))

    def test_polite_question_passes(self):
        assert ts.gate_speech(
            [line(ja="差し支えなければ、募集背景を伺ってもよろしいでしょうか。")]) == []

    def test_interviewer_question_exempt_from_cushion(self):
        assert ts.gate_speech([line("interviewer", ja="強みは何ですか。")]) == []

    def test_markdown_residue_rejected(self):
        assert any("markdown" in v for v in ts.gate_speech([line(ja="**強み**です。")]))
        assert any("markdown" in v for v in ts.gate_speech([line(ja="1. まず結論です。")]))
        assert any("markdown" in v for v in ts.gate_speech([line(ja="{{要確認}}です。")]))

    def test_overlong_line_rejected(self):
        assert any("分割" in v for v in ts.gate_speech([line(ja="あ" * 91 + "です。")]))

    def test_inner_exempt_from_style(self):
        assert ts.gate_speech([line("inner", "inner", ja="（これは心の声だ。）")]) == []


class TestAutofix:
    def test_kisha_to_onsha(self):
        fixed = ts._autofix_line(line(ja="貴社の文化に共感しています。"))
        assert "御社" in fixed["ja"] and "貴社" not in fixed["ja"]

    def test_ryokai_to_shochi(self):
        assert "承知しました" in ts._autofix_line(line(ja="了解しました。"))["ja"]

    def test_invalid_emotion_normalized(self):
        ln = line(ja="はい。")
        ln["emotion"] = "angry"
        assert ts._autofix_line(ln)["emotion"] == "neutral"


# ---------------------------------------------------------------- 分桶

class TestBucket:
    def test_themed_assignment_and_fallback(self):
        items = [
            QAItem("", "自己紹介・経歴を教えてください。", "はい。"),
            QAItem("", "転職理由を教えてください。", "はい。"),
            QAItem("", "希望年収について教えてください。", "はい。"),
            QAItem("", "宇宙開発の経験はありますか。", "ありません。"),  # 未マッチ → 幕6
        ]
        b = ts.bucket_qa(items)
        assert [i.question[:4] for i in b[2]] == ["自己紹介"]
        assert len(b[3]) == 1 and len(b[8]) == 1
        assert any("宇宙" in i.question for i in b[6])

    def test_full_coverage(self):
        items = [QAItem("", f"質問その{i}ですか。", "はい。") for i in range(12)]
        b = ts.bucket_qa(items)
        assert sum(len(v) for v in b.values()) == len(items)


# ---------------------------------------------------------------- 降級・組裝

class TestFallbackAndFinalize:
    def test_fallback_shape(self):
        acts = ts.fallback_act(SPEC4, [QAItem("", "失敗経験は。", "結論です。", ["要点1です。"])])
        assert [l["speaker"] for l in acts] == ["interviewer", "candidate", "candidate"]

    def test_fallback_greeting_and_closing_never_empty(self):
        """幕1/9は QA が分桶されず items=[] になる — 降級時に無音消失しないこと。"""
        spec1 = next(s for s in ts.SKELETON if s.no == 1)
        spec9 = next(s for s in ts.SKELETON if s.no == 9)
        m = mat()
        assert ts.fallback_act(spec1, [], m)
        assert ts.fallback_act(spec9, [], m)
        assert m.company in ts.fallback_act(spec1, [], m)[0]["ja"]

    def test_fallback_other_empty_act_stays_empty(self):
        spec5 = next(s for s in ts.SKELETON if s.no == 5)
        assert ts.fallback_act(spec5, []) == []

    def test_finalize_ids_unique_and_simulated_note(self):
        lines = [line("interviewer", ja="模擬回答です。", simulated=True), line()]
        out = ts._finalize_lines(SPEC4, lines)
        assert len({l["id"] for l in out}) == 2
        assert out[0]["note"]["ja"] == ts.SIMULATED_NOTE["ja"]
        assert "simulated" not in out[1]


class TestGateQACoverage:
    """Gate D — 桶に入った QA が台詞に反映されているか（実 pack で「強み」「生成AI」
    等の QA が桶には数えられているのに台詞から丸ごと消えていた回帰の防止）。"""

    def test_no_items_passes(self):
        assert ts.gate_qa_coverage([line()], [], SPEC4) == []

    def test_keyword_present_in_dialogue_passes(self):
        items = [QAItem("", "あなたの強みを教えてください。", "実行力です。", [])]
        spec2 = next(s for s in ts.SKELETON if s.no == 2)
        lines = [line(ja="私の強みは実行力です。")]
        assert ts.gate_qa_coverage(lines, items, spec2) == []

    def test_keyword_missing_from_dialogue_flagged(self):
        items = [QAItem("", "あなたの強みを教えてください。", "実行力です。", [])]
        spec2 = next(s for s in ts.SKELETON if s.no == 2)
        lines = [line(ja="よろしくお願いいたします。")]
        v = ts.gate_qa_coverage(lines, items, spec2)
        assert v and "強み" in v[0]

    def test_fallback_ngram_used_when_no_keyword_hit(self):
        # SPEC4 のキーワードに一致しない質問 → 質問文から抽出した n-gram で照合
        items = [QAItem("", "生成AIを業務に組み込んだ経験はありますか。", "あります。", [])]
        lines_missing = [line(ja="実行力を大事にしています。")]
        lines_present = [line(ja="生成AIを業務に組み込んだ実運用経験があります。")]
        assert ts.gate_qa_coverage(lines_missing, items, SPEC4) != []
        assert ts.gate_qa_coverage(lines_present, items, SPEC4) == []


# ---------------------------------------------------------------- end-to-end（mock LLM）

QA_MD = """# 想定問答
### Q. 自己紹介・経歴を教えてください。
エンジニア出身で約9年プロダクトに関わってきました。
1. 前職では年収700万円の水準で決済領域を担当しました。
### Q. 転職理由を教えてください。
次の挑戦のためです。
1. AIを前提としたプロダクト開発に挑戦したいと考えています。
"""

VALID_LINES = [
    {"speaker": "interviewer", "type": "talk", "ja": "本日はよろしくお願いいたします。",
     "emotion": "smile"},
    {"speaker": "candidate", "type": "talk", "ja": "こちらこそ、よろしくお願いいたします。",
     "emotion": "neutral"},
    {"speaker": "candidate", "type": "talk",
     "ja": "経歴としては、エンジニア出身で決済領域を担当してまいりました。",
     "emotion": "neutral"},
    {"speaker": "candidate", "type": "talk",
     "ja": "転職理由は、AIを前提としたプロダクト開発に挑戦したいためです。",
     "emotion": "neutral"},
    {"speaker": "candidate", "type": "talk",
     "ja": "差し支えなければ、募集背景を伺ってもよろしいでしょうか。", "emotion": "think"},
    {"speaker": "interviewer", "type": "talk", "ja": "新機能立ち上げに伴う増員です。",
     "emotion": "neutral", "simulated": True},
    {"speaker": "candidate", "type": "talk",
     "ja": "恐れ入りますが、その立ち上げの体制を伺えますか。", "emotion": "neutral",
     "quoteFrom": "立ち上げ"},
    {"speaker": "interviewer", "type": "talk", "ja": "少人数で主体的に進める体制です。",
     "emotion": "neutral", "simulated": True},
]


def combined_json(lines=VALID_LINES) -> str:
    return json.dumps(
        {"acts": {str(s.no): lines for s in ts.SKELETON}, "done": True},
        ensure_ascii=False)


class TestGenerate:
    def test_generate_single_call_writes_script_and_report(self, tmp_path, monkeypatch):
        pack = tmp_path / "999_テスト社"
        pack.mkdir()
        (pack / "03_interview_qa.md").write_text(QA_MD, encoding="utf-8")
        calls = []

        def fake(prompt):
            calls.append(prompt)
            return combined_json()
        monkeypatch.setattr(ts, "_llm_call", fake)

        stats = ts.generate(pack, {"company": "テスト社", "title": "PdM", "raw_jd": "JD本文"},
                            log=lambda *a: None)
        assert len(calls) == 1                        # 全幕合格 → 合併 1 call のみ
        assert stats["llm_calls"] == 1
        script = json.loads((pack / "theater" / "script.json").read_text(encoding="utf-8"))
        assert script["_meta"]["interactive"] is True
        assert script["noFavor"] is True
        assert stats["degraded"] == []
        ids = [l["id"] for a in script["acts"] for l in a["lines"]]
        assert len(ids) == len(set(ids))
        assert (pack / "theater" / "review_report.md").exists()
        # 模擬回答に note が付いている
        sim = [l for a in script["acts"] for l in a["lines"] if l.get("simulated")]
        assert sim and all(l["note"]["ja"] == ts.SIMULATED_NOTE["ja"] for l in sim)

    def test_generate_repairs_only_failing_act(self, tmp_path, monkeypatch):
        pack = tmp_path / "996_テスト社"
        pack.mkdir()
        (pack / "03_interview_qa.md").write_text(QA_MD, encoding="utf-8")
        bad = VALID_LINES + [
            {"speaker": "candidate", "type": "talk",
             "ja": "工数を45%削減しました。", "emotion": "neutral"}]  # 捏造数字
        combined = {"acts": {str(s.no): (bad if s.no == 2 else VALID_LINES)
                             for s in ts.SKELETON}, "done": True}
        calls = []

        def fake(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return json.dumps(combined, ensure_ascii=False)
            return json.dumps({"lines": VALID_LINES, "done": True}, ensure_ascii=False)
        monkeypatch.setattr(ts, "_llm_call", fake)

        stats = ts.generate(pack, None, log=lambda *a: None)
        assert len(calls) == 2                        # 合併 1 + 幕2 修復 1
        assert stats["degraded"] == []
        assert stats["regens"] == 1

    def test_generate_degrades_on_llm_failure(self, tmp_path, monkeypatch):
        pack = tmp_path / "998_テスト社"
        pack.mkdir()
        (pack / "03_interview_qa.md").write_text(QA_MD, encoding="utf-8")

        def dead(prompt):
            raise RuntimeError("all providers down")
        monkeypatch.setattr(ts, "_llm_call", dead)

        stats = ts.generate(pack, None, log=lambda *a: None)
        assert stats["degraded"]                      # 降級幕あり
        script = json.loads((pack / "theater" / "script.json").read_text(encoding="utf-8"))
        assert script["acts"]                         # 平板版でも完走している
        report = (pack / "theater" / "review_report.md").read_text(encoding="utf-8")
        assert "降級" in report

    def test_generate_cache_hit_zero_calls(self, tmp_path, monkeypatch):
        pack = tmp_path / "997_テスト社"
        pack.mkdir()
        (pack / "03_interview_qa.md").write_text(QA_MD, encoding="utf-8")
        calls = []

        def fake(prompt):
            calls.append(1)
            return combined_json()
        monkeypatch.setattr(ts, "_llm_call", fake)

        ts.generate(pack, None, log=lambda *a: None)
        assert len(calls) == 1
        stats2 = ts.generate(pack, None, log=lambda *a: None)
        assert len(calls) == 1                         # 2 回目は 0 call（全幕キャッシュ）
        assert stats2["llm_calls"] == 0
        assert stats2["cached"] > 0
