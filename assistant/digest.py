"""每日/每週總結 — 今天/這週問了什麼、求職進度變化，推播到職涯助手專用 bot。

分工比照 gap_analyzer(Haiku)/gap_summary(Sonnet)：daily 純規則彙整（量小不需要
LLM），weekly 用 LLM 歸納重複主題（量夠大才值得歸納）。

CLI:
    python3 -m assistant.digest daily
    python3 -m assistant.digest weekly
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from interview._llm import call
from notify import api_call, bot_config
from tools.app_config import get as _cfg

from . import context, store

DIGEST_MODEL = _cfg("assistant", "digest_model", "claude-sonnet-4-5")
_PREFIX = "ASSISTANT_"


def _enabled(key: str, default: bool = True) -> bool:
    return bool(_cfg("assistant", key, default))


def _send(text: str, title: str) -> bool:
    token, chat_id = bot_config(_PREFIX)
    if not (token and chat_id):
        print(f"[{title}]\n{text}", file=sys.stderr)
        return True
    payload = {"chat_id": chat_id, "text": f"{title}\n\n{text}"[:4000]}
    return api_call("sendMessage", payload, token=token) is not None


def daily_digest() -> str:
    if not _enabled("daily_digest_enabled"):
        return "（daily digest 已在 config/assistant.yaml 關閉）"

    today = date.today().isoformat()
    turns = store.turns_since(today)
    lines: list[str] = []
    if turns:
        lines.append(f"今天問了 {len(turns)} 個問題：")
        lines.extend(f"- {t['question']}" for t in turns)
    else:
        lines.append("今天沒有問過職涯助手任何問題。")

    finds = context.findings()
    if finds:
        lines.append("\n目前待處理：")
        lines.extend(f"- [{f['level']}] {f['tag']} · {f['text']}" for f in finds[:5])

    text = "\n".join(lines)
    _send(text, "🌙 職涯助手 · 今日總結")
    return text


def weekly_digest() -> str:
    if not _enabled("weekly_digest_enabled"):
        return "（weekly digest 已在 config/assistant.yaml 關閉）"

    since = (date.today() - timedelta(days=7)).isoformat()
    turns = store.turns_since(since)
    if not turns:
        text = "這週沒有使用職涯助手。"
        _send(text, "📅 職涯助手 · 本週總結")
        return text

    qa_block = "\n".join(f"Q: {t['question']}\nA: {t['answer'][:200]}" for t in turns)
    funnel = context.funnel_snapshot()["last_30d"]
    prompt = (
        "把以下一週的求職者問答紀錄歸納成週報，抓出重複出現的主題/困惑點，"
        "150 字內，繁體中文，條列最多 4 點，不要編造紀錄裡沒有的內容：\n\n"
        f"[本週問答]\n{qa_block}\n\n"
        f"[應募漏斗-近30天] 總投遞 {funnel['total']} 筆\n"
    )
    try:
        summary = call(prompt, timeout=120, model=DIGEST_MODEL)
    except Exception as e:
        summary = (f"（LLM 歸納失敗：{type(e).__name__}: {e}，以下為原始問題清單）\n"
                   + "\n".join(f"- {t['question']}" for t in turns))

    text = f"共 {len(turns)} 輪問答\n\n{summary}"
    _send(text, "📅 職涯助手 · 本週總結")
    return text


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "daily":
        print(daily_digest())
    elif mode == "weekly":
        print(weekly_digest())
    else:
        raise SystemExit("Usage: python3 -m assistant.digest daily|weekly")


if __name__ == "__main__":
    main()
