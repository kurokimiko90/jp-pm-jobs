"""元氣站：努力之牆（真實數據）+ 每日 check-in + 小勝利 + 拒絕重構。"""
from datetime import date, timedelta

from fastapi import APIRouter, Body, HTTPException

from db import query as jobs_query
from paths import PREP_DIR
from practice_db import execute, query

router = APIRouter(prefix="/api/genki")

# 貓咪文案：規則觸發，非隨機雞湯
CAT_LINES = {
    "low_streak": "low_streak",
    "practiced_today": "practiced_today",
    "applied_recent": "applied_recent",
    "default": "default",
}


@router.get("/summary")
def summary():
    today = date.today()
    # 努力之牆（全部真實數據）
    base = jobs_query(
        "SELECT COUNT(*) total, SUM(score IS NOT NULL) scored, "
        "SUM(gap_analysis IS NOT NULL) gap FROM jobs")[0]
    apps = jobs_query("SELECT status, COUNT(*) n FROM applications GROUP BY status")
    applied = sum(a["n"] for a in apps)
    rejected = next((a["n"] for a in apps if a["status"] == "rejected"), 0)
    prep = len([p for p in PREP_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")])
    practiced = query("SELECT COUNT(*) n FROM reviews")[0]["n"]

    # 30 天 checkin
    since = (today - timedelta(days=29)).isoformat()
    checkins = query("SELECT day, level, note FROM checkins WHERE day >= ? ORDER BY day", (since,))
    today_checkin = next((c for c in checkins if c["day"] == today.isoformat()), None)

    # 連 3 日元氣 ≤2 → 貓咪勸休
    recent = [c["level"] for c in checkins[-3:]]
    low_streak = len(recent) == 3 and all(v <= 2 for v in recent)

    practiced_today = query(
        "SELECT COUNT(*) n FROM reviews WHERE date(reviewed_at)=date('now','localtime')")[0]["n"] > 0

    if low_streak:
        cat_key = "low_streak"
    elif practiced_today:
        cat_key = "practiced_today"
    elif applied > 0:
        cat_key = "applied_recent"
    else:
        cat_key = "default"

    wins = query("SELECT id, day, text FROM wins ORDER BY day DESC, id DESC LIMIT 30")

    # 週活動量（近 8 週：練習次數 + 勝利數）
    weekly = query("""
        SELECT strftime('%Y-%W', reviewed_at) wk, COUNT(*) n FROM reviews
        WHERE reviewed_at >= date('now','-56 days') GROUP BY wk ORDER BY wk""")

    return {
        "wall": {"scraped": base["total"], "scored": base["scored"], "gap": base["gap"],
                 "prep": prep, "practiced": practiced, "applied": applied},
        "checkins": checkins, "today_checkin": today_checkin,
        "cat_key": cat_key, "low_streak": low_streak,
        "wins": wins, "weekly_practice": weekly,
        "rejected": rejected,
        "rejection_note": (
            f"{rejected} rejections ≈ market calibration data, not a verdict on your value."
            if rejected > 0 else None),
    }


@router.post("/checkin")
def checkin(body: dict = Body(...)):
    level = body.get("level")
    if not isinstance(level, int) or not 1 <= level <= 5:
        raise HTTPException(400, "level 1–5 必填")
    execute(
        "INSERT INTO checkins (day, level, note) VALUES (date('now','localtime'),?,?) "
        "ON CONFLICT(day) DO UPDATE SET level=excluded.level, note=excluded.note",
        (level, (body.get("note") or "").strip()[:200]))
    return {"ok": True}


@router.post("/win")
def add_win(body: dict = Body(...)):
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text 必填")
    execute("INSERT INTO wins (day, text) VALUES (date('now','localtime'),?)", (text[:200],))
    return {"ok": True}


@router.delete("/win/{win_id}")
def del_win(win_id: int):
    execute("DELETE FROM wins WHERE id = ?", (win_id,))
    return {"ok": True}
