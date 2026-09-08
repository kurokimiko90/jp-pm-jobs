"""jobs 域端點 — 職缺列表/詳情 + stats / analytics / funnel / actions。"""
import json
from datetime import date, datetime

from fastapi import APIRouter, HTTPException

from db import query, query_one
from paths import APPLY_DIR, PREP_DIR, TAILORED_DIR
from queries import _APPLIED_SQL, _app_status_cols, SORT_EXPR, SORTABLE
from scoring_meta import ATS_SOURCES

router = APIRouter()

# ── stats / funnel ────────────────────────────────────────────────


@router.get("/api/stats")
def stats():
    base = query_one(
        "SELECT COUNT(*) total, SUM(score IS NOT NULL) scored, "
        "SUM(gap_analysis IS NOT NULL) gap FROM jobs"
    )
    prep = sorted(p.name for p in PREP_DIR.iterdir() if p.is_dir() and not p.name.startswith("_"))
    by_source = query("SELECT source, COUNT(*) n FROM jobs GROUP BY source ORDER BY n DESC")
    by_tier = query("SELECT COALESCE(tier,'unknown') tier, COUNT(*) n FROM jobs GROUP BY tier ORDER BY n DESC")
    bins = query(
        "SELECT (score/10)*10 bin, COUNT(*) n FROM jobs WHERE score IS NOT NULL "
        "GROUP BY bin ORDER BY bin"
    )
    # 首次使用引導用：全空 / 只有 demo 種子資料 兩種狀態
    demo_only = bool(base["total"]) and all(s["source"] == "demo" for s in by_source)
    return {**base, "prep": len(prep), "prep_dirs": prep,
            "by_source": by_source, "by_tier": by_tier, "score_bins": bins,
            "demo_only": demo_only}


_GRANULARITIES = ("run", "week", "month")
_MIN_BUCKET_N = 5  # go率 分桶樣本數低於此值視為噪音，回 null 由前端斷點處理


def _trend_buckets(granularity: str, periods: int) -> list[str]:
    """產生最近 periods 個桶的 key（升冪，最舊在前）。run=日、week=ISO週一、month=YYYY-MM。"""
    from datetime import date, timedelta

    today = date.today()
    if granularity == "run":
        return [(today - timedelta(days=i)).isoformat() for i in range(periods - 1, -1, -1)]
    if granularity == "week":
        monday = today - timedelta(days=today.weekday())
        return [(monday - timedelta(weeks=i)).isoformat() for i in range(periods - 1, -1, -1)]
    keys, y, m = [], today.year, today.month
    for _ in range(periods):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(keys))


def _trend_bucket_key(d, granularity: str) -> str:
    from datetime import timedelta

    if granularity == "run":
        return d.isoformat()
    if granularity == "week":
        return (d - timedelta(days=d.weekday())).isoformat()
    return f"{d.year:04d}-{d.month:02d}"


def _trend_bucket_label(key: str, granularity: str) -> str:
    from datetime import date as _date

    if granularity == "month":
        y, m = key.split("-")
        return f"{y}/{m}"
    d = _date.fromisoformat(key)
    return f"{d.month}/{d.day}"


def _build_trend(granularity: str, periods: int) -> dict:
    """件数 + go率 時序（僅這兩項時間切；其餘指標維持快照，見 CLAUDE.md 平台分析規劃）。"""
    from datetime import date as _date

    rows = query(
        "SELECT source, first_seen, json_extract(gap_analysis,'$.verdict') verdict, "
        "(gap_analysis IS NOT NULL) analyzed FROM jobs WHERE first_seen IS NOT NULL"
    )
    buckets = _trend_buckets(granularity, periods)
    bucket_idx = {b: i for i, b in enumerate(buckets)}
    by_source: dict[str, dict] = {}
    for r in rows:
        try:
            d = _date.fromisoformat(r["first_seen"])
        except (TypeError, ValueError):
            continue
        idx = bucket_idx.get(_trend_bucket_key(d, granularity))
        if idx is None:
            continue
        agg = by_source.setdefault(
            r["source"], {"count": [0] * periods, "go_n": [0] * periods, "analyzed_n": [0] * periods}
        )
        agg["count"][idx] += 1
        if r["analyzed"]:
            agg["analyzed_n"][idx] += 1
            if r["verdict"] == "go":
                agg["go_n"][idx] += 1

    return {
        "buckets": [_trend_bucket_label(b, granularity) for b in buckets],
        "by_source": {
            src: {
                "count": agg["count"],
                "go_n": agg["go_n"],
                "go_rate": [
                    round(agg["go_n"][i] / agg["analyzed_n"][i] * 100, 1)
                    if agg["analyzed_n"][i] >= _MIN_BUCKET_N else None
                    for i in range(periods)
                ],
            }
            for src, agg in by_source.items()
        },
    }


