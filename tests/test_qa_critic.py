"""interview/qa/critic の批評検証層のテスト（LLM を呼ばない層だけ）。

批評そのものを信じないための仕掛け — 引用照合・ラベル引き当て・改善判定 —
が効いているかだけを見る。LLM が何と言うかはここでは検査しない。
"""

import pytest

from interview.qa import critic
from interview.qa.render import Labelled


def _item(label: str, conclusion: str, points: list[str]) -> Labelled:
    return Labelled(label=label, question="問", conclusion=conclusion,
                    points=points, origin="jd")


ITEMS = [
    _item("C01 経歴", "ポイント基盤の統一を担当しました。",
          ["8 ブランドの接続を 1 つのテンプレートへ寄せました。",
           "検収の観点を 250 件に体系化しました。"]),
    _item("JD01 権限設計", "権限設計は運用から決めました。",
          ["現場が実際に踏む導線を先に描きました。"]),
]


# ------------------------------------------------------------ parse

def test_parse_extracts_json_from_code_fence():
    raw = """前置きの文章です。
```json
{"scores": {"具体性": 7, "質問応答": 8, "差別化": 4, "一貫性": 9, "接続": 6},
 "findings": [{"label": "C01 経歴", "quote": "a", "problem": "b", "fix": "c"}]}
```
後書き。"""
    scores, findings = critic.parse(raw)
    assert scores["具体性"] == 7
    assert len(findings) == 1


def test_parse_clamps_and_fills_axes():
    scores, _ = critic.parse('{"scores": {"具体性": 99, "差別化": -3, "未知軸": 5}}')
    assert scores["具体性"] == critic.MAX_AXIS
    assert scores["差別化"] == 0
    assert "未知軸" not in scores
    # 出てこなかった軸は満点扱いにしない（黙って甘く付けない）
    assert scores["接続"] == 0
    assert set(scores) == set(critic.AXES)


def test_parse_returns_empty_on_garbage():
    assert critic.parse("JSON ではありません") == ({}, [])


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

def test_total_normalizes_to_100():
    full = critic.Critique({axis: critic.MAX_AXIS for axis in critic.AXES}, [])
    assert full.total == 100
    half = critic.Critique({axis: 5 for axis in critic.AXES}, [])
    assert half.total == 50


def test_weakest_axes_are_the_lowest_two():
    scores = {"具体性": 9, "質問応答": 3, "差別化": 2, "一貫性": 8, "接続": 7}
    assert critic.Critique(scores, []).weakest == ["差別化", "質問応答"]


def test_passed_uses_target_score():
    scores = {axis: 8 for axis in critic.AXES}
    assert critic.Critique(scores, []).total == 80
    assert critic.Critique(scores, []).passed


@pytest.mark.parametrize("before,after,expect", [
    (60, 70, True),    # 上がった → 採用
    (60, 60, False),   # 動かない → 打ち切り（同じ所を回り続けない）
    (70, 55, False),   # 下がった → 前の版へ戻す
])
def test_improved(before, after, expect):
    mk = lambda n: critic.Critique({axis: n // 10 for axis in critic.AXES}, [])  # noqa: E731
    assert critic.improved(mk(before), mk(after)) is expect


def test_improved_accepts_none_as_first_round():
    assert critic.improved(None, critic.Critique({}, [])) is True


# ------------------------------------------------------------ 監査レポート

def test_render_report_shows_rounds_scores_and_dropped():
    history = [
        critic.Critique({axis: 6 for axis in critic.AXES},
                        [critic.Finding("C01 経歴", "引用", "抽象的", "一場面へ")],
                        dropped=2),
        critic.Critique({axis: 9 for axis in critic.AXES}, []),
    ]
    report = critic.render_report(history)
    assert "60" in report and "90" in report
    assert "抽象的" in report
    assert "2" in report  # 捨てた指摘の数を隠さない


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
        score = next(calls)
        return critic.Critique({axis: score // 10 for axis in critic.AXES},
                               [critic.Finding("C01 問", "引用", "弱い", "直す")])

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
    build_mod = _stub_loop(monkeypatch, [80, 90])
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
