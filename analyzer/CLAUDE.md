# analyzer/ — 評分與 Gap 分析

`jd_scorer.py`（6 維加權評分，零 LLM）→ `gap_analyzer.py`（LLM 逐筆差距分析）→
`gap_summary.py`（LLM 批次歸納推薦度報告）。評分權重/關鍵字/企業清單全部走
`config/scoring.yaml`（見根目錄 CLAUDE.md「Config-Driven 設定」），不需碰程式碼。

## Gap Analysis — 三種呼叫模式的關鍵差異

**推薦度報告頁讀的是 `gap_batches` 表，不是 `jobs.gap_analysis`。**

- `--job-id`: 單筆 debug，只寫 `jobs.gap_analysis` + `output/gap-{id}.md`，**不建 batch**
- `--top N`: 批次模式，走完整流程：`create_gap_batch()` → `analyze_one()` × N →
  `assign_gap_batch()` → `summarize_batch()` → `finalize_gap_batch()`（batch 於首筆成功時才建，
  整批失敗不留空記錄）
- `--backfill`: 迴圈分批（預設每批 20 = 一個 batch + 報告）清空所有未跑 gap 的職缺；整批 0
  成功即中止防空轉（失敗 id 記憶於本輪不重試，下輪重新嘗試）

`summarize_batch()` 走同一 LLM 鏈；失敗時直接自己生成 summary JSON 再呼叫
`finalize_gap_batch(batch_id, count, json_str)` 寫入。

## Prompt 瘦身

profile 用 `build_deid_profile(compact=True, facts_only=True)`（26.5k→16.4k→9.4k 字元；
compact 砍冗長敘事，facts_only 再砍面試敘事/元標籤——differentiators 整塊、gap_bridge_map、
proof_projects 元標籤、ai_engineering capabilities——只留要件匹配事實，known_gaps/SIer/語言
事實保留）；輸出限每項字數；`accept` 驗收（末 key 缺失 = 截斷 → gateway 自動換 brain）+ JSON
抽取失敗重試 1 次。

## Gap 常駐掃描（`scripts/gap_backfill_scan.sh`）

launchd `com.jp-pm-jobs.gap-backfill`，每 30 分鐘一輪，不綁固定時刻，只要機器/session 醒著就跑：

- 每輪呼叫 `gap_analyzer --backfill --max-jobs 0`，分批（每批 20）清空所有 score≥55 且有 JD
  本文、未跑 gap 的職缺，直到 backlog 清空或整批全失敗才停止本輪
- `LLM_CONFIG=config/llm.gap.yaml` 強制只走 `miko_gateway`（miko-ws 指揮中心），無 fallback；
  指揮中心不可用 → 整批失敗即中止本輪，下一輪自動重試
- `mkdir` 原子鎖（`output/logs/.gap_backfill.lock`）防止上一輪跑太久與下一輪重疊
- backlog 已清空時本輪秒級 no-op，不浪費呼叫
- `scripts/daily_job_scan.sh`（09:00 一次性）不再跑 gap，只負責重評分 + top10 客製

## 指令

```bash
python3 -m analyzer.gap_analyzer --top 30 --min-score 55
python3 -m analyzer.gap_analyzer --top 30 --min-score 55 --source "linkedin_jp"
python3 -m analyzer.gap_analyzer --job-id 123                          # 單筆 debug
LLM_CONFIG=config/llm.gap.yaml python3 -m analyzer.gap_analyzer --backfill --min-score 55
python3 -m tools.salary_parser                                         # 薪資重新解析
```
