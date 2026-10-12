#!/usr/bin/env bash
# AI 職涯助手每日總結：今天問了什麼 + 目前待處理發現，推播到 assistant 專用 bot。
#
# 手動跑:   bash scripts/assistant_daily_digest.sh
# launchd:  com.jp-pm-jobs.assistant-daily-digest.plist（StartCalendarInterval 每日固定時刻）
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/output/logs"
mkdir -p "$LOG_DIR"

LOCK_DIR="$LOG_DIR/.assistant_daily_digest.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[assistant_daily_digest] 上一輪尚未結束，跳過本輪"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"

echo "── AI 職涯助手 · 每日總結 ──"
"$PY" -m assistant.digest daily
