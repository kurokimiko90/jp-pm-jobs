"""QA 生成・批評の LLM 呼び出し口 — 指揮中心の task API へ投げる。

同期 `/api/llm/text` は長い prompt（応募者プロフィール込みで 3〜5 万字）を全 brain で
落とすことがある（2026-10-16 実測: 31,000 字でも HTTP 500、同じ prompt が task API
では 36 秒で返った）。投入と取得が別リクエストの task API なら接続断で失敗しない。
"""

from __future__ import annotations

import sys

from tools import miko_llm

# task が brain 側の一時障害（実測 2026-10-18: "[Grok] 找不到輸入框"）で落ちたとき、
# 1 回だけ投げ直す（再投入で別 brain に回るかは指揮中心次第 — 未検証）。
# 投げ直さないと、16 回ある呼び出しのどれか 1 つの瞬断で pack 生成ごと止まる。
RETRIES = 1

# 指揮中心の agy（Antigravity CLI）を先に試す。Chrome brain の pool は長い task で
# gateway ごと固まることがあった（2026-10-18、3 回連続）。agy は CLI サブプロセスなので
# Chrome の並列枠を通らない。モデル名は `agy models` の一覧から選ぶ（gemini-3.1-pro-high は
# 未登録で即失敗した）。持ち時間の残りは pool の備援に回す。
# Sonnet は個人枠の上限に当たると exit 3、その後は応答せず持ち時間を燃やした（2026-10-18）。
# 枠の大きい Argon を先頭に置く。
AGY_MODELS = ["gemini-4-argon-medium", "claude-sonnet-5-5-medium"]
AGY_SHARE = 0.6


def _opts(timeout: int, accept: dict) -> dict:
    return {
        "accept": accept,
        "engine": "agy",
        "agyModels": AGY_MODELS,
        "agyTimeoutMs": int(timeout * AGY_SHARE * 1000),
    }


def _once(prompt: str, timeout: int, opts: dict) -> str:
    try:
        result, _meta = miko_llm.text_with_meta(prompt, timeout=timeout, opts=opts)
    except miko_llm.TaskAPIUnsupported:
        return miko_llm.text(prompt, timeout=timeout, opts=opts)
    return result


def text(prompt: str, timeout: int, accept: dict) -> str:
    opts = _opts(timeout, accept)
    for attempt in range(RETRIES + 1):
        try:
            return _once(prompt, timeout, opts)
        except RuntimeError as exc:
            if attempt >= RETRIES:
                raise
            print(f"  ⚠ 指揮中心の task 失敗、投げ直します: {exc}", file=sys.stderr)
    raise AssertionError("unreachable")
