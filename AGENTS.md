# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## PII 去識別化規則（LLM 呼叫必讀）

**任何送往外部 LLM 的 prompt 都必須對 candidate_profile.yaml 做去識別化。**

| 欄位 | 處理方式 |
|------|---------|
| `identity.name_ja` / `identity.name_romaji` | 替換為「本人」 |
| `contact.*`（email, github, portfolio, linkedin） | 替換為 `***` |
| `identity.birth_year` | 移除 |
| `identity.base`（住所） | 移除 |
| `compensation.current_annual` | 移除（僅保留 target range） |

**白名單送出**（僅以下 key 可進入 prompt）：
`positioning`, `domains`, `skills`, `experience`, `proof_projects`, `ai_engineering`,
`differentiators`, `match_summary`, `education`, `certifications`, `languages`,
`sier_experience`, `developer_tool_design`

**共用函數：** `tools.deid.build_deid_profile(profile)` — 所有 LLM 腳本統一使用。
參考實作：`tools/shokumu_tailor.py` 的 `build_deid_subset()`。

## What This Is

日本求職自動化管線：多站爬蟲 → 評分 → gap 分析 → 面試準備 → dashboard。求職者畫像（定位/目標年收/技術棧）全部來自 `data/candidate_profile.yaml` 等使用者資料檔（見「Key Data Files」），非寫死於程式碼——換人使用只需替換這些資料檔。

## Tech Stack

- **Backend/Scripts:** Python 3.14, FastAPI, SQLite
- **Frontend:** React 18 + TypeScript + Vite (inline styles, no CSS framework)
- **LLM:** 多 provider 抽象層（`llm/`），見下方「LLM Provider 層」
- **Browser automation:** Playwright + playwright-stealth

## LLM Provider 層（2026-07 開源改造）

`interview/_llm.py` 已改為 adapter，實際邏輯在 `llm/`（`llm/__init__.py` 的 `call()` / `health()`）。
設定檔 `config/llm.yaml`（本人環境用 `miko_gateway`，範本見 `config/llm.yaml.example`）：

```yaml
chain: [anthropic, cli]   # 依序嘗試，第一個成功者勝出
providers:
  anthropic: {api_key_env: ANTHROPIC_API_KEY, model: Codex-sonnet-4-5}
  cli: {binaries: [Codex, codex, gemini]}   # OAuth 訂閱制，免 API key
```

- 缺 `config/llm.yaml` 時自動偵測：`.env` 有哪家 API key 用哪家，都沒有則試本機 CLI
- 每個 provider 實作見 `llm/providers/{anthropic_api,openai_api,gemini_api,cli,miko_gateway}.py`，共同基類 `llm/providers/base.py`
- 呼叫端（`analyzer/gap_analyzer.py`、`tools/resume_tailor.py` 等）不受影響，仍是 `from interview._llm import call`

## Pipeline

```
scrape.py (多站爬蟲 + Gmail 郵件來源)
  → jd_tier_classifier (企業分類: ai_startup/mega_venture/traditional_sier)
  → salary_parser (raw_jd 正則抽取 → 萬円)
  → jd_scorer (6 維加權: salary_fit/role_fit/market_keywords/tech_overlap/tier/domain)
  → resume_tailor (高分職缺客製履歷)
  → gap_analyzer (LLM Haiku, 單筆或批次)
  → gap_summary (LLM Sonnet, 批次歸納 → gap_batches 表)
```

評分和客製履歷是純規則（zero LLM token）。只有 gap 分析和面試準備呼叫 LLM。

## Key Commands

```bash
# 全量掃描（自動觸發後處理）
python3 scrape.py --preset all --source all

# 單獨後處理（分類→薪資→評分→tailor）
python3 -c "from scrape import run_postprocess; run_postprocess()"

# Gap 批次分析 — 必須用 --top 才會建 gap_batch 記錄
python3 -m analyzer.gap_analyzer --top 30 --min-score 55
python3 -m analyzer.gap_analyzer --top 30 --min-score 55 --source "linkedin_jp"

# Gap 單筆（只寫 jobs.gap_analysis，不建 batch，推薦度報告頁看不到）
python3 -m analyzer.gap_analyzer --job-id 123

# 薪資重新解析
python3 -m tools.salary_parser

# 投遞包（應募時）— 公司調研 + cover note + 職務経歴書 + 志望動機
python3 prep.py 123 apply                            # 自動偵測語言（見下）+ 全 stage
python3 prep.py 123 apply --lang en                  # 強制英文包（外資）
python3 prep.py 123 apply --lang jp                  # 強制日文包
python3 prep.py 123 apply --stage brief              # 只跑公司調研
python3 -m resume.jp.render --shokumu                # 2 頁職務経歴書 HTML/PDF（獨立渲染）

# 面試包（面試邀請後才跑）— 想定問答 + checklist
python3 prep.py 123 interview                        # 全 2 stage

# Dashboard
cd dashboard && bash run.sh          # build + uvicorn :8000
cd dashboard && bash run.sh --skip-build  # 跳過前端 build
```

