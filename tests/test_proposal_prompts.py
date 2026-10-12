"""prompt 側と機械閘門側がずれていないことの回帰テスト（v4）。

このパックの品質は「LLM がうまく書いたか」ではなく「生成側と検査側が同じ規格を
見ているか」で決まる。人間が prompt に閘門の条件を書き写す運用だと、spec を
直したときに片方だけ古くなる（実測: `min_lines` を緩めた後も prompt には旧値が
残っていた）。v4 で以下を単一の出所に寄せたので、その錠をここに置く:

- 閘門条件 → `gates.self_check(spec)` が GateSpec から生成
- 版面の高さ予算 → `deck.capacity_hint()` が `deck._H` から生成
- 素材と指示の境界 → `context.build()` のタグと `prompts.SOURCE_ISOLATION_RULE`
"""

from pathlib import Path

from proposal import context, deck, gates, pipeline, prompts


def _job() -> dict:
    return {"id": 1, "company": "テスト株式会社", "title": "PdM",
            "raw_jd": "## 職務内容\nプロダクトマネージャーを募集します。\n"
                      "## 求める能力・経験\nSaaS の要件定義。",
            "url": "https://example.com/jobs/1"}


# ---- 素材と指示の境界 -------------------------------------------------

def test_context_wraps_every_source_in_tags():
    """素材は必ずタグの中。Markdown 見出しだと JD 原文の `##` と同階層に並ぶ。"""
    text = context.build(_job(), facts="調査済みの事実", with_profile=True)
    assert text.count("<素材") == text.count("</素材>") == 4
    # タグの外に素材が漏れていないか（先頭がタグで始まる）
    assert text.lstrip().startswith("<素材")


def test_common_rules_declare_source_isolation():
    """JD も取得原文も外部文字列。指示として読ませない宣言が要る。"""
    assert prompts.SOURCE_ISOLATION_RULE in prompts.COMMON_RULES
    assert "指示ではありません" in prompts.COMMON_RULES


# ---- 閘門と prompt の単一化 -------------------------------------------

def test_self_check_covers_every_required_marker():
    """必須の記述は自己点検にも必ず出る（片方だけ増やす事故を防ぐ）。"""
    for name, meta in prompts.STAGES.items():
        spec = meta.get("spec")
        if not spec or not spec.required_markers:
            continue
        checklist = gates.self_check(spec)
        for marker in spec.required_markers:
            assert marker in checklist, f"{name}: {marker} が自己点検に無い"


def test_required_markers_exist_in_the_prompt_itself():
    """機械が要求する記述は、prompt が書き方を教えていなければ通らない。"""
    for name, meta in prompts.STAGES.items():
        spec = meta.get("spec")
        if not spec or not spec.required_markers or not meta.get("prompt"):
            continue
        for marker in spec.required_markers:
            assert marker in meta["prompt"], \
                f"{name}: 閘門が「{marker}」を求めているのに prompt が指示していない"


def test_self_check_is_appended_to_every_stage_prompt(tmp_path: Path):
    for stage in prompts.STAGE_ORDER:
        if prompts.STAGES[stage].get("custom") == "deck":
            continue
        prompt = pipeline.build_prompt(_job(), stage, tmp_path, facts="")
        assert "提出前の自己点検" in prompt, f"{stage} に自己点検が無い"


def test_self_check_allows_fact_tags_when_jd_exists(tmp_path: Path):
    """Gate F の照合先は取得原文 ＋ JD。研究層を回さない stage でも JD があれば使える。"""
    prompt = pipeline.build_prompt(_job(), "hypotheses", tmp_path, facts="")
    assert "`[事実]` は使えない" not in prompt


# ---- 是正再生成は差分修正 ---------------------------------------------

def test_feedback_carries_previous_body():
    """指摘だけ渡すと白紙から書き直され、通っていた節が巻き添えで壊れる。"""
    result = gates.GateResult(ok=False, errors=["[Gate A] 見出しが無い"],
                              warnings=[])
    fb = result.as_feedback(previous="## 1. 前提\n本文です。")
    assert "<前回の本文>" in fb and "本文です。" in fb
    assert "そのまま再掲" in fb


def test_feedback_without_previous_stays_backward_compatible():
    result = gates.GateResult(ok=False, errors=["[Gate A] 見出しが無い"],
                              warnings=[])
    assert "<前回の本文>" not in result.as_feedback()


# ---- deck: 版面の容量を生成側へ渡す -----------------------------------

def test_capacity_hint_is_derived_from_measured_heights():
    """数値を手書きすると、実測に合わせて `_H` を直したときにここが古くなる。"""
    hint = deck.capacity_hint()
    assert str(deck._HEAD_BASE) in hint
    for key in ("steps", "lane", "tree", "phases", "table_row"):
        assert str(deck._H[key]) in hint, f"{key} の値が容量表に出ていない"


def test_capacity_examples_agree_with_the_gate():
    """「収まる」と書いた組み合わせが実際に予算内か（例が嘘だと逆効果）。"""
    budget = deck._HEAD_BASE
    fits = {
        "flow+lanes2": deck._H["steps"] + deck._H["lane"] * 2,
        "table5": deck._H["table_head"] + deck._H["table_row"] * 5,
        "table4+footnote": (deck._H["table_head"] + deck._H["table_row"] * 4
                            + deck._H["footnote"]),
        "arch3": (deck._H["layer"] + deck._H["layer_note"]) * 3,
        "tree+footnote": deck._H["tree"] + deck._H["footnote"],
    }
    for name, h in fits.items():
        assert h <= budget, f"{name} は収まると書いてあるが {h}px > {budget}px"
    overflows = {
        "table6": deck._H["table_head"] + deck._H["table_row"] * 6,
        "flow+lanes2+footnote": (deck._H["steps"] + deck._H["lane"] * 2
                                 + deck._H["footnote"]),
    }
    for name, h in overflows.items():
        assert h > budget, f"{name} は溢れると書いてあるが {h}px <= {budget}px"


def test_deck_prompt_includes_capacity_and_tagged_sources(tmp_path: Path):
    (tmp_path / prompts.STAGES["main_case"]["file"]).write_text(
        "本文", encoding="utf-8")
    prompt = deck.build_prompt(tmp_path)
    assert "本文領域の持ち点" in prompt
    assert prompt.count("<素材") == prompt.count("</素材>") == 5
