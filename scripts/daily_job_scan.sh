#!/usr/bin/env bash
# 每日求職掃描：重評分 → 客製 top10 → 今日精選摘要（stdout）。
# Gap 分析已改為常駐掃描（scripts/gap_backfill_scan.sh，每 30 分鐘一輪，不綁本腳本）。
# stdout 摘要供 Samurai task_runner 推 Telegram（沿用 pipeline 既有約定）。
#
# 手動跑:   bash scripts/daily_job_scan.sh
# launchd:  載入 scripts/com.jp-pm-jobs.daily.plist
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"
MIN_SCORE="${MIN_SCORE:-70}"
LOG_DIR="$PROJECT_DIR/output/logs"
mkdir -p "$LOG_DIR"
TS="$(date +%Y-%m-%d)"

echo "── 求職日次スキャン · $TS ──"

# 0) 確保 gap_analysis 欄位存在（idempotent）
"$PY" -m tracker.migrations.002_add_gap_analysis >/dev/null

# 1) 重評分 + 客製 top10 + 今日精選摘要（pipeline 既有 stdout）
"$PY" pipeline.py --min-score "$MIN_SCORE"

echo ""
echo "Gap 分析：由常駐掃描持續清空（見 output/logs/gap-backfill.out.log）"
echo "評分報告：output/scored-$TS.md"
