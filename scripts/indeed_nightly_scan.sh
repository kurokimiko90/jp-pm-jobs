#!/usr/bin/env bash
# 每日 03:00：抓 Indeed 新職缺 → 在 r-agent 找同一求人，找到就切換為 r-agent 來源/連結。
# 結果推 Telegram（tools.ragent_crossmatch --notify）。
#
# 手動跑:   bash scripts/indeed_nightly_scan.sh
# launchd:  載入 scripts/com.jp-pm-jobs.indeed-nightly.plist
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
echo "── Indeed 夜間スキャン · $(date '+%Y-%m-%d %H:%M') ──"

# 1) Indeed 抓取（indeed → indeed_cdp，自動起動 CDP Chrome；含評分等後處理）
if ! "$PY" scrape.py --preset all --source indeed; then
    echo "⚠ Indeed 抓取失敗，仍對既有今日新增做 r-agent 突合"
fi

# 2) 今日新增的 Indeed 職缺 → r-agent 突合（找到就轉 r-agent 來源）
"$PY" -m tools.ragent_crossmatch --days 1 --notify
