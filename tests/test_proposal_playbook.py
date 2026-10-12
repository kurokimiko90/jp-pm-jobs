"""能力プレイブック stage（`playbook`）の回帰テスト。

cards / mapping が「本人が過去に何をしたか」を書く文書なのに対し、playbook は
**その能力を発揮する仕事が実務でどう動くか**（仕事の型）を書く。v6.1 まで本人の
実績を「具体例」に書かせていて 3 枚目の焼き直しになっていたので、v6.2 で本人の
素材を外し（`needs_profile: False`・deps は product）、対象企業の製品領域で
組み立てた「想定シナリオ」へ置き換えた。

ここで固定するのは 4 点:
- stage 定義が STAGE_ORDER / LAYERS / capabilities 注入の配線に正しく載っているか
- **本人の経歴が prompt に入らない**こと（禁止文ではなく素材の非注入で担保）
- Gate A（block_required）が prompt の出力形式と一致しているか
- REVIEW_FILE の番号繰り下げ（11→12）で playbook（11）と番号が衝突していないか
"""

from pathlib import Path

from proposal import gates, pipeline, prompts

PERSONA_FILE = prompts.STAGES["persona"]["file"]
CARDS_FILE = prompts.STAGES["cards"]["file"]
JOB = {"id": 1, "company": "テスト株式会社", "title": "PdM",
       "raw_jd": "## 職務内容\nプロダクトマネージャーを募集します。",
       "url": "https://example.com/jobs/1"}


# ------------------------------------------------------------- stage 定義

def test_playbook_is_registered_after_deck():
    assert "playbook" in prompts.STAGES
    assert prompts.STAGE_ORDER.index("deck") < prompts.STAGE_ORDER.index("playbook")


def test_playbook_is_in_interview_layer():
    assert "playbook" in prompts.LAYERS["interview"]


def test_playbook_depends_on_persona_and_product():
    """想定シナリオは製品の構造から組み立てる。cards を渡すと経歴の焼き直しへ寄る。"""
    assert prompts.STAGES["playbook"]["deps"] == ["persona", "product"]


def test_playbook_never_receives_the_candidate_profile():
    """「経歴を書くな」の禁止文より、素材として渡さない方が確実。"""
    assert prompts.STAGES["playbook"]["needs_profile"] is False


def test_playbook_prompt_has_no_profile_material(tmp_path):
    """prompt に応募者プロフィールのタグが出ないこと（機械的な保証）。"""
    prompt = pipeline.build_prompt(JOB, "playbook", tmp_path, facts="")
    assert "応募者プロフィール" not in prompt


def test_playbook_file_number_does_not_collide_with_review_file():
    """11 のまま繰り下げないと、README の行番号とファイル名の対応が崩れる。"""
    playbook_file = prompts.STAGES["playbook"]["file"]
    assert playbook_file != prompts.REVIEW_FILE
    assert playbook_file.split("_", 1)[0] != prompts.REVIEW_FILE.split("_", 1)[0]
    assert prompts.REVIEW_FILE == "12_review_report.md"


# ------------------------------------------------------- Gate A（block_required）

def test_block_required_keys_appear_in_the_prompt_output_format():
    """機械が要求する 5 項目は、prompt がその書式を教えていなければ通らない。"""
    spec = prompts.STAGES["playbook"]["spec"]
    for group in spec.block_required:
        assert any(kw in prompts.PLAYBOOK_PROMPT for kw in group), \
            f"prompt が「{group[0]}」の書式を指示していない"


def test_gate_a_flags_missing_block_field():
    spec = prompts.STAGES["playbook"]["spec"]
    text = ("### 事業KPIを持ってプロダクトを成長させる力\n"
            "- **PM実務での表れ方**: 説明\n"
            "- **想定シナリオ**: 説明\n"
            "- **進め方**: 説明\n"
            "- **注意点**: 説明\n")  # 思考ロジックが無い
    result = gates.check(text, spec, JOB, corpus="")
    assert any("思考ロジック" in e for e in result.errors)


def test_gate_a_passes_when_all_six_fields_present():
    spec = prompts.STAGES["playbook"]["spec"]
    text = ("### 事業KPIを持ってプロダクトを成長させる力\n"
            "- **PM実務での表れ方**: 説明\n"
            "- **想定シナリオ**: 説明\n"
            "- **進め方**: 説明\n"
            "- **注意点**: 説明\n"
            "- **思考ロジック**: 説明\n"
            "- **選考での問われ方と答えの論点**: 説明\n") * 25  # min_chars/min_lines を満たす反復
    result = gates.check(text, spec, JOB, corpus="")
    assert not [e for e in result.errors if "Gate A" in e]


