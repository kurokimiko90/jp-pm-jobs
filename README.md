# jp-pm-jobs

日本求職自動化管線 + Dashboard：多站爬蟲 → 規則評分 → LLM gap 分析 → 客製履歷/投遞包/面試準備 → 應募追蹤。

自架（self-host）專案：clone 下來、填自己的資料、跑自己的一份，資料完全留在你自己的機器上，不上傳任何第三方伺服器（LLM API 呼叫除外，且已做 PII 去識別化）。

## 這是什麼

一條端到端的求職管線：

```
多站爬蟲（Indeed / LinkedIn / BizReach / Wantedly / Gmail 推薦信）
  → 企業分類 + 薪資解析
  → 6 維規則評分（zero LLM token）
  → 客製履歷（zero LLM token）
  → LLM gap 分析（你 vs JD 的差距，附推薦度）
  → 投遞包（公司調研 + cover letter + 客製履歷）
  → 面試包（想定問答 + checklist）
  → 應募追蹤（漏斗：applied → recruiter → tech → onsite → offer）
```

外加一個 FastAPI + React 的 Dashboard，把整條管線的產出視覺化。

## 快速開始

**前置需求：** Python 3.11+、Node.js 18+

```bash
git clone <your-fork-url>
cd jp-pm-jobs
bash setup.sh
```

`setup.sh` 全程冪等（重跑不會覆蓋你已編輯過的設定檔），依序做：

| 步驟 | 內容 |
|---|---|
| 1/6 檢查依賴 | 確認 `python3` / `node` 存在，印版本號 |
| 2/6 複製設定範本 | `.env`、`config/llm.yaml`、`config/scraping.yaml`、`data/candidate_profile.yaml`、`data/cognitive_profile.yaml`、`data/tech_footprint.yaml`、`data/blacklist.yaml`（已存在的檔案會跳過，不覆蓋） |
| 3/6 安裝 Python 套件 | `pip install -r requirements.txt` |
| 4/6 安裝 Playwright 瀏覽器 | `playwright install chromium`（爬蟲用） |
| 5/6 安裝前端套件 | `cd dashboard/frontend && npm install` |
| 6/6 初始化資料庫 | 建立 `data/jobs.sqlite` schema |

跑完會印出下一步指引。接著手動做：

1. 編輯 `data/candidate_profile.yaml` — 你的求職畫像，**gap 分析與投遞包的唯一資料來源**（零編造原則：這裡沒有的經歷/數字不會出現在任何產出文件）
2. 編輯 `.env` — 填一個 LLM API key（見下方「LLM 設定」）
3. `bash dashboard/run.sh` → http://localhost:8000
4. （可選）需要爬 LinkedIn/BizReach 等需登入站點，見下方「爬蟲設定」
5. 多人使用 / 對外公開部署，設定 `.env` 的 `DASHBOARD_PASSWORD` 啟用登入保護

`config/scoring.yaml`（評分權重客製）非必填，`setup.sh` 不會自動建立，需要時自行 `cp config/scoring.yaml.example config/scoring.yaml`。

## 三種部署方式

### A. 本機開發

```bash
bash setup.sh
bash dashboard/run.sh              # build 前端 + 啟動 FastAPI，:8000
bash dashboard/run.sh --skip-build # 前端已 build 過，跳過重build
```

前端獨立開發模式（hot reload）：`cd dashboard/frontend && npm run dev`（:5173，API proxy 到 :8000）。

### B. Docker（只跑 Dashboard，不含爬蟲）

```bash
cp .env.example .env   # 填好 LLM API key
docker compose up -d
```

開 http://localhost:8000。`data/` `output/` `config/` 皆掛載為 volume，跟 host 共用 — 你在 host 上跑爬蟲寫入 `data/jobs.sqlite`，容器內的 Dashboard 立刻看得到。

> **為什麼爬蟲不跑在容器裡：** LinkedIn / BizReach 用 CDP 連你本機已登入的 Chrome（需要你手動登入一次，之後吃 cookie）。這種互動式登入在容器裡做不到，所以爬蟲固定在 host 上跑，只有 Dashboard 進容器。Indeed（公開來源）不需登入，容器內外都能跑。

### C. 純本機 / 伺服器手動部署

跟 A 相同，差別只在你自己管 process（systemd / pm2 / screen 皆可）。`dashboard/run.sh` 支援 `DASHBOARD_PORT` 環境變數改埠。

## LLM 設定

`interview/_llm.py` 統一走 `llm/` 多 provider 層，設定檔 `config/llm.yaml`（範本 `config/llm.yaml.example`）：

