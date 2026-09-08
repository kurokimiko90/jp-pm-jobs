"""proposal/ の機械層のテスト（LLM を呼ばない部分だけ）。

閘門・錨定判定・能力抽出は「提案が信じられるか」の唯一の自動判定なので、
ここが黙って壊れると degraded に落ちず素通りする。
"""

from pathlib import Path

from proposal import frameworks_build as fb
from proposal import gates, pipeline, prompts, research

# ファイル名はここでは固定しない。番号を振り直すたびにテストが落ちるのは無意味
PERSONA_FILE = prompts.STAGES["persona"]["file"]

CORPUS = """\
8 つ以上のポイントブランド接続を 1 つの標準テンプレートに統一しました。
テストと受け入れ確認は 250 件以上を体系化しています。
プロダクトマネジメントの経験は約 9 年です。
直しが 3 回失敗したら手を止めて体系的なデバッグに切り替えるルールにしています。
"""

JOB = {"id": 1, "company": "テスト株式会社", "title": "PdM", "raw_jd": "求人本文"}


# ---------------------------------------------------------------- 錨定判定

def test_anchored_finds_verbatim_quote():
    corpus = fb._norm(CORPUS)
    quote = "直しが 3 回失敗したら手を止めて体系的なデバッグに切り替える"
    assert fb.anchored(quote, corpus) == 1.0


def test_anchored_rejects_invented_quote():
    corpus = fb._norm(CORPUS)
    quote = "全社の売上を 3 倍にする戦略を立案し実行まで完遂しました"
    assert fb.anchored(quote, corpus) < fb.MIN_ANCHOR


def test_anchored_rejects_too_short_quote():
    """短すぎる引用は照合の意味がないので 0 扱い（水増し防止）。"""
    assert fb.anchored("8 ブランド", fb._norm(CORPUS)) == 0.0


# ---------------------------------------------------- 同趣旨判定（votes の基盤）

def test_same_source_matches_overlapping_quotes():
    """命名が違っても根拠原文が重なれば同趣旨。votes はこれで数える。"""
    a = fb._norm("直しが 3 回失敗したら手を止めて体系的なデバッグに切り替える")
    b = fb._norm("3 回失敗したら手を止めて体系的なデバッグに切り替えるルール")
    assert fb.same_source(a, b)


def test_same_source_rejects_unrelated_quotes():
    a = fb._norm("8 つ以上のポイントブランド接続を 1 つの標準テンプレートに統一")
    b = fb._norm("直しが 3 回失敗したら手を止めて体系的なデバッグに切り替える")
    assert not fb.same_source(a, b)


def test_count_votes_counts_models_not_items():
    """同じモデルが似た項目を 2 つ出しても 1 票（多重投票させない）。"""
    item = {"evidence": {"quote": "3 回失敗したら体系的なデバッグに切り替える"}}
    drafts = {
        "codex": [{"evidence": {"quote": "直しが 3 回失敗したら体系的なデバッグに切り替える"}},
                  {"evidence": {"quote": "3 回失敗したら体系的なデバッグに切り替えるルール"}}],
        "gemini1": [{"evidence": {"quote": "8 つ以上のポイントブランド接続を統一"}}],
    }
    votes, voters = fb.count_votes(item, drafts)
    assert votes == 1 and voters == ["codex"]


# ------------------------------------------------------------------ Gate B

def test_gate_b_blocks_invented_numbers():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    text = "解約率を 42 % 改善した経験があります。"
    result = gates.check(text, spec, JOB, corpus=CORPUS)
    assert any(e.startswith("[Gate B]") for e in result.errors)


def test_gate_b_allows_numbers_present_in_material():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    text = "8 つ以上のブランド接続を標準化し、250 件の受け入れ確認を体系化しました。"
    result = gates.check(text, spec, JOB, corpus=CORPUS)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


