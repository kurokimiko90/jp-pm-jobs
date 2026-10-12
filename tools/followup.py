#!/usr/bin/env python3
"""Follow-up 節奏追蹤 — 投遞後自動計算跟進時程，標記逾期。

Zero-token：純 DB 查詢 + 日期計算。

用法:
    python3 -m tools.followup                    # 顯示所有待跟進
    python3 -m tools.followup --overdue          # 只顯示逾期
    python3 -m tools.followup --json             # JSON 輸出
    python3 -m tools.followup log 42 "HR 回覆面談時間"  # 記錄跟進
    python3 -m tools.followup history 42         # 查看單筆跟進歷史

跟進規則:
    applied → 7 天後第一次跟進
    casual → 5 天後跟進（カジュアル面談，非正式階段，非每家都有）
    recruiter → 5 天後跟進
    tech → 3 天後跟進（面試結果）
    onsite → 3 天後跟進
    offer/rejected → 不需跟進
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from tracker import db

CADENCE_DAYS = {
    "applied": 7,
    "casual": 5,
    "recruiter": 5,
    "tech": 3,
    "onsite": 3,
    "offer": None,
    "rejected": None,
}


def _ensure_followup_tables() -> None:
    with db.connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                logged_at DATE NOT NULL,
                note TEXT NOT NULL,
                method TEXT DEFAULT 'email'
            );
            CREATE INDEX IF NOT EXISTS idx_followups_job ON followups(job_id);
        """)


def get_followup_schedule() -> list[dict]:
    today = date.today()
    apps = db.list_applications()
    schedule = []

    with db.connect() as conn:
        for app in apps:
            cadence = CADENCE_DAYS.get(app["status"])
            if cadence is None:
                continue

            last_action = conn.execute(
                "SELECT MAX(logged_at) as last_fu FROM followups WHERE job_id = ?",
                (app["job_id"],),
            ).fetchone()

            fu_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM followups WHERE job_id = ?",
                (app["job_id"],),
            ).fetchone()["cnt"]

            base_date_str = (
                last_action["last_fu"] if last_action and last_action["last_fu"]
                else app["last_updated"]
            )
            base_date = date.fromisoformat(base_date_str[:10])
            next_followup = base_date + timedelta(days=cadence)
            days_overdue = (today - next_followup).days

            schedule.append({
                "job_id": app["job_id"],
                "title": app["title"],
                "company": app["company"],
                "status": app["status"],
                "applied_at": app["applied_at"],
                "last_updated": app["last_updated"],
                "followup_count": fu_count,
                "next_followup": next_followup.isoformat(),
                "days_overdue": days_overdue,
                "is_overdue": days_overdue > 0,
                "urgency": _urgency(days_overdue, app["status"]),
                "score": app["score"],
            })

    return sorted(schedule, key=lambda x: (-x["days_overdue"], -(x["score"] or 0)))


def _urgency(days_overdue: int, status: str) -> str:
    if days_overdue <= 0:
        return "on_track"
    if days_overdue <= 3:
        return "soon"
    if days_overdue <= 7:
        return "overdue"
    return "critical"


