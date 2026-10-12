"""dashboard/backend/app_time_stats — 時間分布（週次・待ち日数・面接密度）。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))

import app_time_stats as ts  # noqa: E402

TODAY = date(2026, 9, 18)  # 金曜


def _ann(job_id, applied_at, outcome):
    return {"job_id": job_id, "applied_at": applied_at, "outcome": outcome}


def _ev(job_id, status, day):
    return {"job_id": job_id, "status": status, "changed_at": day}


def test_week_start_is_monday():
    assert ts.week_start(date(2026, 9, 20)) == date(2026, 9, 14)  # 日曜 → その週の月曜


def test_weekly_fills_empty_weeks_and_rolls():
    ann = [_ann(1, "2026-08-31", "pass"), _ann(2, "2026-09-01", "fail"),
           _ann(3, "2026-09-16", "pending")]
    weeks = ts.build_weekly(ann, today=TODAY)
    assert [w["week"] for w in weeks] == ["2026-08-31", "2026-09-07", "2026-09-14"]
    assert weeks[1]["applied"] == 0            # 投遞の無い週も 0 で残す（軸が歪まない）
    assert weeks[2]["pending"] == 1
    assert weeks[2]["rolling_decided"] == 2    # 直近 4 週に 8/31 週の 2 件を含む
    assert weeks[2]["rolling_rate"] == 50.0


def test_wait_curve_conditions_on_days_without_result():
    ann = [_ann(1, "2026-09-01", "fail"), _ann(2, "2026-09-01", "fail"),
           _ann(3, "2026-09-01", "pass"), _ann(4, "2026-09-10", "pending")]
    events = [_ev(1, "applied", "2026-09-01"), _ev(1, "rejected", "2026-09-02"),   # 1 日で不合格
              _ev(2, "rejected", "2026-09-12"),                                    # 11 日
              _ev(3, "casual", "2026-09-08")]                                      # 7 日で通過
    curve = ts.build_wait_curve(ann, events, today=TODAY)
    pts = {p["day"]: p for p in curve["points"]}
    assert (pts[0]["passed"], pts[0]["decided"]) == (1, 3)
    assert (pts[3]["passed"], pts[3]["decided"]) == (1, 2)   # 3 日待っても来ない＝早い不合格は除外
    assert (pts[10]["passed"], pts[10]["decided"]) == (0, 1)
    assert pts[7]["waiting_now"] == 1                         # job4 は 8 日待ち（率には入れない）
    assert curve["median_pass_days"] == 7.0 and curve["median_fail_days"] == 6.0


def test_interview_load_dedupes_and_marks_weeks():
    load = ts.build_interview_load(
        [(1, "2026-09-08"), (1, "2026-09-08"), (2, "2026-09-09"), (3, "2026-09-10"),
         (4, "2026-09-29")], today=TODAY)
    weeks = {w["week"]: w for w in load["weeks"]}
    assert weeks["2026-09-07"]["n"] == 3 and weeks["2026-09-07"]["when"] == "past"
    assert weeks["2026-09-14"]["when"] == "current"
    assert weeks["2026-09-28"]["when"] == "future"
    assert load["busy_weeks"] == 1


def test_empty_inputs():
    assert ts.build_weekly([], today=TODAY) == []
    assert ts.build_interview_load([], today=TODAY)["weeks"] == []