def test_gate_b_ignores_section_numbering():
    """章番号や表の連番まで捏造扱いすると全文が永久に落ちる。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    text = "## 4. プロダクト構造\n| 1 | 行 |\n| 2 | 行 |"
    result = gates.check(text, spec, JOB, corpus=CORPUS)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


# ------------------------------------------------------------------ Gate C

def test_gate_b_ignores_period_expressions():
    """90 日計画の「31〜60 日目」は構造。ここを弾くと毎回 1 回無駄に再生成する。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    text = "| 1〜30 日目 | 調査 |\n| 31〜60 日目 | 試作 |\n| 61〜90 日目 | 限定提供 |"
    result = gates.check(text, spec, JOB, corpus=CORPUS)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


def test_gate_b_still_blocks_invented_numbers_next_to_periods():
    """期間表現を除外しても、実績の捏造数字は落ちること。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    text = "| 31〜60 日目 | 解約率を 42 % 改善した実績を活かします |"
    result = gates.check(text, spec, JOB, corpus=CORPUS)
    assert any("42" in e for e in result.errors if e.startswith("[Gate B]"))


def test_gate_b_ignores_dates():
    """表紙の生成日を捏造扱いすると deck が毎回再生成に落ちる（実測で 2 回無駄にした）。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    text = "作成日 2026-08-11 ／ 2026年8月11日時点の理解です"
    result = gates.check(text, spec, JOB, corpus=CORPUS)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


def test_gate_c_requires_hypothesis_marks():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          require_hypothesis_marks=True, check_numbers=False)
    text = "テスト株式会社のプロダクトは検索機能が弱いです。改善します。"
    result = gates.check(text, spec, JOB, corpus="", has_facts=False)
    assert any(e.startswith("[Gate C]") for e in result.errors)


def test_gate_c_passes_when_hypotheses_are_marked():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          require_hypothesis_marks=True, check_numbers=False)
    text = ("仮説：検索の導線が長いのではないかと考えています。\n"
            "もし現状がそうであれば、次の順で検証したいです。\n"
            "この点は面接で確認したいです。")
    result = gates.check(text, spec, JOB, corpus="", has_facts=False)
    assert not [e for e in result.errors if e.startswith("[Gate C]")]


