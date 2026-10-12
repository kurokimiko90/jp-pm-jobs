"""跨 router 共用的 SQL 片段 — jobs × applications 關聯標識與排序白名單。"""

SORTABLE = {"score", "recommend_score", "company", "title", "tier", "source", "first_seen", "salary_max"}
SORT_EXPR = {"recommend_score": "CAST(json_extract(gap_analysis, '$.recommend_score') AS INTEGER)"}


_APPLIED_SQL = {
    "applied": "EXISTS (SELECT 1 FROM applications ap WHERE ap.job_id = {tbl}.id)",
    "not_applied": "NOT EXISTS (SELECT 1 FROM applications ap WHERE ap.job_id = {tbl}.id)",
    "company": ("EXISTS (SELECT 1 FROM applications ap JOIN jobs j2 ON j2.id = ap.job_id "
                "WHERE j2.company = {tbl}.company AND {tbl}.company IS NOT NULL AND {tbl}.company != '')"),
    "not_company": ("NOT EXISTS (SELECT 1 FROM applications ap JOIN jobs j2 ON j2.id = ap.job_id "
                    "WHERE j2.company = {tbl}.company AND {tbl}.company IS NOT NULL AND {tbl}.company != '')"),
}


def _app_status_cols(tbl: str) -> str:
    """三個應募標識欄位：本職缺狀態 / 同公司最新狀態 / 同公司已投職缺 id。"""
    return (
        f"(SELECT ap.status FROM applications ap WHERE ap.job_id = {tbl}.id) AS app_status, "
        f"(SELECT ap2.status FROM applications ap2 JOIN jobs jb2 ON jb2.id = ap2.job_id "
        f"WHERE jb2.company = {tbl}.company AND {tbl}.company IS NOT NULL AND {tbl}.company != '' "
        f"ORDER BY ap2.last_updated DESC, ap2.applied_at DESC LIMIT 1) AS company_app_status, "
        f"(SELECT ap3.job_id FROM applications ap3 JOIN jobs jb3 ON jb3.id = ap3.job_id "
        f"WHERE jb3.company = {tbl}.company AND {tbl}.company IS NOT NULL AND {tbl}.company != '' "
        f"ORDER BY ap3.last_updated DESC, ap3.applied_at DESC LIMIT 1) AS company_applied_job_id"
    )
