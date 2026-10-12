# Data Contract

本文件定義哪些檔案屬於 **user layer**（個人數據，不可自動覆蓋），哪些屬於 **system layer**（系統邏輯，可安全更新）。

## User Layer（個人數據，不可自動覆蓋）

| 路徑 | 用途 |
|------|------|
| `data/jobs.sqlite` | 職缺資料庫（核心數據） |
| `data/*.yaml` | 個人履歷數據（data.yaml 等） |
| `data/frameworks.yaml` | 本人の方法論庫。**自動生成だが User Layer** — `proposal.frameworks_build --rebuild` を明示したときだけ作り直す（手修正は失われる）。パイプラインからは読むだけ |
| `resume/` | 履歷模板與生成物 |
| `resume-lm/` | 履歷語言模型相關 |
| `output/` | 生成的客製履歷、報告 |
| `interview/companies/` | 公司調研筆記 |
| `interview/retros/` | 面試覆盤紀錄 |
| `interview/checklists/` | 個人面試清單 |
| `interview/question-bank/` | 個人面試題庫＋全部手寫問答稿（共通底稿 / 実戦頻出 / 道場題卡 / 自己PR / エージェント面談） |
| `interview/materials/` | 面試事實素材（miko-ws からコピー、取引先名を含む） |
| `auth/` | Cookie、登入狀態 |
| `shokumu-keirekisho*.html` | 職務経歴書 |
| `參考資料/` | 個人參考文件 |

## System Layer（系統邏輯，可安全更新）

| 路徑 | 用途 |
|------|------|
| `scrapers/*.py` | 爬蟲邏輯 |
| `scrapers/provider.py` | Provider 插件架構 |
| `analyzer/*.py` | JD 評分、角色篩選 |
| `tools/*.py` | 工具腳本（liveness / followup / doctor） |
| `tracker/*.py` | DB 操作與 CLI |
| `tracker/migrations/` | DB migration 腳本 |
| `pipeline.py` | Pipeline orchestrator |
| `scrape.py` | 爬蟲入口 |
| `fetch_jd.py` | JD 補抓 |
| `prep.py` | 面試準備入口 |
| `dashboard/` | 前端 dashboard |
| `scripts/` | 輔助腳本 |
| `notify/` | 通知模組 |
| `portfolio/` | 作品集生成 |
| `interview/_llm.py` | LLM 呼叫邏輯 |
| `interview/mock.py` | 模擬面試 |
| `interview/company_brief.py` | 公司簡報生成 |
| `interview/retro.py` | 覆盤生成腳本 |
| `interview/slides_template.html` | 簡報模板 |

## 規則

1. **User layer 檔案永遠不會被系統更新覆蓋。** 任何修改都需要用戶明確同意。
2. **System layer 檔案可以隨版本更新安全替換。**
3. **新增個人數據時**，寫入 user layer 路徑（`data/`、`output/`、`interview/companies/` 等）。
4. **新增系統功能時**，寫入 system layer 路徑（`tools/`、`scrapers/`、`analyzer/` 等）。
5. **環境配置**（API key、cookie）放在 `auth/` 或 `.env`，永遠不進 git。
