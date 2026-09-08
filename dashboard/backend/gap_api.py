"""gap 域端點 — 單筆分析 / 批次推薦度報告 / 推薦列表。"""
import json

from fastapi import APIRouter, HTTPException

from db import query, query_one
from queries import _app_status_cols, _APPLIED_SQL
from tools.refetch_jd import cdp_port_for, fetch_one
from tracker.db import update_liveness, update_raw_jd

router = APIRouter()

# JD が取れなかった理由 → 画面に出す文言（400 の detail）
_JD_FAIL_HINT = {
    "needs_login": "JD 補抓失敗：詳情頁需要登入。請開對應的 CDP Chrome（{source} → port {port}）並登入後再試。",
    "expired": "此職缺已關閉（JD 頁面已下架），無法 gap 分析。",
    "no_content": "JD 補抓失敗：詳情頁抓不到內文（可能被擋或版型改了）。",
    "unsupported": "此來源無法自動補抓 JD（無 URL 或未支援的站點）。請手動補 raw_jd。",
    "error": "JD 補抓時發生錯誤（多為 CDP 連線失敗或頁面逾時），詳見後端 log。",
}

# ── gap 單筆分析 ─────────────────────────────────────────────────


@router.post("/api/gap/{job_id}")
def gap_single(job_id: int):
    """對單一職缺觸發 gap 分析（LLM 呼叫，約 30-180 秒）。"""
    from analyzer.gap_analyzer import analyze_one, load_profile_summary

    row = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if not row:
        raise HTTPException(404, f"job_id {job_id} 不存在")
    if not (row.get("raw_jd") or "").strip():
        # raw_jd 空 = 爬蟲當時只拿到卡片。先自動補抓一次，抓不到才回報原因。
        text, liveness = fetch_one(row)
        if text:
            update_raw_jd(job_id, text)
            row = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        else:
            if liveness == "expired":
                update_liveness(job_id, "expired")
            hint = _JD_FAIL_HINT.get(liveness, _JD_FAIL_HINT["error"]).format(
                source=row.get("source") or "?", port=cdp_port_for(row.get("source") or "") or "?")
            raise HTTPException(400, f"job_id {job_id} {hint}")

    profile_yaml = load_profile_summary()
    result = analyze_one(row, profile_yaml)

    # 回傳更新後的完整 job（含 gap_analysis）
    updated = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if updated and updated.get("gap_analysis"):
        try:
            updated["gap_analysis"] = json.loads(updated["gap_analysis"])
        except (json.JSONDecodeError, TypeError):
            pass
    return {"gap_analysis": result, "job": updated}


# ── gap 批次推薦度報告 ──────────────────────────────────────────

@router.get("/api/gap-batches")
def gap_batches():
    rows = query(
        "SELECT id, created_at, source_filter, min_score, job_count "
        "FROM gap_batches WHERE summary_json IS NOT NULL "
        "ORDER BY id DESC")
    return {"items": rows}


@router.get("/api/gap-batches/{batch_id}")
def gap_batch_detail(batch_id: int):
    row = query_one("SELECT * FROM gap_batches WHERE id = ?", (batch_id,))
    if not row:
        raise HTTPException(404)
    if row.get("summary_json"):
        try:
            row["summary"] = json.loads(row["summary_json"])
        except (json.JSONDecodeError, TypeError):
            row["summary"] = None
    row.pop("summary_json", None)
    if row.get("summary") and isinstance(row["summary"].get("tiers"), dict):
        blacklisted_ids = {
            r["id"] for r in query("SELECT id FROM jobs WHERE COALESCE(blacklisted, 0) = 1")
        }
        for tier_items in row["summary"]["tiers"].values():
            tier_items[:] = [it for it in tier_items if it.get("id") not in blacklisted_ids]
        tier_ids = [
            it.get("id") for tier_items in row["summary"]["tiers"].values()
            for it in tier_items if isinstance(it.get("id"), int)
        ]
        if tier_ids:
            placeholders = ",".join("?" * len(tier_ids))
            details = query(
                f"SELECT j.id, j.title, j.company, j.score, j.employee_count, j.mentions_ai, "
                f"cr.openwork_score, cr.openwork_url FROM jobs j "
                f"LEFT JOIN company_ratings cr ON cr.company_name = j.company "
                f"WHERE j.id IN ({placeholders})",
                tuple(tier_ids),
            )
            detail_by_id = {d["id"]: d for d in details}
            for tier_items in row["summary"]["tiers"].values():
                for item in tier_items:
                    if detail := detail_by_id.get(item.get("id")):
                        item.update(detail)
    return row


# ── 推薦列表（直接讀 jobs 表，不依賴 batch summary）────────────