def log_followup(job_id: int, note: str, method: str = "email") -> int:
    _ensure_followup_tables()
    today = date.today().isoformat()
    with db.connect() as conn:
        app = conn.execute(
            "SELECT id FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not app:
            raise ValueError(f"job_id={job_id} 尚未投遞，無法記錄跟進")
        cur = conn.execute(
            "INSERT INTO followups (job_id, logged_at, note, method) VALUES (?, ?, ?, ?)",
            (job_id, today, note, method),
        )
        conn.execute(
            "UPDATE applications SET last_updated = ? WHERE job_id = ?",
            (today, job_id),
        )
        return cur.lastrowid


def snooze_followup(job_id: int, days: int = 3) -> str:
    """把下次跟進推遲到 today+days。回傳新的下次跟進日（ISO）。

    原理：schedule 以 MAX(followups.logged_at) + cadence 計算，
    寫入一筆基準日位移的 snooze 記錄即可，不動 cadence 規則。
    """
    _ensure_followup_tables()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT status FROM applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        cadence = CADENCE_DAYS.get(row["status"] if row else "applied") or 7
        base = date.today() + timedelta(days=days - cadence)
        conn.execute(
            "INSERT INTO followups (job_id, logged_at, note, method) VALUES (?, ?, ?, ?)",
            (job_id, base.isoformat(), f"snooze +{days}d", "snooze"),
        )
    return (date.today() + timedelta(days=days)).isoformat()


def get_followup_history(job_id: int) -> list[dict]:
    _ensure_followup_tables()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM followups WHERE job_id = ? ORDER BY logged_at DESC",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def notify_overdue(schedule: list[dict]) -> None:
    """推送逾期跟進提醒到 notify 模組。"""
    overdue = [s for s in schedule if s["is_overdue"]]
    if not overdue:
        return

    lines = [f"逾期跟進 {len(overdue)} 筆："]
    for s in overdue[:10]:
        company = (s["company"] or "?")[:18]
        lines.append(
            f"  [{s['job_id']}] {company} — {s['status']} "
            f"逾期 {s['days_overdue']}d ({s['urgency']})"
        )
    if len(overdue) > 10:
        lines.append(f"  … 共 {len(overdue)} 筆")

    # 前 5 筆附互動按鈕（notify/bot.py 處理 callback）
    buttons = [
        [{"text": f"✓ 已跟進 {(s['company'] or '?')[:10]}",
          "callback_data": f"fu:{s['job_id']}"},
         {"text": "+3天", "callback_data": f"snooze:{s['job_id']}:3"}]
        for s in overdue[:5]
    ]

    from notify import send
    send("\n".join(lines), title="Follow-up 逾期提醒", buttons=buttons)


def print_schedule(schedule: list[dict], overdue_only: bool = False) -> None:
    filtered = [s for s in schedule if s["is_overdue"]] if overdue_only else schedule
    if not filtered:
        print("（無待跟進項目）")
        return

    print(f"{'ID':>4} {'狀態':>9} {'公司':<20} {'下次跟進':<12} {'逾期':>4} {'急迫度':<8} {'已跟進':>3}")
    print("-" * 80)
    for s in filtered:
        urgency_mark = {"on_track": "  ", "soon": "⏳", "overdue": "⚠️", "critical": "🔴"}.get(s["urgency"], "  ")
        company = (s["company"] or "?")[:18]
        print(
            f"{s['job_id']:>4} {s['status']:>9} {company:<20} "
            f"{s['next_followup']:<12} {s['days_overdue']:>+4}d {urgency_mark} {s['urgency']:<8} {s['followup_count']:>3}"
        )


def main() -> None:
    _ensure_followup_tables()

    p = argparse.ArgumentParser(description="Follow-up 節奏追蹤")
    sub = p.add_subparsers(dest="cmd")

    show = sub.add_parser("show", help="顯示跟進時程（預設）")
    show.add_argument("--overdue", action="store_true", help="只顯示逾期")
    show.add_argument("--json", action="store_true", help="JSON 輸出")
    show.add_argument("--notify", action="store_true", help="推送逾期提醒（Telegram / stdout）")

    lg = sub.add_parser("log", help="記錄一次跟進")
    lg.add_argument("job_id", type=int)
    lg.add_argument("note")
    lg.add_argument("--method", default="email", choices=["email", "phone", "linkedin", "agent", "other"])

    hist = sub.add_parser("history", help="查看跟進歷史")
    hist.add_argument("job_id", type=int)

    args = p.parse_args()
    cmd = args.cmd or "show"

    if cmd == "show":
        schedule = get_followup_schedule()
        if hasattr(args, "json") and args.json:
            print(json.dumps(schedule, ensure_ascii=False, indent=2))
        else:
            print_schedule(schedule, overdue_only=getattr(args, "overdue", False))
        if getattr(args, "notify", False):
            notify_overdue(schedule)

    elif cmd == "log":
        fid = log_followup(args.job_id, args.note, args.method)
        print(f"已記錄跟進 followup_id={fid} job_id={args.job_id}")

    elif cmd == "history":
        history = get_followup_history(args.job_id)
        if not history:
            print(f"job_id={args.job_id} 無跟進紀錄")
        else:
            for h in history:
                print(f"  [{h['logged_at']}] ({h['method']}) {h['note']}")


if __name__ == "__main__":
    main()
