# inbox/ — 收件匣自動掃描

`scripts/inbox_reply_scan.sh`（launchd `com.jp-pm-jobs.inbox-scan`，30 分鐘一輪）依序跑：
規則分類 → 応募受付自動記録 → 日程確定自動入庫 → 生成草稿 → 面試提醒檢查 → Telegram 通知
→ 面接パック自動生成。

## 規則分類（`rule_classify.py`，零 LLM）

排除清單（推薦模板信／電子報／noreply 群發信）優先命中即判「其他」；分類優先權：
`application_ack`（応募受付）> `schedule_confirmed`（日程確定通知）> 其餘關鍵詞判斷
（拒信 → 錄用 → 時間協調 → 面試邀請 → 初次聯繫）。

⚠ **`application_ack` 必須排最前**：r-agent 応募受付信本文裡有一段注意事項寫著
「書類選考お見送りの場合は…」，關鍵詞比對會直接命中「拒信」規則——順序錯了會把
「你的應募已受理」誤判成「你被拒絕了」。

## 応募受付自動記録（`application_ack.py`，零 LLM 正則）

r-agent「応募手続きを承りました【リクルートエージェント】」信件正則抽出
【企業名】【仕事の名称】【求人No】→ `company_norm` 對應 `jobs`（同公司多筆時用職務名相似度）
→ 寫入 `applications`（`status='applied'` / `channel='r-agent'` / `applied_at=`受信日，
`notes` 存實際応募職名＋求人No）→ Telegram 通知。

**非破壞**：已有応募記錄的 job 完全不碰（不會把 rejected/recruiter 倒回 applied）；對不到
職缺只報告不寫入。

```bash
python3 -m inbox.application_ack --days 60 --dry-run   # 先確認配對結果
python3 -m inbox.application_ack --days 60             # 過去分一次補齊（--days 必要，
                                                        # 避免撈到更早求職期的舊信）
```

## 日程確定自動入庫（`schedule.py`，零 LLM 正則）

r-agent「日程確定のお知らせ」（【企業名】【確定日時】）與 TimeRex「日程調整が完了しました」
→ 對應 `applications` → 更新 `next_event`（保留既有段位詞如「1次選考」，不覆蓋）→ Telegram
通知公司＋日時。冪等：リマインド重送同值不重寫、不重複通知；對不到職缺仍通知（標記需手動確認）。

## 面接パック自動生成（`prep_trigger.py`）

日程が登記された面接（`applications.next_event` に今日以降の日付）でパック未生成なら
`prep.py {id} interview` を **1 ラウンド 1 本** 走らせ、完了を Telegram へ推す。
起点を `schedule.apply_schedule()` の書き込みに直結せず `applications` を毎回スキャンする
方式にしたのは、Dashboard / bot から手で `next_event` を入れた場合も同じ扱いにするため。

- **既存パックは再生成しない** — 手編集した `01_interview_qa.md` が prep.py 再実行で消える
  （`/hire-audit` 節の警告と同じ問題）。代わりに「既存パックあり」を 1 回だけ通知して人間に渡す
- 既定 stage は `qa,jikoshoukai,checklist,script`。slides は品質未達、voice は指揮中心の
  ChatGPT アカウントを十数分占有するので既定から外す（`config/prep.yaml` で足せる）
- 冪等は `notify_log`（`auto_interview_prep_done` = 終端、`auto_interview_prep` = 試行回数）。
  面接日程が変われば ref も変わるので、新しい選考段階では再評価される
- パック生成は 30 分の launchd 間隔をまたぐことがあるので `output/logs/.interview_prep.lock`
  （mkdir 原子ロック）でラウンド跨ぎの重複を防ぐ

```bash
python3 -m inbox.prep_trigger --dry-run             # 対象だけ表示
python3 -m inbox.prep_trigger --job-id 123 --force  # 既存パックでも強制再生成
```

## 草稿自癒

LLM 故障時草稿生成失敗的信存 `body_raw`（`store.py`），之後每輪補生（上限 8 封/輪，
`store.list_pending_drafts`）；舊資料無正文則按 msg id 回 Gmail 重抓。

## launchd TCC 陷阱

背景 `/bin/bash` 讀不了 `~/Documents`（Operation not permitted），所有 plist 一律經
`scripts/launchd_shim.py`（python3 進入點，bash 子行程繼承授權）。

## 手動指令

```bash
bash scripts/inbox_reply_scan.sh                # 全流程一輪
python3 -m inbox.reply --days 2 --max-results 30
python3 -m inbox.reply --days 2 --dry-run       # 只看分類結果，不生成草稿
python3 -m inbox.reply --days 7 --starred --all # 已加星號的郵件（不限收件匣）
python3 -m inbox.auth                            # 重新授權 Gmail API（token 過期時）
```
