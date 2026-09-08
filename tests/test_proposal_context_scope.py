"""提案そのものを書く stage に本人のプロフィールを渡していないことの回帰テスト。

パックは 2 層に分かれる:

- **提案層**（company〜plan90, redteam）… 「この会社のこの製品に何をすべきか」。
  読み手が書き手を知らなくても成立しなければならない。プロフィールを入れると
  提案の説得力が「私はこれをやった」に寄りかかり、提案自体が弱くても通ってしまう。
  persona に至っては求める人物像と評価配点まで本人の形へ寄る（しかも後続 6 stage の
  入力なので汚染が全部へ伝播する）。
- **自己証明層**（cards, mapping）… 「その提案を書いた人間が実際に何をやってきたか」。
  ここだけがプロフィールを要る。

`with_profile` は元から context.build にあった引数だが、pipeline が一度も渡して
おらず既定の True のまま全 stage に入っていた（実測: prompt の 46〜57% が
プロフィール）。同じ事故を二度やらないための錠。
"""

from pathlib import Path

from proposal import context, pipeline, prompts

# 本人側の素材を渡してはいけない stage（提案層）
RELATIONAL_STAGES = ("company", "product", "persona", "hypotheses",
                     "main_case", "plan90", "redteam")

PROFILE_HEADING = "応募者プロフィール"


def _job() -> dict:
    return {"id": 1, "company": "テスト株式会社", "title": "PdM",
            "raw_jd": "プロダクトマネージャーを募集します。SaaS の要件定義。",
            "url": "https://example.com/jobs/1"}


def test_relational_stages_declare_no_profile():
    for stage in RELATIONAL_STAGES:
        assert prompts.STAGES[stage].get("needs_profile") is False, \
            f"{stage} は提案そのものを書く stage なので needs_profile=False であること"


def test_relational_stage_prompts_exclude_profile(tmp_path: Path):
    for stage in RELATIONAL_STAGES:
        prompt = pipeline.build_prompt(_job(), stage, tmp_path, facts="")
        assert PROFILE_HEADING not in prompt, \
            f"{stage} の prompt に本人プロフィールが混入している"


def test_self_referential_stages_keep_profile(tmp_path: Path):
    """本人の実例を書かせる stage は逆に渡っていないと閘門が通らない。"""
    for stage in ("cards", "mapping"):
        prompt = pipeline.build_prompt(_job(), stage, tmp_path, facts="")
        assert PROFILE_HEADING in prompt, \
            f"{stage} は本人の実例を書く stage なのでプロフィールが要る"


def test_persona_sections_have_no_self_positioning():
    """「本人の立ち位置」節は mapping §1/§2 と重複。persona からは外してある。"""
    joined = "".join(prompts.PERSONA_SECTIONS)
    assert "本人" not in joined
    assert len(prompts.PERSONA_SECTIONS) == 5


# 提案層の文書は「製品にとって正しいか」で立たせる。選考の語彙が混ざると
# 「面接で落ちない書き方」へ最適化され、提案そのものの質は問われなくなる
# （読み手を面接官だと思っている提案書は、社内の意思決定者には刺さらない）。
# persona は例外 — あの stage の分析対象が選考そのものなので語彙が要る
_SELECTION_VOCABULARY = ("面接官", "候補者", "採用しない", "減点", "面接で落ちる")

# 提案の根拠を書き手の経歴に求めさせる指示。これがあると提案が弱くても
# 「私はこれをやった」で通ってしまう
_PERSONAL_TRACK_RECORD = ("本人が実際にやったこと", "本人が過去に同じ",
                          "本人の近い経験", "本人が過去に同じ入り方")

PROPOSAL_PROMPTS = {
    "hypotheses": prompts.HYPO_PROMPT,
    "main_case": prompts.MAIN_PROMPT,
    "plan90": prompts.PLAN_PROMPT,
    "redteam": prompts.REDTEAM_PROMPT,
}


def test_proposal_prompts_have_no_selection_framing():
    """提案層は製品への提案。選考の語彙で書かせない。"""
    for name, prompt in PROPOSAL_PROMPTS.items():
        found = [w for w in _SELECTION_VOCABULARY if w in prompt]
        assert not found, f"{name} に選考の語彙が残っている: {found}"


def test_proposal_prompts_do_not_ask_for_personal_track_record():
    """提案の根拠は製品の構造と打ち手の理屈。経歴で補強させない。"""
    for name, prompt in PROPOSAL_PROMPTS.items():
        found = [w for w in _PERSONAL_TRACK_RECORD if w in prompt]
        assert not found, f"{name} が書き手の経歴を要求している: {found}"


def test_redteam_reviews_only_the_proposal():
    """cards / mapping を渡すと査読が「経験が足りない」方向へ流れる。

    禁じているのは**書き手側の文書**であって、素材の量ではない。製品の事実
    （product）はむしろ渡す — v5 まで査読側が製品を知らず、指摘が
    「仮説として〜の可能性を疑います」と推測になっていた。
    """
    deps = prompts.STAGES["redteam"]["deps"]
    assert "cards" not in deps and "mapping" not in deps
    assert "{prev_cards}" not in prompts.REDTEAM_PROMPT
    assert "{prev_mapping}" not in prompts.REDTEAM_PROMPT


def test_redteam_scoring_is_diagnostic_not_suppressed():
    """点を人為的に押し下げると、どの軸が弱いのか読めず --refine が壊れる。"""
    assert "軸ごとに差をつける" in prompts.REDTEAM_PROMPT
    assert "甘く付けない" not in prompts.REDTEAM_PROMPT


def test_redteam_requires_actionable_findings():
    """直し方の無い指摘は提案を良くしない（潰すだけの査読を防ぐ）。"""
    assert "直し方の書けない指摘はしないでください" in prompts.REDTEAM_PROMPT


def test_self_referential_prompts_keep_interview_framing():
    """逆に cards / mapping は面接で口に出す文書。選考の語彙が要る。"""
    assert "面接" in prompts.CARDS_PROMPT
    assert "面接" in prompts.MAPPING_PROMPT


def test_evidence_corpus_still_has_profile():
    """Gate B の数字素材は prompt とは別枠。外した stage でも捏造判定は緩まない。"""
    corpus = context.evidence_corpus(_job())
    assert corpus.strip(), "数字錨定の素材が空になっている"


def test_product_facts_reach_the_stages_that_write_the_proposal():
    """研究層で取った製品の事実は、提案を書く／査読する stage まで届くこと。

    v4 までの実測バグ（ある応募先の新規事業案件）: main_case の deps が
    persona+hypotheses だけで、素材鎖の中で唯一ここだけが後退していた。
    結果、`02_product.md` に「5万円からのデータ小口購入」があるのに主提案は
    「課金単位は要確認」と書き、査読で 事業理解 5/10・根拠 3/10。prompt の
    文言ではなく**配線**が原因なので、書き方をいくら直しても上がらない。
    """
    for stage in ("main_case", "redteam"):
        deps = prompts.STAGES[stage]["deps"]
        assert "product" in deps, (
            f"{stage} が製品の事実を見ずに書いている（deps={deps}）"
        )
        assert "{prev_product}" in prompts.STAGES[stage]["prompt"], (
            f"{stage} の prompt が prev_product を差し込んでいない"
        )


def test_proposal_moves_are_written_as_a_diff_from_today():
    """打ち手は現況からの差分。現況欄が無いと既存機能の再提案を検出できない。"""
    assert "今はどうなっているか" in prompts.MAIN_PROMPT
    assert "今はどうなっているか" in prompts.STAGES["main_case"]["spec"].required_markers
