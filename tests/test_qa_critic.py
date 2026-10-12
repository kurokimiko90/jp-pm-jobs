"""interview/qa/critic の批評検証層のテスト（LLM を呼ばない層だけ）。

批評そのものを信じないための仕掛け — 引用照合・ラベル引き当て・改善判定 —
が効いているかだけを見る。LLM が何と言うかはここでは検査しない。
"""

import pytest

from interview.qa import critic
from interview.qa.render import Labelled


def _item(label: str, conclusion: str, points: list[str],
          category: str = "jd") -> Labelled:
    return Labelled(label=label, question="問", conclusion=conclusion,
                    points=points, origin="jd", category=category)


ITEMS = [
    _item("C01 経歴", "ポイント基盤の統一を担当しました。",
          ["8 ブランドの接続を 1 つのテンプレートへ寄せました。",
           "検収の観点を 250 件に体系化しました。"], category="career"),
    _item("JD01 権限設計", "権限設計は運用から決めました。",
          ["現場が実際に踏む導線を先に描きました。"]),
]


def _score(label: str, category: str, value: int) -> critic.ItemScore:
    """その問に適用される全軸へ同じ点を付けた ItemScore。"""
    return critic.ItemScore(label, category,
                            {axis: value for axis in critic.axes_for(category)})


def _critique(*scores: critic.ItemScore, findings=None, dropped=0) -> critic.Critique:
    return critic.Critique({s.label: s for s in scores}, findings or [], dropped)


# ------------------------------------------------------------ 採点軸

def test_axes_vary_by_category():
    """問が何を確かめたいかで軸が変わる — 全問同じ軸にしない。"""
    assert critic.axes_for("career") == ("具体性", "質問応答", "定着可能性", "意欲")
    assert critic.axes_for("closing") == ("具体性", "質問応答", "意欲")
    assert critic.axes_for("未知カテゴリ") == ("具体性", "質問応答", "専門性")


def test_every_axis_has_a_description():
    """prompt の軸定義から漏れた軸は LLM が採点できない。"""
    used = set(critic.UNIVERSAL_AXES) | set(critic.DEFAULT_CATEGORY_AXES)
    for axes in critic.CATEGORY_AXES.values():
        used |= set(axes)
    assert used <= set(critic.AXIS_DESC)


# ------------------------------------------------------------ parse

def test_parse_extracts_json_from_code_fence():
    raw = """前置きの文章です。
```json
{"items": [{"label": "JD01 権限設計", "scores": {"具体性": 7, "質問応答": 8, "専門性": 4}}],
 "findings": [{"label": "C01 経歴", "quote": "a", "problem": "b", "fix": "c"}]}
```
後書き。"""
    scores, findings = critic.parse(raw, ITEMS)
    assert scores["JD01 権限設計"].scores == {"具体性": 7, "質問応答": 8, "専門性": 4}
    assert len(findings) == 1


def test_parse_clamps_and_fills_axes():
    raw = ('{"items": [{"label": "JD01 権限設計",'
           ' "scores": {"具体性": 99, "質問応答": -3, "未知軸": 5}}]}')
    scores, _ = critic.parse(raw, ITEMS)
    item = scores["JD01 権限設計"]
    assert item.scores["具体性"] == critic.MAX_AXIS
    assert item.scores["質問応答"] == 0
    assert "未知軸" not in item.scores
    # 出てこなかった軸は満点扱いにしない（黙って甘く付けない）
    assert item.scores["専門性"] == 0


def test_parse_fills_missing_items_with_zero():
    """LLM が問を書き落としても、その問は 0 点で残る（合格扱いにしない）。"""
    raw = '{"items": [{"label": "JD01 権限設計", "scores": {"具体性": 9}}]}'
    scores, _ = critic.parse(raw, ITEMS)
    assert set(scores) == {"C01 経歴", "JD01 権限設計"}
    assert scores["C01 経歴"].total == 0


def test_parse_ignores_unknown_labels():
    """一覧に無い問の採点は捨てる（作り話の問を採点扱いしない）。"""
    raw = '{"items": [{"label": "X99 架空", "scores": {"具体性": 10}}]}'
    scores, _ = critic.parse(raw, ITEMS)
    assert "X99 架空" not in scores