def test_gate_c_warns_on_unhedged_company_assertion():
    """会社事実を渡していないのに社内の状態を断定 → 参考指摘（非ブロッキング）。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          require_hypothesis_marks=True, check_numbers=False)
    text = ("仮説：導線が長いと考えています。\nもし違っていれば修正します。\n"
            "確認したい点があります。\n"
            "テスト株式会社は現在オンボーディングを内製化しています。")
    result = gates.check(text, spec, JOB, corpus="", has_facts=False)
    assert any(w.startswith("[Gate C 参考]") for w in result.warnings)


# ------------------------------------------------------------------ Gate D

def test_gate_d_flags_missing_capability_card():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_capability_coverage=True, check_numbers=False)
    text = "### 顧客体験を一貫して設計する力\n- 本人の実績: あり"
    result = gates.check(text, spec, JOB, corpus="",
                         capabilities=["顧客体験を一貫して設計する力",
                                       "生成AIを顧客向け機能へ落とす力"])
    assert any(e.startswith("[Gate D]") for e in result.errors)


def test_gate_d_passes_when_all_capabilities_covered():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_capability_coverage=True, check_numbers=False)
    text = ("### 顧客体験を一貫して設計する力\n- 実績\n"
            "### 生成AIを顧客向け機能へ落とす力\n- 実績")
    result = gates.check(text, spec, JOB, corpus="",
                         capabilities=["顧客体験を一貫して設計する力",
                                       "生成AIを顧客向け機能へ落とす力"])
    assert not [e for e in result.errors if e.startswith("[Gate D]")]


# ------------------------------------------------------- 能力抽出（stage 間の受け渡し）

def test_capabilities_parses_persona_table(tmp_path: Path):
    (tmp_path / PERSONA_FILE).write_text(
        "## 3. 評価される能力（優先順位つき）\n\n"
        "| 順位 | 能力 | JD 上の根拠 | なぜこの順位か |\n"
        "|---:|---|---|---|\n"
        "| 1 | 顧客体験を設計する力 | 「引用」 | 必須だからです |\n"
        "| 2 | PMF へ導く力 | 「引用」 | 成果責任だからです |\n"
        "\n## 4. 次の見出し\n本文\n", encoding="utf-8")
    caps = pipeline.capabilities(tmp_path, {"id": 1})
    assert caps == ["顧客体験を設計する力", "PMF へ導く力"]


def test_capabilities_skips_header_and_separator_rows(tmp_path: Path):
    (tmp_path / PERSONA_FILE).write_text(
        "## 3. 評価される能力（優先順位つき）\n"
        "| 順位 | 能力 | 根拠 |\n|---|---|---|\n| 1 | 実装力 | 「引用」 |\n",
        encoding="utf-8")
    caps = pipeline.capabilities(tmp_path, {"id": 1})
    assert caps == ["実装力"]


# ------------------------------------------------- deck（版式の妥当性・投影密度）

def test_deck_rejects_overlong_bullet():
    from proposal import deck
    fields = {"slides": [{"layout": "cover", "title": "表紙"},
                         {"layout": "bullets", "title": "論点", "note": "話す",
                          "bullets": [{"text": "あ" * (deck.MAX_BULLET_CHARS + 5)}]},
                         {"layout": "closing", "title": "締め", "note": "話す"}]}
    errs = deck.check(fields, JOB, CORPUS)
    assert any(e.startswith("[Deck B]") for e in errs)


def test_deck_requires_cover_and_closing():
    from proposal import deck
    fields = {"slides": [{"layout": "bullets", "title": "本題", "note": "話す"}]}
    errs = deck.check(fields, JOB, CORPUS)
    assert any("cover" in e for e in errs) and any("closing" in e for e in errs)


def test_deck_rejects_unknown_layout():
    from proposal import deck
    fields = {"slides": [{"layout": "cover", "title": "表紙"},
                         {"layout": "carousel", "title": "謎", "note": "話す"},
                         {"layout": "closing", "title": "締め", "note": "話す"}]}
    errs = deck.check(fields, JOB, CORPUS)
    assert any("carousel" in e for e in errs)


def test_deck_render_escapes_html():
    """スライド本文は面接官に投影する。タグが素通りすると描画が壊れる。"""
    from proposal import deck_render
    html = deck_render.render({"deck": {"footer": "f"}, "slides": [
        {"layout": "bullets", "title": "<script>alert(1)</script>",
         "bullets": [{"text": "a & b"}]}]})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html and "a &amp; b" in html


# ------------------------------------------------------- 自己進化（履歴の集計）

def test_history_extracts_gate_ids():
    from proposal import history
    ids = history.gate_ids([
        "[Gate B] 素材に無い数字を書いている: ['42']",
        "[Deck A] 1 枚目は layout=cover にすること",
        "[Deck D] PII が残っている",
        "[Deck E] IT 日本語として不自然な表現がある",
        "指摘の形式になっていない行"])
    assert ids == ["Gate B", "Deck A", "Deck D", "Deck E"]


def test_history_stats_counts_first_pass(tmp_path, monkeypatch):
    from proposal import history
    monkeypatch.setattr(history, "PATH", tmp_path / "h.jsonl")
    job = {"id": 1, "company": "A"}
    history.record(job, "main_case", "v1", "ok", 1, [])
    history.record(job, "main_case", "v1", "ok", 2, ["[Gate B] x"])
    history.record(job, "main_case", "v1", "cached", 0, [])   # 集計対象外
    s = history.stats()["main_case@v1"]
    assert s["runs"] == 2 and s["first_pass_rate"] == 0.5


def test_lessons_render_empty_when_unbuilt(tmp_path, monkeypatch):
    """教訓庫が無いときに prompt へ空文字以外を混ぜない（毎回の prompt を汚さない）。"""
    from proposal import lessons
    monkeypatch.setattr(lessons, "PATH", tmp_path / "none.yaml")
    assert lessons.render() == ""


def test_gate_b_allows_numbers_from_our_own_instructions():
    """prompt が「最初の 90 日を 3 期に」と指示している以上、90 は捏造ではない。

    pipeline は corpus に prompt テンプレートを足してこれを通す（実測で 2 求人とも
    ここで 1 回無駄に再生成した）。
    """
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    corpus = CORPUS + "\n最初の 90 日を 3 期に分ける。"
    result = gates.check("最初の 90 日を 3 期に分けて進めます。", spec, JOB, corpus=corpus)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


def test_deck_validation_corpus_includes_upstream_stage_numbers(tmp_path):
    """deck が plan90 の期間を使っても、上流にある数字なら捏造扱いしない。"""
    from proposal import deck, prompts

    plan_file = prompts.STAGES["plan90"]["file"]
    (tmp_path / plan_file).write_text("61〜90 日に最初の施策を出す。", encoding="utf-8")
    corpus = deck._validation_corpus(tmp_path, "求人本文")
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    result = gates.check("61〜90 日に検証します。", spec, JOB, corpus=corpus)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


# ------------------------------------------- 漢数字による閘門回避（実測で踏んだ）

def test_gate_b_catches_invented_number_written_in_kanji():
    """Gate B に弾かれた再生成が数字を漢数字へ書き換えて素通りした事故の再発防止。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    result = gates.check("解約率を四十二％改善した実績があります。", spec, JOB, corpus=CORPUS)
    assert any(e.startswith("[Gate B]") for e in result.errors)


