# notify/ — Telegram 通知與互動 bot

## 設定

`.env` 的 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`（`notify` 模組自動載入 `.env`；缺 = 降級
stdout）。開關/門檻在 `config/notify.yaml`（範本 `.example`）。**token 永不硬編碼進 plist /
程式碼**（開源保護，`.env` 已 gitignore + publicignore）。

## 推送點

`notify/events.py`，冪等靠 `notify_log` 表 / `notify/dedupe.py`：新高分職缺 score≥80（scrape
後處理觸發）、inbox 重要信件（面試邀請/offer/婉拒）、面試 T-1/當日提醒（inbox 掃描每輪檢查）、
日程確定、逾期跟進（附互動按鈕）。

## 互動 daemon

`python3 -m notify.bot`（launchd 範本 `scripts/com.jp-pm-jobs.telegram-bot.plist.example`）。
callback：`applied` / `stage` / `ignore` / `fu` / `snooze` / `qa` / `prep`（背景生成投遞包，
完成回推）；指令 `/today` `/funnel` `/pscore <job_id> <0-100> [備註]`（提案パック人工評分，
<90 背景重寫並推送新版，詳見 `proposal/CLAUDE.md` 評分駆動の迭代）。只回應 `TELEGRAM_CHAT_ID`
來源，其他靜默丟棄；bot 無寄信能力。

⚠ **一個 bot token 只允許一個 `getUpdates` 消費者**：與 Claude Code telegram plugin 共用同一個
bot 會 409 Conflict（互搶 update）。互動 daemon 要用專案專用 bot（@BotFather 另建）。

## Web UI 對應

跟進打卡 / snooze / 忽略職缺按鈕在 `JobDrawer`，後端 `dashboard/backend/actions_api.py`
（含 `POST /api/notify/test` 連通測試）。