## 投遞包語言自動偵測

`prep.py {id} apply` 預設 `--lang auto`，由 `_detect_lang()` 判斷生成英文還是日文包：

- **JD 的 CJK 字元比例 < 20% → `en`**（英文包：英文 cover letter + 英文 tailored 履歷 + 繁中 brief）
- **Fallback**（JD 為空）：URL 含 `greenhouse.io / lever.co / ashbyhq.com / workable.com` → `en`
- **其他 → `jp`**（日文包：志望動機 + 職務経歴書 + 繁中 brief）

外資 JD 通常 0% CJK，日企 80%+，混合日企（如 25% CJK）判為 `jp`。可用 `--lang en/jp` 強制覆蓋。

- 英文包輸出目錄：`output/apply/{id}_{slug}_en/`，stage = `brief_cover, resume`
- 日文包輸出目錄：`output/apply/{id}_{slug}/`，stage = `brief, resume, shibou`

## Gap Analysis — Critical Distinction

**推薦度報告頁讀的是 `gap_batches` 表，不是 `jobs.gap_analysis`。**

- `--job-id`: 單筆 debug，只寫 `jobs.gap_analysis` + `output/gap-{id}.md`，**不建 batch**
- `--top N`: 批次模式，走完整流程: `create_gap_batch()` → `analyze_one()` × N → `assign_gap_batch()` → `summarize_batch()` → `finalize_gap_batch()`
- `summarize_batch()` 用 CLI subprocess 呼叫 Sonnet。如果因 PATH 問題失敗，直接自己生成 summary JSON 再呼叫 `finalize_gap_batch(batch_id, count, json_str)` 寫入

## Database

路徑: `data/jobs.sqlite`

| 表 | 用途 |
|---|---|
| `jobs` | 職缺（含 score, gap_analysis JSON, tier, salary_min/max） |
| `applications` | 應募追蹤（status 漏斗: applied→recruiter→tech→onsite→offer/rejected） |
| `gap_batches` | 推薦度報告（summary_json 供 dashboard 渲染） |

Dashboard 用唯讀連線 (`?mode=ro`)。寫入時可能遇到 DB locked（殘留 sqlite3 進程），`salary_parser` 已有重試機制。

## Scraper Architecture

`scrapers/` 下是插件式 provider（實作 `scrape()` + `PROVIDER_META`，自動註冊）：
- `indeed_jp` — 公開，不需 cookie
- `green` — 公開，不需 cookie；JD 全文直接解析 SSR 頁面內嵌的 `__NEXT_DATA__` JSON（比 DOM 選擇器穩定，不需 CDP）
- `linkedin_jp` — 需 cookie 或 CDP (port 9253)
- `bizreach` — 需 cookie 或 CDP (port 9270)
- `wantedly` — 需 cookie
- `recruiter_agent` — Gmail API 讀取仲介推薦郵件，需 OAuth（`python3 -m inbox.auth`）+ `config/scraping.yaml` 設 `recruiter_agent.sender_query`（未設定則自動 skip）

CDP port / Chrome profile 路徑可由 `config/scraping.yaml` 覆蓋（`tools/app_config.py` 統一載入，缺檔則用 `scrape.py` / `inbox/_cdp.py` 內建預設值，本人環境行為不變）。

## Config-Driven 設定（開源改造新增）

`tools/app_config.py` 提供 `load(name)` / `get(name, key, default)`，讀 `config/{name}.yaml`（缺檔 = 用程式碼內建預設值，行為零變化）：

| 檔案 | 覆蓋內容 | 範本 |
|---|---|---|
| `config/scoring.yaml` | `analyzer/jd_scorer.py` 全部權重/關鍵字/企業清單、`analyzer/gap_analyzer.py` 的 `_TIER1/_TIER2/_HARD_STOP/_SIER/_LARGE_CO` | `config/scoring.yaml.example` |
| `config/scraping.yaml` | CDP port/profile、Chrome 路徑、recruiter_agent sender_query | `config/scraping.yaml.example` |
| `config/llm.yaml` | LLM provider chain | `config/llm.yaml.example` |

換職種/換求職偏好只需改 `config/scoring.yaml`，不需碰 `analyzer/` 原始碼。

## 開源部署

