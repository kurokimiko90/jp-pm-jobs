"""通知模組 — 統一推送介面。

支援通道：
  - stdout（預設，供 Samurai/cron 管線捕獲）
  - telegram（透過 Bot API 推送，支援 inline keyboard 互動按鈕）

用法:
    from notify import send
    send("有 3 筆逾期跟進", channel="telegram")
    send("有 3 筆逾期跟進")  # stdout fallback
    send("新高分職缺", buttons=[[
        {"text": "生成投遞包", "callback_data": "prep:123"},
        {"text": "忽略", "callback_data": "ignore:123"},
    ]])

環境變數（自動從專案根目錄 .env 載入，不覆蓋既有環境變數）:
    TELEGRAM_BOT_TOKEN  — Telegram Bot token
    TELEGRAM_CHAT_ID    — 目標 chat/group ID
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

_ENV_LOADED = False

# inline keyboard 按鈕：{"text": ..., "callback_data": ...} 或 {"text": ..., "url": ...}
Buttons = list[list[dict]]


def _load_dotenv() -> None:
    """讀專案根目錄 .env（不覆蓋已存在的環境變數）。"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _ENV_LOADED = True


def bot_token() -> str:
    _load_dotenv()
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


def chat_id() -> str:
    _load_dotenv()
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def bot_config(prefix: str = "") -> tuple[str, str]:
    """(token, chat_id) 讀取，prefix 選其他 bot（例如 "ASSISTANT_" 讀
    ASSISTANT_TELEGRAM_BOT_TOKEN / ASSISTANT_TELEGRAM_CHAT_ID）。

    多個獨立 bot 各自 getUpdates 互不干擾（一個 token 只允許一個消費者），
    見 assistant/CLAUDE.md 的職涯助手 bot 與本模組互動 bot 的分工說明。
    """
    _load_dotenv()
    return (
        os.environ.get(f"{prefix}TELEGRAM_BOT_TOKEN", ""),
        os.environ.get(f"{prefix}TELEGRAM_CHAT_ID", ""),
    )


def send(
    message: str, *, channel: str = "auto", title: str | None = None,
    buttons: Buttons | None = None,
) -> bool:
    """推送通知。channel: auto / stdout / telegram。

    auto: 有 Telegram 環境變數就用 Telegram，否則 stdout。
    buttons: inline keyboard（僅 telegram 通道有效，stdout 忽略）。
    回傳 True 表示成功送出。
    """
    if channel == "auto":
        channel = "telegram" if _telegram_configured() else "stdout"

    if channel == "telegram":
        return _send_telegram(message, title, buttons)

    return _send_stdout(message, title)


def _telegram_configured() -> bool:
    return bool(bot_token() and chat_id())


def _send_stdout(message: str, title: str | None = None) -> bool:
    if title:
        print(f"[{title}]", file=sys.stderr)
    print(message, file=sys.stderr)
    return True


def api_call(method: str, payload: dict, timeout: float = 10.0, token: str | None = None):
    """呼叫 Telegram Bot API，回傳 result（失敗回 None）。供 send / bot daemon 共用。

    token 省略時用預設 TELEGRAM_BOT_TOKEN；傳入時打給指定 bot（供其他獨立 bot 復用同一套
    HTTP 呼叫邏輯，見 bot_config()）。
    """
    token = token if token is not None else bot_token()
    if not token:
        return None
    try:
        import httpx
    except ImportError:
        print("[notify] httpx 未安裝", file=sys.stderr)
        return None
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload, timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json().get("result")
        print(f"[notify] Telegram {method} {resp.status_code}: {resp.text[:200]}",
              file=sys.stderr)
        return None
    except Exception as e:
        print(f"[notify] Telegram {method} 失敗: {e}", file=sys.stderr)
        return None


def send_document(path, caption: str = "") -> bool:
    """傳檔案到 Telegram（sendDocument，multipart）。未設定 Telegram 時 stdout 降級。"""
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return False
    if not _telegram_configured():
        return _send_stdout(f"[附件] {p.name} {caption}")
    try:
        import httpx
    except ImportError:
        return _send_stdout(f"[附件] {p.name} {caption}")
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token()}/sendDocument",
            data={"chat_id": chat_id(), "caption": caption[:1000]},
            files={"document": (p.name, p.read_bytes())},
            timeout=60,
        )
        if resp.status_code == 200:
            return True
        print(f"[notify] sendDocument {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[notify] sendDocument 失敗: {e}", file=sys.stderr)
        return False


def send_audio(path, caption: str = "", title: str | None = None,
               performer: str | None = None) -> bool:
    """傳音訊到 Telegram（sendAudio，播放器樣式含時長/標題，優於 sendDocument）。

    未設定 Telegram 時 stdout 降級。
    """
    from pathlib import Path as _P
    p = _P(path)
    if not p.exists():
        return False
    if not _telegram_configured():
        return _send_stdout(f"[音声] {p.name} {caption}")
    try:
        import httpx
    except ImportError:
        return _send_stdout(f"[音声] {p.name} {caption}")
    data = {"chat_id": chat_id(), "caption": caption[:1000]}
    if title:
        data["title"] = title
    if performer:
        data["performer"] = performer
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{bot_token()}/sendAudio",
            data=data,
            files={"audio": (p.name, p.read_bytes())},
            timeout=60,
        )
        if resp.status_code == 200:
            return True
        print(f"[notify] sendAudio {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[notify] sendAudio 失敗: {e}", file=sys.stderr)
        return False


def _send_telegram(message: str, title: str | None = None,
                   buttons: Buttons | None = None) -> bool:
    if not _telegram_configured():
        return _send_stdout(message, title)

    text = f"*{title}*\n\n{message}" if title else message
    payload: dict = {"chat_id": chat_id(), "text": text, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    if api_call("sendMessage", payload) is not None:
        return True
    # 公司名等含 Markdown 特殊字元會 400，去掉 parse_mode 重送一次
    payload.pop("parse_mode")
    payload["text"] = f"{title}\n\n{message}" if title else message
    return api_call("sendMessage", payload) is not None
