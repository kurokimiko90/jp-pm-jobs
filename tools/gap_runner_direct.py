"""Gap 分析直接実行 — claude CLI (Haiku) を直接呼び出す（miko-ws 不使用）。

usage:
    python3 tools/gap_runner_direct.py --source recruiter_agent --min-score 50
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.deid import build_deid_profile
from tracker.db import connect, update_gap_analysis, create_gap_batch, assign_gap_batch, finalize_gap_batch
from analyzer.gap_analyzer import build_prompt, write_gap_md, _extract_json
from analyzer.gap_summary import summarize_batch

CLAUDE_BIN = "/opt/homebrew/bin/claude"
MODEL = "claude-haiku-4-5"
MAX_JD_CHARS = 6000


def call_haiku(prompt: str, timeout: int = 120) -> str:
    result = subprocess.run(
        [CLAUDE_BIN, "-p", prompt, "--model", MODEL, "--output-format", "text"],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"Haiku CLI failed: {result.stderr[:200]}")
    return result.stdout.strip()


def get_pending(source: str, min_score: int):
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM jobs
               WHERE source = ? AND score >= ?
               AND (gap_analysis IS NULL OR gap_analysis = '')
               AND raw_jd IS NOT NULL AND raw_jd != ''
               ORDER BY score DESC""",
            (source, min_score),
        ).fetchall()
    return rows


def run(source: str, min_score: int):
    profile_yaml = build_deid_profile()
    rows = get_pending(source, min_score)
    total = len(rows)
    print(f"[gap_runner] 対象: {total} 件 (source={source} score>={min_score})")
    if not total:
        return

    batch_id = create_gap_batch(source, min_score)
    print(f"[gap_runner] batch #{batch_id} 開始")

    done = 0
    items = []
    for i, row in enumerate(rows, 1):
        try:
            prompt = build_prompt(profile_yaml, row)
            raw = call_haiku(prompt)
            result = _extract_json(raw)
            update_gap_analysis(row["id"], json.dumps(result, ensure_ascii=False))
            write_gap_md(row, result)
            assign_gap_batch(row["id"], batch_id)
            items.append({
                "id": row["id"], "company": row["company"], "title": row["title"],
                "score": row["score"], "rec": result.get("recommend_score"),
                "sal": [row["salary_min"], row["salary_max"]],
                "gaps": result.get("gaps"), "reason": result.get("recommend_reason"),
            })
            print(f"  [{i}/{total}] ✓ [{row['id']}] score={row['score']} 推薦={result.get('recommend_score')} {row['title'][:40]}")
            done += 1
        except Exception as e:
            print(f"  [{i}/{total}] ✗ [{row['id']}] {e}")

    print(f"[gap_runner] 完成 {done}/{total} 件、batch #{batch_id} 集計中…")
    try:
        summary = summarize_batch(profile_yaml, items)
        finalize_gap_batch(batch_id, done, json.dumps(summary, ensure_ascii=False))
        print(f"[gap_runner] batch #{batch_id} 落庫完了")
    except Exception as e:
        finalize_gap_batch(batch_id, done, json.dumps({"error": str(e)}, ensure_ascii=False))
        print(f"[gap_runner] ✗ 集計失敗: {e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--min-score", type=int, default=50)
    args = ap.parse_args()
    run(args.source, args.min_score)
