#!/usr/bin/env bash
# 官網直投常駐掃描：基礎評分 >55 且推薦分 >80 的日本職缺 → 探測窗口 + 生成投遞包 + Telegram 審閱推送。
# 每輪處理上限 DIRECT_APPLY_LIMIT（預設 5）筆，backlog 逐輪分攤；沒東西可跑就快速結束。
# LLM 全走 miko-ws 指揮中心（tools/miko_llm.py）；指揮中心掛掉 → 自動 kickstart
# 重啓 com.miko.runtime（每輪最多 1 次）並重試該筆；重啓失敗 → 中止本輪下輪補跑。
# 每輪同時跑寄出偵測（Gmail 草稿消失 + SENT 有信 → 自動標 applied channel=direct）。
#
# 手動跑:   bash scripts/direct_apply_scan.sh
# launchd:  com.jp-pm-jobs.direct-apply.plist（StartInterval 1800s）
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/output/logs"
mkdir -p "$LOG_DIR"

# mkdir 原子鎖：防止上一輪還沒跑完下一輪疊上去
LOCK_DIR="$LOG_DIR/.direct_apply.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[direct_apply_scan] 上一輪尚未結束，跳過本輪"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"
LIMIT="${DIRECT_APPLY_LIMIT:-5}"

echo "── 官網直投掃描（每輪上限 ${LIMIT}）$(date '+%F %T') ──"
"$PY" -m tools.direct_apply --backfill --limit "$LIMIT"
"$PY" -m tools.direct_apply --check-sent
