# growth/ — 成長實戰手冊產生器

從 JD 生成「這份工作實際上要怎麼做」的 7 段式操作手冊。跟根目錄 CLAUDE.md 描述的
主線 8 階段（找到並投遞職缺）是不同層次的問題，**獨立指令**（`python3 -m growth`），
不掛在 `prep.py` 上，不寫入 `jobs.sqlite`。

## 7 段（`prompts.py` 的 `STAGE_ORDER`）

| # | stage | 檔案 | 回答什麼問題 | deps | needs_profile |
|---|---|---|---|---|---|
| 1 | `decompose` | `01_jd_decomposition.md` | 這份工作實際上要解決什麼增長課題 | — | 否 |
| 2 | `metrics` | `02_metric_tree.md` | 用什麼指標衡量做得好不好 | decompose | 否 |
| 3 | `benchmark` | `03_benchmark.md` | 優秀產品實際上怎麼做到的、能不能搬過來 | decompose | 否 |
| 4 | `playbook` | `04_playbook.md` | 具體怎麼做（誰做／何時／交付什麼） | decompose, metrics, benchmark | 否 |
| 5 | `cadence` | `05_operating_cadence.md` | 各階段要交出什麼報告 | metrics, playbook | 否 |
| 6 | `crosscheck` | `06_crosscheck.md` | 對照真實經歷，我到底做不做得到 | decompose, benchmark, playbook | **是** |
| 7 | `skillplan` | `07_skill_growth_plan.md` | 90 天內怎麼把落差補起來 | crosscheck, metrics | **是** |

**為何前 5 段不放候選人資料**：prompt 已因 JD 全文 + 前段成果物 + 知識庫案例而夠長，太早混入
「自己」的資訊會讓客觀職位設計討論偏向自我辯護；只有 `crosscheck` / `skillplan` 真正需要對照
「候選人做不做得到」時，才在 `pipeline.build_prompt()` 讀取 `context.profile_text()`。

## PII / 遮蔽

`context.py` 是 growth pipeline 唯一的 prompt 素材入口：
- `profile_text()` → `tools.deid.build_deid_profile(compact=True)`（跟主線 LLM 呼叫同一白名單）
- `jd_text()` / `gap_summary()` → 都先過 `tools.redact.redact()`（取引先品牌遮蔽）
- `gap_block()` → `tools.gap_facts.match_evidence()`，DB 內既有 gap 分析素材

## 機械閘門（`gates.py`，仿 `interview/theater_script.py` 的 Gate A/B/C）

| Gate | 檢查什麼 | 觸發條件 |
|---|---|---|
| A 結構 | 必須章節齊全、最低字數/行數、是否有表格 | `GateSpec.required_sections` / `min_chars` / `min_lines` / `require_table` |
| B 事實錨定 | 「數字＋公司名」同行時，公司名不在案例庫（`knowledge.case_names()`）也不在 JD 原文 | 判定為疑似編造他社數字，逼 AI 用 `{{要出典確認}}` |
| C 可執行 | `block_marker`（如 `###`）劃出的每個小節必須含 `block_required` 任一組關鍵詞（例：擔當/期間/成果物） | 只有 `playbook`（步驟）與 `cadence`（報告）啟用 |
| D 安全 | PII 殘留（`tools.pii_gate.scrub_for_external`）+ 取引先品牌名殘留（`tools.redact.scan`） | 全段皆檢查 |

不合格時把具體指摘（`GateResult.as_feedback()`）附進 prompt 要求重寫，`pipeline.RETRY_MAX = 2`。
仍不過關的段落標記 `degraded` 直接寫入產出並附上未解決指摘（不會靜默假裝通過），不阻擋其他段落
（各段獨立快取、可單獨 `--force` 重跑）。

## 知識庫（`knowledge/*.yaml`，Gate B 白名單來源）

- `growth_cases.yaml`（15 件）— 真實增長案例，每件含 situation/action/result/why_it_works/
  transferable_when/caveats/source/confidence
- `metric_frameworks.yaml`（8 個框架）— north_star / aarrr / heart / saas_unit_economics /
  plg_funnel / ai_product_metrics / experiment_design / rice_prioritization
- `report_catalog.yaml`（15 份）— 例行報告骨架（cadence/audience/decision/inputs/skeleton）

檢索純規則、零 LLM：`knowledge.classify_jd()` 用正則對 JD 文字打標籤
（business_model / lever / growth_stage / japan_market），`select_cases()` 依標籤命中分數排序。
案例庫沒有貼合類型時退回 `b2b_saas` + `acquisition`/`retention` 的通用組合，不會硬套。

## 快取與冪等

`pipeline.run_stage()` 用 prompt 的 sha256 做內容尋址快取（`_cache/{stage}.json`）；素材沒變
（JD、前段成果物、facts 都相同）時直接跳過，不重複呼叫 AI。下游段落讀前段成果物時會自動剝除
機械插入的原 JD 區塊（`JD_BLOCK_START/END`），避免二次膨脹 prompt。

## 輸出

`output/growth/{job_id}_{company_slug}/`：
```
01_jd_decomposition.md … 07_skill_growth_plan.md
08_review_report.md   # 閘門結果彙總 + 人工必須自己查證的 5 類事項
README.md             # 讀的順序、使用時機、重新生成指令
_cache/{stage}.json   # 內容尋址快取
_prompts/{stage}.prompt.md   # --no-llm 時落地的 prompt（除錯用）
```

## 指令

```bash
python3 -m growth 123                                   # 全 7 段（快取命中則跳過）
python3 -m growth 123 --stage playbook --force           # 只重跑一段
python3 -m growth 123 --facts output/apply/123_X/01_company_brief.md   # 帶已查證公司事實
python3 -m growth 123 --dry-run                          # 零 AI，只看 JD 標籤與選中案例
python3 -m growth 123 --no-llm                           # 只落地 prompt 檔案，不呼叫 AI
python3 -m growth --list-stages                          # 看 7 段順序與依賴
```

詳見人類可讀版本：`docs/流程/stage-growth.html`。