def test_gate_b_allows_kanji_number_present_in_material():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    result = gates.check("受け入れ確認を二百五十件体系化しました。", spec, JOB, corpus=CORPUS)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


def test_gate_b_ignores_kanji_words_that_are_not_quantities():
    """「一貫」「十分」まで数値化すると、無い数字を捏造扱いしてしまう。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    result = gates.check("一貫して十分に対応します。", spec, JOB, corpus=CORPUS)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


# ------------------------------------------ Gate F（`[事実]` の出所照合）

RESEARCH = """\
当社は中小企業向けのクラウド勤怠管理サービスを提供しています。
2019 年の提供開始以来、導入企業は 1200 社を超えました。
料金は従業員 1 人あたり月額 300 円の従量課金です。
"""


def test_gate_f_accepts_fact_quoted_from_research():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    text = "- [事実] 課金は従量制です。「従業員 1 人あたり月額 300 円の従量課金」"
    result = gates.check(text, spec, JOB, research=RESEARCH)
    assert not [e for e in result.errors if e.startswith("[Gate F]")]


def test_gate_f_rejects_fact_without_quote():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    result = gates.check("- [事実] 課金は従量制です。", spec, JOB, research=RESEARCH)
    assert any("出典の引用が無い" in e for e in result.errors)


def test_gate_f_rejects_quote_not_in_research():
    """原文に無いものを引用の形で書く — もっとも危ない捏造の型。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    text = "- [事実] 海外展開しています。「北米とアジアの 12 カ国で展開しています」"
    result = gates.check(text, spec, JOB, research=RESEARCH)
    assert any("見つからない" in e for e in result.errors)


def test_gate_f_forbids_fact_tag_when_research_empty():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    text = "- [事実] 課金は従量制です。「従業員 1 人あたり月額 300 円の従量課金」"
    result = gates.check(text, spec, JOB, research="")
    assert any("すべて `[仮説]` に" in e for e in result.errors)


def test_gate_f_ignores_hypothesis_lines():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    result = gates.check("- [仮説] 解約率が高いのではないでしょうか。", spec, JOB,
                         research=RESEARCH)
    assert not [e for e in result.errors if e.startswith("[Gate F]")]


# --------------------------------------- Gate G（経験マッピングの実例欄）

_MAP_HEAD = ("| JD 要件 | 求められる能力 | 本人の実例 | 数字の証拠 | 面接での言い方 |\n"
             "|---|---|---|---|---|\n")


def test_gate_g_flags_empty_experience_cell():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          mapping_evidence_col=3)
    text = _MAP_HEAD + "| 戦略策定 | 方向を決める力 | - | 数字なし | 話します |\n"
    result = gates.check(text, spec, JOB)
    assert any("実例が空欄" in e for e in result.errors)


def test_gate_g_flags_vague_experience_cell():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          mapping_evidence_col=3)
    text = _MAP_HEAD + "| 戦略策定 | 方向を決める力 | 豊富な経験 | 数字なし | 話します |\n"
    result = gates.check(text, spec, JOB)
    assert any("実例ではない" in e for e in result.errors)


