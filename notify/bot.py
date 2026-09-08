"""Telegram 互動 bot — long-polling daemon（手機遙控器）。

處理通知卡片上的按鈕 callback 與查詢指令：
  callback:  applied:{id} / ignore:{id} / stage:{id}:{status} / prep:{id}
             fu:{id} / snooze:{id}:{days} / qa:{id}
  指令:      /funnel  /today  /help

安全：只回應 .env 的 TELEGRAM_CHAT_ID，其他來源一律靜默丟棄（單人系統）。
冪等：每個 action 對應既有單一 DB 寫入函數，重複點擊安全。
永不 send 郵件：本 bot 無任何寄信能力。

啟動:
    python3 -m notify.bot                 # 前景（debug）
    launchd: scripts/com.jp-pm-jobs.telegram-bot.plist.example
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

from notify import api_call, bot_token, chat_id, send
from tools import app_config
from tracker import db

ROOT = Path(__file__).parent.parent
POLL_TIMEOUT = 50  # getUpdates long-poll 秒數


# ── offset 持久化（重啟不重播舊 update）──────────────────


def _get_offset() -> int:
    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notify_state (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute(
            "SELECT value FROM notify_state WHERE key = 'tg_offset'").fetchone()
        return int(row["value"]) if row else 0


def _set_offset(offset: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notify_state (key, value) VALUES ('tg_offset', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(offset),))


# ── callback actions（回傳顯示在卡片上的結果文字）─────────


def _job_label(job_id: int) -> str:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT company, title FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return f"{row['company'] or '?'}" if row else f"#{job_id}"


def _act_applied(job_id: int) -> str:
    db.record_application(job_id, "applied", notes="via telegram")
    return f"✅ 已標記投遞：{_job_label(job_id)}"


def _act_stage(job_id: int, status: str) -> str:
    if status not in db.FUNNEL_STAGES:
        return f"⚠ 未知階段 {status}"
    db.record_application(job_id, status)  # upsert：未投遞過的邀請也能直接進階段
    return f"▶ {_job_label(job_id)} → {status}"


def _act_ignore(job_id: int) -> str:
    with db.connect() as conn:
        conn.execute("UPDATE jobs SET blacklisted = 1 WHERE id = ?", (job_id,))
    return f"🚫 已忽略：{_job_label(job_id)}"


def _act_fu(job_id: int) -> str:
    from tools.followup import log_followup
    log_followup(job_id, "跟進完成（via telegram）", method="telegram")
    return f"✓ 已記錄跟進：{_job_label(job_id)}"


def _act_snooze(job_id: int, days: str = "3") -> str:
    from tools.followup import snooze_followup
    nxt = snooze_followup(job_id, int(days))
    return f"⏸ {_job_label(job_id)} 推遲到 {nxt}"


def _act_qa(job_id: int) -> str:
    """回推想定問答摘要（電車上速讀用）。"""
    hits = sorted((ROOT / "output" / "prep").glob(f"{job_id}_*/01_interview_qa.md"))
    if not hits:
        return f"（#{job_id} 尚無面試包，先跑 prep.py {job_id} interview）"
    text = hits[0].read_text(encoding="utf-8")
    send(text[:3000], title=f"📋 想定問答 · {_job_label(job_id)}")
    return "已送出想定問答摘要"


def _act_prep(job_id: int) -> str:
    """背景生成投遞包，完成後另發訊息。"""
    if not app_config.get("notify", "bot_allow_prep_trigger", True):
        return "（config 已關閉遠端觸發，請在電腦上跑 prep.py）"

    def _run() -> None:
        proc = subprocess.run(
            [sys.executable, "prep.py", str(job_id), "apply"],
            cwd=ROOT, capture_output=True, text=True, timeout=1800,
        )
        label = _job_label(job_id)
        if proc.returncode == 0:
            hits = sorted((ROOT / "output" / "apply").glob(f"{job_id}_*"))
            path = str(hits[0].relative_to(ROOT)) if hits else "output/apply/"
            send(f"{label}\n輸出：{path}", title=f"📦 投遞包完成 [#{job_id}]")
        else:
            send(f"{label}\n{(proc.stderr or proc.stdout)[-300:]}",
                 title=f"⚠ 投遞包失敗 [#{job_id}]")

    threading.Thread(target=_run, daemon=True).start()
    return f"📦 投遞包生成中：{_job_label(job_id)}（完成後通知）"


def _act_da_draft(job_id: int) -> str:
    """官網直投：把最高信心 email 設為確認地址 → 背景建 Gmail 草稿（永不 send）。"""
    import json

    row = db.get_direct_apply(job_id)
    if row is None:
        return f"⚠ #{job_id} 沒有直投記錄"
    if not row["confirmed_email"]:
        try:
            emails = json.loads(row["emails_json"] or "[]")
        except Exception:
            emails = []
        if not emails:
            return f"⚠ #{job_id} 沒有 email 候選，去 Web UI 手動填"
        db.upsert_direct_apply(job_id, confirmed_email=emails[0]["email"])

    def _run() -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "tools.direct_apply", "--create-draft", str(job_id)],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0:
            send(f"{_job_label(job_id)}\n請在 Gmail 草稿匣確認後手動寄出",
                 title=f"✉️ Gmail 草稿已建 [#{job_id}]",
                 buttons=[[{"text": "📧 開 Gmail 草稿匣",
                            "url": "https://mail.google.com/mail/u/0/#drafts"}]])
        else:
            send(f"{_job_label(job_id)}\n{(proc.stderr or proc.stdout)[-300:]}",
                 title=f"⚠ 草稿建立失敗 [#{job_id}]")

    threading.Thread(target=_run, daemon=True).start()
    return f"✉️ 建立 Gmail 草稿中：{_job_label(job_id)}"


def _act_da_skip(job_id: int) -> str:
    db.upsert_direct_apply(job_id, status="skipped")
    return f"⏭ 直投已略過：{_job_label(job_id)}"


_ACTIONS = {
    "applied": _act_applied,
    "stage": _act_stage,
    "ignore": _act_ignore,
    "fu": _act_fu,
    "snooze": _act_snooze,
    "qa": _act_qa,
    "prep": _act_prep,
    "da_draft": _act_da_draft,
    "da_skip": _act_da_skip,
}


def _handle_callback(cb: dict) -> None:
    data = cb.get("data") or ""
    parts = data.split(":")
    action, args = parts[0], parts[1:]
    handler = _ACTIONS.get(action)
    try:
        if not handler or not args:
            result = f"⚠ 未知操作 {data}"
        else:
            result = handler(int(args[0]), *args[1:])
    except Exception as e:
        result = f"⚠ 失敗：{type(e).__name__}: {e}"

    api_call("answerCallbackQuery",
             {"callback_query_id": cb["id"], "text": result[:190]})
    msg = cb.get("message") or {}
    if msg.get("message_id"):  # 卡片改寫成處理結果，防重複點擊
        api_call("editMessageText", {
            "chat_id": msg["chat"]["id"],
            "message_id": msg["message_id"],
            "text": f"{msg.get('text', '')}\n\n{result}",
        })


# ── 查詢指令 ──────────────────────────────────────────────


def _cmd_funnel() -> str:
    s = db.funnel_stats()
    lines = [f"應募 {s['total']} 筆｜回覆率 {s['replied_rate']:.0%}" if s.get("replied_rate") is not None
             else f"應募 {s['total']} 筆"]
    lines += [f"  {k}: {v}" for k, v in s["by_status"].items() if v]
    return "\n".join(lines)


def _cmd_today() -> str:
    today = date.today().isoformat()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, title, company, score FROM jobs "
            "WHERE first_seen = ? AND COALESCE(blacklisted, 0) = 0 "
            "ORDER BY score DESC LIMIT 8", (today,)).fetchall()
    if not rows:
        return "今日尚無新職缺"
    return "\n".join(f"[{r['id']}] {r['score'] or '-'} {r['title']} @ {r['company'] or '?'}"
                     for r in rows)


def _cmd_pscore(args: list[str]) -> str:
    """提案パックの人間採点 → 90 未満なら背景で書き直して次の版を推送。

    用法: /pscore <job_id> <0-100> [備註…]
    重い処理（LLM 数 call・数分）なので背景 thread で回し、完了は iterate 側の
    Telegram 推送が知らせる。
    """
    if len(args) < 2 or not args[0].isdigit() or not args[1].lstrip("-").isdigit():
        return "用法: /pscore <job_id> <0-100> [備註]"
    job_id, score = int(args[0]), int(args[1])
    if not (0 <= score <= 100):
        return "評分要在 0〜100"
    note = " ".join(args[2:])
    if not app_config.get("notify", "bot_allow_prep_trigger", True):
        return "（config 已關閉遠端觸發，請在電腦上跑 --score）"

    def _run() -> None:
        cmd = [sys.executable, "-m", "proposal", str(job_id),
               "--score", str(score)]
        if note:
            cmd += ["--note", note]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              timeout=3600)
        if proc.returncode != 0:
            # exit 1 = 有 stage 閘門未通過（新版仍已推送）或整體失敗，都值得知道
            send(f"{_job_label(job_id)}\n{(proc.stderr or proc.stdout)[-300:]}",
                 title=f"⚠ 提案重寫有狀況 [#{job_id}]")
        # 成功時は iterate.push / 合格通知が既に飛んでいる — 二重に送らない

    threading.Thread(target=_run, daemon=True).start()
    if score >= 90:
        return f"✅ 記錄 {score}/100 — 合格，確定通知稍後送達"
    return (f"📝 記錄 {score}/100 — 背景重寫中（scope 自動判定），"
            f"新版完成後推送")


_HELP = ("/today — 今日新職缺 top8\n/funnel — 應募漏斗\n"
         "/pscore <job_id> <0-100> [備註] — 提案パック評分（<90 自動重寫）\n"
         "按鈕操作直接點通知卡片")


def _handle_message(msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    if text.startswith("/funnel"):
        send(_cmd_funnel(), title="📊 應募漏斗")
    elif text.startswith("/today"):
        send(_cmd_today(), title="🗾 今日新職缺")
    elif text.startswith("/pscore"):
        send(_cmd_pscore(text.split()[1:]))
    elif text.startswith("/"):
        send(_HELP)


# ── main loop ─────────────────────────────────────────────


def _authorized(chat: dict | None) -> bool:
    return bool(chat) and str(chat.get("id")) == chat_id()


def run() -> None:
    if not (bot_token() and chat_id()):
        print("[bot] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID（.env），退出")
        sys.exit(1)
    print(f"[bot] long-polling 啟動（chat {chat_id()[:4]}***）")
    offset = _get_offset()
    while True:
        updates = api_call("getUpdates", {
            "offset": offset, "timeout": POLL_TIMEOUT,
            "allowed_updates": ["message", "callback_query"],
        }, timeout=POLL_TIMEOUT + 10)
        if updates is None:  # 網路/API 錯誤：退避重試
            time.sleep(15)
            continue
        for u in updates:
            offset = max(offset, u["update_id"] + 1)
            try:
                if "callback_query" in u:
                    cb = u["callback_query"]
                    if _authorized((cb.get("message") or {}).get("chat")):
                        _handle_callback(cb)
                elif "message" in u:
                    if _authorized(u["message"].get("chat")):
                        _handle_message(u["message"])
            except Exception as e:  # 單筆失敗不中斷 daemon
                print(f"[bot] update 處理失敗: {e}", file=sys.stderr)
        if updates:
            _set_offset(offset)


if __name__ == "__main__":
    run()
