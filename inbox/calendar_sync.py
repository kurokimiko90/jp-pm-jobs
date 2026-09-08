"""確定面接日程 → 自動建立/更新 Google Calendar 事件。

沿用 inbox/auth.py 的同一組 OAuth token（額外 calendar.events scope，
既有 token 若未含此 scope 需重跑 `python3 -m inbox.auth` 補授權）。

事件 id 存在 applications.gcal_event_id，重複觸發（如リマインド信同值重掃）
只 patch 既有事件，不建重複事件；找不到既有事件（被使用者手動刪除）則改建新的。
Calendar API 呼叫失敗（未授權/API 未啟用等）不中斷呼叫端流程，僅印警告。
"""
from __future__ import annotations

import datetime as dt

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from inbox.auth import get_credentials
from tracker.db import connect

CALENDAR_ID = "primary"
DEFAULT_DURATION_MIN = 60
_JST = dt.timezone(dt.timedelta(hours=9))


def get_service():
    """回傳 Calendar API service（v3）。"""
    return build("calendar", "v3", credentials=get_credentials(), cache_discovery=False)


def _parse_start(date: str, time: str) -> dt.datetime:
    y, m, d = (int(x) for x in date.split("/"))
    hh, mm = (int(x) for x in time.split(":"))
    return dt.datetime(y, m, d, hh, mm, tzinfo=_JST)


def upsert_event(
    job_id: int,
    company: str,
    title: str,
    date: str,
    time: str,
    duration_min: int | None = None,
    meeting_url: str | None = None,
) -> str | None:
    """建立/更新面接事件，回傳 event_id；失敗回 None（不拋例外）。

    meeting_url: 郵件抽出できたオンライン会議URL（TimeRex等）。r-agent形式は
    メール本文にURLが載らないため通常 None——その場合は location/description を付けない。
    """
    start = _parse_start(date, time)
    end = start + dt.timedelta(minutes=duration_min or DEFAULT_DURATION_MIN)
    body = {
        "summary": f"面接: {company}" + (f"（{title}）" if title else ""),
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "reminders": {"useDefault": True},
    }
    if meeting_url:
        body["location"] = meeting_url
        body["description"] = f"面接URL: {meeting_url}"
    try:
        svc = get_service()
        with connect() as conn:
            row = conn.execute(
                "SELECT gcal_event_id FROM applications WHERE job_id = ?", (job_id,)
            ).fetchone()
        event_id = row["gcal_event_id"] if row else None

        if event_id:
            try:
                ev = svc.events().patch(
                    calendarId=CALENDAR_ID, eventId=event_id, body=body
                ).execute()
                return ev["id"]
            except HttpError as e:
                if e.resp.status != 404:
                    raise
                event_id = None  # 事件已被刪除（使用者手動移除等）→ 改建新的

        ev = svc.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        with connect() as conn:
            conn.execute(
                "UPDATE applications SET gcal_event_id = ? WHERE job_id = ?",
                (ev["id"], job_id),
            )
        return ev["id"]
    except Exception as e:
        print(f"  （Google 日曆同步略過：{e}）")
        return None
