#!/usr/bin/env bash
# 每 30 分鐘掃 Gmail → 規則分類 → 對應職缺 → 日程確定自動寫入 applications.next_event
# → 需回覆的信生成 Gmail 草稿 → Telegram 通知。
# 永不自動寄信：只生成草稿，人工在 Gmail 確認後手動送出。
#
# 手動跑:   bash scripts/inbox_reply_scan.sh
# launchd:  載入 scripts/com.jp-pm-jobs.inbox-scan.plist
set -uo pipefail   # 不用 -e：兩輪掃描互相獨立，第一輪失敗不應擋掉星號輪

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PY="${PYTHON_BIN:-/opt/homebrew/bin/python3}"
LOG_DIR="$PROJECT_DIR/output/logs"
mkdir -p "$LOG_DIR"
export PYTHONUNBUFFERED=1   # launchd 下 stdout 是塊緩衝，不設會看不到即時 log

echo "── Inbox 掃描 · $(date '+%Y-%m-%d %H:%M:%S') ──"
FAIL=0

# 已讀與是否需要回覆無關，因此掃所有已知招募平台郵件；只保留最近 15
# 小時這個精確滾動窗口。gmail_msg_id 去重加上草稿互斥鎖，確保先前已產生
# 草稿的信不會在後續 30 分鐘輪詢中再次建立草稿。
"$PY" -m inbox.reply --hours 15 --max-results 100 --all \
  || { echo "⚠ 近 15 小時掃描失敗（exit $?）"; FAIL=1; }

# 応募受付メール（r-agent「応募手続きを承りました」）→ applications 自動記録の
# 取りこぼし回収。通常は上の inbox.reply が同じ処理を行うが、inbox_mails の
# gmail_msg_id 去重は「分類より前」に効くため、一度入庫済みのメールは二度と
# 再処理されない（分類ルール追加前に入った分・apply_ack が例外で落ちた分が該当）。
# 直近 3 日を毎回さらい直すことで自癒する。冪等（既存応募には一切書き込まない）。
"$PY" -m inbox.application_ack --days 3 || echo "⚠ 応募受付メール取り込み失敗（exit $?）"

# 面試前日/當日提醒（冪等：同一場只提醒一次，失敗不影響掃描結果）
"$PY" -m notify.events reminders || echo "⚠ 面試提醒失敗（exit $?）"

# 面試已確定但缺會議URL：每小時提醒一次、上限 5 次（每 30 分鐘輪詢一次本腳本，
# 冷卻由 notify.events 內部按時間戳判斷，不會每輪都推）
"$PY" -m notify.events meeting-url-missing || echo "⚠ 會議連結提醒失敗（exit $?）"

# 日程確定済みで面接パック未生成の応募 → prep.py {id} interview を 1 本生成。
# 十数分かかるので最後に置く（先に通知系を出し切ってから重い処理へ入る）。
# 冪等・既存パックは再生成しない・自前 mkdir ロックでラウンド跨ぎの重複を防ぐ。
"$PY" -m inbox.prep_trigger || echo "⚠ 面接パック自動生成失敗（exit $?）"

if [ "$FAIL" -ne 0 ]; then
  echo "── 掃描結束（部分失敗）· $(date '+%H:%M:%S') ──"
  exit 1
fi
echo "── 掃描完成 · $(date '+%H:%M:%S') ──"
