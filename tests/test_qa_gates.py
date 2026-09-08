"""interview/qa の機械閘門と描画のテスト（LLM を呼ばない層だけ）。"""

from interview.qa import bank, build, gates, generate, render
from interview.qa.render import QA, Labelled

MATERIAL = """\
現職では 8 ブランドのポイント接続を 1 つの標準テンプレートへ統一した。
テストと検収の項目は 250 件を体系化した。前職 POS は 1 万店舗規模。
プロダクトマネジメントの経験は約 9 年。
"""


def _core(qid: str, question: str, points: list[str],
          conclusion: str = "結論です。") -> bank.CoreAnswer:
    return bank.CoreAnswer(qid=qid, category="pm", question=question,
                           conclusion=conclusion, points=points,
                           evidence=[], jd_dependent=False)


def _labelled(label: str, points: list[str], conclusion: str = "",
              origin: str = "jd") -> Labelled:
    return Labelled(label=label, question="問", conclusion=conclusion,
                    points=points, origin=origin)


# ------------------------------------------------------------ A 事実錨定

def test_fact_anchor_flags_number_absent_from_material():
    items = [_labelled("C01 テスト", ["導入工数を 47 %削減しました。"])]
    unsupported, _ = gates.fact_anchor(items, MATERIAL)
    assert "C01 テスト" in unsupported
    assert any("47" in token for token in unsupported["C01 テスト"])


def test_fact_anchor_accepts_number_present_in_material():
    items = [_labelled("C01 テスト", ["検収項目を 250 件に体系化しました。"])]
    unsupported, _ = gates.fact_anchor(items, MATERIAL)
    assert unsupported == {}


def test_fact_anchor_checks_the_conclusion_sentence_too():
    """結論文に置いた数字も検査対象（ここが抜けると年数の矛盾が素通りする）。"""

    items = [_labelled("C01 テスト", ["検収項目を 250 件にしました。"],
                       conclusion="約 12 年プロダクトマネジメントをしています。")]
    unsupported, _ = gates.fact_anchor(items, MATERIAL)
    assert "C01 テスト" in unsupported


def test_fact_anchor_flags_answer_without_any_concrete_thing():
    items = [_labelled("C02 抽象", ["丁寧に進めることを大事にしています。"])]
    _, thin = gates.fact_anchor(items, MATERIAL)
    assert thin == ["C02 抽象"]


# ------------------------------------------------------------ B 要件覆蓋

def test_requirement_coverage_reports_untouched_requirement():
    text = "ロードマップを四半期単位で更新しています。"
    uncovered, covered = gates.requirement_coverage(
        text, ["ロードマップ策定", "Kubernetes 運用経験"])
    assert covered == 1
    assert uncovered == ["Kubernetes 運用経験"]


def test_requirement_coverage_ignores_boilerplate_suffix():
    """「3年以上」の『年以上』だけが未一致で未覆蓋へ倒れないこと。"""

    text = "プロダクトマネジメントに約 9 年携わってきました。"
    uncovered, covered = gates.requirement_coverage(
        text, ["プロダクトマネジメント経験3年以上"])
    assert covered == 1
    assert uncovered == []


def test_requirement_coverage_accepts_the_abbreviation_used_in_answers():
    """要件は「プロダクトマネジメント経験」、答えは「PdM」と書くのが自然。"""

    text = "PdM として決済プロダクトを担当しています。"
    uncovered, covered = gates.requirement_coverage(
        text, ["プロダクトマネジメント経験"])
    assert covered == 1
    assert uncovered == []


def test_coverage_ignores_question_text_so_it_cannot_self_fulfil():
    """質問文に JD 語を書いただけでは覆蓋にしない（answers_text が本文だけを返す）。"""

    items = [_labelled("JD01 Kubernetes の経験は", ["経験はありません。"])]
    uncovered, covered = gates.requirement_coverage(
        render.answers_text(items), ["Kubernetes 運用経験"])
    assert covered == 0
    assert uncovered == ["Kubernetes 運用経験"]


# ------------------------------------------------------------ C 一貫性

def test_year_consistency_detects_conflict_and_points_at_the_minority():
    items = [
        _labelled("C01 経歴", ["PM 経験は 9 年です。"]),
        _labelled("C02 現職", ["PM 経験は 9 年になります。"]),
        _labelled("JD01 求人", ["PM 経験は 7 年です。"]),
    ]
    findings, offenders = gates.year_consistency(items)
    assert findings
    assert offenders == ["JD01 求人"]  # 多数派 9 年ではなく少数派だけを直す


def test_year_consistency_allows_distinct_future_horizons():
    items = [_labelled("C05 ビジョン", ["3 年で仕組みを作り、5 年で事業判断に関わりたいです。"])]
    findings, offenders = gates.year_consistency(items)
    assert findings == []
    assert offenders == []


def test_year_conflicts_are_repairable_not_just_reported():
    """年数の食い違いが blocking に入る（入らないと修復ループが空回りする）。"""

    report = gates.GateReport(year_conflicts=["「PM」の年数が複数: 7 / 9"],
                              year_offenders=["JD01 求人"])
    assert not report.ok
    assert "JD01 求人" in report.blocking


# ------------------------------------------------------------ D 素材偏り

