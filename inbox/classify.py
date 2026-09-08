"""LLM 招聘郵件分類 → {category, confidence, summary}。

走 interview._llm.call（LLM 指揮中心）。郵件正文送 LLM 前經 _redact 遮罩 PII。
"""
from __future__ import annotations

import json
import re

from interview._llm import call
from inbox._redact import redact_pii

CATEGORIES = [
    "schedule_confirmed", "interview_invite", "scheduling", "rejection",
    "initial_contact", "offer", "other",
]

_PROMPT = """あなたは求職者のメール仕分けアシスタント。以下の受信メールを1カテゴリに分類せよ。

カテゴリ:
- schedule_confirmed: 面接日程の確定通知（日程確定のお知らせ・日程調整完了）
- interview_invite: 面接の招待・案内
- scheduling: 面接日程の調整・候補日提示の依頼
- rejection: お見送り・不採用通知
- initial_contact: 転職エージェント/採用担当からの初回スカウト・接触
- offer: 内定・オファー通知
- other: 上記以外（求人と無関係を含む）

出力は JSON のみ（前後に説明文を書かない）:
{{"category": "<上記のいずれか>", "confidence": <0.0-1.0>, "summary": "<日本語1文要約>"}}

--- メール ---
件名: {subject}
本文:
{body}
"""


def _parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    cat = data.get("category", "other")
    if cat not in CATEGORIES:
        cat = "other"
    try:
        conf = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "category": cat,
        "confidence": round(max(0.0, min(1.0, conf)), 2),
        "summary": (data.get("summary") or "").strip()[:300],
    }


def classify_mail(mail: dict, model: str = "haiku") -> dict:
    prompt = _PROMPT.format(
        subject=mail.get("subject", ""),
        body=redact_pii(mail.get("body", ""))[:4000],
    )
    raw = call(prompt, model=model, accept={"includesAny": CATEGORIES})
    return _parse(raw)
