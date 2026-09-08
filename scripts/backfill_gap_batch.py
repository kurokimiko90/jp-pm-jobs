"""一次性：把現有零散 gap 分析歸入「初始匯總」批次 #N，並生成推薦度報告。

批次功能上線前已跑的 gap 沒有 gap_batch_id，本腳本：
1. 建一個 source_filter='(歷史匯總)' 的批次
2. 把所有有 gap 但無批次的職缺 assign 給它
3. 對推薦度≥MIN 的職缺生成 summary 落庫

之後每次跑 gap_analyzer 會自動建新批次，本腳本只需跑一次。

用法：python3 -m scripts.backfill_gap_batch [--min 60]
"""

from __future__ import annotations

import argparse
import json

from analyzer.gap_analyzer import load_profile_summary
from analyzer.gap_summary import summarize_batch
from tracker.db import (connect, create_gap_batch, assign_gap_batch,
                        finalize_gap_batch)


def run(min_rec: int) -> None:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, company, title, score, salary_min, salary_max, gap_analysis "
            "FROM jobs WHERE gap_analysis IS NOT NULL AND gap_analysis != '' "
            "AND gap_batch_id IS NULL"
        ).fetchall()

    if not rows:
        print("[backfill] 無未歸批的 gap，跳過。")
        return

    batch_id = create_gap_batch("(歷史匯總)", min_rec)
    items = []
    for r in rows:
        assign_gap_batch(r["id"], batch_id)
        try:
            g = json.loads(r["gap_analysis"])
        except (json.JSONDecodeError, TypeError):
            continue
        rec = g.get("recommend_score")
        if rec is not None and rec >= min_rec:
            items.append({
                "id": r["id"], "company": r["company"], "title": r["title"],
                "score": r["score"], "rec": rec,
                "sal": [r["salary_min"], r["salary_max"]],
                "gaps": g.get("gaps"), "reason": g.get("recommend_reason"),
            })

    print(f"[backfill] 批次 #{batch_id}：歸入 {len(rows)} 筆，報告取推薦度≥{min_rec} 的 {len(items)} 筆，生成中…")
    summary = summarize_batch(load_profile_summary(), items)
    finalize_gap_batch(batch_id, len(items), json.dumps(summary, ensure_ascii=False))
    print(f"[backfill] 批次 #{batch_id} 報告已落庫。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=60)
    run(ap.parse_args().min)
