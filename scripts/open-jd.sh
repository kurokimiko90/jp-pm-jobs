#!/usr/bin/env bash
# 開啟 LinkedIn（或其他）職缺原頁面的預設 Chrome。
#
# 用已登入的 Chrome profile（miko-ws slot4 = .chrome-chatgpt4）。
# 同一 user-data-dir 再次啟動 Chrome 時，會交給既有實例以「新分頁」開啟，
# 不會起第二個進程，也不需要 CDP port。
#
# 用法：
#   bash scripts/open-jd.sh <url> [url2] [url3] ...
#   bash scripts/open-jd.sh https://www.linkedin.com/jobs/view/4427170172/
#
# 覆寫帳號 profile：OPEN_JD_PROFILE=$HOME/.chrome-gemini4 bash scripts/open-jd.sh <url>

set -euo pipefail

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE_DIR="${OPEN_JD_PROFILE:-$HOME/.chrome-chatgpt4}"  # slot4

if [[ $# -lt 1 ]]; then
  echo "用法: bash scripts/open-jd.sh <url> [url2 ...]" >&2
  exit 1
fi

if [[ ! -x "$CHROME" ]]; then
  echo "❌ 找不到 Chrome: $CHROME" >&2
  exit 1
fi

N=$#
"$CHROME" --user-data-dir="$PROFILE_DIR" "$@" >/dev/null 2>&1 &
echo "已用 Chrome [${PROFILE_DIR}] 開啟 ${N} 個分頁"
