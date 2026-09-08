# tools/ — 模組治理

`tools/` 分兩類，界線如下。新增檔案前先看 CLAUDE.md「文件夾架構與新檔案放置規則」。

## 共用庫（穩定 API — 被其他模組 import）

以下模組被 `scrapers/`、`analyzer/`、`dashboard/`、`prep.py` 等 import，
**函數簽名改動視為 breaking change**：改之前全域 grep 引用方，並跑 `python3 -m pytest tests/`。

| 模組 | 穩定入口 | 主要引用方 | 測試 |
|---|---|---|---|
| `deid.py` | `build_deid_profile()`, `load_profile()` | 所有 LLM 腳本（PII 紅線） | `tests/test_deid.py` |
| `app_config.py` | `load(name)`, `get(name, key, default)` | scrape / scrapers / analyzer / inbox | — |
| `salary_parser.py` | `parse_salary(jd)` | jd_scorer / scrapers / tracker.db | `tests/test_salary_parser.py` |
| `jd_tier_classifier.py` | `classify()`, `classify_posting_type()` | scrape / fetch_jd / scrapers | `tests/test_jd_tier_classifier.py` |
| `dedup_match.py` | `find_duplicate()` 等 | tracker.db / oneoff | `tests/test_dedup_match.py` |
| `followup.py` | `get_followup_schedule()`, `get_followup_history()` | dashboard（jobs_api / applications） | — |
| `liveness.py` | `check_url()`, `classify_liveness()` | 各 liveness CLI / scripts | — |
| `blacklist.py` | — | prep / scrape / tracker.db | — |
| `resume_tailor.py` / `shokumu_tailor.py` | `tailor_for_row()` 等 | prep / pipeline / scrape | — |
| `drilldown_gen.py` | `run()` | dashboard（prep_api） | — |
| `miko_llm.py` | — | llm/providers/miko_gateway | — |

## CLI 工具（使用者手動執行，無反向依賴）

其餘 `tools/*.py` 是 CLI（`python3 -m tools.<name>`）：doctor、refetch_jd、liveness、
ragent_liveness、open_*_jd、match_brief、shokumu_*、seed_demo、cognitive 挖掘系列
（cognitive_mine / extract_corpus / profile_signals / project_signature / synthesize_v2 /
tech_footprint / temporal_analysis）、`screenshot_dashboard`（Dashboard 15 頁整頁截圖）等。
改動自由，但 usage 字串與 docs wiki 同步。

### 面接スライド解説詞

`interview_slide_script.py` は既存 PPTX を変更せず、指定ページから日本語の面接用台本を
生成する。候補者 profile は `build_deid_profile()` の白名單、PPTX 文字も PII scrub 後
に LLM へ送る。サンプル値、未確認数字、書面語、長文、AI と人の責任分担を機械検査する。

```bash
python3 -m tools.interview_slide_script \
  ~/Downloads/自己紹介.pptx --slides 1-6

# 外部 LLM を呼ばず、去識別化済み prompt だけ確認
python3 -m tools.interview_slide_script \
  ~/Downloads/自己紹介.pptx --slides 1-6 --no-llm
```

出力先に同名ファイルがある場合は自動上書きしない。明示的に置換する場合だけ `--force`。

Dashboard 已啟動時可執行：

```bash
python3 -m tools.screenshot_dashboard
python3 -m tools.screenshot_dashboard --output-dir output/screenshots/manual
```

每張 PNG 都會檢查實際尺寸是否涵蓋完整頁面高度，並在同目錄寫入 `manifest.json`。

## 一次性腳本

跑完即歸檔的資料修補/遷移腳本放 `tools/oneoff/`（見該目錄 README），不要放本層。
