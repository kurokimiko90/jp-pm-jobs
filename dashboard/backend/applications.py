"""應募狀態管理：jobs.sqlite 中唯一允許寫入的表（applications）。

爬蟲資料（jobs 表）仍經 db.py 唯讀連線；本模組的 rw 連線只碰 applications。
"""
import re
import sqlite3

from fastapi import APIRouter, Body, HTTPException

from paths import DB_PATH
from tracker.db import (
    REJECTION_STAGES, channel_for_source, date_events_since, normalize_rejection_stage,
)

router = APIRouter(prefix="/api/applications")

STATUSES = ["applied", "casual", "recruiter", "tech", "onsite", "offer", "rejected"]
STATUS_LABELS = {
    "applied": "書類提出", "casual": "カジュアル面談", "recruiter": "一次面接", "tech": "二次面接",
    "onsite": "最終面接", "offer": "オファー", "rejected": "お見送り",
}

REJECTION_STAGE_LABELS = {
    "shorui": "書類選考", "casual": "カジュアル面談", "ichiji": "一次面接",
    "niji": "二次面接", "saishu": "最終面接",
}
REJECTION_REASONS = ["experience", "language", "age", "salary", "unspecified"]
REJECTION_REASON_LABELS = {
    "experience": "経験不足", "language": "語言", "age": "年齢帯",
    "salary": "薪資", "unspecified": "無說明",
}