def test_gate_a_flags_missing_verification_question_row():
    """v6 の初版が §4 に 1 枚も答えなかったので、行の存在を機械で要求する。"""
    spec = prompts.STAGES["playbook"]["spec"]
    text = ("### 事業KPIを持ってプロダクトを成長させる力\n"
            "- **PM実務での表れ方**: 説明\n"
            "- **想定シナリオ**: 説明\n"
            "- **進め方**: 説明\n"
            "- **注意点**: 説明\n"
            "- **思考ロジック**: 説明\n")  # 答えの論点が無い
    result = gates.check(text, spec, JOB, corpus="")
    assert any("答えの論点" in e for e in result.errors)


# ------------------------------------------------- 分量の下限（能力数が最小のときから逆算）

def test_min_lines_does_not_kill_a_compact_but_complete_pack():
    """実測 4407: 48 行の良い出力を min_lines=55 が落とし、再生成を 1 回捨てた。

    min_lines は「1 段落にべた書き」を弾く下限であって密度の判定ではない
    （密度は min_chars の担当）。能力 5 枚 × 7 行 = 35 を下回らせない。
    """
    spec = prompts.STAGES["playbook"]["spec"]
    assert spec.min_lines <= 35, "能力数が最小(5枚)の完成品を落とす位置にある"


def test_min_chars_is_below_the_five_card_floor():
    """1 枚 ≒700 字 × 最小 5 枚 = 3500 字。ここを超える敷居は誤殺する。"""
    spec = prompts.STAGES["playbook"]["spec"]
    assert spec.min_chars < 3500


# ------------------------------------------- persona §4（選考で確かめられること）の受け渡し

def _write_persona(pdir: Path) -> None:
    (pdir / PERSONA_FILE).write_text(
        "## 3. 評価される能力（優先順位つき）\n\n"
        "| 順位 | 能力 | JD 上の根拠 | なぜこの順位か |\n"
        "|---:|---|---|---|\n"
        "| 1 | 事業KPIを持ってプロダクトを成長させる力 | 「引用」 | 必須だからです |\n"
        "\n## 4. 選考で必ず確かめられること\n\n"
        "- あなたが直接責任を負ったKPIは何で、なぜその指標を選びましたか。\n\n"
        "- KPIが伸びなかったとき、どのデータを確認し、何を中止しましたか。\n\n"
        "## 5. 選考の評価モデル\n\n| 評価軸 | 配点 |\n|---|---:|\n| KPI | 30 |\n",
        encoding="utf-8")


def test_verification_questions_parsed_from_persona_section4(tmp_path: Path):
    _write_persona(tmp_path)
    qs = pipeline.verification_questions(tmp_path)
    assert qs == ["あなたが直接責任を負ったKPIは何で、なぜその指標を選びましたか。",
                  "KPIが伸びなかったとき、どのデータを確認し、何を中止しましたか。"]


def test_verification_questions_stops_at_next_section(tmp_path: Path):
    """§5 の評価モデル表を巻き込むと、prompt に配点表が二重に載る。"""
    _write_persona(tmp_path)
    assert not any("配点" in q or "評価軸" in q
                   for q in pipeline.verification_questions(tmp_path))


def test_verification_questions_empty_without_persona(tmp_path: Path):
    assert pipeline.verification_questions(tmp_path) == []


def test_verification_questions_are_injected_into_the_prompt(tmp_path: Path):
    _write_persona(tmp_path)
    prompt = pipeline.build_prompt(JOB, "playbook", tmp_path, facts="")
    assert "あなたが直接責任を負ったKPIは何で" in prompt


def test_prompt_falls_back_when_section4_missing(tmp_path: Path):
    """§4 が読めなくてもフォーマット漏れを起こさず、退避文言を入れる。"""
    prompt = pipeline.build_prompt(JOB, "playbook", tmp_path, facts="")
    assert "{verification_questions}" not in prompt
    assert "自分で 1 つ置くこと" in prompt


def test_only_playbook_receives_verification_questions(tmp_path: Path):
    """cards に渡すと「面接で聞かれること」へ寄ってカードの役割が重複する。"""
    _write_persona(tmp_path)
    (tmp_path / prompts.STAGES["main_case"]["file"]).write_text("主提案", encoding="utf-8")
    prompt = pipeline.build_prompt(JOB, "cards", tmp_path, facts="")
    assert "選考で必ず確かめられること（人物像の分析から機械で抽出した問い）" not in prompt


# ------------------------------------------ 想定シナリオは経歴ではない（v6.2 の主眼）

def test_prompt_forbids_writing_the_candidate_history():
    """「私は〜しました」で書かれると、面接で話せない話を話せる話だと誤読する。"""
    assert "想定シナリオは本人の経歴ではない" in prompts.PLAYBOOK_PROMPT
    assert "〜という状況を想定します" in prompts.PLAYBOOK_PROMPT


def test_prompt_forbids_inventing_numbers_even_in_a_scenario():
    """想定であっても数字を作れば Gate B に落ちるし、偽の精度は害しかない。"""
    assert "数字を作らない" in prompts.PLAYBOOK_PROMPT