@router.get("/api/analytics")
def analytics(granularity: str = "week", periods: int = 8):
    import re as _re
    from statistics import median as _median

    if granularity not in _GRANULARITIES:
        raise HTTPException(400, f"granularity must be one of {_GRANULARITIES}")
    periods = max(2, min(periods, 52))

    sources_raw = query("SELECT source, COUNT(*) n FROM jobs GROUP BY source ORDER BY n DESC")
    sources = [r["source"] for r in sources_raw]

    all_scores = query("SELECT source, score FROM jobs WHERE score IS NOT NULL ORDER BY source, score")
    scores_by_src: dict[str, list[int]] = {}
    for r in all_scores:
        scores_by_src.setdefault(r["source"], []).append(r["score"])

    all_salary = query(
        "SELECT source, salary_min, salary_max FROM jobs "
        "WHERE salary_min IS NOT NULL OR salary_max IS NOT NULL"
    )
    salary_by_src: dict[str, list[float]] = {}
    for r in all_salary:
        mid = (
            (r["salary_min"] + r["salary_max"]) / 2
            if r["salary_min"] and r["salary_max"]
            else r["salary_min"] or r["salary_max"]
        )
        if mid:
            salary_by_src.setdefault(r["source"], []).append(mid)

    verdict_raw = query(
        "SELECT source,"
        " SUM(CASE WHEN json_extract(gap_analysis,'$.verdict')='go' THEN 1 ELSE 0 END) go_n,"
        " SUM(CASE WHEN json_extract(gap_analysis,'$.verdict')='improve' THEN 1 ELSE 0 END) imp_n,"
        " SUM(CASE WHEN json_extract(gap_analysis,'$.verdict')='skip' THEN 1 ELSE 0 END) skip_n,"
        " SUM(CASE WHEN gap_analysis IS NOT NULL THEN 1 ELSE 0 END) analyzed,"
        " COUNT(*) total FROM jobs GROUP BY source"
    )

    tier_raw = query(
        "SELECT source, COALESCE(tier,'unknown') tier, COUNT(*) n "
        "FROM jobs GROUP BY source, tier"
    )

    dist_raw = query(
        "SELECT source,"
        " CASE WHEN score<20 THEN '0-19' WHEN score<40 THEN '20-39'"
        " WHEN score<60 THEN '40-59' WHEN score<80 THEN '60-79' ELSE '80+' END bin,"
        " COUNT(*) n FROM jobs WHERE score IS NOT NULL GROUP BY source, bin"
    )

    geo_raw = query("SELECT source, location FROM jobs")
    JP_RE = _re.compile(
        r"Japan|日本|Tokyo|東京|Osaka|大阪|Yokohama|横浜|Kyoto|京都"
        r"|Nagoya|名古屋|Fukuoka|福岡|Sapporo|札幌|Sendai|仙台",
        _re.IGNORECASE,
    )
    JP_ADDR = _re.compile(r"[都道府県区市町村]")
    geo_by_src: dict[str, dict[str, int]] = {}
    for r in geo_raw:
        src, loc = r["source"], r["location"] or ""
        if src == "bizreach":
            is_jp = True
        elif src == "indeed_jp":
            is_jp = bool(JP_ADDR.search(loc)) if loc else True
        else:
            is_jp = bool(JP_RE.search(loc)) if loc else False
        geo_by_src.setdefault(src, {"japan": 0, "overseas": 0})
        geo_by_src[src]["japan" if is_jp else "overseas"] += 1

    BINS = ["0-19", "20-39", "40-59", "60-79", "80+"]
    summary = {}
    score_dist = {}
    verdict = {}
    tier: dict[str, dict[str, int]] = {}

    for r in sources_raw:
        src, total = r["source"], r["n"]
        sc = scores_by_src.get(src, [])
        sal = salary_by_src.get(src, [])
        vr = next((v for v in verdict_raw if v["source"] == src), {})
        geo = geo_by_src.get(src, {"japan": 0, "overseas": 0})
        analyzed = vr.get("analyzed", 0)
        go_n = vr.get("go_n", 0)
        summary[src] = {
            "total": total,
            "median_score": int(_median(sc)) if sc else None,
            "gap_covered": analyzed,
            "gap_pct": round(analyzed / total * 100, 1) if total else 0,
            "go_count": go_n,
            "go_rate": round(go_n / analyzed * 100, 1) if analyzed else None,
            "salary_median": int(_median(sal)) if sal else None,
            "salary_count": len(sal),
            "japan_pct": round(geo["japan"] / total * 100, 1) if total else 0,
        }
        src_dist = [d for d in dist_raw if d["source"] == src]
        total_scored = sum(d["n"] for d in src_dist)
        score_dist[src] = [
            {"bin": b, "count": next((d["n"] for d in src_dist if d["bin"] == b), 0),
             "pct": round(next((d["n"] for d in src_dist if d["bin"] == b), 0) / total_scored * 100, 1) if total_scored else 0}
            for b in BINS
        ]
        verdict[src] = {
            "go": go_n, "improve": vr.get("imp_n", 0), "skip": vr.get("skip_n", 0),
            "unanalyzed": total - analyzed, "analyzed": analyzed, "total": total,
        }

    for r in tier_raw:
        tier.setdefault(r["source"], {})[r["tier"]] = r["n"]

    pt_raw = query(
        "SELECT source, COALESCE(posting_type,'unknown') pt, COUNT(*) n "
        "FROM jobs GROUP BY source, pt"
    )
    posting_type: dict[str, dict[str, int]] = {}
    for r in pt_raw:
        posting_type.setdefault(r["source"], {})[r["pt"]] = r["n"]

    return {
        "sources": sources,
        "summary": summary,
        "score_dist": score_dist,
        "verdict": verdict,
        "tier": tier,
        "geo": geo_by_src,
        "posting_type": posting_type,
        "granularity": granularity,
        "trend": _build_trend(granularity, periods),
    }


