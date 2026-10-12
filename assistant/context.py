"""白名單資料檢索 — AI 職涯助手只能讀這裡定義的函數，不開放自由 SQL。

每個函數對應一種可被引用的事實來源，回傳結構化 dict/list，供 chat.py 組 prompt
與附上引用（citation）。零編造紀律：LLM 只能複述這裡給的事實，不能自己編數字。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from tools import followup
from tracker import db

from . import lookup

# next_event 是自由文字，實測同時存在 2026/09/07(月) 與 2026-09-03 11:00 兩種寫法。
# 只認 `/` 會讓 `-` 格式的日程整條漏掉（近期面試區塊直接看不到那家公司）。
_DATE_RE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")

# 漏斗階段的人話標籤（同一份代碼在 dashboard/backend/applications.py 也有對應）
STATUS_LABELS = {
    "applied": "書類選考中", "casual": "カジュアル面談", "recruiter": "一次面接",
    "tech": "技術面接", "onsite": "最終面接", "offer": "內定", "rejected": "不採用",
}
# 已收到回覆、選考仍在進行中的階段（applied = 尚未回覆，rejected = 已結束）
ACTIVE_STAGES = ("casual", "recruiter", "tech", "onsite", "offer")

# 見送り段階／理由の表示名（dashboard/backend/applications.py と同じ対応表）
REJECTION_STAGE_LABELS = {
    "shorui": "書類選考", "casual": "カジュアル面談", "ichiji": "一次面接",
    "niji": "二次面接", "saishu": "最終面接",
}
REJECTION_REASON_LABELS = {
    "experience": "経験不足", "language": "語言", "age": "年齢帯",
    "salary": "薪資", "unspecified": "無說明",
}


def funnel_snapshot() -> dict:
    """應募漏斗統計（近 30 天 + 全期）。"""
    since = (date.today() - timedelta(days=30)).isoformat()
    return {
        "last_30d": db.funnel_stats(since=since),
        "all_time": db.funnel_stats(),
    }


def active_pipeline() -> list[dict]:
    """進行中的選考 — 「現在還有幾條線在跑」的唯一事實來源。

    只有這裡能回答「推進中的面試管線有多少條」。缺這塊時 LLM 會退回去讀
    [對話紀錄] 裡的舊職缺，把上週的公司當成現在的進度講。
    """
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT a.job_id, a.status, a.applied_at, a.last_updated, a.next_event, "
            "j.company, j.title FROM applications a JOIN jobs j ON j.id = a.job_id "
            f"WHERE a.status IN ({','.join('?' * len(ACTIVE_STAGES))}) "
            "ORDER BY a.last_updated DESC",
            ACTIVE_STAGES,
        ).fetchall()
    return [dict(r) for r in rows]


def _event_note(next_event: str | None) -> str:
    """把 next_event 標成「將到」或「已過」。

    `applications.next_event` 是自由文字，過去的日程不會自動清掉。不標的話
    LLM 會把上週已結束的面試當成即將發生的行程講出來。
    """
    if not next_event:
        return ""
    m = _DATE_RE.search(next_event)
    if not m:
        return f"，下次日程：{next_event}"
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return f"，下次日程：{next_event}"
    delta = (d - date.today()).days
    tag = f"{delta}天後" if delta >= 0 else f"已過期{-delta}天，可能已結束或未更新"
    return f"，日程：{next_event}（{tag}）"


def awaiting_reply(limit: int = 10) -> list[dict]:
    """已投遞、尚未收到任何回覆（status=applied）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT a.job_id, a.status, a.applied_at, a.last_updated, "
            "j.company, j.title FROM applications a JOIN jobs j ON j.id = a.job_id "
            "WHERE a.status = 'applied' ORDER BY a.applied_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_applications(days: int = 21, limit: int = 15) -> list[dict]:
    """近 N 天的投遞（含已不採用）— 「最近投了哪些公司」的事實來源。"""
    since = (date.today() - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT a.job_id, a.status, a.applied_at, j.company, j.title "
            "FROM applications a JOIN jobs j ON j.id = a.job_id "
            "WHERE a.applied_at >= ? ORDER BY a.applied_at DESC LIMIT ?", (since, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_new_jobs(days: int = 3, limit: int = 8) -> list[dict]:
    """近 N 天新入庫的職缺。

    只看 `first_seen = today` 會在爬蟲當天沒跑時整段消失，回答就變成「無新職缺」
    或改抄舊資料；改成近 N 天並附上入庫日，讓 LLM 能講清楚是哪一天的。
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, title, company, score, tier, first_seen FROM jobs "
            "WHERE first_seen >= ? AND COALESCE(blacklisted, 0) = 0 "
            "ORDER BY score DESC LIMIT ?", (since, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def today_new_jobs(limit: int = 8) -> list[dict]:
    today = date.today().isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, title, company, score, tier FROM jobs "
            "WHERE first_seen = ? AND COALESCE(blacklisted, 0) = 0 "
            "ORDER BY score DESC LIMIT ?", (today, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def overdue_followups(limit: int = 5) -> list[dict]:
    schedule = followup.get_followup_schedule()
    return [s for s in schedule if s["is_overdue"]][:limit]


def upcoming_interviews(days: int = 3) -> list[dict]:
    """applications.next_event 落在近 N 天內的面試/日程。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT a.job_id, a.next_event, j.company, j.title "
            "FROM applications a JOIN jobs j ON j.id = a.job_id "
            "WHERE a.next_event IS NOT NULL AND a.status != 'rejected'"
        ).fetchall()

    today = date.today()
    out = []
    for r in rows:
        m = _DATE_RE.search(r["next_event"] or "")
        if not m:
            continue
        try:
            event_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        delta = (event_date - today).days
        if 0 <= delta <= days:
            out.append({
                "job_id": r["job_id"], "company": r["company"], "title": r["title"],
                "next_event": r["next_event"], "days_until": delta,
            })
    return sorted(out, key=lambda x: x["days_until"])


def high_score_unread_jobs(threshold: int = 80, limit: int = 5) -> list[dict]:
    """高分且尚未進漏斗（未投遞）的職缺 — 候選人可能還沒看過。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT j.id, j.title, j.company, j.score, j.tier FROM jobs j "
            "LEFT JOIN applications a ON a.job_id = j.id "
            "WHERE j.score >= ? AND a.id IS NULL AND COALESCE(j.blacklisted, 0) = 0 "
            "ORDER BY j.score DESC LIMIT ?", (threshold, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def gap_batch_highlights(limit: int = 5) -> dict | None:
    """最新一批 gap 分析的推薦度 top N。"""
    with db.connect() as conn:
        batch = conn.execute(
            "SELECT id, created_at, job_count FROM gap_batches ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not batch:
            return None
        rows = conn.execute(
            "SELECT id, title, company, recommend_score FROM jobs "
            "WHERE gap_batch_id = ? AND recommend_score IS NOT NULL "
            "ORDER BY recommend_score DESC LIMIT ?", (batch["id"], limit),
        ).fetchall()
    return {
        "batch_id": batch["id"], "created_at": batch["created_at"],
        "job_count": batch["job_count"], "top": [dict(r) for r in rows],
    }


def _job_line(h: dict) -> str:
    """單筆職缺的一行摘要（含應募狀態、日程、gap 理由）。"""
    if h.get("status"):
        status = (f"應募狀態={STATUS_LABELS.get(h['status'], h['status'])}"
                  f"（{h['applied_at']} 投遞，最後更新 {h['last_updated']}）")
        if h["status"] == "rejected":
            stage = REJECTION_STAGE_LABELS.get(h.get("rejection_stage") or "")
            reason = REJECTION_REASON_LABELS.get(h.get("rejection_reason") or "")
            detail = "／".join(x for x in (stage, reason) if x)
            status += f"（見送り: {detail}）" if detail else "（見送り段階の記録なし）"
    else:
        status = "未投遞"
    line = (f"job:{h['id']} {h.get('company', '')}「{h['title']}」{status}"
            f"／score={h['score']}／推薦度={h['recommend_score']}"
            f"{_event_note(h.get('next_event'))}")
    if h.get("gap_reason"):
        line += f"\n    gap 分析：{h['gap_reason']}"
    return line


def _company_block(hits: list[dict]) -> str:
    """企業単位の応募歴。「投過沒有」に答えられる唯一の区塊。"""
    lines = []
    for c in hits:
        head = (f"■ {c['company']}（庫內共 {c['job_count']} 筆職缺；"
                f"比對命中「{c['matched_text']}」）")
        if c["applied"]:
            head += f" → **應募過，共 {len(c['applications'])} 筆**"
        else:
            head += " → **未應募過（庫內有此公司職缺，但沒有任何投遞紀錄）**"
        lines.append(head)
        lines.extend("  " + _job_line(a) for a in c["applications"])
        lines.extend("  " + _job_line(o) for o in c["other_jobs"])
    return "[提問提到的企業（應募歷史）]\n" + "\n".join(lines)


def build_context(question: str | None = None) -> str:
    """組成給 LLM 的結構化事實摘要（純文字，附引用 ID）。

    `question` 傳入時額外附上「提問提到的職缺」區塊（見 lookup_jobs）。
    """
    funnel = funnel_snapshot()["last_30d"]
    today_jobs = recent_new_jobs()
    overdue = overdue_followups()
    interviews = upcoming_interviews()
    unread = high_score_unread_jobs()
    gap = gap_batch_highlights()
    active = active_pipeline()
    waiting = awaiting_reply()
    recent_apps = recent_applications()

    by_status = funnel.get("by_status") or {}
    dist = "、".join(
        f"{STATUS_LABELS.get(k, k)} {v}" for k, v in by_status.items() if v
    )
    head = (
        f"[資料截止] {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        "（以下每個數字都是此刻從資料庫重算的，與過去回答不一致時以此為準）"
    )
    funnel_line = f"[應募漏斗-近30天] 總投遞 {funnel['total']} 筆"
    if funnel.get("replied_rate") is not None:
        funnel_line += f"，回覆率 {funnel['replied_rate']:.0%}"
    if dist:
        funnel_line += f"\n階段分佈：{dist}"

    parts = [head, funnel_line]

    if question:
        by_id = lookup.find_jobs_by_id(question)
        if by_id:
            parts.append("[提問指名的職缺（當前狀態）]\n"
                         + "\n".join(_job_line(h) for h in by_id))

        companies = lookup.find_companies(question)
        if companies:
            parts.append(_company_block(companies))
        elif lookup.has_apply_intent(question):
            # 意圖是查「投過沒有」卻一家都沒對上 → 必須明說查無，
            # 否則 LLM 會拿別家的紀錄硬套，或含糊回一句「可能有投過」。
            parts.append(
                "[提問提到的企業（應募歷史）] 查無。已用模糊比對掃過 jobs 表全部"
                "公司名，沒有任何一家與提問中的企業名相符。這代表：該公司不在本"
                "專案資料庫中＝沒有透過本管線投遞過（庫外自行投遞的不在此列，"
                "如有請說明無法確認）。"
            )

    if active:
        lines = [
            f"job:{a['job_id']} {a['company']}「{a['title']}」"
            f"{STATUS_LABELS.get(a['status'], a['status'])}"
            f"（{a['applied_at']} 投遞，最後更新 {a['last_updated']}"
            f"{_event_note(a['next_event'])}）"
            for a in active
        ]
        parts.append(f"[進行中的選考 共 {len(active)} 條]\n" + "\n".join(lines))
    else:
        parts.append("[進行中的選考] 0 條（無已回覆且仍在進行的應募）")

    if waiting:
        lines = [f"job:{w['job_id']} {w['company']}「{w['title']}」{w['applied_at']} 投遞"
                 for w in waiting]
        parts.append(f"[已投遞待回覆 共 {len(waiting)} 筆]\n" + "\n".join(lines))
    else:
        parts.append("[已投遞待回覆] 無")

    if recent_apps:
        lines = [f"job:{a['job_id']} {a['company']}「{a['title']}」{a['applied_at']} 投遞"
                 f"／{STATUS_LABELS.get(a['status'], a['status'])}" for a in recent_apps]
        parts.append("[近 21 天投遞]\n" + "\n".join(lines))
    else:
        parts.append("[近 21 天投遞] 無")

    if interviews:
        lines = [f"job:{i['job_id']} {i['company']}「{i['title']}」{i['next_event']}"
                  f"（{i['days_until']}天後）" for i in interviews]
        parts.append("[近期面試/日程]\n" + "\n".join(lines))
    else:
        parts.append("[近期面試/日程] 無")

    if overdue:
        lines = [f"job:{o['job_id']} {o['company']} 逾期 {o['days_overdue']} 天未跟進"
                  for o in overdue]
        parts.append("[逾期跟進]\n" + "\n".join(lines))
    else:
        parts.append("[逾期跟進] 無")

    if unread:
        lines = [f"job:{j['id']} {j['company']}「{j['title']}」score={j['score']}"
                  for j in unread]
        parts.append("[高分未投遞職缺]\n" + "\n".join(lines))
    else:
        parts.append("[高分未投遞職缺] 無")

    if today_jobs:
        lines = [f"job:{j['id']} {j['company']}「{j['title']}」score={j['score']}"
                  f"（{j['first_seen']} 入庫）" for j in today_jobs]
        parts.append("[近 3 天新職缺]\n" + "\n".join(lines))
    else:
        parts.append("[近 3 天新職缺] 無")

    if gap:
        lines = [f"job:{j['id']} {j['company']}「{j['title']}」推薦度={j['recommend_score']}"
                  for j in gap["top"]]
        parts.append(
            f"[最新 Gap 批次 #{gap['batch_id']}（{gap['created_at']}，共 {gap['job_count']} 筆）]\n"
            + "\n".join(lines)
        )
    else:
        parts.append("[Gap 分析] 尚無批次")

    return "\n\n".join(parts)


def findings() -> list[dict]:
    """右側「AI 目前發現」— 按優先序排列的主動提醒，附可跳轉的 job_id。"""
    out: list[dict] = []
    for i in upcoming_interviews():
        out.append({
            "level": "P0", "tag": "面試將近",
            "text": f"{i['company']}「{i['title']}」{i['days_until']}天後",
            "job_id": i["job_id"],
        })
    for o in overdue_followups(limit=3):
        out.append({
            "level": "P1", "tag": "逾期跟進",
            "text": f"{o['company']} 逾期 {o['days_overdue']} 天",
            "job_id": o["job_id"],
        })
    for j in high_score_unread_jobs(limit=3):
        out.append({
            "level": "P2", "tag": "高分未投遞",
            "text": f"{j['company']}「{j['title']}」score={j['score']}",
            "job_id": j["id"],
        })
    return out
