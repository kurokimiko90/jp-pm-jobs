# assistant/ — AI 職涯助手

對話式問答，事實只能來自本專案資料庫（`context.py` 白名單檢索），零編造紀律比照
gap 分析 / growth 手冊。回答格式固定「結論／依據／未知／建議」四段，依據一律附
`job:ID` 引用，方便 Dashboard 點擊跳轉。

## 模組

| 檔案 | 用途 |
|---|---|
| `context.py` | 白名單資料檢索：漏斗統計（含階段分佈）、**進行中的選考／已投遞待回覆／近 21 天投遞**、逾期跟進、近期面試、高分未投遞、近 3 天新職缺、最新 Gap 批次。`build_context(question)` 組成給 LLM 的事實摘要，`findings()` 給 Dashboard 右側卡片 |
| `lookup.py` | **提問導向的企業／職缺模糊檢索**（`find_companies()` / `find_jobs_by_id()` / `has_apply_intent()`）。「XX 投過沒有」只能靠這裡回答 |
| `chat.py` | 問答主邏輯：組 prompt（事實 + `tools.deid.build_deid_profile()` 去識別化的候選人背景 + 最近幾輪對話）→ `interview._llm.call()`（Haiku）→ `tools.redact.redact()` 過濾取引先品牌名 → 落地 `store` |
| `store.py` | 對話紀錄，`data/practice.sqlite` 的 `assistant_turns` 表（可寫，跟唯讀的 jobs.sqlite 分開，比照 `dashboard/backend/practice_db.py` 慣例） |
| `digest.py` | 每日總結（純規則彙整）/ 每週總結（Sonnet 歸納主題），推播到職涯助手專用 bot |
| `bot.py` | 職涯助手專用 Telegram 互動 daemon |

## 「回答用的是舊職缺資料」的防線（2026-09 修）

事實區塊一旦缺某類資料，LLM 不會說「不知道」，而是**退回去讀 [對話紀錄] 裡的
舊回答**當依據 — 實測出現過「回覆率 82%」（當前 87%，82% 是幾天前那輪的舊值）、
「不知道哪家公司要求 SPI」（清單根本沒進 prompt）。四道防線：

1. **現況清單進事實區**：`active_pipeline()`（進行中的選考，回答「還有幾條線在跑」
   的唯一來源）／`awaiting_reply()`／`recent_applications()`。原本只有漏斗總數，
   沒有任何一筆具體應募。
2. **問題導向檢索**（`lookup.py`，見下節）：問句提到的公司／職缺帶著當前應募
   狀態、日程、見送り段階、gap 理由進 prompt。
3. **[對話紀錄] 降級為語境**：每輪標日期，system prompt 明寫「其中的數字與職缺是
   過期舊快照，不得複述、不得當依據」。事實區塊開頭加 `[資料截止]` 時間戳。
4. **日程標「已過期 N 天」**（`_event_note()`）：`applications.next_event` 是自由
   文字、過去的日程不會自動清掉，不標就會被當成即將發生的行程講出來。實測同時
   存在 `2026/09/07(月)` 與 `2026-09-03 11:00` 兩種寫法，日期正則兩種都要吃
   （原本只認 `/`，`-` 格式的日程整條漏出 `upcoming_interviews()`）。

## 「這家投過沒有」的模糊查詢（`lookup.py`）

使用者打的是簡稱或部分名（「サンプル」「SAMPLE STUDIO」），DB 存的是官方全名
（「株式会社サンプルロボティクス」「samplestudio」），精確比對接不住。比對規則：

- **比對基準是 `compact()`**（NFKC → 小寫 → 去空白與分隔記號），兩邊同款處理。
  不潰空白的話 `SAMPLE STUDIO` 對不上 `samplestudio`，反而會誤中「Studio 株式会社」
- **ASCII 名**：需 ≥5 字且前後非英數（潰掉空白後用這個代替單詞邊界）。
  `find`／`core` 這種日常英文同形的短社名直接排除
- **CJK 名**：最長公共子串 ≥4 字，或佔社名 ≥7 成；且**部分一致必須是社名的
  開頭**。日文社名語尾常共通（〜キャスト／〜スタジオ／〜ティング），允許尾部
  一致會把同業他社總撈進來；略称取頭所以前綴要求接得住。
  代價：正式名埋在 DB 社名中段的情形會漏（已知取捨）
- **企業單位聚合**（`company_norm`）：同一家常有多筆求人，只看 1 筆會把「有投過」
  答成「沒投過」。輸出同時列已應募與未應募的求人
- **見送り段階／理由**一併帶出（`REJECTION_STAGE_LABELS`），問「結果如何」才答得出

**查無時要明確否定。** `has_apply_intent()` 命中「應募／投過／選考／apply」等詞卻
一家都沒對上時，事實區塊寫「查無」並說明「＝沒有透過本管線投遞過」。不寫這句的話
LLM 會拿別家紀錄硬套，或含糊回一句「可能有投過」。

## 為什麼 Telegram 用獨立 bot（不是掛在 notify/bot.py）

`notify/bot.py` 是管線通知/操作 bot（按鈕 callback 為主）。自由文字問答混進同一個
bot 會讓通知卡片跟對話訊息互相干擾，且一個 bot token 只允許一個 `getUpdates`
消費者。比照 Samurai 專案「一個角色一個 bot」慣例（`TELEGRAM_TOKEN` /
`TELEGRAM_TOKEN_GROW` / `_DEV` / `_CODE` 各自獨立 token），職涯助手另開一個
`ASSISTANT_TELEGRAM_BOT_TOKEN`。`notify/__init__.py` 的 `bot_config(prefix)` /
`api_call(..., token=...)` 支援任意前綴讀 token/chat_id，供多個獨立 bot 復用同一套
HTTP 呼叫邏輯。

## 設定

`.env`：`ASSISTANT_TELEGRAM_BOT_TOKEN` / `ASSISTANT_TELEGRAM_CHAT_ID`（留空 =
`assistant/bot.py` 不啟動，Web Dashboard 的職涯助手頁面不受影響）。
`config/assistant.yaml`（範本 `.example`）：`chat_model` / `digest_model` /
`history_window` / `daily_digest_enabled` / `weekly_digest_enabled`。

## 排程

`scripts/assistant_daily_digest.sh`（`python3 -m assistant.digest daily`，鎖檔機制
比照 `gap_backfill_scan.sh`）、`scripts/assistant_weekly_digest.sh`（`... weekly`）。
launchd 範本：`com.jp-pm-jobs.assistant-bot.plist.example`（常駐互動 daemon）、
`com.jp-pm-jobs.assistant-daily-digest.plist.example`（每日 22:00）、
`com.jp-pm-jobs.assistant-weekly-digest.plist.example`（每週日 21:00）。

## Dashboard

`dashboard/backend/assistant_api.py`：`POST /api/assistant/chat`、
`GET /api/assistant/history`、`GET /api/assistant/findings`。前端頁面
`dashboard/frontend/src/pages/Assistant.tsx`，nav key `assistant`。

## CLI

```bash
python3 -m assistant.bot            # Telegram 互動 daemon（前景 debug）
python3 -m assistant.digest daily   # 手動跑一次每日總結
python3 -m assistant.digest weekly  # 手動跑一次每週總結
```