def test_parse_on_garbage_returns_zero_scores_and_no_findings():
    scores, findings = critic.parse("JSON ではありません", ITEMS)
    assert findings == []
    assert all(item.total == 0 for item in scores.values())


# ------------------------------------------------------------ verify（引用照合）

def test_verify_drops_finding_whose_quote_is_absent():
    raw = [{"label": "C01 経歴", "quote": "12 か国へ展開しました。",
            "problem": "抽象的", "fix": "具体へ"}]
    findings, dropped = critic.verify(raw, ITEMS)
    assert findings == []
    assert dropped == 1


def test_verify_keeps_finding_with_real_quote_ignoring_whitespace():
    raw = [{"label": "C01 経歴", "quote": "8ブランドの接続を1つのテンプレートへ寄せました",
            "problem": "実績の羅列", "fix": "一場面へ書き直す"}]
    findings, dropped = critic.verify(raw, ITEMS)
    assert dropped == 0
    assert findings[0].label == "C01 経歴"


def test_verify_relabels_by_quote_when_label_is_wrong():
    """LLM はラベルをよく取り違える。引用が在る問へ引き当て直す。"""
    raw = [{"label": "存在しないラベル", "quote": "現場が実際に踏む導線を先に描きました。",
            "problem": "根拠が薄い", "fix": "判断の理由を足す"}]
    findings, dropped = critic.verify(raw, ITEMS)
    assert dropped == 0
    assert findings[0].label == "JD01 権限設計"


def test_verify_drops_finding_without_fix():
    """直し方の無い批評は是正に使えないので捨てる。"""
    raw = [{"label": "C01 経歴", "quote": "権限設計は運用から決めました。",
            "problem": "弱い", "fix": ""}]
    findings, dropped = critic.verify(raw, ITEMS)
    assert findings == []
    assert dropped == 1


def test_verify_caps_findings():
    raw = [{"label": "C01 経歴", "quote": "権限設計は運用から決めました。",
            "problem": f"問題 {i}", "fix": "直す"}
           for i in range(critic.MAX_FINDINGS + 5)]
    findings, _ = critic.verify(raw, ITEMS)
    assert len(findings) == critic.MAX_FINDINGS


# ------------------------------------------------------------ 採点と停止条件

def test_item_total_normalizes_to_100_over_its_own_axes():
    assert _score("C01 経歴", "career", critic.MAX_AXIS).total == 100
    assert _score("C01 経歴", "career", 5).total == 50
    # 軸数が違っても同じ尺度になる
    assert _score("JD01 権限設計", "jd", 5).total == 50


def test_pack_fails_if_any_single_item_is_below_target():
    """平均で誤魔化さない — 1 問でも合格線未満なら pack は不合格。"""
    good = _score("C01 経歴", "career", 10)
    bad = _score("JD01 権限設計", "jd", 8)
    critique = _critique(good, bad)
    assert critique.total == 90          # 平均は合格線に届いている
    assert not critique.passed
    assert [i.label for i in critique.failing] == ["JD01 権限設計"]


def test_pack_passes_when_all_items_reach_target():
    critique = _critique(_score("C01 経歴", "career", 9), _score("JD01 権限設計", "jd", 9))
    assert critique.passed


def test_empty_critique_is_not_passed():
    assert not critic.Critique().passed


def test_weakest_only_looks_at_failing_items_and_low_axes():
    failing = critic.ItemScore("JD01 権限設計", "jd", {"具体性": 9, "質問応答": 3, "専門性": 6})
    passing = _score("C01 経歴", "career", 10)
    assert _critique(failing, passing).weakest == ["質問応答", "専門性"]


@pytest.mark.parametrize("before,after,expect", [
    (6, 7, 10),     # 上がった
    (6, 6, 0),      # 動かない → 呼び出し側が停滞として数える
    (7, 5, -20),    # 下がった → 呼び出し側がこの巡を捨てる
])
def test_round_delta(before, after, expect):
    prev = _critique(_score("JD01 権限設計", "jd", before))
    cur = _critique(_score("JD01 権限設計", "jd", after))
    assert critic.round_delta(prev, cur, ["JD01 権限設計"]) == expect


def test_round_delta_ignores_items_not_rewritten():
    """書き直していない問の採点ブレは比較に入れない。"""
    prev = _critique(_score("C01 経歴", "career", 9), _score("JD01 権限設計", "jd", 5))
    cur = _critique(_score("C01 経歴", "career", 2), _score("JD01 権限設計", "jd", 6))
    assert critic.round_delta(prev, cur, ["JD01 権限設計"]) == 10


