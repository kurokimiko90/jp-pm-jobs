"""AI 職涯助手 — Web 版問答 + 主動發現。實際邏輯在頂層 assistant/ 套件。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from assistant import context as assistant_context
from assistant import store as assistant_store
from assistant.chat import answer as assistant_answer
from paths import ASSISTANT_SUMMARY_DIR

router = APIRouter(prefix="/api/assistant")


@router.post("/chat")
def chat(body: dict = Body(...)):
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "question 必填")
    return assistant_answer(question, channel="web")


@router.get("/history")
def history(limit: int = 30):
    return assistant_store.recent_turns(limit=limit)


@router.get("/findings")
def findings():
    return assistant_context.findings()


# ── 人工整理的對話總結（output/assistant/*.md，非自動生成） ──────────

@router.get("/summaries")
def summaries():
    if not ASSISTANT_SUMMARY_DIR.exists():
        return []
    return sorted((p.name for p in ASSISTANT_SUMMARY_DIR.glob("*.md")), reverse=True)


@router.get("/summary")
def summary(name: str):
    base = ASSISTANT_SUMMARY_DIR.resolve()
    p = (base / name).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise HTTPException(403)
    if not p.is_file() or p.suffix != ".md":
        raise HTTPException(404)
    return {"content": p.read_text(encoding="utf-8")}
