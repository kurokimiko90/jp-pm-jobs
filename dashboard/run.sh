#!/bin/bash
# jp-pm-jobs dashboard 一鍵啟動：build 前端 → FastAPI 託管
# 埠號可用 DASHBOARD_PORT 覆蓋（預設 8000）。
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${DASHBOARD_PORT:-8000}"

if [ "$1" != "--skip-build" ]; then
  (cd "$DIR/frontend" && npm run build)
fi
cd "$DIR/backend"
echo "→ http://localhost:$PORT"
exec uvicorn main:app --port "$PORT"
