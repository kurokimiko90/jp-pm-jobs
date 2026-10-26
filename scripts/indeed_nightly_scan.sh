#!/usr/bin/env bash
# 每日 03:00：抓 Indeed / Green / r-agent 新職缺 + r-agent「気になる」→ Indeed 新職缺在 r-agent 找同一求人，找到就切換為 r-agent 來源/連結。
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

# 1) Indeed / Green / r-agent（検索 + Gmail）をまとめて抓取（含評分等後處理）。
#    scrape.py は source ごとに例外を握るので、1 つ落ちても残りは走る。
if ! "$PY" scrape.py --preset all --source indeed green ragent_search recruiter_agent; then
    echo "⚠ 抓取失敗，仍對既有今日新增做 r-agent 突合"
fi

# 1b) r-agent「気になる」一覧（星を付けた求人）— 9270 Chrome を突合と共用
if ! "$PY" scrape.py --source ragent_interests; then
    echo "⚠ r-agent 気になる取得失敗"
fi

# 1c) r-agent の JD 補全 — Gmail 由来はタイトル＋数行（〜70 字）しか無いので、
#     raw_jd < 300 字の求人を詳細頁から取り直す（scrapers.recruiter_agent.enrich_jds）
if ! "$PY" scrape.py --source recruiter_agent_cdp; then
    echo "⚠ r-agent JD 補全失敗"
fi

# 2) 今日新增的 Indeed 職缺 → r-agent 突合（找到就轉 r-agent 來源）
"$PY" -m tools.ragent_crossmatch --days 1 --notify