```yaml
chain:
  - anthropic     # 依序嘗試，第一個成功者勝出
  - cli           # 沒 API key 時退回本機已登入的 CLI

providers:
  anthropic:
    api_key_env: ANTHROPIC_API_KEY   # 在 .env 填值
    model: claude-sonnet-4-5
```

**兩種認證方式擇一：**

| 方式 | 怎麼設 | 適合 |
|---|---|---|
| API key | `.env` 填 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | 最穩定，按用量計費 |
| 本機 CLI（OAuth 訂閱） | 安裝並登入 `claude` / `codex` / `gemini` CLI，`chain` 設為 `[cli]` | 吃你已有的 Claude Pro / ChatGPT Plus 額度，免另付 API 費 |

不建 `config/llm.yaml` 也能跑：系統會自動偵測 `.env` 裡有哪家 key 就用哪家，都沒有則試本機 CLI。

`providers.openai.base_url` 可改成任何 OpenAI 相容端點（OpenRouter / Ollama / LM Studio），細節見 `config/llm.yaml.example` 註解。

## 爬蟲設定

| 來源 | 需要登入 | 設定 |
|---|---|---|
| Indeed | 否 | 開箱即用 |
| LinkedIn / BizReach | 是（CDP） | 見下 |
| Gmail 推薦信（recruiter_agent） | 是（OAuth） | `python3 -m inbox.auth`；`config/scraping.yaml` 填 `sender_query` |

**LinkedIn / BizReach（CDP 模式）：**

```bash
python3 scrape.py --preset core_pm --source linkedin_cdp
```

首次執行會用 `config/scraping.yaml`（範本 `config/scraping.yaml.example`）裡指定的獨立 Chrome profile 開一個瀏覽器視窗，你手動登入一次即可，之後 cookie 留在該 profile，不需再登入。

```bash
# 全量掃描（自動觸發後處理：分類→薪資→評分→履歷 tailor）
python3 scrape.py --preset all --source all

# Gap 批次分析（推薦度報告用，dashboard 只讀 batch 結果）
python3 -m analyzer.gap_analyzer --top 30 --min-score 55
```

## 客製評分邏輯

換職種（PM → 工程師 / 設計師 / 資料科學家等）不需要改程式碼，改 `config/scoring.yaml`（範本 `config/scoring.yaml.example`）即可：權重、市場關鍵字、目標職銜、目標薪資帶、企業偏好清單都是可設定項。不建此檔則用內建的「2026 日本 AI PM 市場」預設值。

## 目錄結構

```
scrapers/     插件式爬蟲 provider（indeed/linkedin/bizreach/wantedly/recruiter_agent）
analyzer/     評分器 + gap 分析器
tools/        工具腳本（salary_parser / dedup / liveness / resume_tailor 等）
llm/          LLM 多 provider 抽象層
tracker/      SQLite schema + migrations
dashboard/    FastAPI 後端 + React 前端
resume/       職務経歴書/履歴書 渲染（Jinja2 + Playwright PDF）
interview/    面試準備 prompt 模板
config/       使用者可設定的 yaml（llm / scoring / scraping）
data/         個人資料（candidate_profile.yaml 等，git 不追蹤，見 .example 範本）
```

## FAQ

**Q: 一定要用 Claude 嗎？**
不用。`config/llm.yaml` 的 `chain` 可換成 `openai` / `gemini`，或本機 CLI（吃訂閱額度免 API key）。

**Q: 資料會傳到哪裡？**
爬蟲結果存在你本機的 `data/jobs.sqlite`。送往 LLM 的內容經過 `tools/deid.py` 白名單去識別化（姓名/聯絡方式/生年/現年收一律移除，只送職業相關欄位）。

**Q: 多人可以共用一個部署嗎？**
不行，這不是多租戶 SaaS。每人各自 clone、各自跑一份、各自的資料庫，彼此獨立。如需公開部署給陌生訪客看，設定 `.env` 的 `DASHBOARD_PASSWORD` 啟用單一使用者登入保護。

**Q: LinkedIn/BizReach 爬蟲會不會被封號？**
用你自己已登入的 Chrome + CDP 連線，行為等同你手動瀏覽，但仍建議控制掃描頻率，遵守各站 ToS。

**Q: `apply_strategy.py`（投遞波次規劃）跟 Dashboard 的關係？**
獨立 CLI 工具，非 Dashboard 必要功能，是否使用自行決定。

## License

MIT — 見 [LICENSE](LICENSE)。
