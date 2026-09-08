"""收件匣招聘郵件 API：列表（唯讀）+ 觸發掃描 + 更新狀態。

讀 inbox_mails 表（唯讀連線）；狀態寫入沿用 applications.py 的 rw 連線 pattern。
掃描以背景 subprocess 觸發 `python3 -m inbox.reply`（避免阻塞請求）。
"""
import sqlite3
import subprocess
import sys

from fastapi import APIRouter, Body, HTTPException

from paths import DB_PATH, PROJECT_ROOT

router = APIRouter(prefix="/api/inbox")

STATUSES = ["new", "classified", "draft_ready", "sent", "skipped"]


def _ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rw() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@router.get("")
def list_inbox():
    conn = _ro()
    try:
        rows = conn.execute(
            "SELECT m.*, j.company, j.title FROM inbox_mails m "
            "LEFT JOIN jobs j ON j.id = m.job_id "
            "WHERE COALESCE(j.blacklisted, 0) = 0 "
            "ORDER BY m.received_at DESC, m.id DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"mails": [], "categories": {}}  # inbox_mails 表尚未建立
    finally:
        conn.close()

    mails = [dict(r) for r in rows]
    cats: dict[str, int] = {}
    for m in mails:
        key = m.get("category") or "other"
        cats[key] = cats.get(key, 0) + 1
    return {"mails": mails, "categories": cats}


@router.post("/scan")
def scan(body: dict = Body(default={})):
    """背景觸發收件匣掃描。注意：reply.py 會呼叫 LLM 指揮中心；
    背景進程下若 :3005 未啟動會在 log 失敗（不影響本請求）。"""
    days = int(body.get("days", 7))
    dry = bool(body.get("dry_run", False))
    args = [sys.executable, "-m", "inbox.reply", "--days", str(days)]
    if dry:
        args.append("--dry-run")
    subprocess.Popen(
        args,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "started": True, "days": days, "dry_run": dry}


@router.post("/{mail_id}/status")
def set_status(mail_id: int, body: dict = Body(...)):
    status = body.get("status")
    if status not in STATUSES:
        raise HTTPException(400, f"status 必填（{'/'.join(STATUSES)}）")
    conn = _rw()
    try:
        if not conn.execute(
            "SELECT 1 FROM inbox_mails WHERE id = ?", (mail_id,)
        ).fetchone():
            raise HTTPException(404, "mail 不存在")
        conn.execute(
            "UPDATE inbox_mails SET status = ? WHERE id = ?", (status, mail_id)
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}
