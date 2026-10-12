"""應募データの時間分布（日単位・時刻は使わない。純函數、FastAPI 非依存）。

- 週次：投遞週ごとの結果構成＋直近 4 週の滾動通過率（Wilson CI）
- 待ち日数：「投遞から N 日たってまだ結果が無い」応募が、最終的に書類を通る確率
- 面接密度：週ごとの面接件数（日程確定メール＋手動の next_event）

判定（pass / fail / pending …）は app_analytics.annotate() の outcome をそのまま使う。
ここで判定し直すと、同じページの他カードと数字が食い違う。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import app_analytics as aa

ROLLING_WEEKS = 4
# 待ち日数の観測点。1 日刻みだと各点の標本が数件になるので粗く取る
WAIT_CHECKPOINTS = [0, 3, 5, 7, 10, 14, 21]
BUSY_WEEK = 3
_OUTCOMES = ("pass", "fail", "pending", "withdrawn", "unknown")


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def week_start(d: date) -> date:
    """その週の月曜日。"""
    return d - timedelta(days=d.weekday())


def _week_range(first: date, last: date) -> list[date]:
    weeks, w = [], week_start(first)
    while w <= last:
        weeks.append(w)
        w += timedelta(days=7)
    return weeks


# ───────────────────────── 週次 ─────────────────────────

def build_weekly(annotated: list[dict], today: date | None = None) -> list[dict]:
    """投遞週ごとの件数（結果別）と、その週までの直近 4 週の通過率。

    1 週 15〜30 件では単週の通過率が跳ねるので、率は滾動で出す。直近の週は
    結果待ち（pending）が多く率が低く見える — pending を別に返すのはそのため。
    """
    today = today or date.today()
    by_week: dict[date, dict] = defaultdict(lambda: defaultdict(int))
    for r in annotated:
        d = _day(r["applied_at"])
        if d:
            by_week[week_start(d)][r["outcome"]] += 1
    if not by_week:
        return []

    weeks = _week_range(min(by_week), today)
    out = []
    for i, w in enumerate(weeks):
        counts = by_week.get(w, {})
        window: dict = defaultdict(int)
        for pw in weeks[max(0, i - ROLLING_WEEKS + 1): i + 1]:
            for k, v in by_week.get(pw, {}).items():
                window[k] += v
        rolling = aa._rate_entry(w.isoformat(), window, None)
        out.append({
            "week": w.isoformat(),
            "applied": sum(counts.values()),
            **{k: counts.get(k, 0) for k in _OUTCOMES},
            "rolling_rate": rolling["rate"], "rolling_ci_low": rolling["ci_low"],
            "rolling_ci_high": rolling["ci_high"], "rolling_decided": rolling["decided"],
            "rolling_insufficient": rolling["insufficient"],
        })
    return out


# ───────────────────────── 待ち日数 ─────────────────────────

def result_days(annotated: list[dict], cleaned_events: list) -> dict[int, int]:
    """job → 投遞日から最初の結果（applied 以外の事件）までの日数。

    事件の日付は P0 で実際の日（通知メール受信日 / 手入力の結果日）に揃えてある。
    """
    applied = {r["job_id"]: _day(r["applied_at"]) for r in annotated}
    first: dict[int, int] = {}
    for e in cleaned_events:
        jid = e["job_id"]
        if jid in first or e["status"] == "applied" or not applied.get(jid):
            continue
        d = _day(e["changed_at"])
        if d:
            first[jid] = max(0, (d - applied[jid]).days)
    return first


def build_wait_curve(annotated: list[dict], cleaned_events: list,
                     today: date | None = None) -> dict:
    """N 日たっても結果が無い応募の、最終的な書類通過率（N = WAIT_CHECKPOINTS）。

    結果が出た応募（pass / fail）のうち「結果まで N 日以上かかったもの」を母集団にする。
    まだ待っている応募（pending）は結果が分からないので率には入れず、
    「いま N 日以上待っている件数」として並べる（今の待ち案件をどう見るかの目安）。
    """
    today = today or date.today()
    days = result_days(annotated, cleaned_events)
    decided = [r for r in annotated if r["outcome"] in ("pass", "fail")]
    timed = [(days[r["job_id"]], r["outcome"]) for r in decided if r["job_id"] in days]
    waiting = [
        (today - d).days for r in annotated
        if r["outcome"] == "pending" and (d := _day(r["applied_at"]))
    ]
    points = []
    for n in WAIT_CHECKPOINTS:
        counts: dict = defaultdict(int)
        for dd, outcome in timed:
            if dd >= n:
                counts[outcome] += 1
        points.append({
            **aa._rate_entry(str(n), counts, None), "day": n,
            "waiting_now": sum(1 for w in waiting if w >= n),
        })
    pass_days = sorted(dd for dd, o in timed if o == "pass")
    fail_days = sorted(dd for dd, o in timed if o == "fail")
    return {
        "points": points,
        # 通過と不合格で結果が来るまでの日数が違うか（中央値）
        "median_pass_days": _median(pass_days), "median_fail_days": _median(fail_days),
        "missing_dates": len(decided) - len(timed),
    }


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    return float(values[mid]) if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


# ───────────────────────── 面接密度 ─────────────────────────

def build_interview_load(interviews: list[tuple[int, str]], today: date | None = None) -> dict:
    """週ごとの面接件数。`interviews` は (job_id, 'YYYY-MM-DD') の列（重複可）。

    同じ応募・同じ日は 1 件に数える（日程確定メールのリマインド・再送で重複する）。
    ⚠ 日程変更（リスケ）で日付が変わった場合は別の面接と区別できず 2 件に数える。
    """
    today = today or date.today()
    days = {(jid, d) for jid, s in interviews if (d := _day(s))}
    if not days:
        return {"weeks": [], "max": 0, "busy_weeks": 0}
    by_week: dict[date, int] = defaultdict(int)
    for _jid, d in days:
        by_week[week_start(d)] += 1
    last = max(max(by_week), week_start(today))
    this_week = week_start(today)
    weeks = [
        {"week": w.isoformat(), "n": by_week.get(w, 0),
         "when": "past" if w < this_week else "current" if w == this_week else "future"}
        for w in _week_range(min(by_week), last)
    ]
    return {
        "weeks": weeks, "max": max(w["n"] for w in weeks),
        # 週 BUSY_WEEK 件以上＝過密週（準備時間が取れない目安）
        "busy_weeks": sum(1 for w in weeks if w["n"] >= BUSY_WEEK), "busy_threshold": BUSY_WEEK,
    }