def test_gate_g_allows_honest_no_experience():
    """「直接の実績なし」と正直に書いた行は落とさない（誤魔化しだけを落とす）。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          mapping_evidence_col=3)
    text = (_MAP_HEAD
            + "| 戦略策定 | 方向を決める力 | 直接の実績なし。近いのは受託の要件定義 "
              "| 数字なし | 正直に話します |\n")
    result = gates.check(text, spec, JOB)
    assert not [e for e in result.errors if e.startswith("[Gate G]")]


def test_gate_g_accepts_concrete_experience():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          mapping_evidence_col=3)
    text = (_MAP_HEAD
            + "| 標準化 | 仕組みを作る力 | 接続を 1 つの標準テンプレートに統一 "
              "| 8 ブランド | そのまま話せます |\n")
    result = gates.check(text, spec, JOB)
    assert not [e for e in result.errors if e.startswith("[Gate G]")]


# ------------------------------------------------ Gate H（紅隊の自己採点）

_SCORE_TABLE = "\n".join(
    f"| {axis} | {n}/10 | 理由 |" for n, axis in enumerate(gates.SCORE_AXES, 4))


def test_gate_h_requires_all_axes():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          require_score_table=True)
    partial = "| 事業理解 | 7/10 | 理由 |\n| 課題定義 | 6/10 | 理由 |"
    result = gates.check(partial, spec, JOB)
    assert any(e.startswith("[Gate H]") for e in result.errors)


def test_gate_h_passes_with_full_table():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          require_score_table=True)
    result = gates.check(_SCORE_TABLE, spec, JOB)
    assert not [e for e in result.errors if e.startswith("[Gate H]")]


def test_total_score_sums_all_axes():
    assert gates.total_score(_SCORE_TABLE) == sum(range(4, 4 + len(gates.SCORE_AXES)))


def test_total_score_is_none_when_incomplete():
    assert gates.total_score("| 事業理解 | 7/10 | 理由 |") is None


# --------------------------------------------------- 研究層（ネットワーク無し）

def test_research_classifies_page_kinds():
    assert research.classify("https://x.co.jp/company/about")[0] == "about"
    assert research.classify("https://x.co.jp/service/")[0] == "service"
    assert research.classify("https://x.co.jp/ir/library")[0] == "ir"
    assert research.classify("https://x.co.jp/misc")[1] == 0.0


def test_research_quote_corpus_extracts_only_raw_blocks(tmp_path: Path):
    """見出しや備考を照合先に含めると、自前の文言に引用が当たってしまう。"""
    research.raw_path(tmp_path).write_text(
        "# 会社研究 生素材 — テスト株式会社\n\n"
        "- 備考: 検索で官網を特定\n\n"
        "## 1. [about] 会社概要\n\n出典: https://x.co.jp/about\n\n"
        "```text\n当社は勤怠管理サービスを提供しています。\n```\n",
        encoding="utf-8")
    corpus = research.quote_corpus(tmp_path)
    assert "勤怠管理サービス" in corpus
    assert "備考" not in corpus and "出典" not in corpus


def test_research_render_says_so_when_nothing_fetched():
    text = research.render({"company": "テスト株式会社"}, research.ResearchResult())
    assert "取得結果: 0 ページ" in text
    assert "すべて仮説" in text


# ------------------ 実測で踏んだ誤判定（研究層の原文は人間向けの体裁で来る）

def test_gate_f_accepts_short_fact_quote():
    """「1.5兆円突破」は正規化すると窓幅 12 に届かない。窓幅方式のままだと
    原文にある引用を捏造扱いした（実際の応募先の官網で踏んだ）。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    corpus = "働く世代を中心に多くの方に選ばれています\n1.5兆円突破 ※2\n30万人ご利用中 ※3"
    result = gates.check("- [事実] 預かり資産の規模です。「1.5兆円突破」", spec, JOB,
                         research=corpus)
    assert not [e for e in result.errors if e.startswith("[Gate F]")]


