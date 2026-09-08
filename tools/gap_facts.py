"""gap 分析（jobs.gap_analysis）→ 応募書類の「要件対位」素材ブロック。

志望動機・応募メールの説得力は「JD の要件 ⇄ 本人の事実」の対位で決まる。
既に走らせた gap 分析の成果（requirements / matched / gaps）を LLM prompt に
再利用し、狙う論点を明示する。素材は内部メモ（繁体中文）なので、
prompt 側で「日本語で書く」ことを必ず指示する。

zero LLM・zero 網路。gap 未実施なら空文字列を返す（呼び出し側は素通し）。
"""

from __future__ import annotations

import json

MAX_ITEMS = 5
MAX_CHARS = 140


def _load(job: dict) -> dict:
    raw = job.get("gap_analysis")
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _items(data: dict, key: str) -> list[str]:
    out = []
    for item in (data.get(key) or [])[:MAX_ITEMS]:
        text = str(item).strip()
        if text:
            out.append(text[:MAX_CHARS])
    return out


def match_evidence(job: dict) -> str:
    """要件 / 対位できる事実 / 未充足 を日本語見出しで並べたブロック。

    gap 分析が無い、または中身が空なら空文字列。
    """
    data = _load(job)
    reqs = _items(data, "requirements")
    matched = _items(data, "matched")
    gaps = _items(data, "gaps")
    if not (reqs or matched):
        return ""

    lines = ["# 要件対位メモ（内部分析・繁体中文のまま。書類は必ず日本語で書く）"]
    if reqs:
        lines.append("## JD が求める要件")
        lines += [f"- {r}" for r in reqs]
    if matched:
        lines.append("## 対位できる本人の事実（ここを書類の中心に据える）")
        lines += [f"- {m}" for m in matched]
    if gaps:
        lines.append("## 未充足（触れない。埋め合わせを捏造しない）")
        lines += [f"- {g}" for g in gaps]
    reason = str(data.get("recommend_reason") or "").strip()
    if reason:
        lines.append(f"## 総評\n- {reason[:200]}")
    # gap 分析は profile の記述をそのまま写しており取引先ブランド名が混ざる
    # （DB 内の既存レコードも含む）。prompt に渡す直前でここでも遮蔽する。
    from tools.redact import redact
    text, _ = redact("\n".join(lines) + "\n")
    return text