# ------------------------------------------------------------ 監査レポート

def test_render_report_shows_rounds_scores_and_dropped():
    history = [
        _critique(_score("JD01 権限設計", "jd", 6),
                  findings=[critic.Finding("JD01 権限設計", "引用", "抽象的", "一場面へ")],
                  dropped=2),
        _critique(_score("JD01 権限設計", "jd", 9)),
    ]
    report = critic.render_report(history)
    assert "**60**" in report and "**90**" in report
    assert "抽象的" in report
    assert "| 2 |" in report  # 捨てた指摘の数を隠さない


def test_render_report_lists_failing_items_and_reverted_round():
    history = [_critique(_score("JD01 権限設計", "jd", 7)),
               _critique(_score("JD01 権限設計", "jd", 5))]
    report = critic.render_report(history, adopted=1)
    assert "v1 ★採用" in report
    assert "合格線に届いていない問" in report
    assert "v2 は書き直しの対象問の合計点が下がったため捨て" in report


def test_render_report_empty_history():
    assert critic.render_report([]) == ""


# ------------------------------------------------------------ build の巡回

def _triple(text: str):
    """(core, jd_items, drill) の最小構成。text で版を見分ける。"""
    from interview.qa import bank
    from interview.qa.render import QA
    core = [bank.CoreAnswer(qid="C01", category="pm", question="問",
                            conclusion=text, points=[f"{text} の要点"],
                            evidence=[], jd_dependent=False)]
    return core, [QA("JD の問", text, [f"{text} の要点"], "pm", "鍵")], []


def _stub_loop(monkeypatch, scores: list[int]):
    """critic.review が呼ばれるたびに scores を順に返す。是正は版名だけ変える。"""
    from interview.qa import build as build_mod

    calls = iter(scores)

    def review(ctx, items):
        value = next(calls) // 10
        return critic.Critique(
            {i.label: _score(i.label, i.category, value) for i in items},
            [critic.Finding(items[0].label, "引用", "弱い", "直す")])

    monkeypatch.setattr(build_mod.critic, "review", review)
    monkeypatch.setattr(build_mod, "_refine_round",
                        lambda ctx, cr, core, jd, dr: _triple("改稿"))
    monkeypatch.setattr(build_mod.gates, "run",
                        lambda *a, **k: build_mod.gates.GateReport())
    return build_mod


def test_critique_loop_keeps_better_version(monkeypatch):
    build_mod = _stub_loop(monkeypatch, [50, 70, 90])
    core, jd, dr = _triple("初版")
    _, _, history, adopted = build_mod._critique_loop("ctx", "求人ctx", "素材", [], core, jd, dr)
    assert [c.total for c in history] == [50, 70, 90]
    assert adopted == 3


def test_critique_loop_reverts_when_score_drops(monkeypatch):
    build_mod = _stub_loop(monkeypatch, [70, 40])
    core, jd, dr = _triple("初版")
    best, _, history, adopted = build_mod._critique_loop("ctx", "求人ctx", "素材", [], core, jd, dr)
    assert [c.total for c in history] == [70, 40]
    assert adopted == 1                       # 下がった版は採らない
    assert best[0][0].conclusion == "初版"    # 元の版がそのまま残る


def test_critique_loop_stops_when_passed(monkeypatch):
    build_mod = _stub_loop(monkeypatch, [90, 100])
    core, jd, dr = _triple("初版")
    _, _, history, adopted = build_mod._critique_loop("ctx", "求人ctx", "素材", [], core, jd, dr)
    assert len(history) == 1                  # 合格線を越えたら書き直さない
    assert adopted == 1


def test_critique_loop_survives_review_failure(monkeypatch):
    from interview.qa import build as build_mod
    monkeypatch.setattr(build_mod.critic, "review", lambda ctx, items: None)
    monkeypatch.setattr(build_mod.gates, "run",
                        lambda *a, **k: build_mod.gates.GateReport())
    core, jd, dr = _triple("初版")
    best, _, history, adopted = build_mod._critique_loop("ctx", "求人ctx", "素材", [], core, jd, dr)
    assert history == [] and adopted == 0     # 批評できない ＝ 合格ではない
    assert best[0][0].conclusion == "初版"