def test_gate_f_still_rejects_too_short_quote():
    """短ければ何でも通る、ではない（2〜3 字の断片に識別力は無い）。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    result = gates.check("- [事実] 規模です。「兆円」", spec, JOB, research="2.1兆円突破")
    assert any(e.startswith("[Gate F]") for e in result.errors)


def test_gate_b_matches_letterspaced_numbers_in_source():
    """企業サイトは字間演出で「2 . 1 兆円」と書く。素材側だけがこの形だと、
    正しく引用した「2.1兆円」が捏造扱いになる（実測で踏んだ）。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    corpus = "預かり資産は 2 . 1 兆円突破、5 0 万人ご利用中です。"
    result = gates.check("預かり資産は 2.1 兆円で、50 万人が利用しています。",
                         spec, JOB, corpus=corpus)
    assert not [e for e in result.errors if e.startswith("[Gate B]")]


def test_gate_b_still_blocks_invented_number_with_spaced_source():
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0)
    corpus = "預かり資産は 2 . 1 兆円突破です。"
    result = gates.check("預かり資産は 9.9 兆円です。", spec, JOB, corpus=corpus)
    assert any("9.9" in e for e in result.errors if e.startswith("[Gate B]"))


def test_gate_f_ignores_fact_tags_inside_code_block():
    """構造図では引用が次の行へ折り返す。同一行に引用を求めると図が毎回落ちる。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    text = ("```text\n[事実] MAP AI\n「独自のAIとアルゴリズム」\n  │\n```\n"
            "- [事実] 中核は AI です。「独自のAIとアルゴリズム」")
    result = gates.check(text, spec, JOB, research="当社は独自のAIとアルゴリズムを使います")
    assert not [e for e in result.errors if e.startswith("[Gate F]")]


def test_gate_f_accepts_short_proper_noun_quote():
    """「生成AI」のような 4 字の固有名詞は正当な引用（実測で誤判定した）。"""
    spec = gates.GateSpec(required_sections=[], min_chars=0, min_lines=0,
                          check_fact_quotes=True)
    result = gates.check("- [事実] 対象は『生成AI』です。", spec, JOB,
                         research="「生成AI」を個人ユーザー向け機能として活用し")
    assert not [e for e in result.errors if e.startswith("[Gate F]")]


def test_stale_v1_files_detected(tmp_path: Path):
    """v1 のパックに再実行すると 01_persona.md と 01_company.md が並ぶ。"""
    (tmp_path / "01_persona.md").write_text("旧", encoding="utf-8")
    (tmp_path / "04_redteam.md").write_text("旧", encoding="utf-8")
    (tmp_path / prompts.STAGES["company"]["file"]).write_text("新", encoding="utf-8")
    stale = pipeline.stale_v1_files(tmp_path)
    assert stale == ["01_persona.md", "04_redteam.md"]


def test_stale_v1_files_empty_for_fresh_pack(tmp_path: Path):
    (tmp_path / prompts.STAGES["company"]["file"]).write_text("新", encoding="utf-8")
    assert pipeline.stale_v1_files(tmp_path) == []


def test_gate_feedback_carries_its_own_heading():
    """採点駆動の書き直しにも「閘門で不合格」と書いてしまう事故の防止。"""
    result = gates.GateResult(ok=False, errors=["[Gate A] 見出しが無い"], warnings=[])
    fb = result.as_feedback()
    assert fb.startswith("#") and "機械閘門で不合格" in fb
    assert gates.GateResult(ok=True, errors=[], warnings=[]).as_feedback() == ""


# --------------------------------- 採点駆動の書き直し（--refine の判断部分）

_REDTEAM_DOC = """\
# 紅隊レビュー

## 1. 致命的な弱点
- 課題の根拠が公開情報だけで、社内データを見ていません。
- 指標にベースラインがありません。

## 2. 事実確認が必要な箇所
- 省略

## 5. 採点