def _rw() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    for col, coltype in (  # 冪等遷移；tracker 用具名欄位查詢，不受影響
        ("next_event", "TEXT"),
        ("rejection_stage", "TEXT"),
        ("rejection_reason", "TEXT"),
        ("gcal_event_id", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {coltype}")
            conn.commit()
        except sqlite3.OperationalError:
            pass
    try:  # channel 首次加欄時回填既有記錄為 r-agent（與 tracker.db._migrate 同步）
        conn.execute("ALTER TABLE applications ADD COLUMN channel TEXT")
        conn.execute("UPDATE applications SET channel = 'r-agent'")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    return conn


def _ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _startup_migrate() -> None:
    """啟動即跑遷移，避免唯讀端先查到不存在的欄位/表。"""
    conn = _rw()
    try:
        from tracker.db import (
            ensure_application_events, ensure_apply_requests, ensure_salary_replies,
        )

        ensure_application_events(conn)  # 選考歷程表 + trigger + 既有記錄回填
        ensure_salary_replies(conn)      # 年収回答メール記録（inbox.salary_reply）
        ensure_apply_requests(conn)      # 応募リクエスト記録（inbox.apply_request）
        conn.commit()
    except sqlite3.OperationalError:
        pass  # DB 尚未初始化（setup.sh 未跑）；與既有欄位遷移同樣容錯
    finally:
        conn.close()


_startup_migrate()


@router.get("/meta")
def meta():
    return {
        "statuses": STATUSES, "labels": STATUS_LABELS,
        "rejection_stages": REJECTION_STAGES, "rejection_stage_labels": REJECTION_STAGE_LABELS,
        "rejection_reasons": REJECTION_REASONS, "rejection_reason_labels": REJECTION_REASON_LABELS,
    }


@router.get("/list")
def list_all():
    """所有應募記錄 + 職缺資訊，rejected 沉底，其餘按最近更新排序。"""
    from datetime import date

    from tools.followup import CADENCE_DAYS

    conn = _ro()
    try:
        rows = conn.execute(
            "SELECT a.job_id, a.status, a.applied_at, a.last_updated, a.next_event, a.notes, "
            "a.rejection_stage, a.rejection_reason, a.channel, "
            "j.title, j.company, j.score, j.tier, j.source, j.url, j.posting_type, "
            "j.employee_count, j.mentions_ai, j.lang_env, j.company_norm, cr.openwork_score, cr.openwork_url, "
            "CAST(json_extract(j.gap_analysis, '$.recommend_score') AS INTEGER) recommend_score "
            "FROM applications a JOIN jobs j ON j.id = a.job_id "
            "LEFT JOIN company_ratings cr ON cr.company_name = j.company "
            "WHERE COALESCE(j.blacklisted, 0) = 0 "
            "ORDER BY CASE a.status WHEN 'rejected' THEN 1 ELSE 0 END, "
            "a.last_updated DESC, a.applied_at DESC"
        ).fetchall()
        items = [dict(r) for r in rows]
        hist: dict[int, list] = {}
        for r in conn.execute(
            "SELECT job_id, status, changed_at FROM application_events ORDER BY id"
        ):
            hist.setdefault(r["job_id"], []).append(
                {"status": r["status"], "changed_at": (r["changed_at"] or "")[:10]}
            )
        salary: dict[int, list] = {}
        for r in _salary_rows(conn):
            if r["job_id"] is not None:
                salary.setdefault(r["job_id"], []).append({
                    "replied_at": r["replied_at"], "stage": r["stage"],
                    "desired_man": r["desired_man"], "minimum_man": r["minimum_man"],
                })
        requests = _request_rows(conn)
    finally:
        conn.close()

    import app_request_stats

    matched = app_request_stats.match_requests(requests, items)

    today = date.today()
    for it in items:
        it["history"] = hist.get(it["job_id"], [])
        # 年収回答（送信日昇順）。一覧に出すのは書類前の最初の回答＝企業が書類で見た額
        it["salary_replies"] = salary.get(it["job_id"], [])
        req = matched.get(it["job_id"])
        it["apply_request"] = (
            {"requested_at": req["requested_at"], "title": req["title"]} if req else None
        )
        days = (today - date.fromisoformat(it["last_updated"][:10])).days
        it["days_since_update"] = days
        cadence = CADENCE_DAYS.get(it["status"])
        it["is_overdue"] = bool(cadence is not None and days > cadence)
    counts: dict = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    replied = sum(n for s, n in counts.items() if s not in ("applied", "rejected"))
    return {
        "items": items, "counts": counts, "total": len(items),
        "replied": replied, "offers": counts.get("offer", 0),
        "statuses": STATUSES, "labels": STATUS_LABELS,
    }


def _salary_rows(conn: sqlite3.Connection) -> list:
    """salary_replies を送信日昇順で。表が無い（inbox.salary_reply 未実行の新環境）なら空。"""
    try:
        return conn.execute(
            "SELECT job_id, company, replied_at, stage, desired_man, minimum_man "
            "FROM salary_replies ORDER BY replied_at, id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _request_rows(conn: sqlite3.Connection) -> list:
    """apply_requests を受信日昇順で。表が無い（inbox.apply_request 未実行の新環境）なら空。"""
    try:
        return conn.execute(
            "SELECT requested_at, company, company_norm, offer_id, title "
            "FROM apply_requests ORDER BY requested_at, id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _interview_days(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """面接日の一覧 (job_id, YYYY-MM-DD)。next_event は最新 1 件しか持たないので、
    過去の面接は日程確定メールの記録（inbox_mails.summary「日程確定 → …」）から拾う。"""
    out = []
    rows = conn.execute(
        "SELECT job_id, next_event AS ev FROM applications WHERE next_event IS NOT NULL"
    ).fetchall()
    try:
        rows += conn.execute(
            "SELECT job_id, summary AS ev FROM inbox_mails "
            "WHERE category = 'schedule_confirmed' AND job_id IS NOT NULL AND summary LIKE '日程確定 → %'"
        ).fetchall()
    except sqlite3.OperationalError:
        pass  # inbox 未使用の環境
    for r in rows:
        parsed = _parse_agenda_event((r["ev"] or "").replace("日程確定 → ", ""))
        if parsed:
            out.append((r["job_id"], parsed["date"]))
    return out


# next_event 兩種既有格式：r-agent「{階段} 確定 2026/09/04(金) 10:30」、
# 手動記錄「2026-09-03 11:00 {階段}」。純文字備註（如「自己拒絕了」）不匹配，正常略過。
_RE_EVENT_A = re.compile(
    r"(?P<stage>\S*?)\s*確定\s*(?P<y>\d{4})/(?P<m>\d{1,2})/(?P<d>\d{1,2})\([^)]+\)\s*(?P<time>\d{1,2}:\d{2})"
)
_RE_EVENT_B = re.compile(
    r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\s+(?P<time>\d{1,2}:\d{2})\s*(?P<stage>\S*)"
)
# 本人主動辭退の判定は app_analytics.is_self_withdrawn()（Python 側）に一本化。
# SQL でも判定していた頃はファネル・通過率・一覧が別々の答えを出していた。


def _parse_agenda_event(next_event: str) -> dict | None:
    ev = (next_event or "").strip()
    if not ev:
        return None
    m = _RE_EVENT_A.search(ev) or _RE_EVENT_B.search(ev)
    if not m:
        return None
    g = m.groupdict()
    try:
        iso = f"{int(g['y']):04d}-{int(g['m']):02d}-{int(g['d']):02d}"
    except (KeyError, ValueError):
        return None
    return {"date": iso, "time": g.get("time"), "stage": (g.get("stage") or "").strip() or "面接"}


@router.get("/timeline")
def timeline():
    """應募データ分析：漏斗 / 條件通過率 / 月別 cohort / 日曆 / 議程 / 停留天數。"""
    import statistics
    from collections import defaultdict
    from datetime import date, datetime

    import app_analytics
    import app_request_stats
    import app_time_stats

    conn = _ro()
    try:
        apps = conn.execute(
            "SELECT a.job_id, j.company, a.status, a.applied_at, a.next_event, a.notes, "
            "a.rejection_stage, a.channel, COALESCE(j.tier, 'unknown') AS tier, "
            "j.job_type, j.mentions_ai, j.lang_env, j.score_breakdown, j.score, j.employee_count, "
            "CAST(json_extract(j.gap_analysis, '$.recommend_score') AS INTEGER) recommend_score, "
            "j.url, j.company_norm "
            "FROM applications a JOIN jobs j ON j.id = a.job_id "
            "WHERE COALESCE(j.blacklisted, 0) = 0"
        ).fetchall()
        events = conn.execute(
            "SELECT job_id, status, changed_at FROM application_events ORDER BY job_id, id"
        ).fetchall()
        salary_rows = _salary_rows(conn)
        request_rows = _request_rows(conn)
        interviews = _interview_days(conn)
    finally:
        conn.close()

    # 投遞日曆：applied_at 正規化成日期（欄位混雜純日期/datetime/ISO with T）
    daily = defaultdict(int)
    for r in apps:
        d = (r["applied_at"] or "")[:10]
        if d:
            daily[d] += 1
    daily_counts = [{"date": d, "count": n} for d, n in sorted(daily.items())]

    # 面試議程：解析 next_event，只留今天以後的預定（過去分は面接密度カードが担当）
    today_iso = date.today().isoformat()
    upcoming = []
    for r in apps:
        parsed = _parse_agenda_event(r["next_event"])
        if parsed and parsed["date"] >= today_iso:
            upcoming.append(
                {"job_id": r["job_id"], "company": r["company"], "status": r["status"], **parsed})
    upcoming.sort(key=lambda x: (x["date"], x["time"] or ""))

    # 書類選考の判定は annotate() で一度だけ行い、以降の統計は全てその結果を食う。
    # UI 連打の痕跡は先に除く（残すと「オファー滞留 0.0 日」のような幽霊が出る）
    clean_events = app_analytics.drop_misclick_events(events)
    annotated = app_analytics.annotate(apps, clean_events)
    summary = app_analytics.build_summary(annotated)

    # 各階段停留天數：同一 job 相鄰事件的時間差，用「起點階段」分組取中位數。
    # 到達実績を超える段階のイベントは落とす — ファネルが「二次面接 1 件」と
    # 言っているのに滞留日数が n=2 と言う、同一ページ内の食い違いを防ぐ。
    reached_by_job = {r["job_id"]: r["reached"] for r in annotated}
    by_job = defaultdict(list)
    for r in clean_events:
        idx = app_analytics.STAGE_INDEX.get(r["status"])
        if idx is not None and idx > reached_by_job.get(r["job_id"], 0):
            continue
        by_job[r["job_id"]].append((r["status"], r["changed_at"]))
    stage_days = defaultdict(list)
    for job_events in by_job.values():
        for (stage, t0), (_, t1) in zip(job_events, job_events[1:]):
            try:
                d0 = datetime.fromisoformat((t0 or "")[:19])
                d1 = datetime.fromisoformat((t1 or "")[:19])
            except ValueError:
                continue
            delta = (d1 - d0).total_seconds() / 86400
            if delta >= 0:
                stage_days[stage].append(delta)
    stage_durations = {
        stage: {"median_days": round(statistics.median(v), 1), "n": len(v)}
        for stage, v in stage_days.items()
    }

    # 書類結果の着信時間帯：まだ applied で結果待ちの件は右側打ち切りなので
    # 別枠（pending_days）に回す——分布に混ぜると遅い案件が消えて甘く見える。
    now = datetime.now()
    pending_days = []
    for r in annotated:
        if r["status"] != "applied":
            continue
        try:
            applied_dt = datetime.fromisoformat((r["applied_at"] or "")[:19])
        except ValueError:
            continue
        pending_days.append((now - applied_dt).total_seconds() / 86400)
    shorui_turnaround = app_analytics.build_shorui_turnaround(
        stage_days.get("applied", []), pending_days
    )

    # 拒絕階段分布（已在寫入時正規化，這裡直接分組）。
    # 本人辞退は企業のお見送りではないので除く — 残すと同じページの
    # 「書類で不合格 128 件」と「お見送り段階：書類 129 件」が食い違う。
    rej_dist = defaultdict(int)
    for r in annotated:
        if r["status"] == "rejected" and not r["self_withdrawn"]:
            rej_dist[r["rejection_stage"] or "unspecified"] += 1

    return {
        "daily_counts": daily_counts,
        "agenda": {"upcoming": upcoming},
        "stage_durations": stage_durations,
        "shorui_turnaround": shorui_turnaround,
        "recommend_distribution": app_analytics.build_recommend_distribution(annotated),
        "rejection_dist": [
            {"stage": k, "label": REJECTION_STAGE_LABELS.get(k, "未分類"), "n": v}
            for k, v in sorted(rej_dist.items(), key=lambda kv: -kv[1])
        ],
        # 書類選考の成績分析（条件付き通過率・信頼区間・漏斗）は app_analytics に集約
        "summary": summary,
        "funnel": app_analytics.build_funnel(annotated),
        "segments": app_analytics.build_segments(annotated, summary["pass_rate"]),
        "cohorts": app_analytics.build_cohorts(annotated, summary["pass_rate"]),
        "quality": app_analytics.build_quality(annotated),
        # 年収を答えた応募の書類通過率（同時期・未回答の応募と対照）
        "salary_ask": app_analytics.build_salary_ask(annotated, salary_rows),
        # r-agent 応募リクエスト経由の応募の書類通過率（同時期 r-agent 応募と対照）
        "apply_request": app_request_stats.build_apply_request(
            annotated, app_request_stats.match_requests(request_rows, apps), request_rows,
        ),
        # 時間分布（日単位）：週次の結果構成と滾動通過率 / 待ち日数別の通過率 / 面接密度
        "time": {
            "weekly": app_time_stats.build_weekly(annotated),
            "wait_curve": app_time_stats.build_wait_curve(annotated, clean_events),
            "interview_load": app_time_stats.build_interview_load(interviews),
        },
        # 辞退一覧もファネルと同じ annotate() の判定を使う（別クエリで判定すると
        # 「ファネルは 4 件・一覧は 5 件」のような食い違いが起きる）
        "self_withdrawn": [
            {"job_id": r["job_id"], "company": r["company"], "status": r["status"],
             "rejection_stage": r["rejection_stage"],
             "rejection_stage_label": REJECTION_STAGE_LABELS.get(r["rejection_stage"] or ""),
             "reached_label": STATUS_LABELS.get(
                 app_analytics.STAGE_SEQUENCE[r["reached"]], ""),
             "before_result": r["outcome"] == "withdrawn",
             "note": r["notes"] or r["next_event"]}
            for r in annotated if r["self_withdrawn"]
        ],
    }


@router.post("")
def upsert(body: dict = Body(...)):
    job_id, status = body.get("job_id"), body.get("status")
    if not job_id or status not in STATUSES:
        raise HTTPException(400, f"job_id + status({'/'.join(STATUSES)}) 必填")
    notes = (body.get("notes") or "").strip()[:500]
    next_event = (body.get("next_event") or "").strip()[:200]
    rejection_stage = normalize_rejection_stage(body.get("rejection_stage"))
    rejection_reason = body.get("rejection_reason") or None
    if rejection_reason not in REJECTION_REASONS:
        rejection_reason = None
    event_date = _valid_event_date(body.get("event_date"))
    conn = _rw()
    try:
        job_row = conn.execute("SELECT source FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job_row:
            raise HTTPException(404, "job 不存在")
        last_event = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM application_events WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        channel = channel_for_source(job_row["source"])
        conn.execute(
            "INSERT INTO applications (job_id, status, applied_at, last_updated, notes, next_event, "
            "rejection_stage, rejection_reason, channel) "
            "VALUES (?,?,date('now','localtime'),date('now','localtime'),?,?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, "
            "last_updated=date('now','localtime'), "
            "notes=CASE WHEN excluded.notes != '' THEN excluded.notes ELSE notes END, "
            "next_event=excluded.next_event, "
            "rejection_stage=excluded.rejection_stage, rejection_reason=excluded.rejection_reason, "
            "channel=COALESCE(channel, excluded.channel)",
            (job_id, status, notes, next_event, rejection_stage, rejection_reason, channel))
        if event_date:
            # 結果日（通知が来た日）を後から記録した場合、選考歴程をその日付にする
            date_events_since(conn, job_id, last_event, event_date)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


def _valid_event_date(value) -> str | None:
    """YYYY-MM-DD かつ未来でない日付だけ受け付ける（それ以外は今日扱い＝None）。"""
    from datetime import date

    v = (value or "").strip()[:10]
    try:
        d = date.fromisoformat(v)
    except ValueError:
        return None
    return v if d <= date.today() else None


@router.delete("/{job_id}")
def remove(job_id: int):
    conn = _rw()
    try:
        conn.execute("DELETE FROM applications WHERE job_id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}
