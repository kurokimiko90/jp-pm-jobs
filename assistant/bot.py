"""AI 職涯助手專用 Telegram 互動 daemon — 獨立 bot token，跟 notify/bot.py 分開。

為什麼要另開一個 bot（而不是塞進 notify/bot.py）：
  1. notify/bot.py 是管線通知/操作 bot（按鈕 callback 為主），混進自由文字問答會讓
     訊息流互相干擾，聊天記錄也會混在一起不好回溯。
  2. 一個 Telegram bot token 只允許一個 getUpdates 消費者，獨立 token 天生沒有這問題。
  3. 比照 Samurai 專案「一個角色一個 bot」的既有慣例（NAVIGATOR/STRATEGIST/... 各自
     token），本專案目前只需要一個「職涯助手」角色，故只開一個新 bot。

設定：.env 的 ASSISTANT_TELEGRAM_BOT_TOKEN / ASSISTANT_TELEGRAM_CHAT_ID
（@BotFather 另建一個新 bot，流程同 TELEGRAM_BOT_TOKEN，見 .env.example）。

啟動:
    python3 -m assistant.bot                # 前景（debug）
    launchd: scripts/com.jp-pm-jobs.assistant-bot.plist.example
"""

from __future__ import annotations

import sys
import time

from notify import api_call, bot_config
from tracker import db

from . import chat

POLL_TIMEOUT = 50  # getUpdates long-poll 秒數
_PREFIX = "ASSISTANT_"
_OFFSET_KEY = "assistant_tg_offset"

_HELP = "直接輸入問題即可（職缺 / 應募漏斗 / 面試準備 / Gap 分析）。\n/help — 顯示本說明"


def _get_offset() -> int:
    with db.connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS notify_state (key TEXT PRIMARY KEY, value TEXT)")
        row = conn.execute(
            "SELECT value FROM notify_state WHERE key = ?", (_OFFSET_KEY,)).fetchone()
        return int(row["value"]) if row else 0


def _set_offset(offset: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO notify_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_OFFSET_KEY, str(offset)))


def _authorized(chat_obj: dict | None, expected_chat_id: str) -> bool:
    return bool(chat_obj) and str(chat_obj.get("id")) == expected_chat_id


def _reply(token: str, chat_id: str, text: str) -> None:
    api_call("sendMessage", {"chat_id": chat_id, "text": text[:4000]}, token=token)


def _handle_message(token: str, chat_id: str, msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if text.startswith("/help") or text.startswith("/start"):
        _reply(token, chat_id, _HELP)
        return
    try:
        result = chat.answer(text, channel="telegram")
        _reply(token, chat_id, result["answer"])
    except Exception as e:  # 單筆失敗不中斷 daemon
        _reply(token, chat_id, f"⚠ 回答失敗：{type(e).__name__}: {e}")


def run() -> None:
    token, chat_id = bot_config(_PREFIX)
    if not (token and chat_id):
        print("[assistant.bot] 未設定 ASSISTANT_TELEGRAM_BOT_TOKEN / "
              "ASSISTANT_TELEGRAM_CHAT_ID（.env），退出")
        sys.exit(1)
    print(f"[assistant.bot] long-polling 啟動（chat {chat_id[:4]}***）")
    offset = _get_offset()
    while True:
        updates = api_call("getUpdates", {
            "offset": offset, "timeout": POLL_TIMEOUT,
            "allowed_updates": ["message"],
        }, timeout=POLL_TIMEOUT + 10, token=token)
        if updates is None:  # 網路/API 錯誤：退避重試
            time.sleep(15)
            continue
        for u in updates:
            offset = max(offset, u["update_id"] + 1)
            msg = u.get("message")
            if msg and _authorized(msg.get("chat"), chat_id):
                try:
                    _handle_message(token, chat_id, msg)
                except Exception as e:
                    print(f"[assistant.bot] update 處理失敗: {e}", file=sys.stderr)
        if updates:
            _set_offset(offset)


if __name__ == "__main__":
    run()
