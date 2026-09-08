#!/bin/bash
# jp-pm-jobs 初次安裝腳本
#   bash setup.sh
#
# 做的事：檢查依賴 → 複製設定範本 → 安裝 Python/Node 套件 →
#         裝 Playwright 瀏覽器 → 初始化 SQLite schema → 印下一步指引。
# 全程冪等：重跑不會覆蓋你已編輯過的設定檔。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "── 1/6 檢查基礎依賴 ─────────────────────"
command -v python3 >/dev/null || { echo "✗ 需要 python3（建議 3.11+）"; exit 1; }
command -v node >/dev/null || { echo "✗ 需要 Node.js（建議 18+，跑 dashboard 前端）"; exit 1; }
python3 --version
node --version

echo "── 2/6 複製設定範本（已存在則跳過）──────"
copy_if_missing() {
  if [ ! -f "$2" ]; then
    cp "$1" "$2"
    echo "  建立 $2"
  else
    echo "  已存在，跳過 $2"
  fi
}
copy_if_missing .env.example .env
copy_if_missing config/llm.yaml.example config/llm.yaml
copy_if_missing config/scraping.yaml.example config/scraping.yaml
copy_if_missing data/candidate_profile.yaml.example data/candidate_profile.yaml
copy_if_missing data/cognitive_profile.yaml.example data/cognitive_profile.yaml
copy_if_missing data/tech_footprint.yaml.example data/tech_footprint.yaml
copy_if_missing data/blacklist.yaml.example data/blacklist.yaml
copy_if_missing dashboard/frontend/src/vn/scripts.main.example.ts dashboard/frontend/src/vn/scripts.main.ts
[ -f config/scoring.yaml ] || echo "  提示：config/scoring.yaml 為選填（不建 = 用預設評分邏輯），需要客製時複製 config/scoring.yaml.example"
[ -f config/resume.yaml ] || echo "  提示：config/resume.yaml 為選填（投遞用履歴書/職務経歴書路徑），換成自己的完成版時複製 config/resume.yaml.example；診斷：python3 -m tools.resume_assets"
[ -f config/redaction.yaml ] || echo "  提示：config/redaction.yaml 為選填（取引先ブランド名の遮蔽・缺檔=不遮蔽），有 NDA 需求時複製 config/redaction.yaml.example"

echo "── 3/6 安裝 Python 套件 ─────────────────"
python3 -m pip install -q -r requirements.txt

echo "── 4/6 安裝 Playwright 瀏覽器（爬蟲用）──"
python3 -m playwright install chromium

echo "── 5/6 安裝前端套件 ─────────────────────"
(cd dashboard/frontend && npm install --silent)

echo "── 6/6 初始化資料庫 schema ──────────────"
python3 -c "
from tracker.db import init_db
init_db()
print('  data/jobs.sqlite schema 就緒')
"

cat <<'EOF'

✓ 安裝完成。接下來：

1. 編輯 data/candidate_profile.yaml（你的求職畫像，gap 分析與投遞包的唯一資料來源）
2. 編輯 .env：填 LLM API key（ANTHROPIC_API_KEY 任一即可）
   或安裝並登入 claude/codex/gemini CLI（走 config/llm.yaml 的 cli provider）
3. 啟動 dashboard：bash dashboard/run.sh
   → http://localhost:8000
4. （可選）需要爬 LinkedIn/BizReach 等需登入站點：
   見 README.md「爬蟲 cookie 設定」章節

多人 / 公開部署請設定 DASHBOARD_PASSWORD（見 .env）啟用登入保護。
EOF