def test_evidence_balance_flags_reused_numbers_across_questions():
    items = [_labelled(f"JD{i:02d} 問", ["250 件の検収項目を体系化しました。"])
             for i in range(1, 9)]
    overused, offenders = gates.evidence_balance(items)
    assert "250件" in overused
    assert offenders  # 使い回しだけで出来ている問は作り直し対象


def test_evidence_balance_does_not_rewrite_handwritten_core():
    items = [_labelled(f"C{i:02d} 問", ["250 件の検収項目を体系化しました。"], origin="core")
             for i in range(1, 9)]
    overused, offenders = gates.evidence_balance(items)
    assert "250件" in overused   # 報告はする
    assert offenders == []        # 手書きの正典は機械で作り直さない


def test_evidence_balance_keeps_questions_with_their_own_content():
    items = [_labelled(f"JD{i:02d} 問", ["250 件の検収項目を体系化しました。"])
             for i in range(1, 9)]
    items.append(_labelled("JD09 固有", [
        "250 件の検収に加え、1 万店舗規模の POS で 16 回のリリースを回しました。"]))
    _, offenders = gates.evidence_balance(items)
    assert "JD09 固有" not in offenders


# ------------------------------------------------- 締めの型（報告のみ）

def test_template_endings_reported_when_too_many():
    items = [_labelled(f"JD{i:02d} 問", ["場面です。", "御社でも活かせます。"])
             for i in range(1, 5)]
    items.append(_labelled("JD05 固有", ["場面です。", "家賃保証の審査フローを直します。"]))
    assert len(gates.template_endings(items)) == 4


def test_template_endings_quiet_when_within_ratio():
    items = [_labelled(f"JD{i:02d} 問", ["場面です。", "固有の締めです。"])
             for i in range(1, 10)]
    items.append(_labelled("JD10 型", ["場面です。", "御社でも活かせます。"]))
    assert gates.template_endings(items) == []


def test_template_endings_are_not_auto_repaired():
    """どれを削るかは人が決める。機械が勝手に作り直さない。"""

    report = gates.GateReport(template_endings=["JD01 問", "JD02 問"])
    assert report.blocking == []
    assert report.ok


# ------------------------------------------------------------ E 口語

def test_oral_style_flags_written_language():
    items = [_labelled("JD01 問", ["当該案件において品質向上に寄与しました。"])]
    findings = gates.oral_style(items)
    assert "JD01 問" in findings
    assert "当該" in findings["JD01 問"]


def test_written_style_is_repairable():
    report = gates.GateReport(written_style={"JD01 問": ["当該"]})
    assert not report.ok
    assert "JD01 問" in report.blocking


# ------------------------------------------------------ 機械修正（呼び直さない）

def test_clean_strips_the_preface_llm_always_adds():
    assert generate._clean("結論として、標準化の力です。") == "標準化の力です。"
    assert generate._clean("結論から言うと、御社に貢献できます。") == "御社に貢献できます。"


def test_clean_rewrites_kisha_to_onsha():
    assert generate._clean("貴社の事業に貢献します。") == "御社の事業に貢献します。"


def test_accept_conditions_carry_no_banned_terms():
    """禁止語を gateway の accept へ入れない（一括出力が丸ごと差し戻され 500 になる）。"""

    assert "notIncludes" not in generate._ACCEPT_BASE


# -------------------------------------------------------------- 重複排除

def test_jd_question_that_paraphrases_a_core_question_is_dropped():
    core_questions = ["複数の要望がある中で、優先順位をどのように決めますか"]
    items = [
        QA("複数の要望がある中で、優先順位をどのように決めますか？", "結論", ["点"], "jd"),
        QA("外国人利用者の本人確認で最初に確認することは何ですか", "結論", ["点"], "jd"),
    ]
    kept, dropped = build._drop_duplicates(items, core_questions)
    assert len(kept) == 1
    assert kept[0].question.startswith("外国人利用者")
    assert len(dropped) == 1


# ------------------------------------------------------------------ 描画

def test_render_emits_questions_and_answers_only():
    core = [_core("C01", "経歴を教えてください", ["1 万店舗の POS を担当しました。"])]
    jd = [QA("この職種の難所は何だと思いますか", "結論です。", ["要点です。"], "jd", "難所")]
    text = render.render("テスト株式会社", core, jd, [])

    assert text.startswith("# 想定問答 — テスト株式会社")
    # 見出しは `[キーワード] 質問文`。正典のキーワードは taxonomy 固定值
    assert "### Q. [経歴] 経歴を教えてください" in text
    assert "### Q. [難所] この職種の難所は何だと思いますか" in text
    assert "## この求人について" in text
    # 説明文・メタ情報を混ぜない
    assert "hiring-fit" not in text
    assert "共通底稿" not in text
    assert "<!--" not in text


def test_labels_match_between_gate_and_repair():
    core = [_core("C01", "経歴を教えてください", ["点"])]
    jd = [QA("JD の問", "結論", ["点"], "jd")]
    drill = [QA("深掘りの問", "結論", ["点"], "drilldown")]
    labels = [item.label for item in render.labelled(core, jd, drill)]

    assert "C01 経歴を教えてください" in labels
    assert "JD01 JD の問" in labels
    assert "DD01 深掘りの問" in labels
