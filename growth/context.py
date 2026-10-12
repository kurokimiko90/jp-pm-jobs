"""上下文組裝 — 求人 / 会社事実 / 去識別化プロフィール / gap 素材。

PII 規則（CLAUDE.md 必讀）：送 LLM 的所有內容都經過
  tools.deid.build_deid_profile()  白名單去識別化
  tools.redact.redact()            取引先ブランド遮蔽
兩道關卡，這裡是 growth pipeline 唯一的 prompt 素材入口。
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.deid import build_deid_profile
from tools.gap_facts import match_evidence
from tools.redact import redact

ROOT = Path(__file__).parent.parent
JD_MAX_CHARS = 6000


def jd_text(job: dict) -> str:
    """JD 本文（遮蔽済み・長さ制限つき）。Gate B 的引用照合基準もこれ。"""
    raw = (job.get("raw_jd") or "")[:JD_MAX_CHARS]
    cleaned, _ = redact(raw)
    return cleaned


def gap_block(job: dict) -> str:
    """gap 分析の要件対位素材（無ければ空文字）。"""
    try:
        return match_evidence(job) or ""
    except Exception:
        return ""


def gap_summary(job: dict) -> str:
    """gap_analysis JSON から推薦度と主要な差分だけを平文化。"""
    raw = job.get("gap_analysis")
    if not raw:
        return ""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    lines: list[str] = []
    for key in ("recommend_score", "verdict", "summary", "strengths", "gaps", "risks"):
        val = data.get(key)
        if not val:
            continue
        if isinstance(val, list):
            val = " / ".join(str(v) for v in val[:6])
        lines.append(f"- {key}: {val}")
    text = "\n".join(lines)
    cleaned, _ = redact(text)
    return cleaned


def profile_text(compact: bool = True) -> str:
    """去識別化済み候補者プロフィール（YAML 文字列）。

    compact=True で冗長な敘事欄位を落とす（約 -38%）。growth の prompt は
    JD + 前段成果物 + 知識庫で既に長いため既定で compact。
    """
    return build_deid_profile(compact=compact)


def job_header(job: dict) -> str:
    salary = ""
    if job.get("salary_min"):
        salary = f"{job['salary_min']}万〜{job.get('salary_max') or '?'}万円"
    return (
        f"- 会社: {job.get('company') or '{{要確認}}'}\n"
        f"- 職位: {job.get('title')}\n"
        f"- 勤務地: {job.get('location') or '?'}\n"
        f"- 想定年収: {salary or '不明'}\n"
        f"- 企業タイプ: {job.get('tier') or 'unknown'} / 従業員数: {job.get('employee_count') or '不明'}\n"
        f"- 求人スコア: score={job.get('score')} recommend={job.get('recommend_score')}"
    )


def build(job: dict, facts: str = "", *, with_profile: bool = True,
          with_gap: bool = True) -> str:
    """全 stage 共用の基礎コンテキスト。"""
    parts = [
        "# 対象求人",
        job_header(job),
        "",
        "# JD 全文（この範囲外の求人内容を引用しないこと）",
        jd_text(job),
    ]
    if facts:
        parts += ["", "# 確認済みの会社事実（Web 調査済み・数字はここからのみ引用可）", facts]
    else:
        parts += ["", "# 確認済みの会社事実", "（未提供 — 会社固有の数字は必ず {{要確認}} と書く）"]
    if with_gap:
        g = gap_block(job)
        s = gap_summary(job)
        if g or s:
            parts += ["", "# 既存の gap 分析（応募者の要件対位）", s, g]
    if with_profile:
        parts += ["", "# 応募者プロフィール（去識別化済み・職業情報のみ）", "```yaml",
                  profile_text(), "```"]
    return "\n".join(p for p in parts if p is not None)