@router.get("/api/funnel")
def funnel():
    base = query_one(
        "SELECT COUNT(*) total, SUM(score IS NOT NULL) scored, "
        "SUM(gap_analysis IS NOT NULL) gap FROM jobs"
    )
    apps = {r["status"]: r["n"] for r in query("SELECT status, COUNT(*) n FROM applications GROUP BY status")}
    applied = sum(apps.values())
    interviewing = sum(apps.get(s, 0) for s in ("casual", "recruiter", "tech", "onsite"))
    apply_ids: set[int] = set()
    if APPLY_DIR.exists():
        import re as _re
        for p in APPLY_DIR.iterdir():
            m = _re.match(r"^(\d+)_", p.name)
            if p.is_dir() and m:
                apply_ids.add(int(m.group(1)))
    if TAILORED_DIR.exists():
        import re as _re
        for f in TAILORED_DIR.glob("*"):
            m = _re.match(r"^(\d+)_", f.name)
            if m:
                apply_ids.add(int(m.group(1)))
    apply = len(apply_ids)
    prep = len([p for p in PREP_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")])

    stages = [
        {"key": "scraped", "label": "Scraped", "n": base["total"]},
        {"key": "scored", "label": "Scored", "n": base["scored"]},
        {"key": "gap", "label": "Gap Analyzed", "n": base["gap"]},
        {"key": "apply", "label": "Apply Pack", "n": apply},
        {"key": "prep", "label": "Interview Pack", "n": prep},
        {"key": "applied", "label": "Applied", "n": applied},
        {"key": "interview", "label": "Interviewing", "n": interviewing},
        {"key": "offer", "label": "Offer", "n": apps.get("offer", 0)},
    ]
    # pct = 佔總抓取量的比例（而非逐段轉換率——prep/applied 兩段非嚴格巢狀子集，逐段算會出現 >100%）
    total_n = stages[0]["n"] or None
    for i, s in enumerate(stages):
        s["pct"] = round(s["n"] / total_n * 100) if i > 0 and total_n else None

    # 回音天數中位數：投遞後第一次狀態變化（last_updated 相對 applied_at）
    import statistics
    replied_days = [
        (date.fromisoformat(r["last_updated"][:10]) - date.fromisoformat(r["applied_at"][:10])).days
        for r in query("SELECT applied_at, last_updated FROM applications WHERE status != 'applied'")
        if r["last_updated"] and r["applied_at"]
    ]
    reply_days_median = round(statistics.median(replied_days)) if replied_days else None

    return {"stages": stages, "by_status": apps, "reply_days_median": reply_days_median}


@router.get("/api/actions")
def actions():
    """待辦事項：24h 新職缺 / 跟進 / 未投遞 / Go 未備包 / 未 gap。"""
    from datetime import timedelta
    import re as _re

    from tools.followup import get_followup_schedule

    yesterday = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d")

    # 1. 24h 內新進高分職缺
    new_high = query(
        "SELECT j.id, j.title, j.company, j.score, j.tier, j.source, j.url, j.posting_type, j.location, "
        "j.employee_count, j.mentions_ai, cr.openwork_score, cr.openwork_url FROM jobs j "
        "LEFT JOIN company_ratings cr ON cr.company_name = j.company "
        "WHERE j.first_seen >= ? AND j.score >= 60 AND COALESCE(j.liveness_status, 'active') != 'expired' AND COALESCE(j.blacklisted, 0) = 0 "
        "ORDER BY j.score DESC LIMIT 20", (yesterday,))

    # 2. 進行中應募（需跟進）— reason 帶逾期天數（reuse tools.followup 節奏規則）
    in_flight = query(
        "SELECT j.id, j.title, j.company, j.score, a.status, a.applied_at, a.last_updated, a.notes, "
        "a.next_event FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE a.status NOT IN ('rejected') AND COALESCE(j.blacklisted, 0) = 0 ORDER BY a.last_updated DESC")
    followup_by_job = {s["job_id"]: s for s in get_followup_schedule()}
    for r in in_flight:
        fu = followup_by_job.get(r["id"])
        if fu and fu["is_overdue"]:
            r["reason_type"] = "followup_overdue"
            r["reason_days"] = fu["days_overdue"]
        elif fu:
            r["reason_type"] = "followup_due"
            r["reason_days"] = -fu["days_overdue"]
        else:
            r["reason_type"] = "followup_now"

    # 3. 投遞包已備但未投遞 — reason 帶已備天數（資料夾 mtime）
    apply_mtime: dict[int, float] = {}
    if APPLY_DIR.exists():
        for p in APPLY_DIR.iterdir():
            m = _re.match(r"^(\d+)_", p.name)
            if p.is_dir() and m:
                apply_mtime[int(m.group(1))] = p.stat().st_mtime
    if TAILORED_DIR.exists():
        for f in TAILORED_DIR.glob("*"):
            m = _re.match(r"^(\d+)_", f.name)
            if m:
                apply_mtime.setdefault(int(m.group(1)), f.stat().st_mtime)
    apply_ids = set(apply_mtime.keys())
    pack_not_applied = []
    if apply_ids:
        ph = ",".join("?" * len(apply_ids))
        pack_not_applied = query(
            f"SELECT j.id, j.title, j.company, j.score, j.tier, j.posting_type, j.location FROM jobs j "
            f"LEFT JOIN applications a ON a.job_id = j.id "
            f"WHERE j.id IN ({ph}) AND a.id IS NULL AND COALESCE(j.blacklisted, 0) = 0 ORDER BY j.score DESC",
            tuple(apply_ids))
        for r in pack_not_applied:
            days = int((datetime.now().timestamp() - apply_mtime[r["id"]]) / 86400)
            r["reason_type"] = "pack_ready"
            r["reason_days"] = days

    # 4. Go 職缺（recommend ≥75）但無投遞包
    go_no_pack = query(
        "SELECT id, title, company, score, "
        "CAST(json_extract(gap_analysis, '$.recommend_score') AS INTEGER) rec "
        "FROM jobs WHERE gap_analysis IS NOT NULL "
        "AND CAST(json_extract(gap_analysis, '$.recommend_score') AS INTEGER) >= 75 "
        "AND COALESCE(liveness_status, 'active') != 'expired' AND COALESCE(blacklisted, 0) = 0 "
        "ORDER BY rec DESC")
    go_no_pack = [r for r in go_no_pack if r["id"] not in apply_ids]
    for r in go_no_pack:
        r["reason_type"] = "go_no_pack"
        r["reason_rec"] = r["rec"]

    # 5. 高分但未跑 gap
    no_gap = query_one(
        "SELECT COUNT(*) n FROM jobs WHERE score >= 70 AND gap_analysis IS NULL AND COALESCE(blacklisted, 0) = 0")
    no_gap_count = no_gap["n"] if no_gap else 0

    # 6. 官網直投待寄出（pack_ready + drafted＝包已備妥但還沒按寄出）
    direct_pending = query_one(
        "SELECT COUNT(*) n FROM direct_apply WHERE status IN ('pack_ready', 'drafted')")
    direct_pending_count = direct_pending["n"] if direct_pending else 0

    return {
        "new_high_24h": new_high,
        "in_flight": in_flight,
        "pack_not_applied": pack_not_applied,
        "go_no_pack": go_no_pack,
        "no_gap_count": no_gap_count,
        "direct_pending_count": direct_pending_count,
    }


# ── jobs ──────────────────────────────────────────────────────────


@router.get("/api/jobs")
def jobs(page: int = 1, size: int = 25, min_score: int = 0, max_score: int = 100,
         tier: str = "", source: str = "", q: str = "",
         sort: str = "score", order: str = "desc", hide_unknown: bool = False,
         loc: str = "", show_closed: bool = False, posting_type: str = "",
         applied_filter: str = "", days: int = 0, job_type: str = ""):
    where, params = ["COALESCE(blacklisted, 0) = 0"], []
    if min_score > 0:
        where.append("score >= ?"); params.append(min_score)
    if max_score < 100:
        where.append("score <= ?"); params.append(max_score)
    if tier:
        ts = tier.split(",")
        where.append(f"COALESCE(tier,'unknown') IN ({','.join('?' * len(ts))})"); params += ts
    if hide_unknown:
        where.append("COALESCE(tier,'unknown') != 'unknown'")
    if source:
        ss = source.split(",")
        where.append(f"source IN ({','.join('?' * len(ss))})"); params += ss
    if posting_type:
        where.append("COALESCE(posting_type,'direct') = ?"); params.append(posting_type)
    if job_type:
        where.append("COALESCE(job_type,'other') = ?"); params.append(job_type)
    if days > 0:
        where.append("first_seen >= date('now', ?)"); params.append(f"-{days} days")
    if q:
        q_num = q.lstrip('#').strip()
        if q_num.isdigit():
            where.append("(title LIKE ? OR company LIKE ? OR id = ?)")
            params += [f"%{q_num}%", f"%{q_num}%", int(q_num)]
        else:
            where.append("(title LIKE ? OR company LIKE ?)"); params += [f"%{q}%", f"%{q}%"]
    if loc == "japan":
        where.append("(location LIKE '%Tokyo%' OR location LIKE '%東京%' OR location LIKE '%Japan%' "
                     "OR location LIKE '%大阪%' OR location LIKE '%Osaka%' OR location LIKE '%京都%' "
                     "OR location LIKE '%Kyoto%' OR location LIKE '%リモート%' OR location LIKE '%在宅%' "
                     "OR location LIKE '%Remote%' OR location LIKE '%Hybrid%')")
    elif loc == "overseas":
        where.append("location IS NOT NULL AND location != '' "
                     "AND location NOT LIKE '%Tokyo%' AND location NOT LIKE '%東京%' "
                     "AND location NOT LIKE '%Japan%' AND location NOT LIKE '%大阪%' "
                     "AND location NOT LIKE '%Osaka%' AND location NOT LIKE '%京都%' "
                     "AND location NOT LIKE '%Kyoto%' AND location NOT LIKE '%リモート%' "
                     "AND location NOT LIKE '%在宅%' AND location NOT LIKE '%Remote%' "
                     "AND location NOT LIKE '%Hybrid%')")
    if not show_closed:
        where.append("COALESCE(liveness_status, 'active') != 'expired'")
    if applied_filter in _APPLIED_SQL:
        where.append(_APPLIED_SQL[applied_filter].format(tbl="jobs"))
    w = " AND ".join(where)
    sort = sort if sort in SORTABLE else "score"
    order_col = SORT_EXPR.get(sort, sort)
    order = "DESC" if order.lower() != "asc" else "ASC"
    total = query_one(f"SELECT COUNT(*) n FROM jobs WHERE {w}", tuple(params))["n"]
    items = query(
        f"SELECT id, source, title, company, location, score, tier, jp_level, remote, "
        f"salary_min, salary_max, employee_count, mentions_ai, "
        f"(SELECT cr.openwork_score FROM company_ratings cr WHERE cr.company_name = jobs.company LIMIT 1) openwork_score, "
        f"(SELECT cr.openwork_url FROM company_ratings cr WHERE cr.company_name = jobs.company LIMIT 1) openwork_url, "
        f"first_seen, url, posting_type, liveness_status, "
        f"(gap_analysis IS NOT NULL) has_gap, "
        f"CAST(json_extract(gap_analysis, '$.recommend_score') AS INTEGER) recommend_score, "
        # 全庫百分位（分數飽和時的區分度輔助）：比此分高的職缺占比
        f"ROUND((SELECT COUNT(*) FROM jobs jx WHERE jx.score > jobs.score) * 100.0 "
        f"  / MAX((SELECT COUNT(*) FROM jobs WHERE score IS NOT NULL), 1), 1) score_pct_above, "
        f"{_app_status_cols('jobs')} "
        f"FROM jobs WHERE {w} "
        f"ORDER BY {order_col} {order} NULLS LAST LIMIT ? OFFSET ?",
        tuple(params) + (size, (page - 1) * size))
    for it in items:
        it["source_kind"] = "career-ops" if it["source"] in ATS_SOURCES else "media"
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/api/jobs/{job_id}")
def job_detail(job_id: int):
    row = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(404)
    for f in ("score_breakdown", "gap_analysis"):
        if row.get(f):
            try:
                row[f] = json.loads(row[f])
            except (json.JSONDecodeError, TypeError):
                pass
    row["application"] = query_one("SELECT * FROM applications WHERE job_id = ?", (job_id,))
    prep_dir = next((p for p in PREP_DIR.iterdir()
                     if p.is_dir() and p.name.startswith(f"{job_id}_")), None)
    row["prep_dir"] = str(prep_dir) if prep_dir else None
    row["source_kind"] = "career-ops" if row["source"] in ATS_SOURCES else "media"
    row["timeline"] = _job_timeline(job_id, row, row["application"])
    return row


def _job_timeline(job_id: int, job: dict, application: dict | None) -> list[dict]:
    """合併 first_seen／gap 分析／投遞／跟進／inbox 郵件，依時間排成一條軸給 JobDrawer 時間線 tab。"""
    from tools.followup import get_followup_history

    events: list[dict] = []
    if job.get("first_seen"):
        events.append({"date": job["first_seen"], "kind": "discovered"})
    if job.get("gap_analyzed_at"):
        events.append({"date": job["gap_analyzed_at"], "kind": "gap"})
    if application:
        events.append({"date": application["applied_at"], "kind": "applied"})
    for fu in get_followup_history(job_id):
        events.append({"date": fu["logged_at"], "kind": "followup", "note": fu.get("note")})
    for m in query(
        "SELECT received_at, category, subject FROM inbox_mails WHERE job_id = ? ORDER BY received_at",
        (job_id,)
    ):
        events.append({
            "date": m["received_at"],
            "kind": "mail",
            "category": m["category"],
            "subject": m["subject"],
        })
    events = [e for e in events if e["date"]]
    events.sort(key=lambda e: str(e["date"]))
    return events
