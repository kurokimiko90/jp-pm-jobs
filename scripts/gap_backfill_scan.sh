#!/usr/bin/env bash
# Gap backfill 常駐掃描：分批清空所有未跑 gap 的職缺，沒東西可跑就快速結束（no-op）。
# 只走 miko-ws 指揮中心（config/llm.gap.yaml 存在時強制、無 fallback）；
# 指揮中心不可用 → 整批失敗即中止本輪，下一輪（30 分鐘後）自動重試。
#
# 手動跑:   bash scripts/gap_backfill_scan.sh
# launchd:  com.jp-pm-jobs.gap-backfill.plist（StartInterval 1800s，機器/session 醒著就跑，
#           不綁固定時刻 — 只要專案在跑就持續清 backlog，跑到沒得跑為止）
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/output/logs"
mkdir -p "$LOG_DIR"

# mkdir 是原子操作 → 簡單可靠的跨進程鎖，防止上一輪還沒跑完下一輪又疊上去
LOCK_DIR="$LOG_DIR/.gap_backfill.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[gap_backfill_scan] 上一輪尚未結束，跳過本輪"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"
GAP_MIN_SCORE="${GAP_MIN_SCORE:-55}"
GAP_BATCH_SIZE="${GAP_BATCH_SIZE:-20}"

GAP_LLM_CFG="$PROJECT_DIR/config/llm.gap.yaml"
if [ -f "$GAP_LLM_CFG" ]; then
  export LLM_CONFIG="$GAP_LLM_CFG"
fi

echo "── Gap backfill 常駐掃描（min_score ${GAP_MIN_SCORE}）──"
"$PY" -m analyzer.gap_analyzer --backfill --min-score "$GAP_MIN_SCORE" \
  --batch-size "$GAP_BATCH_SIZE" --max-jobs 0
