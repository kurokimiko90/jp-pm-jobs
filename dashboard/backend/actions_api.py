"""操作端點 — 跟進打卡 / snooze / 職缺忽略（blacklist）/ 通知測試。

寫入面窄化：followups 表 + jobs.blacklisted 單欄 + applications.last_updated
（經 tools.followup 共用函數，與 Telegram bot 同一套邏輯，行為一致）。
"""
import sqlite3

from fastapi import APIRouter, Body, HTTPException

from paths import DB_PATH

router = APIRouter()


@router.post("/api/followups/{job_id}")
def followup_log(job_id: int, body: dict = Body(default={})):
    """記錄一次跟進（重置跟進節奏時鐘）。"""
    from tools.followup import log_followup
    note = (body.get("note") or "跟進完成（via dashboard）").strip()[:200]
    method = body.get("method") or "dashboard"
    try:
        fu_id = log_followup(job_id, note, method=method)
    except sqlite3.Error as e:
        raise HTTPException(500, f"DB error: {e}")
    return {"ok": True, "followup_id": fu_id}


@router.post("/api/followups/{job_id}/snooze")
def followup_snooze(job_id: int, body: dict = Body(default={})):
    """把下次跟進推遲 N 天（預設 3）。"""
    from tools.followup import snooze_followup
    days = int(body.get("days") or 3)
    if not 1 <= days <= 30:
        raise HTTPException(422, "days 必須在 1-30")
    try:
        next_date = snooze_followup(job_id, days)
    except sqlite3.Error as e:
        raise HTTPException(500, f"DB error: {e}")
    return {"ok": True, "next_followup": next_date}


@router.post("/api/jobs/{job_id}/blacklist")
def job_blacklist(job_id: int, body: dict = Body(default={})):
    """忽略/取消忽略職缺（jobs.blacklisted 單欄，所有列表查詢都會過濾）。"""
    on = bool(body.get("on", True))
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE jobs SET blacklisted = ? WHERE id = ?", (1 if on else 0, job_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, f"job {job_id} 不存在")
    finally:
        conn.close()
    return {"ok": True, "blacklisted": on}


@router.post("/api/notify/test")
def notify_test():
    """發一則測試通知，驗證 .env 的 Telegram 設定。"""
    from notify import send, _telegram_configured
    configured = _telegram_configured()
    ok = send("🔔 測試通知 — dashboard 設定正常")
    return {"ok": ok, "channel": "telegram" if configured else "stdout"}