- **一鍵安裝：** `bash setup.sh`（檢查依賴 → 複製 `.env`/`config/*.yaml`/`data/*.yaml` 範本 → 裝套件 → `tracker.db.init_db()` 建 schema）
- **Docker：** `docker compose up -d`，只跑 Dashboard（不含爬蟲，CDP 需互動登入不適合容器化，爬蟲固定在 host 跑，`data/`/`output`/`config` volume 掛載共用）
- **單人登入保護：** `.env` 設 `DASHBOARD_PASSWORD` 啟用 HTTP Basic Auth（`dashboard/backend/main.py` 的 `_auth_gate` middleware），留空 = 不驗證
- **CORS/Port：** `DASHBOARD_CORS_ORIGINS`（逗號分隔）、`DASHBOARD_PORT` 環境變數，預設沿用本機開發值
- **公開匯出（私 repo → 乾淨公開 repo，不改寫私 repo 歷史）：** `bash scripts/export_public.sh [目標目錄]` — 依 `.publicignore` 清單剔除個人資料/個人工具/portfolio/docs wiki，並掃描殘留個資後才 `git init` 首次 commit。私 repo 本身的 `data/candidate_profile.yaml` 等個人檔案原地保留、不受影響。

## Dashboard Frontend

14 個頁面，純 state 切頁（無路由庫）。頁面整合（14→8 模組）已規劃但尚未執行，見下輪待辦。Design tokens 在 `src/theme.ts`（暖白米底 #FFF8F0 + 流金 #F5C842 + Noto Sans JP）。共用元件在 `src/components/ui.tsx`。`JobDrawer` 是全局側邊抽屜。

## Key Data Files

| 檔案 | 用途 | 可否自動改寫 |
|------|------|------------|
| `data/candidate_profile.yaml` | 候選人畫像（gap 分析唯一真實來源） | ❌ 禁止 |
| `data/cognitive_profile.yaml` | 認知數據 / 目標年收 | ❌ 禁止 |
| `data/tech_footprint.yaml` | 38 技術實體（scorer 比對用） | ❌ 禁止 |
| `resume/jp/data.yaml` | 職務経歴書結構化數據 | ❌ 禁止 |
| `interview/companies/*.md` | 各公司調研筆記 | ⚠️ 只增不刪 |

上述每個檔案都有對應 `*.example` 範本（結構骨架 + TODO 佔位，無真實個資），供 `setup.sh` 複製初始化。`data/archive/candidate_profile_{zh,original}.yaml` 是舊版備份/翻譯，零程式碼引用，僅供人工查閱。

## 文件夾架構與新檔案放置規則

新建檔案一律按下表放置，禁止散落根目錄：

| 要新增的東西 | 位置 | 備註 |
|---|---|---|
| 管線入口 CLI | 根目錄**僅限現有 5 個**（scrape / prep / pipeline / fetch_jd / apply_strategy） | 新功能掛進現有入口或以 `-m` 模組執行，不再新增根目錄腳本 |
| 爬蟲 provider | `scrapers/{site}.py` | 實作 `scrape()` + `PROVIDER_META` 自動註冊 |
| 評分 / 分析邏輯 | `analyzer/` | |
| LLM provider | `llm/providers/` | 繼承 `base.py` |
| 共用可 import 模組、常用 CLI | `tools/` | 例：`deid`、`app_config`、`salary_parser` |
| 一次性 / 歷史資料修補腳本 | `tools/oneoff/` | 跑完即歸檔；`python3 -m tools.oneoff.<name>` 執行 |
| 操作性 shell / launchd | `scripts/` | |
| 後端 API 端點 | `dashboard/backend/` **新開 router 檔**（照 `applications.py` 模式） | 不要再往 `main.py` 加端點 |
| 前端頁面 / 共用元件 | `dashboard/frontend/src/pages/` / `src/components/` | |
| 開發文檔 | `docs/*.md` | wiki HTML 在 `docs/{產品,技術,流程,運營}/` |
| 面試手寫 source | `interview/*.md` | 手寫檔是 dashboard 讀取來源，位置不可動 |
| 面試生成物（生成 QA / 深掘） | `interview/generated/`（gitignored） | 其他管線生成物一律 `output/` |
| 臨時 / 實驗檔 | 系統暫存目錄 | 禁止進 repo（禁 `tmp-*` 檔落地根目錄） |
| 參考 repo / 資料 | `参考資料/`（gitignored） | `jobfunnel/`、`resume-lm/` 為既有參考 gitlink，不再新增根目錄參考 repo |

新增**含個人資料**的檔案時，同步檢查 `.gitignore` 與 `.publicignore`（公開匯出排除清單）。

## Gotchas

- **背景進程 PATH 丟失:** `llm/providers/cli.py`（CLI provider）用 subprocess 呼叫 Codex/codex/gemini，在 Bash `run_in_background` 下 PATH 可能丟失（已有 fallback PATH 防護）。遇到全 provider 失敗不要反覆重試，直接用 Codex 自己完成 LLM 歸納任務。
- **零編造紀律:** gap 分析、面試準備、履歷 — 候選人沒有的數字/經驗禁止編造。
- **Data Contract:** `data/`, `output/`, `interview/companies/` 是 User Layer，不可自動覆蓋。`scrapers/`, `analyzer/`, `tools/` 是 System Layer，可安全更新。
- **PII 去識別化:** 任何送往外部 LLM 的 prompt 必須使用 `tools.deid.build_deid_profile()` 去識別化。詳見頂部「PII 去識別化規則」。
