#!/usr/bin/env bash
# AI 職涯助手每週總結：一週問答主題歸納（LLM）+ 應募漏斗趨勢，推播到 assistant 專用 bot。
#
# 手動跑:   bash scripts/assistant_weekly_digest.sh
# launchd:  com.jp-pm-jobs.assistant-weekly-digest.plist（StartCalendarInterval 每週固定時刻）
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/output/logs"
mkdir -p "$LOG_DIR"

LOCK_DIR="$LOG_DIR/.assistant_weekly_digest.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[assistant_weekly_digest] 上一輪尚未結束，跳過本輪"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"

echo "── AI 職涯助手 · 每週總結 ──"
"$PY" -m assistant.digest weekly