def test_prompt_bans_reusing_the_same_scenario_across_cards():
    """8 枚が同じ場面の言い換えになると読む価値が消える。"""
    assert "同じシナリオを 2 枚以上のカードで使わない" in prompts.PLAYBOOK_PROMPT


def test_prompt_refuses_to_script_the_interview_answer():
    """想定シナリオで作った台本は、面接で話した瞬間に破綻する。"""
    assert "答えの台本はここに書かない" in prompts.PLAYBOOK_PROMPT
    assert prompts.STAGES["cards"]["file"] in prompts.PLAYBOOK_PROMPT
    assert prompts.STAGES["mapping"]["file"] in prompts.PLAYBOOK_PROMPT


# ------------------------------------------ Gate I（シナリオ使い回し・警告のみ）
#
# prompt が「〜という状況を想定します。PdM は〜」と書き方を指定しているので、
# 素の n-gram 一致で見ると**自分が指定した雛形**を重複の証拠として数える
# （実測 v6.2: 8 枚すべて別場面なのに 5 件の誤報）。雛形を踏んだ上で
# 「別場面なら鳴らない・同一場面なら鳴る」を両方固定する。

_TMPL = "- **想定シナリオ**: {}状況を想定します。PdMは、判断します。\n"


def _pack(*scenes: str) -> str:
    return "".join(f"### 能力{c}\n" + _TMPL.format(s)
                   for c, s in zip("ABCDEFGH", scenes))


_DUP = _pack("充電器では利用可能と表示されるのにアプリから開始できない",
             "充電器では利用可能と表示されるのにアプリから開始できない",
             "法人の担当者が従業員の利用状況を把握できない",
             "拠点ごとの稼働報告が遅れて意思決定に間に合わない")

_DISTINCT = _pack("充電器では利用可能と表示されるのに開始できない",
                  "法人の担当者が従業員の利用状況を把握できない",
                  "拠点ごとの稼働報告が遅れて意思決定に間に合わない",
                  "決済の失敗が特定の時間帯だけ増えて原因が分からない")


def test_gate_i_flags_the_same_scenario_across_cards():
    spec = prompts.STAGES["playbook"]["spec"]
    warns = [w for w in gates.check(_DUP, spec, JOB, corpus="").warnings
             if "Gate I" in w]
    assert len(warns) == 1, warns


def test_gate_i_ignores_the_template_the_prompt_itself_mandates():
    """雛形は全ブロックに出る。これを数えると誤報だらけで警告が読まれなくなる。"""
    spec = prompts.STAGES["playbook"]["spec"]
    assert not [w for w in gates.check(_DISTINCT, spec, JOB, corpus="").warnings
                if "Gate I" in w]


def test_gate_i_ignores_the_field_label_itself():
    """`- **想定シナリオ**: ` は全ブロック共通の飾りで中身ではない。"""
    spec = prompts.STAGES["playbook"]["spec"]
    for w in gates.check(_DISTINCT, spec, JOB, corpus="").warnings:
        assert "想定シナリオ" not in w.split("共通: ")[-1]


def test_gate_i_never_blocks():
    """製品の場面が限られる求人では「同じ機能の違う判断」が正解になる。

    機械には判断の違いを判定できないので、落とすと妥当な出力を書き直させて
    悪化させる。必ず warnings 側に置く。
    """
    spec = prompts.STAGES["playbook"]["spec"]
    assert not any("Gate I" in e
                   for e in gates.check(_DUP, spec, JOB, corpus="").errors)


def test_gate_i_is_off_unless_the_spec_opts_in():
    """他 stage（cards 等）へ勝手に効かせない。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          block_marker="###", check_numbers=False)
    assert not [w for w in gates.check(_DUP, spec, JOB, corpus="").warnings
                if "Gate I" in w]


def test_boilerplate_cutoff_keeps_a_lone_pair_detectable():
    """ブロックが 2 つのとき「過半数」= 2。下限 3 が無いと唯一のペアが消える。"""
    assert gates._boilerplate_df(2) == 3
    assert gates._boilerplate_df(8) == 5


def test_dup_ngram_is_above_the_measured_template_fragments():
    """12〜14 字は雛形の断片（「い状況を想定しますPdM」等）を拾って誤報した。"""
    assert gates.DUP_NGRAM >= 15


def test_gate_i_matches_the_measured_output_without_false_positives():
    """実データでの誤報ゼロを固定する（8 枚すべて別場面の実測パック）。"""
    candidates = sorted((Path(__file__).parent.parent / "output" / "proposal").glob("4407_*"))
    if not candidates:             # 個人データなので CI には無い
        return
    pack = candidates[0] / prompts.STAGES["playbook"]["file"]
    if not pack.exists():
        return
    spec = prompts.STAGES["playbook"]["spec"]
    result = gates.check(pack.read_text(encoding="utf-8"), spec, JOB, corpus="")
    assert not [w for w in result.warnings if "Gate I" in w]
