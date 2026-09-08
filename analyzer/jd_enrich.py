"""JD 補充欄位回填（純規則零 LLM）— job_type / employee_count / mentions_ai。

run_postprocess 每輪對全表重算（冪等、秒級），新職缺入庫後自動補齊：
  job_type       : 職位類型 pdm / pjm / consulting / other（jd_scorer.classify_role）
  employee_count : 從業員數（sweet_spot.extract_employees；JD 未提及 = NULL）
  mentions_ai    : JD 是否提及 AI（1/0；title 命中也算 1；raw_jd 空且 title 未命中 = NULL）

手動回填 / 看分布:
    python3 -m analyzer.jd_enrich            # 全表回填 + 分布
    python3 -m analyzer.jd_enrich --stats    # 只看當前分布（不寫入）
"""

from __future__ import annotations

import argparse
import re

from analyzer.jd_scorer import _normalize, classify_role
from analyzer.sweet_spot import extract_employees
from tracker.db import connect, init_db

# ASCII 詞用邊界比對（排除英數前後綴：Dubai / said / 600ml 不誤中），CJK 詞子字串
_AI_RE = re.compile(
    r"(?<![a-z0-9])(?:ai|genai|llm|gpt|chatgpt|openai|anthropic|copilot)(?![a-z0-9])"
    r"|machine\s+learning|deep\s+learning"
    r"|人工知能|機械学習|深層学習|ディープラーニング|大規模言語モデル",
    re.IGNORECASE,
)


def mentions_ai(title: str | None, raw_jd: str | None) -> int | None:
    """1/0；title 命中即 1；raw_jd 空且 title 未命中 → None（資訊不足非「無提及」）。"""
    if _AI_RE.search(_normalize(title or "")):
        return 1
    body = _normalize(raw_jd or "")
    if not body.strip():
        return None
    return 1 if _AI_RE.search(body) else 0


def enrich_all() -> dict[str, int]:
    """全表重算三欄位並寫回。回傳 job_type 分布統計。"""
    init_db()  # 確保 employee_count / mentions_ai 欄位存在（增量遷移）
    with connect() as conn:
        rows = conn.execute("SELECT id, title, raw_jd FROM jobs").fetchall()
        updates = [
            (
                classify_role(r["title"] or ""),
                extract_employees(r["raw_jd"]),
                mentions_ai(r["title"], r["raw_jd"]),
                r["id"],
            )
            for r in rows
        ]
        conn.executemany(
            "UPDATE jobs SET job_type = ?, employee_count = ?, mentions_ai = ? "
            "WHERE id = ?",
            updates,
        )
    stats: dict[str, int] = {}
    for jt, _, _, _ in updates:
        stats[jt] = stats.get(jt, 0) + 1
    n_emp = sum(1 for _, emp, _, _ in updates if emp is not None)
    n_ai = sum(1 for _, _, ai, _ in updates if ai == 1)
    print(f"[jd_enrich] 已回填 {len(updates)} 筆。job_type 分布：")
    for jt, n in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {jt:12s} {n}")
    print(f"  從業員數抽到 {n_emp} 筆 / AI 提及 {n_ai} 筆")
    return stats


def print_stats() -> None:
    with connect() as conn:
        for col in ("job_type", "mentions_ai"):
            rows = conn.execute(
                f"SELECT {col}, COUNT(*) AS n FROM jobs GROUP BY {col} ORDER BY n DESC"
            ).fetchall()
            print(f"{col}: " + ", ".join(f"{r[0]}={r['n']}" for r in rows))
        r = conn.execute(
            "SELECT COUNT(employee_count) AS got, COUNT(*) AS total FROM jobs"
        ).fetchone()
        print(f"employee_count: {r['got']}/{r['total']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="JD 補充欄位回填（純規則零 LLM）")
    parser.add_argument("--stats", action="store_true", help="只看當前分布，不寫入")
    args = parser.parse_args()
    if args.stats:
        print_stats()
    else:
        enrich_all()


if __name__ == "__main__":
    main()
