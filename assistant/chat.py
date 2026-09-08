"""AI 職涯助手問答主邏輯。

紀律（比照 gap 分析 / growth 手冊既有慣例）：
  - 只根據 context.build_context() 給的事實回答，禁止編造候選人沒有的經驗/數字
  - 候選人背景一律經 tools.deid.build_deid_profile() 去識別化才進 prompt
  - 輸出經 tools.redact.redact() 過濾取引先品牌名（NDA 相當，PII 以外的另一道關）
  - 每輪問答落地 assistant.store，供 digest.py 產每日/每週總結
"""

from __future__ import annotations

import re
from datetime import datetime

from interview._llm import call
from tools.app_config import get as _cfg
from tools.deid import build_deid_profile
from tools.redact import redact

from . import context, store

MODEL = _cfg("assistant", "chat_model", "claude-haiku-4-5")
HISTORY_WINDOW = _cfg("assistant", "history_window", 6)

_CITATION_RE = re.compile(r"job:(\d+)")

_SYSTEM = """你是本專案求職者的 AI 職涯助手，只服務求職進度相關的問題（職缺/應募漏斗/面試準備/Gap分析），不處理與求職無關的話題。

回答格式固定四段，缺的段落寫「無」，不要省略段落標題：
結論：一句話直接答案
依據：引用 [事實] 區塊裡的 job:ID 或分類名稱，條列最多 3 點，每點都要有出處
未知：[事實] 沒提供的東西就老實說「不知道」「未跟蹤」，禁止用常識腦補具體數字
建議：下一步具體行動，沒有就寫「無」

鐵律：
- 禁止編造候選人沒有的經驗、數字、日期
- [事實] 之外的具體斷言一律不寫，改寫「未知」
- **[對話紀錄] 只用來理解語境，不是事實來源。** 任何職缺、公司、應募狀態、統計
  數字一律只能引用 [事實]；[對話紀錄] 裡出現過的數字與職缺一律視為已過期的舊
  快照，不得複述、不得當成現況、不得拿來當「依據」的出處
- 講「進行中的面試/管線有幾條」只能數 [事實] 的 [進行中的選考]，不得自行推測
- 問「某公司投過沒有」時，答案**只**看 [提問提到的企業（應募歷史）]：寫「應募過」
  就直接說投過並附狀態與日期，寫「未應募過」就直接說沒投過，寫「查無」就說庫內
  沒有這家公司。三種情況都照實講，不准改寫成「可能」「似乎」這種模糊說法
- 用繁體中文回答，除非使用者用日文/英文提問就跟著切換
"""


def _history_block() -> str:
    """最近幾輪對話。標上日期並降級為語境，避免舊答案裡的職缺被當成現況複述。"""
    turns = store.recent_turns(limit=HISTORY_WINDOW)
    if not turns:
        return ""
    lines = ["（以下僅供理解語境。其中的數字與職缺是當時的舊快照，一律以 [事實] 為準）"]
    for t in turns:
        when = (t.get("question_at") or "")[:16]
        lines.append(f"[{when} 先前提問] {t['question']}")
        lines.append(f"[{when} 先前回答] {t['answer']}")
    return "\n".join(lines)


def _extract_citations(text: str) -> list[str]:
    return sorted({f"job:{m}" for m in _CITATION_RE.findall(text)}, key=lambda s: int(s.split(":")[1]))


def answer(question: str, channel: str = "web", include_profile: bool = True) -> dict:
    """回傳 {answer, citations, redacted_hits}。question 為使用者原文提問。"""
    question = (question or "").strip()
    if not question:
        return {"answer": "請輸入問題。", "citations": [], "redacted_hits": []}

    question_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    facts = context.build_context(question)
    history = _history_block()

    parts = [_SYSTEM, "[事實]\n" + facts]

    if include_profile:
        try:
            parts.append("[候選人背景（已去識別化）]\n" + build_deid_profile(compact=True))
        except SystemExit:
            pass  # profile 檔缺失時跳過，不阻斷問答

    if history:
        parts.append("[對話紀錄]\n" + history)

    parts.append(f"[使用者提問]\n{question}")

    prompt = "\n\n".join(parts)
    raw = call(prompt, timeout=120, model=MODEL)
    redacted, hits = redact(raw)
    citations = _extract_citations(redacted)

    store.log_turn(channel, question, redacted, citations, question_at=question_at)
    return {"answer": redacted, "citations": citations, "redacted_hits": hits}