| 評価軸 | 点数 | 減点の理由 |
|---|---|---|
| 事業理解 | 5/10 | 理由 |
| 課題定義 | 4/10 | 理由 |
| 根拠 | 3/10 | 理由 |
| 解決策 | 4/10 | 理由 |
| 指標 | 3/10 | 理由 |
| 実行可能性 | 4/10 | 理由 |
| リスク認識 | 5/10 | 理由 |
"""


def _write_redteam(pdir: Path) -> None:
    (pdir / prompts.STAGES["redteam"]["file"]).write_text(_REDTEAM_DOC, encoding="utf-8")


def test_redteam_score_reads_pack(tmp_path: Path):
    _write_redteam(tmp_path)
    total, scores = pipeline.redteam_score(tmp_path)
    assert total == 28 and scores["根拠"] == 3


def test_redteam_score_none_when_missing(tmp_path: Path):
    assert pipeline.redteam_score(tmp_path) == (None, {})


def test_refine_feedback_targets_only_the_two_weakest_axes(tmp_path: Path):
    """指摘を全部渡すと「全部直す」方向へ薄く広がる。低い 2 軸に絞る。"""
    _write_redteam(tmp_path)
    total, scores = pipeline.redteam_score(tmp_path)
    fb = pipeline._refine_feedback(tmp_path, scores, total)
    assert "根拠（3/10）" in fb and "指標（3/10）" in fb
    assert "リスク認識" not in fb and "事業理解" not in fb


def test_refine_feedback_includes_weakness_section_not_the_whole_report(tmp_path: Path):
    _write_redteam(tmp_path)
    total, scores = pipeline.redteam_score(tmp_path)
    fb = pipeline._refine_feedback(tmp_path, scores, total)
    assert "社内データを見ていません" in fb        # 「1. 致命的な弱点」は渡す
    assert "事実確認が必要な箇所" not in fb        # それ以降は渡さない
    assert "28/70" in fb


def test_refine_feedback_does_not_claim_gate_failure(tmp_path: Path):
    """閘門は通っているので「閘門で不合格」と書いてはいけない。"""
    _write_redteam(tmp_path)
    total, scores = pipeline.redteam_score(tmp_path)
    assert "閘門" not in pipeline._refine_feedback(tmp_path, scores, total)


def test_refine_threshold_matches_observed_score():
    """実測 28/70 が書き直し対象になる位置に閾値があること。"""
    assert 28 < gates.REFINE_THRESHOLD <= 70


def test_existing_results_restores_untouched_stages(tmp_path: Path):
    """`--stage redteam` だけ回しても検証レポートが 1 行に痩せないこと。"""
    import json
    (tmp_path / "_cache").mkdir()
    for name in ("company", "product"):
        (tmp_path / prompts.STAGES[name]["file"]).write_text("本文", encoding="utf-8")
    (tmp_path / "_cache" / "company.json").write_text(
        json.dumps({"status": "degraded", "attempts": 3, "errors": ["[Gate A] x"]}),
        encoding="utf-8")
    restored = pipeline.existing_results(tmp_path, {"redteam"})
    names = [r.name for r in restored]
    assert names == ["company", "product"]              # STAGE_ORDER 順
    assert restored[0].status == "degraded" and restored[0].errors == ["[Gate A] x"]
    assert restored[1].status == "cached"               # キャッシュが無ければ cached 扱い


def test_existing_results_skips_stages_already_run(tmp_path: Path):
    (tmp_path / prompts.STAGES["company"]["file"]).write_text("本文", encoding="utf-8")
    assert pipeline.existing_results(tmp_path, {"company"}) == []


# ---------------------------------------------------------------- max_chars

def test_max_chars_ignores_the_front_matter():
    """「時間内に話せるか」の判定に、声に出さないタイトルと注記を数えない。"""
    from proposal.gates import GateSpec, _gate_a
    spec = GateSpec(required_sections=[], min_chars=0, min_lines=0, max_chars=20)
    body = "## 1. 見出し\nあいうえお。"
    assert _gate_a(body, spec) == []
    with_front = "# タイトル\n\n_生成日: 2026-08-17（長い注記がここに入る）_\n\n" + body
    assert _gate_a(with_front, spec) == []


def test_max_chars_still_catches_a_long_body():
    from proposal.gates import GateSpec, _gate_a
    spec = GateSpec(required_sections=[], min_chars=0, min_lines=0, max_chars=20)
    errs = _gate_a("## 1. 見出し\n" + "あ" * 40, spec)
    assert errs and "話せない" in errs[0]


def test_max_chars_zero_means_no_upper_bound():
    from proposal.gates import GateSpec, _gate_a
    spec = GateSpec(required_sections=[], min_chars=0, min_lines=0)
    assert _gate_a("## 見出し\n" + "あ" * 5000, spec) == []
