#!/usr/bin/env bash
# 每日 liveness 掃描：對高分且超過 7 天未更新的職缺做存活檢測。
# 結果存入 DB liveness_status 欄位，過期高分職缺推通知。
#
# 手動跑:   bash scripts/daily_liveness.sh
# launchd:  可加入 com.jp-pm-jobs.daily.plist
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"
MIN_SCORE="${MIN_SCORE:-60}"
TOP="${LIVENESS_TOP:-30}"

echo "── Liveness 日次スキャン ──"

# 確保 liveness 欄位存在
"$PY" -m tracker.migrations.004_add_liveness_fields >/dev/null

# 對高分職缺做 liveness 檢測（httpx 模式）
RESULT=$("$PY" -m tools.liveness --days 7 --top "$TOP" 2>/dev/null)
EXPIRED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('summary',{}).get('expired',0))")
CHECKED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('checked',0))")

echo "  檢查 $CHECKED 筆，過期 $EXPIRED 筆"

# 過期高分職缺清單（推 stdout 供 Samurai 或通知）
if [ "$EXPIRED" -gt 0 ]; then
    echo ""
    echo "── 過期高分職缺 ──"
    echo "$RESULT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for r in data.get('results', []):
    if r['liveness'] == 'expired':
        score = r.get('score') or '?'
        print(f\"  [{r['job_id']}] score={score} {r.get('company','?')} — {r.get('title','')[:50]}\")
"
fi

# 跑 follow-up 逾期提醒
echo ""
echo "── Follow-up 逾期檢查 ──"
"$PY" -m tools.followup show --overdue --notify 2>/dev/null || echo "  （無逾期項目）"
