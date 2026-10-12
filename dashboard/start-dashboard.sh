#!/bin/bash
# 開機自動啟動 dashboard + 打開瀏覽器
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$DIR/backend"
/opt/homebrew/bin/uvicorn main:app --port 8000 &
PID=$!

for i in $(seq 1 15); do
  if curl -s -o /dev/null http://localhost:8000; then
    open http://localhost:8000
    break
  fi
  sleep 1
done

wait $PID
