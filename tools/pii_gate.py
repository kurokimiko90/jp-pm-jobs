"""PII 閘門 — 所有「對外部服務送出文字」前的統一去識別化檢查點。

LLM prompt 用 tools.deid.build_deid_profile()（白名單重建）；本模組處理另一類場景：
既有文字（QA 台詞、履歷片段等）要送外部 API（TTS、翻譯等）前的替換式清洗。

用法:
    from tools.pii_gate import scrub_for_external
    clean, findings = scrub_for_external(text)
    # findings 非空 = 原文含 PII（已替換），呼叫端應記錄

resume/jp/data.yaml 缺失時（開源環境）退化為 no-op。
"""
from __future__ import annotations

from functools import lru_cache

from tools.deid import load_resume_contact

NAME_REPLACEMENT = "本人"
CONTACT_REPLACEMENT = "***"


@lru_cache(maxsize=1)
def _terms() -> tuple[tuple[str, str], ...]:
    """(原文, 替換) 對。resume/jp/data.yaml 讀不到時回空 tuple。"""
    try:
        rc = load_resume_contact()
    except (FileNotFoundError, OSError):
        return ()
    pairs: list[tuple[str, str]] = []
    name = rc.get("name_ja")
    if name and str(name).strip():
        pairs.append((str(name), NAME_REPLACEMENT))
    for key in ("email", "phone", "linkedin", "github"):
        v = rc.get(key)
        if isinstance(v, str) and v.strip():
            pairs.append((v, CONTACT_REPLACEMENT))
    return tuple(pairs)


def scrub_for_external(text: str) -> tuple[str, list[str]]:
    """清洗要送外部服務的文字。回傳 (清洗後文字, 命中的 PII 種類清單)。"""
    findings: list[str] = []
    for term, replacement in _terms():
        if term in text:
            findings.append(term)
            text = text.replace(term, replacement)
    return text, findings
