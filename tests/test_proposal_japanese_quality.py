"""proposal の日本語品質と deck 最終校閲の回帰テスト。"""

from proposal import deck, japanese_quality, prompts

JOB = {"id": 1, "company": "テスト株式会社", "title": "PdM",
       "raw_jd": "求人本文"}


def test_all_generation_prompts_use_japanese_it_terms():
    text = "\n".join(meta["prompt"] for meta in prompts.STAGES.values())
    text += "\n" + "\n".join(meta["title"] for meta in prompts.STAGES.values())
    text += "\n" + deck.PROMPT
    for literal_translation in ("資深", "職位", "版式", "会社原文", "実項目", "紅隊"):
        assert literal_translation not in text
    for expected in ("シニアPdM", "ポジション", "レイアウト",
                     "公式サイトから取得した原文", "具体的な実績", "レッドチーム"):
        assert expected in text


def test_common_rules_define_native_it_japanese():
    for expected in ("日本の IT 企業", "翻訳調", "要件定義", "ロードマップ",
                     "コンポーネントを「箱」と呼ばない"):
        assert expected in prompts.COMMON_RULES


def test_deck_e_rejects_literal_translation_and_suggests_replacement():
    fields = {"slides": [{"layout": "cover", "title": "資深PdMの職位提案"}]}
    issues = japanese_quality.lint(fields)
    assert any("[Deck E]" in issue and "シニア" in issue for issue in issues)
    assert any("ポジション" in issue for issue in issues)


def test_deck_e_accepts_natural_it_japanese():
    fields = {"deck": {"title": "プロダクト改善提案"}, "slides": [
        {"layout": "cover", "title": "検証結果をロードマップへ反映する"},
        {"layout": "closing", "title": "次に確認したいこと", "note": "確認します。"},
    ]}
    assert japanese_quality.lint(fields) == []


def test_safe_normalization_fixes_unambiguous_literal_terms_only():
    fields = {"slides": [{
        "layout": "cover",
        "title": "取得不能な実項目を確認する",
        "lead": "結合不能や運用負荷も確認する",
    }]}
    normalized, changes = japanese_quality.normalize_safe(fields)
    assert normalized["slides"][0]["title"] == "取得できない具体的な実績を確認する"
    # 文脈で助詞まで変える必要がある語は単純置換しない。
    assert "結合不能" in normalized["slides"][0]["lead"]
    assert changes


def test_deck_check_runs_language_gate_by_default():
    fields = {"slides": [
        {"layout": "cover", "title": "表紙"},
        {"layout": "closing", "title": "落地まで進めます", "note": "説明します。"},
    ]}
    errors = deck.check(fields, JOB, corpus="")
    assert any(error.startswith("[Deck E]") for error in errors)


def test_final_review_is_language_only_and_returns_full_fields():
    prompt = japanese_quality.REVIEW_PROMPT
    for expected in ("日本語だけの最終校閲", "layout、role、badge",
                     "情報を追加・削除しない", "校閲後の ``fields`` 全体"):
        assert expected in prompt


def test_review_contract_rejects_structure_control_and_number_changes():
    before = {"slides": [{
        "layout": "table", "role": "jd_map", "title": "90日で検証する",
        "table": {"columns": ["要件", "対応"], "rows": [["PdM", "実績"]]},
    }]}

    changed_layout = {"slides": [{**before["slides"][0], "layout": "cards"}]}
    assert any("制御値" in issue
               for issue in japanese_quality.contract_issues(before, changed_layout))

    changed_rows = {"slides": [{**before["slides"][0], "table": {
        "columns": ["要件", "対応"], "rows": []}}]}
    assert any("要素数" in issue
               for issue in japanese_quality.contract_issues(before, changed_rows))

    changed_number = {"slides": [{**before["slides"][0], "title": "60日で検証する"}]}
    assert any("数字が変わった" in issue
               for issue in japanese_quality.contract_issues(before, changed_number))


def test_review_contract_allows_japanese_wording_only_change():
    before = {"slides": [{"layout": "cover", "title": "価値判定を実施します"}]}
    after = {"slides": [{"layout": "cover", "title": "顧客価値を検証する"}]}
    assert japanese_quality.contract_issues(before, after) == []


def test_parse_japanese_review_requires_complete_fields():
    valid = ('{"verdict":"PASS","changes":[],"fields":'
             '{"deck":{"title":"提案"},"slides":[{"layout":"cover"}]}}')
    fields, audit = deck._parse_japanese_review(valid)
    assert fields["slides"][0]["layout"] == "cover"
    assert audit["verdict"] == "PASS"

    fields, _ = deck._parse_japanese_review('{"verdict":"PASS","changes":[]}')
    assert fields == {}
