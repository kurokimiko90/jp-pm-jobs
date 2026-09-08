# linkedin_inbox/ — LinkedIn Messaging 自動回覆草稿

`scripts/linkedin_inbox_scan.sh`（launchd `com.jp-pm-jobs.linkedin-inbox`，30 分鐘一輪）：
CDP 連 LinkedIn Messaging → 抓未讀對話 → 規則分類（只留招聘相關）→ 生成回覆草稿 → 存檔 →
Telegram 通知。架構比照 `inbox/`（Gmail 收件匣自動掃描），差異是 LinkedIn 沒有官方草稿
API，草稿只存 DB 與本地檔案，**人工複製貼上到 LinkedIn 網頁後手動送出**。

## ⚠ 永不自動發送

`draft.py` / `reply.py` 全程不呼叫任何 LinkedIn 送出訊息的操作。草稿唯二去處：
`linkedin_messages.draft_text`（DB）與 `output/linkedin_drafts/{conversation_id}.txt`
（純文字檔，開頭附寄件人與 profile URL，供人工核對後複製貼上）。

## ⚠ DOM 選擇器未經實機驗證

`fetch.py` 的 selector 基於 LinkedIn 2026-08 時點的一般結構推測（改版頻率高）。**第一次
跑務必先 `python3 -m linkedin_inbox.reply --dry-run` 人工核對抓到的內容**，抓不到東西
或抓錯時比照 `scrapers/linkedin_jp.py` 的做法調整 selector（多重 fallback、寬鬆比對），
不要無條件信任抓取結果就直接生成草稿。

## 連線（CDP，沿用 linkedin_jp 職缺爬蟲同一個 profile）

`config/scraping.yaml` 的 `linkedin_cdp`（port 9253、`~/.chrome-linkedin`）；缺檔則用
`reply.py` 內建預設值。與 `scrapers/linkedin_jp.py` 共用同一個已登入 Chrome profile——
若該 Chrome 正被職缺爬蟲佔用，本模組會重用同一個 port（`tools/cdp.py` 的
`ensure_cdp()`：port 已監聽則直接連，不重複啟動）。

## 規則分類（`rule_classify.py`，零 LLM）

只留「招聘相關」對話，過濾社交邀請/廣告雜訊：
- 發信人 headline 命中 `recruiter`/`talent acquisition`/`採用`/`人事` 等關鍵詞 → 信號最強
- 訊息正文命中 `position`/`求人`/`面接` 等關鍵詞（≥2 個或搭配 headline 命中）→ 判定招聘相關
- 明顯的「連接邀請」罐頭訊息（`let's connect` 等）優先判定非招聘

信心 < 0.5（`_DRAFT_MIN_CONF`）的招聘相關對話只入庫，不生成草稿。

## 草稿生成（`draft.py`）

- `tools.deid.build_deid_profile(compact=True)` — 候選人畫像去識別化（內部已含
  `tools.redact.redact()` 品牌名遮蔽）
- 對方訊息正文先過本地的 email/電話/URL 遮罩，再過 `tools.redact.redact()`
  （取引先ブランド名，NDA 相當——避免候選人現職接觸過的品牌名意外流入草稿）
- 生成後用 `tools.redact.scan()` 做殘留檢查，命中只印警告不阻擋（草稿本來就要人工複審）
- 零編造：面試時段／目前年收／離職日等候選人沒有的具體數字一律留 `【...】`/`[TBD]` 佔位
- 語言：交給 LLM 判斷（system prompt 要求「用對方訊息相同的語言回覆」），不做程式化的
  CJK 比例偵測 — LinkedIn 對話中日英夾雜比 Gmail 更常見

## 資料表

`linkedin_messages`（`store.py`，System Layer、可重建）：`conversation_id` 去重、
`category`/`confidence` 分類結果、`draft_text` 草稿全文、`status`
(`new`/`classified`/`draft_ready`/`skipped`)。與 `inbox_mails` 同寫入
`data/jobs.sqlite`，重用 `tracker.db.connect()`。

## 手動指令

```bash
bash scripts/linkedin_inbox_scan.sh                      # 全流程一輪
python3 -m linkedin_inbox.reply --dry-run                # 只看分類結果，不生成草稿
python3 -m linkedin_inbox.reply --max-conversations 10   # 限制單輪處理數（新環境先小量測試）
```

## Gotchas

- **未登入自癒**：`fetch.py` 偵測到 `/login`/`/checkpoint` 跳轉時回空清單，不拋錯，
  下一輪 30 分鐘後自動重試（比照 `linkedin_jp` 職缺爬蟲的登入檢查邏輯）。長期未登入
  不會有任何通知——首次部署後建議手動跑一次 `--dry-run` 確認能抓到東西。
- **常駐風險**：LinkedIn 對自動化操作的偵測比 Gmail API 敏感，`linkedin_inbox_scan.sh`
  常駐 30 分鐘一輪等同於持續用瀏覽器自動化操作帳號。若觀察到帳號被限制/驗證碼增加，
  優先降頻或改回手動跑 `reply.py`，不要無視警訊持續常駐。