@router.get("/api/recommend-jobs")
def recommend_jobs(page: int = 1, size: int = 25, verdict: str = "", q: str = "",
                   sort: str = "recommend_score", order: str = "desc", loc: str = "",
                   source: str = "", posting_type: str = "",
                   min_score: int = 0, max_score: int = 100, days: int = 0, tier: str = "",
                   show_closed: bool = False, job_type: str = "",
                   applied_filter: str = ""):
    # 預設列出全部（含已投遞／同公司已投，前端以 badge 標示）；
    # 要排除時走統一的 applied_filter（_APPLIED_SQL 五選項，與 Jobs 頁同名同義）。
    where = [
        "j.recommend_score IS NOT NULL",
        "COALESCE(j.blacklisted, 0) = 0",
    ]
    params: list = []
    if applied_filter in _APPLIED_SQL:
        where.append(_APPLIED_SQL[applied_filter].format(tbl="j"))
    if not show_closed:
        where.append("COALESCE(j.liveness_status, 'active') != 'expired'")
    if min_score > 0:
        where.append("j.recommend_score >= ?"); params.append(min_score)
    if max_score < 100:
        where.append("j.recommend_score <= ?"); params.append(max_score)
    if days > 0:
        where.append("j.first_seen >= date('now', ?)"); params.append(f"-{days} days")
    if tier:
        ts = tier.split(",")
        where.append(f"COALESCE(j.tier,'unknown') IN ({','.join('?' * len(ts))})"); params += ts
    if verdict == "go":
        where.append("(COALESCE(json_extract(j.gap_analysis, '$.verdict'), '') = 'go' OR (json_extract(j.gap_analysis, '$.verdict') IS NULL AND j.recommend_score >= 75))")
    elif verdict == "improve":
        where.append("(COALESCE(json_extract(j.gap_analysis, '$.verdict'), '') = 'improve' OR (json_extract(j.gap_analysis, '$.verdict') IS NULL AND j.recommend_score >= 60 AND j.recommend_score < 75))")
    elif verdict == "skip":
        where.append("(COALESCE(json_extract(j.gap_analysis, '$.verdict'), '') = 'skip' OR (json_extract(j.gap_analysis, '$.verdict') IS NULL AND j.recommend_score < 60))")
    if q:
        q_num = q.lstrip('#').strip()
        if q_num.isdigit():
            where.append("(j.title LIKE ? OR j.company LIKE ? OR j.id = ?)")
            params += [f"%{q_num}%", f"%{q_num}%", int(q_num)]
        else:
            where.append("(j.title LIKE ? OR j.company LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
    if source:
        ss = source.split(",")
        where.append(f"j.source IN ({','.join('?' * len(ss))})"); params += ss
    if posting_type:
        where.append("COALESCE(j.posting_type,'direct') = ?"); params.append(posting_type)
    if job_type:
        where.append("COALESCE(j.job_type,'other') = ?"); params.append(job_type)
    if loc == "japan":
        where.append("(j.location LIKE '%Tokyo%' OR j.location LIKE '%東京%' OR j.location LIKE '%Japan%' "
                     "OR j.location LIKE '%大阪%' OR j.location LIKE '%Osaka%' OR j.location LIKE '%京都%' "
                     "OR j.location LIKE '%Kyoto%' OR j.location LIKE '%リモート%' OR j.location LIKE '%在宅%' "
                     "OR j.location LIKE '%Remote%' OR j.location LIKE '%Hybrid%')")
    elif loc == "overseas":
        where.append("j.location IS NOT NULL AND j.location != '' "
                     "AND j.location NOT LIKE '%Tokyo%' AND j.location NOT LIKE '%東京%' "
                     "AND j.location NOT LIKE '%Japan%' AND j.location NOT LIKE '%大阪%' "
                     "AND j.location NOT LIKE '%Osaka%' AND j.location NOT LIKE '%京都%' "
                     "AND j.location NOT LIKE '%Kyoto%' AND j.location NOT LIKE '%リモート%' "
                     "AND j.location NOT LIKE '%在宅%' AND j.location NOT LIKE '%Remote%' "
                     "AND j.location NOT LIKE '%Hybrid%')")
    w = " AND ".join(where)
    sort_col = "j.recommend_score" if sort not in ("recommend_score", "score", "first_seen", "company", "title") else f"j.{sort}"
    od = "DESC" if order.lower() != "asc" else "ASC"
    total = query_one(
        f"SELECT COUNT(*) n FROM jobs j LEFT JOIN company_ratings cr ON j.company = cr.company_name WHERE {w}",
        tuple(params))["n"]
    rows = query(
        f"SELECT j.id, j.title, j.company, j.tier, j.score, j.recommend_score, j.posting_type, j.location, j.source, "
        f"j.salary_min, j.salary_max, j.employee_count, j.mentions_ai, "
        f"json_extract(j.gap_analysis, '$.verdict') as gap_verdict, "
        f"json_extract(j.gap_analysis, '$.recommend_reason') as reason, "
        f"cr.openwork_score, cr.openwork_url, "
        f"{_app_status_cols('j')} "
        f"FROM jobs j LEFT JOIN company_ratings cr ON j.company = cr.company_name "
        f"WHERE {w} "
        f"ORDER BY {sort_col} {od} NULLS LAST LIMIT ? OFFSET ?",
        tuple(params) + (size, (page - 1) * size))
    # derive verdict for each row
    for r in rows:
        gv = r.get("gap_verdict")
        rec = r.get("recommend_score")
        if gv in ("go", "improve", "skip"):
            r["verdict"] = gv
        elif rec is not None and rec >= 75:
            r["verdict"] = "go"
        elif rec is not None and rec >= 60:
            r["verdict"] = "improve"
        else:
            r["verdict"] = "skip"
    return {"items": rows, "total": total, "page": page, "size": size}
