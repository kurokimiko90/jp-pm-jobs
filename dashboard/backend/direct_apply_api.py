"""官網直投端點 — 列表 / 探測觸發 / email 確認 / Gmail 草稿。

寫入面窄化：只碰 direct_apply 表（經 tracker.db 共用函數，與 CLI / bot 同一套邏輯）。
管線本體跑 subprocess（tools.direct_apply），與 Telegram bot 的 prep 觸發同模式。
"""
import json
import subprocess
import sys
import threading

from fastapi import APIRouter, Body, HTTPException

from paths import PROJECT_ROOT
from queries import _app_status_cols

router = APIRouter(prefix="/api/direct-apply")

STATUS_LABELS = {
    "pending": "待探測", "detected": "待確認", "not_found": "未找到窗口",
    "pack_ready": "待審閱", "drafted": "草稿已建", "sent": "已寄出",
    "skipped": "已略過", "failed": "失敗",
}

# 背景執行中的 job_id（單進程去重；重啟即清空，無礙——管線本身冪等）
_running: set[int] = set()


def _db():
    from tracker import db as tdb
    return tdb


@router.get("/list")
def list_all():
    """全部直投記錄 + 各狀態統計（「跑了多少」看這裡）。"""
    tdb = _db()
    with tdb.connect() as conn:
        rows = conn.execute(
            "SELECT d.*, j.company, j.title, j.recommend_score, j.score, j.url, j.source, "
            "j.location, j.tier, j.first_seen, j.liveness_status, j.posting_type, "
            "j.job_type, j.employee_count, j.mentions_ai, j.salary_min, j.salary_max, "
            "cr.openwork_score, cr.openwork_url, "
            f"{_app_status_cols('j')} "
            "FROM direct_apply d JOIN jobs j ON j.id = d.job_id "
            "LEFT JOIN company_ratings cr ON cr.company_name = j.company "
            "WHERE COALESCE(j.blacklisted, 0) = 0 "
            "ORDER BY CASE d.status "
            "  WHEN 'pack_ready' THEN 0 WHEN 'detected' THEN 1 WHEN 'drafted' THEN 2 "
            "  WHEN 'not_found' THEN 3 WHEN 'failed' THEN 4 WHEN 'sent' THEN 5 "
            "  ELSE 6 END, d.updated_at DESC").fetchall()
    items = []
    counts: dict[str, int] = {}
    for r in rows:
        it = dict(r)
        try:
            it["emails"] = json.loads(it.pop("emails_json") or "[]")
        except Exception:
            it["emails"] = []
        it["running"] = r["job_id"] in _running
        items.append(it)
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"items": items, "counts": counts, "total": len(items),
            "labels": STATUS_LABELS}


def _spawn(args: list[str], job_id: int) -> None:
    """背景 subprocess 跑管線；結束後從 running 集合移除。"""
    def _run() -> None:
        try:
            subprocess.run(
                [sys.executable, "-m", "tools.direct_apply", *args],
                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=1800)
        finally:
            _running.discard(job_id)
    _running.add(job_id)
    threading.Thread(target=_run, daemon=True).start()


@router.post("/{job_id}/run")
def run_pipeline(job_id: int):
    """單筆全管線（探測+生成+通知）背景執行。任意職缺可手動觸發，不限分數。"""
    tdb = _db()
    with tdb.connect() as conn:
        if not conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone():
            raise HTTPException(404, f"job {job_id} not found")
    if job_id in _running:
        return {"ok": True, "already_running": True}
    _spawn(["--job-id", str(job_id)], job_id)
    return {"ok": True, "started": True}


@router.put("/{job_id}")
def update(job_id: int, body: dict = Body(...)):
    """確認/修改 email、手動填求人頁、略過、重置。"""
    tdb = _db()
    if tdb.get_direct_apply(job_id) is None:
        # 手動補錄（探測沒跑過也能直接填窗口）
        tdb.upsert_direct_apply(job_id, status="detected", apply_method="email")
    fields = {}
    if "confirmed_email" in body:
        email = (body.get("confirmed_email") or "").strip()
        if email and "@" not in email:
            raise HTTPException(422, "invalid email format")
        fields["confirmed_email"] = email or None
    if "careers_url" in body:
        fields["careers_url"] = (body.get("careers_url") or "").strip() or None
    if "status" in body:
        status = body["status"]
        if status not in tdb.DIRECT_APPLY_STATUSES:
            raise HTTPException(422, f"status must be one of {tdb.DIRECT_APPLY_STATUSES}")
        fields["status"] = status
    if fields:
        tdb.upsert_direct_apply(job_id, **fields)
    return {"ok": True}


@router.post("/{job_id}/draft")
def create_draft(job_id: int):
    """已確認 email → 同步建 Gmail 草稿（永不 send）。"""
    tdb = _db()
    row = tdb.get_direct_apply(job_id)
    if row is None:
        raise HTTPException(404, "no direct-apply record")
    if not row["confirmed_email"]:
        raise HTTPException(422, "email not confirmed — verify the recipient address in the list first")
    proc = subprocess.run(
        [sys.executable, "-m", "tools.direct_apply", "--create-draft", str(job_id)],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise HTTPException(500, (proc.stderr or proc.stdout)[-400:])
    return {"ok": True, "gmail_drafts_url": "https://mail.google.com/mail/u/0/#drafts"}
