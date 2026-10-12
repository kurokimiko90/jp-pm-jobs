---
name: shokumu-review
description: 三角度並行審核本項目的職務経歴書/履歴書（招聘方/敘事/定位）→ 整合共識 → 直接改 data.yaml 並重新渲染。零編造。
---

# /shokumu-review — 三角度履歷審核（jp-pm-jobs 專案版）

把職務経歴書丟給三個並行 agent 從不同角度審核，整合共識後直接優化。
通用版見 `~/.Codex/skills/resume-review/`；本版本含專案特化規則。

## 本項目鐵則

1. **唯一資料源是 `resume/jp/data.yaml`** — 絕不直接改 `resume/jp/output/*.html`（會被覆蓋）。
2. 改完渲染：`python3 -m resume.jp.render --shokumu`（職務経歴書）或不帶參數（兩份全出）。
3. **零編造**：數字只能來自 `~/Documents/PM_Knowledge/docs/daily/` recap、data.yaml 既有內容、或用戶親口提供。沒有就留 `# TODO（要確認）` 註解。
4. 目標職缺從 `data/jobs.sqlite` 查：`SELECT title, company, score FROM jobs WHERE score IS NOT NULL ORDER BY score DESC LIMIT 30;` — 審核 prompt 必須帶上當前 top 職缺主題。
5. 頁數公式：1（封面）+ 經歷數 + 個人項目數 + 1（自己PR）。砍頁 = 砍 experiences/projects 條目。

## 流程

### 1. 讀檔
Read `resume/jp/output/shokumu-keirekisho.html`（記行號）+ `resume/jp/data.yaml`。

### 2. 三角度並行審核（一次發三個 Agent，subagent_type 精確如下）

| subagent_type | 角度 | 重點 |
|---|---|---|
| `Recruitment Specialist` | 招聘方/HR/獵頭 | 30 秒篩選哪裡扣分、假 KPI/狀態詞、日本格式慣例、空白期、篇幅比例、placeholder |
| `Study Abroad Advisor` | 敘事包裝 | story arc 連貫性（Fintech→AI 轉軸）、自己PR 重複/該砍、開頭抓人、數字是證據還是噪音 |
| `LinkedIn Content Creator` | 個人定位/差異化 | 頭銜+one-liner 夠不夠 sharp、賣點貫穿、與同類候選人的差異化武器、日系 vs 外資適配 |

每個 prompt 必含：HTML 路徑、候選人背景（Fintech PdM 10 年+ → AI Agent PM 轉軸）、當前 top 職缺、「只給意見不改檔案、繁中、行號、P0/P1/P2 排序、簡短、別重複別人的面向」。

### 3. 整合共識
多 agent 重合的 = 最優先。按 P0/P1/P2 分層。

### 4. 分兩類執行
- **A. 直接改 data.yaml**（不問）：定位句統一、PR 砍併、假 KPI 刪除、emoji、格式慣例、篇幅。
- **B. 用 AskUserQuestion 集中問**（只問事實）：空白期理由、真實數字、連結/repo 公開狀態。

### 5. 渲染 + 回報
render → grep 驗證關鍵詞已注入/已清除 → 回報「已改清單 + 留的 TODO」。不貼整份代碼。

## 已定案的審核基準（2026-06-11 三角度共識，下次直接沿用）

- **數字取捨原則**：留「決策含金量」（85% 品質ゲート、12→1/16→4 呼び出し削減、182 pack、104 本一括更新），砍「努力計量」（prompt 數、session 數、工時、活動率、自我測量百分比 49%/77%/5.3%）。
- TOEIC 495 全文只准出現在資格欄 1 次；英語 chip 寫「英語（技術文書読解）」。
- 不寫「正直な弱点」caveat；不寫「公開予定」（公開了才寫）。
- 禁 emoji（🟢 等）；中國公司寫「社名（中国・深セン）」不寫株式会社。
- 空白期已定案交代：2017-2018 = フリーランス受託；2019-2023 = 来日＋情報セキュリティ大学院（修了 2023-03）。
- 定位主軸：「LLM の不確実性を工程で抑え込む」＝ one-liner 層級；頭銜單一釘子（AI Agent / LLM Orchestration），Fintech 降為信任背書。
- 個人項目框法：toy domain（間違い探し等）只是低風險載體，賣的是 eval gate + 失敗率路由的 harness 架構 — 先說 harness 再說 domain。
- 個人項目上限 3 個（miko-ws / btrain / jp-pm-jobs），各佔不重複的能力象限；自己PR 上限 4-5 條。
- jp-pm-jobs 對外框定為「ドキュメント自動生成＋ATS スコアリング基盤」，爬蟲降權重。
- 日系 vs 外資分叉：本 data.yaml 為日系版；外資（Anthropic/Sierra/Cohere/Glean）需另做 1-2 頁英文 resume，賣點順序反轉（未做，TODO）。

## 待辦事實（用戶尚未提供）
- GitHub / LinkedIn handle（contact.github / contact.linkedin / contact.portfolio 仍是 `{{placeholder}}`）
- application_meta 的 `{{要確認}}`：性別、通勤時間、転職理由、趣味
- 現職定量數字：確認無可公開數字 → 維持狀態詞，面接口頭補量級
