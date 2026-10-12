#!/usr/bin/env bash
# 每 30 分鐘掃 LinkedIn Messaging 未讀對話 → 規則分類（只留招聘相關）→ 生成回覆草稿
# → Telegram 通知。
# 永不自動發送：LinkedIn 無官方草稿 API，只存草稿到 DB + output/linkedin_drafts/，
# 人工複製貼上到 LinkedIn 網頁後手動送出。
#
# 手動跑:   bash scripts/linkedin_inbox_scan.sh
# launchd:  載入 scripts/com.jp-pm-jobs.linkedin-inbox.plist
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"
LOG_DIR="$PROJECT_DIR/output/logs"
mkdir -p "$LOG_DIR"
export PYTHONUNBUFFERED=1   # launchd 下 stdout 是塊緩衝，不設會看不到即時 log

echo "── LinkedIn Messaging 掃描 · $(date '+%Y-%m-%d %H:%M:%S') ──"

"$PY" -m linkedin_inbox.reply --max-conversations 20 \
  || { echo "⚠ 掃描失敗（exit $?）"; exit 1; }

echo "── 掃描完成 · $(date '+%H:%M:%S') ──"
