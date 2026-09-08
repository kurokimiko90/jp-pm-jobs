"""dashboard/backend/app_analytics — 書類選考統計の判定・区間・分群ロジック。

統計層は「間違っても落ちない」種類のバグ（誤読を誘う数字を平然と返す）を
持ちやすいので、誤読パターンそのものを回帰テストとして固定する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

import app_analytics as aa  # noqa: E402


def _row(job_id=1, status="rejected", rejection_stage="shorui", channel="r-agent",
         applied_at="2026-07-01", tier="unknown", job_type="pdm", mentions_ai=1,
         score=70, recommend_score=75, employee_count=200, score_breakdown=None,
         notes=None, next_event=None, company="テスト株式会社"):
    return {
        "job_id": job_id, "company": company, "status": status,
        "rejection_stage": rejection_stage,
        "channel": channel, "applied_at": applied_at, "tier": tier, "job_type": job_type,
        "mentions_ai": mentions_ai, "score": score, "recommend_score": recommend_score,
        "employee_count": employee_count, "score_breakdown": score_breakdown,
        "notes": notes, "next_event": next_event,
    }


# ── Wilson 信頼区間 ────────────────────────────────────────────

def test_wilson_zero_events_still_has_width():
    """0/4 を「0%±0」と出すと「絶対通らない」と誤読する。上端は残るべき。"""
    low, high = aa.wilson_interval(0, 4)
    assert low == 0.0
    assert high > 30  # 実測 49.0

def test_wilson_all_events_not_certain():
    low, high = aa.wilson_interval(1, 1)
    assert high == 100.0
    assert low < 50  # n=1 の 100% は下端が大きく開く


def test_wilson_narrows_with_sample_size():
    small = aa.wilson_interval(2, 10)
    large = aa.wilson_interval(20, 100)
    assert (small[1] - small[0]) > (large[1] - large[0])


def test_wilson_empty_denominator():
    assert aa.wilson_interval(0, 0) == (0.0, 0.0)


# ── 書類選考の判定 ─────────────────────────────────────────────

def _ev(job_id, status, changed_at):
    return {"job_id": job_id, "status": status, "changed_at": changed_at}


def test_rejection_stage_implies_reached_that_stage():
    """一次面接で見送り = 書類は通っている。events が無くても通過扱い。"""
    reached = aa.reached_max_stage("rejected", "ichiji", [])
    assert reached == aa._STAGE_INDEX["recruiter"]
    assert aa.classify("rejected", "ichiji", reached) == "pass"


def test_shorui_rejection_is_fail():
    reached = aa.reached_max_stage("rejected", "shorui", [])
    assert aa.classify("rejected", "shorui", reached) == "fail"


def test_missing_stage_is_unknown_not_fail():
    """段階未記録を fail に寄せると通過率を過小評価する。分母から外す。"""
    reached = aa.reached_max_stage("rejected", None, [])
    assert aa.classify("rejected", None, reached) == "unknown"


def test_event_trail_used_only_when_stage_missing():
    """段階未記録なら events が唯一の手がかりなので採用する。"""
    reached = aa.reached_max_stage("rejected", None, ["applied", "recruiter"])
    assert aa.classify("rejected", None, reached) == "pass"


def test_recorded_rejection_stage_beats_event_trail():
    """人が記録した見送り段階 > クリック由来のイベント。
    書類落ちの案件に offer の残骸が残っていて「内定 3 件」になった事故の回帰テスト。"""
    reached = aa.reached_max_stage("rejected", "shorui", ["applied", "offer"])
    assert reached == aa._STAGE_INDEX["applied"]
    assert aa.classify("rejected", "shorui", reached) == "fail"


def test_misclick_burst_collapses_to_last_state():
    """ドロップダウン連打（数分で段階が飛び回る）は最後の状態だけ残す。"""
    events = [
        _ev(1, "recruiter", "2026-08-27 09:19:00"),
        _ev(1, "tech", "2026-08-27 09:19:30"),
        _ev(1, "onsite", "2026-08-27 09:19:40"),
        _ev(1, "offer", "2026-08-27 09:20:00"),
        _ev(1, "rejected", "2026-08-27 09:22:00"),
    ]
    cleaned = aa.drop_misclick_events(events)
    assert [e["status"] for e in cleaned] == ["rejected"]


def test_slow_transitions_are_kept():
    """数日空いた変更は本物の進捗。連打フィルタで消してはいけない。"""
    events = [
        _ev(1, "applied", "2026-08-01 10:00:00"),
        _ev(1, "recruiter", "2026-08-10 10:00:00"),
        _ev(1, "tech", "2026-08-20 10:00:00"),
    ]
    cleaned = aa.drop_misclick_events(events)
    assert [e["status"] for e in cleaned] == ["applied", "recruiter", "tech"]


def test_misclick_burst_does_not_inflate_funnel():
    """連打で最終面接・内定が水増しされた実データの回帰テスト。"""
    apps = [_row(job_id=1, status="rejected", rejection_stage=None)]
    events = [
        _ev(1, "applied", "2026-08-23 11:16:00"),
        _ev(1, "offer", "2026-08-27 09:20:00"),
        _ev(1, "applied", "2026-08-27 09:20:20"),
        _ev(1, "rejected", "2026-08-27 09:23:00"),
    ]
    annotated = aa.annotate(apps, aa.drop_misclick_events(events))
    funnel = {f["stage"]: f["n"] for f in aa.build_funnel(annotated)}
    assert funnel["offer"] == 0
    assert funnel["shorui_pass"] == 0


def test_current_status_is_authoritative_while_in_progress():
    reached = aa.reached_max_stage("offer", None, ["applied"])
    assert reached == aa._STAGE_INDEX["offer"]


def test_pending_excluded_from_denominator():
    reached = aa.reached_max_stage("applied", None, ["applied"])
    assert aa.classify("applied", None, reached) == "pending"


def test_summary_and_funnel_agree_on_passed_count():
    apps = [
        _row(job_id=1, status="rejected", rejection_stage="shorui"),
        _row(job_id=2, status="rejected", rejection_stage="ichiji"),
        _row(job_id=3, status="rejected", rejection_stage=None),  # events で救済
        _row(job_id=4, status="applied", rejection_stage=None),
    ]
    events = [_ev(3, "recruiter", "2026-08-10 10:00:00")]
    annotated = aa.annotate(apps, events)
    summary = aa.build_summary(annotated)
    funnel = {f["stage"]: f["n"] for f in aa.build_funnel(annotated)}
    assert summary["passed"] == 2  # job 2（ichiji 落ち）+ job 3（events 救済）
    assert funnel["shorui_pass"] == summary["passed"]
    assert summary["decided"] == 3  # pending は分母外、unknown は 0 件になった


def test_funnel_is_monotonic():
    """「オファー > 最終面接」のような壊れたファネルを出さない。"""
    apps = [_row(job_id=1, status="offer", rejection_stage=None)]
    annotated = aa.annotate(apps, [_ev(1, "offer", "2026-09-01 10:00:00")])
    counts = [f["n"] for f in aa.build_funnel(annotated)]
    assert counts == sorted(counts, reverse=True)


# ── 本人辞退（企業のお見送りとは別枠） ─────────────────────────

def test_withdrawal_keywords_cover_notation_variants():
    assert aa.is_self_withdrawn("内定(670万) → 本人辞退", None)
    assert aa.is_self_withdrawn(None, "自己拒絕了")
    assert aa.is_self_withdrawn("辭退", None)  # 繁体字
    assert not aa.is_self_withdrawn("r-agent 応募受付メール自動記録", None)
    assert not aa.is_self_withdrawn(None, None)


def test_early_withdrawal_excluded_from_denominator():
    """書類の結果が出る前の辞退を fail に混ぜると通過率を不当に下げる。
    実データで「書類選考で辞退」が企業に切られた扱いになっていた回帰テスト。"""
    apps = [
        _row(job_id=1, rejection_stage="shorui"),                      # 企業に見送られた
        _row(job_id=2, rejection_stage="shorui", notes="応募受付 / 辞退"),  # 本人が降りた
        _row(job_id=3, rejection_stage="ichiji"),                      # 書類は通った
    ]
    annotated = aa.annotate(apps, [])
    by_id = {a["job_id"]: a for a in annotated}
    assert by_id[2]["outcome"] == "withdrawn"

    summary = aa.build_summary(annotated)
    assert summary["failed"] == 1          # 辞退は fail に数えない
    assert summary["withdrawn_early"] == 1
    assert summary["decided"] == 2         # 1 passed + 1 failed
    assert summary["pass_rate"] == 50.0    # 辞退を fail にすると 33.3% になる


def test_withdrawal_after_passing_still_counts_as_pass():
    """面接まで進んでからの辞退は「書類は通った」事実を変えない。"""
    apps = [_row(job_id=1, status="offer", rejection_stage=None,
                 notes="内定 → 本人辞退")]
    annotated = aa.annotate(apps, [_ev(1, "offer", "2026-09-01 10:00:00")])
    summary = aa.build_summary(annotated)
    assert annotated[0]["outcome"] == "pass"
    assert summary["withdrawn_early"] == 0
    assert summary["withdrawn_after_pass"] == 1


def test_funnel_marks_withdrawal_at_the_stage_it_happened():
    """内定 1 件のうち 1 件が本人辞退、という状況を漏斗上で区別できること。"""
    apps = [
        _row(job_id=1, status="offer", rejection_stage=None, notes="内定 → 本人辞退"),
        _row(job_id=2, rejection_stage="ichiji", next_event="自己拒絕了"),
        _row(job_id=3, rejection_stage="shorui"),
    ]
    annotated = aa.annotate(apps, [_ev(1, "offer", "2026-09-01 10:00:00")])
    funnel = {f["stage"]: f for f in aa.build_funnel(annotated)}
    assert funnel["offer"]["n"] == 1 and funnel["offer"]["withdrawn"] == 1
    assert funnel["recruiter"]["withdrawn"] == 1   # 一次面接で降りた
    assert funnel["applied"]["withdrawn"] == 0     # 書類段階の辞退は無い


def test_rejection_dist_excludes_withdrawals():
    """「書類で不合格 N 件」と「お見送り段階：書類 N 件」は必ず一致する。
    辞退を片方だけ数えていて 128 vs 129 になっていた回帰テスト。"""
    apps = [
        _row(job_id=1, rejection_stage="shorui"),
        _row(job_id=2, rejection_stage="shorui", notes="辞退"),
        _row(job_id=3, rejection_stage="ichiji", next_event="自己拒絕了"),
    ]
    annotated = aa.annotate(apps, [])
    summary = aa.build_summary(annotated)
    shorui_rejections = sum(
        1 for r in annotated
        if r["status"] == "rejected" and not r["self_withdrawn"]
        and r["rejection_stage"] == "shorui"
    )
    assert shorui_rejections == summary["failed"] == 1


def test_withdrawal_excluded_from_segment_denominator():
    apps = (
        [_row(job_id=i, job_type="pdm", rejection_stage="shorui") for i in range(9)]
        + [_row(job_id=100, job_type="pdm", rejection_stage="shorui", notes="辞退")]
    )
    annotated = aa.annotate(apps, [])
    segs = {s["dimension"]: s for s in aa.build_segments(annotated, 10.0)}
    if "job_type" in segs:  # 1 バケットしか無ければ次元ごと落ちる
        pdm = next(i for i in segs["job_type"]["items"] if i["key"] == "pdm")
        assert pdm["decided"] == 9


# ── 分群（選択バイアスの是正） ──────────────────────────────────

def test_segment_reports_conditional_rate_not_composition():
    """通過者の 2/3 が PdM でも、PdM の通過率は 2/12 = 16.7%。
    組成比（66.7%）を返してはいけない。"""
    apps = (
        [_row(job_id=i, job_type="pdm", rejection_stage="shorui") for i in range(10)]
        + [_row(job_id=100 + i, job_type="pdm", rejection_stage="ichiji") for i in range(2)]
        + [_row(job_id=200, job_type="consulting", rejection_stage="ichiji")]
    )
    annotated = aa.annotate(apps, [])
    segs = {s["dimension"]: s for s in aa.build_segments(annotated, 20.0)}
    pdm = next(i for i in segs["job_type"]["items"] if i["key"] == "pdm")
    assert pdm["decided"] == 12
    assert pdm["rate"] == pytest.approx(16.7, abs=0.1)


def test_small_sample_flagged():
    apps = [_row(job_id=1, channel="green", rejection_stage="ichiji")]
    annotated = aa.annotate(apps, [])
    segs = {s["dimension"]: s for s in aa.build_segments(annotated, 20.0)}
    # channel は 1 バケットしかないので次元ごと落ちる（対比情報が無い）
    assert "channel" not in segs


def test_single_bucket_dimension_dropped():
    """全件同じ値の次元は「100% がその値」としか言えず、対比にならない。"""
    apps = [_row(job_id=i, channel="r-agent", rejection_stage="shorui") for i in range(5)]
    annotated = aa.annotate(apps, [])
    segs = {s["dimension"]: s for s in aa.build_segments(annotated, 20.0)}
    assert "channel" not in segs


def test_insufficient_items_sorted_last():
    """n=1 の 100% を先頭に置くと「最も通りやすい分群」に見える。標本十分な行を上に。"""
    apps = (
        [_row(job_id=i, channel="r-agent", rejection_stage="ichiji") for i in range(12)]
        + [_row(job_id=100, channel="green", rejection_stage="ichiji")]  # 1/1 = 100%
    )
    annotated = aa.annotate(apps, [])
    segs = {s["dimension"]: s for s in aa.build_segments(annotated, 90.0)}
    keys = [i["key"] for i in segs["channel"]["items"]]
    assert keys[0] == "r-agent"  # 100% の green より先
    assert segs["channel"]["items"][-1]["insufficient"] is True


def test_dimensions_ordered_by_effect_size():
    """効果量の大きい次元が先。アルファベット順だと弱い信号が上に来る。"""
    apps = (
        # job_type: 大きな差（pdm 0/10 vs pjm 10/10）
        [_row(job_id=i, job_type="pdm", mentions_ai=1, rejection_stage="shorui") for i in range(10)]
        + [_row(job_id=100 + i, job_type="pjm", mentions_ai=0, rejection_stage="ichiji") for i in range(10)]
        # mentions_ai は job_type と完全相関しないようノイズを足す
        + [_row(job_id=200 + i, job_type="pjm", mentions_ai=1, rejection_stage="shorui") for i in range(10)]
    )
    annotated = aa.annotate(apps, [])
    segs = aa.build_segments(annotated, 33.3)
    effects = [s["max_effect"] for s in segs]
    assert effects == sorted(effects, reverse=True)


def test_insufficient_flag_threshold():
    apps = (
        [_row(job_id=i, channel="r-agent", rejection_stage="shorui") for i in range(12)]
        + [_row(job_id=100 + i, channel="indeed", rejection_stage="shorui") for i in range(2)]
    )
    annotated = aa.annotate(apps, [])
    segs = {s["dimension"]: s for s in aa.build_segments(annotated, 10.0)}
    items = {i["key"]: i for i in segs["channel"]["items"]}
    assert items["r-agent"]["insufficient"] is False
    assert items["indeed"]["insufficient"] is True


def test_multi_label_domain_denominators_overlap():
    """1 件が fintech と ai の両方に入る＝分母が重複する。合計が母数を超えてよい。"""
    apps = [
        _row(job_id=1, rejection_stage="ichiji",
             score_breakdown='{"matched_domain": ["fintech", "ai"]}'),
        _row(job_id=2, rejection_stage="shorui",
             score_breakdown='{"matched_domain": ["fintech"]}'),
    ]
    annotated = aa.annotate(apps, [])
    segs = {s["dimension"]: s for s in aa.build_segments(annotated, 50.0)}
    domain = segs["domain"]
    assert domain["multi_label"] is True
    assert sum(i["decided"] for i in domain["items"]) > len(apps)


def test_broken_score_breakdown_falls_back_to_none():
    annotated = aa.annotate([_row(job_id=1, score_breakdown="{not json")], [])
    assert annotated[0]["domains"] == ["none"]


# ── 不確実データの上下界 ───────────────────────────────────────

def test_bounds_bracket_the_point_estimate():
    """段階不明を全て不通過／全て通過とした極値が、点推定を挟む。"""
    apps = [
        _row(job_id=1, rejection_stage="ichiji"),
        _row(job_id=2, rejection_stage="shorui"),
        _row(job_id=3, rejection_stage=None),
    ]
    summary = aa.build_summary(aa.annotate(apps, []))
    assert summary["unknown_stage"] == 1
    assert summary["bound_low"] <= summary["pass_rate"] <= summary["bound_high"]


def test_quality_reports_coverage():
    apps = [
        _row(job_id=1, tier="ai_startup", recommend_score=80, employee_count=100),
        _row(job_id=2, tier="unknown", recommend_score=None, employee_count=None),
    ]
    q = aa.build_quality(aa.annotate(apps, []))
    assert q["tier_coverage"] == 50.0
    assert q["recommend_coverage"] == 50.0
    assert q["employee_coverage"] == 50.0


def test_cohorts_grouped_by_application_month():
    apps = [
        _row(job_id=1, applied_at="2026-07-01", rejection_stage="ichiji"),
        _row(job_id=2, applied_at="2026-07-20", rejection_stage="shorui"),
        _row(job_id=3, applied_at="2026-08-02", rejection_stage="shorui"),
    ]
    cohorts = aa.build_cohorts(aa.annotate(apps, []), 30.0)
    by_month = {c["month"]: c for c in cohorts}
    assert by_month["2026-07"]["rate"] == 50.0
    assert by_month["2026-08"]["rate"] == 0.0
