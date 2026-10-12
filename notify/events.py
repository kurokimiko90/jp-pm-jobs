"""事件通知 — 管線各節點的 Telegram 推送點。

推送點（皆可由 config/notify.yaml 開關，冪等靠 notify.dedupe）：
  - 新高分職缺（scrape 後處理自動觸發）
  - inbox 重要信件（面試邀請 / offer / 婉拒，inbox.reply 觸發）
  - 面試前日提醒（inbox 掃描每輪檢查，同一場只提醒一次）
  - 面試已確定但缺會議URL（inbox 掃描每輪檢查，每小時提醒一次、上限 5 次）

CLI:
    python3 -m notify.events reminders             # 面試 T-1 提醒
    python3 -m notify.events meeting-url-missing   # 面試已確定但缺會議URL，每小時提醒（上限5次）
    python3 -m notify.events high-scores           # 新高分職缺（手動補跑）
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta

from notify import send
from notify.dedupe import already_sent, escalation_state, mark_sent
from tools import app_config
from tracker import db

# 分類 → 通知標題（不在此清單的類別不推）
_CATEGORY_LABELS = {
    "interview_invite": "🎯 面試邀請",
    "offer": "🎉 Offer",
    "rejection": "📪 選考結果（婉拒）",
}

_DEFAULT_INBOX_CATEGORIES = list(_CATEGORY_LABELS)


def _enabled(key: str, default: bool = True) -> bool:
    return bool(app_config.get("notify", key, default))


# ── 新高分職缺 ────────────────────────────────────────────


def notify_new_high_scores(limit: int = 5) -> int:
    """近 2 天新進且 score ≥ 門檻的職缺，逐筆推送（附互動按鈕）。回傳送出筆數。"""
    if not _enabled("high_score_enabled"):
        return 0
    threshold = int(app_config.get("notify", "high_score_threshold", 80))
    since = (date.today() - timedelta(days=2)).isoformat()

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, title, company, score, tier, source, url, salary_min, salary_max "
            "FROM jobs WHERE score >= ? AND first_seen >= ? "
            "AND COALESCE(blacklisted, 0) = 0 "
            "AND COALESCE(liveness_status, 'active') != 'expired' "
            "ORDER BY score DESC", (threshold, since),
        ).fetchall()

    sent = 0
    for r in rows:
        if sent >= limit:
            break
        if already_sent("high_score", r["id"]):
            continue
        salary = ""
        if r["salary_min"] and r["salary_max"]:
            salary = f"年収 {r['salary_min']}–{r['salary_max']}万 · "
        msg = (f"{r['title']} @ {r['company'] or '?'}\n"
               f"{salary}{r['tier'] or '?'} · {r['source']}")
        buttons = [[
            {"text": "📦 生成投遞包", "callback_data": f"prep:{r['id']}"},
            {"text": "✅ 已投遞", "callback_data": f"applied:{r['id']}"},
        ], [
            {"text": "🚫 忽略", "callback_data": f"ignore:{r['id']}"},
            *([{"text": "🔗 開 JD", "url": r["url"]}] if r["url"] else []),
        ]]
        if send(msg, title=f"💎 新高分職缺 {r['score']} 分 [#{r['id']}]", buttons=buttons):
            mark_sent("high_score", r["id"])
            sent += 1
    return sent


# ── inbox 重要信件 ────────────────────────────────────────


def notify_inbox_important(record: dict) -> bool:
    """面試邀請 / offer / 婉拒等重要信推送。由 inbox.reply 逐封呼叫。"""
    if not _enabled("inbox_enabled"):
        return False
    categories = app_config.get("notify", "inbox_categories", _DEFAULT_INBOX_CATEGORIES)
    cat = record.get("category")
    if cat not in categories or cat not in _CATEGORY_LABELS:
        return False
    msg_id = record.get("gmail_msg_id", "")
    if not msg_id or already_sent("inbox", msg_id):
        return False

    job_id = record.get("job_id")
    company = record.get("sender") or record.get("sender_email") or "?"
    if job_id:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT company, title FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row:
            company = f"{row['company']}（{row['title']}）"

    lines = [company, (record.get("subject") or "")[:80]]
    if record.get("summary"):
        lines.append(record["summary"][:120])

    buttons: list[list[dict]] = []
    if job_id and cat == "interview_invite":
        buttons.append([{"text": "▶ 進入面試階段", "callback_data": f"stage:{job_id}:tech"}])
    if job_id and cat == "rejection":
        buttons.append([{"text": "✖ 標記 rejected", "callback_data": f"stage:{job_id}:rejected"}])
    buttons.append([{"text": "📧 開 Gmail", "url": f"https://mail.google.com/mail/u/0/#all/{msg_id}"}])

    if send("\n".join(lines), title=_CATEGORY_LABELS[cat], buttons=buttons):
        mark_sent("inbox", msg_id)
        return True
    return False


# ── 面試前日提醒 ──────────────────────────────────────────

_DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")


def notify_interview_reminders() -> int:
    """next_event 落在今天/明天的應募，各提醒一次。回傳送出筆數。"""
    if not _enabled("interview_reminder_enabled"):
        return 0
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT a.job_id, a.next_event, j.company, j.title "
            "FROM applications a JOIN jobs j ON j.id = a.job_id "
            "WHERE a.next_event IS NOT NULL AND a.status != 'rejected'",
        ).fetchall()

    today = date.today()
    sent = 0
    for r in rows:
        m = _DATE_RE.search(r["next_event"] or "")
        if not m:
            continue
        try:
            event_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if event_date not in (today, today + timedelta(days=1)):
            continue
        ref = f"{r['job_id']}:{event_date.isoformat()}"
        if already_sent("interview_t1", ref):
            continue
        when = "今天" if event_date == today else "明天"
        msg = (f"{r['company'] or '?'} — {r['title'] or ''}\n"
               f"{r['next_event']}")
        buttons = [[{"text": "📋 想定問答", "callback_data": f"qa:{r['job_id']}"}]]
        if send(msg, title=f"⏰ {when}面試", buttons=buttons):
            mark_sent("interview_t1", ref)
            sent += 1
    return sent


# ── 面試已確定但缺會議URL ──────────────────────────────────

_MEETING_URL_KIND = "meeting_url_missing"


def notify_meeting_url_missing() -> int:
    """已確定但抽不到會議URL的面試（多為 r-agent，需自己上マイページ查），

    每 interval_hours 提醒一次，最多 max_attempts 次；面試當天過後不再提醒。
    回傳送出筆數。
    """
    if not _enabled("meeting_url_missing_enabled"):
        return 0
    interval_hours = int(app_config.get("notify", "meeting_url_missing_interval_hours", 1))
    max_attempts = int(app_config.get("notify", "meeting_url_missing_max_attempts", 5))

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT a.job_id, a.next_event, j.company, j.title "
            "FROM applications a JOIN jobs j ON j.id = a.job_id "
            "WHERE a.next_event IS NOT NULL AND a.meeting_url IS NULL "
            "AND a.status != 'rejected'",
        ).fetchall()

    today = date.today()
    now = datetime.now()
    sent = 0
    for r in rows:
        m = _DATE_RE.search(r["next_event"] or "")
        if not m:
            continue
        try:
            event_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if event_date < today:  # 面試已過，不再提醒
            continue

        prefix = f"{r['job_id']}:{event_date.isoformat()}"
        count, last_at = escalation_state(_MEETING_URL_KIND, prefix)
        if count >= max_attempts:
            continue
        if last_at and now - last_at < timedelta(hours=interval_hours):
            continue

        msg = (f"{r['company'] or '?'} — {r['title'] or ''}\n"
               f"{r['next_event']}\n"
               f"⚠ 尚未取得會議連結，請自行確認（第 {count + 1}/{max_attempts} 次提醒）")
        if send(msg, title="🔗 面試連結未確認"):
            mark_sent(_MEETING_URL_KIND, f"{prefix}:{count + 1}")
            sent += 1
    return sent


# ── 官網直投 ──────────────────────────────────────────────


def notify_direct_apply_ready(job_id: int) -> bool:
    """直投投遞包生成完成 → 推送審閱（附応募メール全文 + brief + 職務経歴書 PDF）。"""
    if not _enabled("direct_apply_enabled"):
        return False
    if already_sent("direct_apply_ready", job_id):
        return False

    from pathlib import Path

    from notify import send_document
    from tracker.db import get_direct_apply

    row = get_direct_apply(job_id)
    if row is None:
        return False
    with db.connect() as conn:
        job = conn.execute(
            "SELECT company, title, recommend_score, score, url FROM jobs WHERE id = ?",
            (job_id,)).fetchone()
    if job is None:
        return False

    emails = []
    try:
        import json
        emails = json.loads(row["emails_json"] or "[]")
    except Exception:
        pass

    lines = [f"{job['title'][:50]} @ {job['company'] or '?'}",
             f"推薦 {job['recommend_score'] or '—'} · score {job['score'] or '—'}"]
    drafted = row["status"] == "drafted" and row["confirmed_email"]
    if drafted:
        lines.append(f"✉️ Gmail 草稿已建 → {row['confirmed_email']}（檢查後手動寄出）")
        if len(emails) > 1:
            lines.append(f"   其他候選 {len(emails) - 1} 個（Web UI 可換）")
    elif row["apply_method"] == "email" and emails:
        top = emails[0]
        lines.append(f"📮 {top['email']}（信心 {top['confidence']:.0%}）— 待確認後建草稿")
        if len(emails) > 1:
            lines.append(f"   其他候選 {len(emails) - 1} 個（Web UI 可選）")
    elif row["apply_method"] == "form":
        lines.append("📝 只有應募フォーム（無 email）— 文案可貼表單")
    else:
        lines.append("❓ 未找到應募窗口 — 可在 Web UI 手動填 email")
    if row["careers_url"]:
        lines.append(f"🔗 求人頁: {row['careers_url']}")

    buttons: list[list[dict]] = []
    if drafted:
        buttons.append([
            {"text": "✉️ 開 Gmail 草稿匣", "url": "https://mail.google.com/mail/u/0/#drafts"}])
    elif row["apply_method"] == "email" and emails:
        buttons.append([
            {"text": "✅ email 正確 → 建 Gmail 草稿", "callback_data": f"da_draft:{job_id}"}])
    row2: list[dict] = [{"text": "⏭ 略過此職缺", "callback_data": f"da_skip:{job_id}"}]
    if row["form_url"]:
        row2.append({"text": "📝 開應募フォーム", "url": row["form_url"]})
    elif job["url"]:
        row2.append({"text": "🔗 開 JD", "url": job["url"]})
    buttons.append(row2)

    if not send("\n".join(lines), title=f"📬 官網直投包完成 [#{job_id}]", buttons=buttons):
        return False
    mark_sent("direct_apply_ready", job_id)

    # 附審閱文件：応募メール（或 EN cover letter）+ 公司 brief + 志望動機特化履歴書
    # （職務経歴書は毎回同じ完成版なので送らない）
    pack_dir = Path(__file__).parent.parent / (row["pack_dir"] or "")
    if row["pack_dir"] and pack_dir.exists():
        for name, caption in (
            ("06_oubo_mail.md", "応募メール"),
            ("03_cover_letter.md", "Cover letter"),
            ("01_company_brief.md", "公司調查 brief"),
            ("05_rirekisho.pdf", "履歴書（志望動機 JD 特化）"),
            ("04_shokumu.html", "定制職務経歴書"),
            ("04_resume_tailored.md", "Tailored resume"),
        ):
            p = pack_dir / name
            if p.exists():
                send_document(p, caption=f"#{job_id} {caption}")
    return True


def notify_direct_apply_sent(job_id: int) -> bool:
    """寄出偵測命中 → 已自動標 applied 的告知。"""
    if already_sent("direct_apply_sent", job_id):
        return False
    with db.connect() as conn:
        job = conn.execute(
            "SELECT company, title FROM jobs WHERE id = ?", (job_id,)).fetchone()
    msg = f"{job['company'] or '?'} — {job['title'][:40]}\n已自動記錄 applications（channel=direct）"
    if send(msg, title=f"📤 官網直投已寄出 [#{job_id}]"):
        mark_sent("direct_apply_sent", job_id)
        return True
    return False


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "reminders":
        n = notify_interview_reminders()
        print(f"面試提醒送出 {n} 筆")
    elif cmd == "meeting-url-missing":
        n = notify_meeting_url_missing()
        print(f"會議連結未確認提醒送出 {n} 筆")
    elif cmd == "high-scores":
        n = notify_new_high_scores()
        print(f"高分職缺送出 {n} 筆")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
