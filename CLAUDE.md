# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

## 取引先ブランド名の遮蔽（守秘・PII とは別枠）

**現職の取引先・接続ブランド・協業キャリア名は応募書類に出さない。** 個人 PII
（姓名/聯絡/生年/住所）は `deid` の担当、こちらは NDA 相当の別問題。

- 清單：`config/redaction.yaml`（gitignored、範本 `.example`）。缺檔 = 完全 no-op
- 実装：`tools/redact.py` の `redact()`（一般語へ置換・禁止語を含む括弧列挙は丸ごと削除）
  と `scan()`（生成物の残存チェック）
- **遮蔽ポイントは 5 つ**（ここを通せば全 LLM 呼び出しが守られる）：
  `deid.build_deid_profile()` 出口 / `gap_facts.match_evidence()`（DB 内の既存 gap
  にもブランド名が残っている）/ `prep._submitted_docs_facts()` / 生成物の最終関門
  （`rirekisho_tailor.redact_fields()`・`prep.apply_mail` の出力後）/
  `assistant.chat.answer()` の LLM 出力後（AI 職涯助手、Web・Telegram 共通）
- prompt にも明示ルールを入れる（LLM に一般名詞で書かせる → 置換痕が減る）
- 診断：`python3 -m tools.redact`（profile の残存語一覧）
- 既存生成物の一掃：`python3 -m tools.oneoff.redact_existing_packs [--apply --pdf]`
- **`data/candidate_profile.yaml` / `resume/jp/data.yaml` の原文は書き換えない**
  （Data Contract）。遮蔽は送出経路のみで行う

## What This Is

日本求職自動化管線：多站爬蟲 → 評分 → gap 分析 → 面試準備 → dashboard。求職者畫像（定位/目標年收/技術棧）全部來自 `data/candidate_profile.yaml` 等使用者資料檔（見「Key Data Files」），非寫死於程式碼——換人使用只需替換這些資料檔。

## LLM Provider 層（2026-07 開源改造）

`interview/_llm.py` 已改為 adapter，實際邏輯在 `llm/`。設定檔 `config/llm.yaml` 定義 provider
`chain`（依序嘗試，第一個成功者勝出；缺檔時自動偵測 `.env` 有哪家 API key）。呼叫端不受影響，
仍是 `from interview._llm import call`。Provider 清單、gotcha 詳見 `llm/CLAUDE.md`。

## Pipeline

```
scrape.py (多站爬蟲 + Gmail 郵件來源: recruiter_agent / jac_recruitment)
  → jd_tier_classifier (企業分類: ai_startup/mega_venture/traditional_sier)
  → jd_enrich (job_type 職位類型 pdm/pjm/consulting/other + employee_count 從業員數 + mentions_ai，純規則全表冪等回填)
  → salary_parser (raw_jd 正則抽取 → 萬円)
  → jd_scorer (6 維加權: salary_fit/role_fit/market_keywords/tech_overlap/tier/domain) — Jobs 頁列表評分
  → resume_tailor (高分職缺客製履歷)
  → gap_analyzer (LLM Haiku 差距分析 → 8 維推薦度: salary/role_fit/company_product_stage/requirements/domain/evidence/work_conditions/culture_risk)
  → gap_summary (LLM Sonnet 批次歸納 → gap_batches 表) — Recommend/Reports 推薦列表專用
```

評分和客製履歷是純規則（zero LLM token）。只有 gap 分析和面試準備呼叫 LLM。

## Key Commands

最常用的一組，每個管線階段各留一個範例；郵件/CDP 直爬變體、Gap 篩選變體、投遞包語言變體、
官網直投、成長手冊等完整清單見 `docs/COMMANDS.md`。

```bash
python3 scrape.py --preset all --source all            # 全量掃描（自動觸發後處理）
python3 -m analyzer.gap_analyzer --top 30 --min-score 55   # Gap 批次分析（必須 --top/--backfill 才建 gap_batch）
python3 prep.py 123 apply                              # 投遞包，自動偵測語言 + 全 stage
python3 prep.py 123 interview                          # 面試包，全 5 stage（qa/checklist/slides/script/voice）
cd dashboard && bash run.sh                            # Dashboard：build + uvicorn :8000
bash scripts/inbox_reply_scan.sh                       # Inbox 全流程一輪
```

## 想定問答の音声化（tts/，2026-08 GPT 音声へ升級）

面試包 voice stage 的聲音來源有兩個，`config/tts.yaml` 的 `voice_engine` 決定（預設 `gpt`）：

- **`gpt`** — ChatGPT（本人アカウント・CDP 既ログイン Chrome）へ 1 問ずつ骨子を渡し、
  話し言葉へ書き直させ、「大聲朗讀」の音声を捕捉。**画面録音ではなく
  `MediaSource.appendBuffer` を hook して AAC の生バイトを直接受け取る**ので、
  ループバック音声デバイス（BlackHole 等）も録画アプリも不要。**既定は 20 問を
  1 メッセージ＝1 音檔にまとめる** — 音声取得は再生速度に縛られない（実測 0.16 倍）ため、
  まとめた分だけ往復の固定コストが消える（77 問 = 4 バッチ・15 分前後）。
- **`theater`** — edge_tts 逐句合成（面接シアターと同じ音檔庫）。数秒・zero-token。
  gpt が失敗したときの自動受け皿でもある（音檔ゼロで面接前日を迎えないため）。

**録音は miko-ws 指揮中心へ委譲**（`gpt_voice.backend: auto`、既定）。指揮中心の
`POST /api/llm/voice` は **ChatGPT アカウントを 4 つ順に試す**ので、1 アカウントが
時間切れ・未ログインでも残りのバッチを録り切れる（単一アカウント運用だと「途中の
バッチだけ時間切れ」が実際に起きる）。指揮中心が落ちていれば自動で本機 Chrome の
経路（`cdp_port` / `user_data_dir`）へ落ちる。`backend: local` で従来固定も可。
どのアカウントが読んだかは manifest の `account` / `engine` に残る。

**失敗はファイルに残す。** 1 バッチ = 20 問なので、瞬断で 20 問まとめて失う方が高くつく
→ `retries`（既定 1）で録り直し、指揮中心が `miko_failover_after`（既定 2）回連続で
失敗したら残りは本機 Chrome へ切り替える（`backend: miko` を明示した場合は落とさない）。
それでも録れなかったバッチは manifest の `failures[]`（理由・実行主体・段階・試行回数）と
`06_voice_audit.md` 冒頭の表に残る — 成功しか書かないと、空の manifest を見ても
原因が分からない。指揮中心が HTTP 500 を返したときは body の `error` まで拾う
（`raise_for_status()` だけだと「500 Server Error」しか残らない）。

⚠️ local 経路の Chrome は `~/.chrome-chatgpt2` を占有する＝指揮中心の chatgpt2 が
その間使えない。録り終わったらその Chrome は閉じてよい。

**一時チャットでは読み上げが鳴らない**（実測）ので `temporary_chat: false` 固定に近い。
その代わり**外部送信前に `pii_gate` ＋ `redact` の二段閘門を必ず通す — 指揮中心経由でも
遮蔽は呼び出し側（ここ）の責任**。

**錨定閘門:** ChatGPT はアカウントの記憶から情報を足してくる。足された語が
**骨子にも `data/candidate_profile.yaml` / `resume/jp/data.yaml` にも無い**場合だけ
「裏が取れない」として 1 回書き直させ、残れば `06_voice_audit.md` に ⚠ で列挙する
（記憶由来でも本人の実際の経歴なら弾かない）。指令は `docs/COMMANDS.md`。

## 成長實戰手冊（growth/，2026-08 新增）

從 JD 生成「這份工作實際上要怎麼做」的 7 段式操作手冊，跟主線 8 階段（找到並投遞職缺）是不同
層次的問題，**獨立指令**（`python3 -m growth <job_id>`），不掛在 `prep.py` 上。4 種機械閘門把
關（結構/事實錨定/可執行/安全），前 5 段不放候選人資料。詳見 `growth/CLAUDE.md`。

## 提案型面接パック（proposal/，2026-08 新增）

**実際に取ってきた会社の原文**と JD から「この求人が求める人物像」を割り出し、その視点で
**対象企業のプロダクトへ具体提案**を書く。面接で「うちで何をしてくれるのか」に 5 分で
答えるための束。主線 8 階段とも `growth/` とも別問題なので**独立指令**
（`python3 -m proposal <job_id>`）、`prep.py` には掛けない。

**3 層 12 stage**（`--layer` で層ごとに実行可）＋ 9 種の機械閘門：

| 層 | stage | 出すもの |
|---|---|---|
| 研究 | company → product | ビジネスモデル / プロダクト構造・利用の流れ |
| 思考 | persona → hypotheses → main_case → plan90 | 人物像と評価モデル / 課題仮説と検証方法 / 主提案 / 90 日計画 |
| 面接 | cards → mapping → redteam → deck → playbook | 能力カード / JD 要件 × 本人の実例 / 紅隊 + 7 軸採点 / スライド / 能力ごとの**仕事の型**（想定シナリオ・進め方・注意点・思考ロジック・答えの論点。本人の経歴は入れない） |

- **会社は想像せず取りに行く**（`research.py`、0 LLM）。官網を巡回して `_research_raw.md`
  に原文を落とし、**Gate F** が `[事実]` タグの引用を原文へ機械照合する。日本企業のサイトは
  JS 描画が多いので headless Chromium で取り直す。採用サブドメインではなく本体を優先。
- **課題は断定せず「仮説＋根拠＋足りないデータ＋検証方法」で出す**（`hypotheses` stage）。
- **`--refine`** で紅隊の 7 軸採点が閾値未満なら、低い 2 軸だけを指示に翻訳して主提案を
  書き直し再採点（1 回だけ）。agent 同士は会話させない — stage → 機械閘門 → 是正の直列。
- 分析の骨格は `data/frameworks.yaml`（本人の実践から 3 モデル自動構築、
  `python3 -m proposal.frameworks_build`）の型だけ。PDCA/SWOT 等の一般論は prompt で禁止。
- LLM は **miko-ws 指揮中心のみ**（外部 fallback なし）。全 stage 一発通過で 13 call。
- **評分駆動の迭代**（`--iterate`）: 生成 → `versions/v{N}/` 快照 → Telegram 推送 →
  人間採点（`/pscore` か `--score`）→ **90/100 未満なら書き直して次版推送**、90 以上で
  確定。全版保留、採点履歴 `_scores.jsonl`。deck は 10 必須 role（課題再定義/打ち手/
  **打ち手の設計**/プロダクト構造/KGI ツリー/**指標の定義式**/JD 対応表/**数字で
  判断した実例**/なぜ私/ロードマップ）を機械照合（Deck C）。太字の 3 つが v3.4 で、
  「方法論を具体へ落とす」担当 — 何を作り、何を数え、それを過去にやったのか。

詳細は `proposal/CLAUDE.md`。

## 面接 Q&A の採用競争力審査（`/hire-audit`、2026-08 新增）

`interview/qa/gates.py`（零 LLM 機械閘門）→ `critic.py`（5 軸逐題批評、TARGET 80）の**後段**に
人が回す第 3 層。前 2 層は 1 問ずつ見るので、**パック横断でしか見えない欠陥**を構造的に拾えない：
開場の自曝連鎖（「〜経験はなく」が必須要件に連続）／証拠の出所偏り（個人開発 vs 顧客案件）／
JD 要件の重み付け（`requirement_coverage` は覆蓋の真偽しか見ない）／題材選定の戦略ミス／
職種アイデンティティのズレ／**面接官が後から明示してきた確認事項**（生成時には存在しない情報）。

- 実体は `.claude/skills/hire-audit/SKILL.md`（session 内で回す。CLI 化しない — 確認事項が
  毎回違い、外部調査を伴い、JD のどの条件が採用を決めるかの判断が要るため）
- 起動タイミングは**面接の招待・確認事項を受け取った後**。`prep.py` には掛けない
  （投遞包 vs 面試包を混ぜない方針と同じ）
- 出力は `output/prep/{id}_{slug}/hire_audit_{YYYYMMDD}.md`。**番号列 00〜06 には入れない**
  （`05_qa_audit.md` は再生成される生成時レポート。同じ帯に置くと区別できず陳腐化に気づけない）
- ⚠️ 手編集した `01_interview_qa.md` は `prep.py {id} interview` の再実行で消える。
  `04_qa_audio.mp3`・`theater/` も古いまま残るので、`interview.theater_script` と
  `tools.interview_voice` の再同期まで含めて一連の作業とする

## Gap 常駐掃描

`scripts/gap_backfill_scan.sh`（launchd `com.jp-pm-jobs.gap-backfill`，每 30 分鐘一輪）分批清空
所有未跑 gap 的高分職缺，`LLM_CONFIG=config/llm.gap.yaml` 強制只走 miko-ws 指揮中心無
fallback。鎖檔機制、批次大小、與 `daily_job_scan.sh` 的分工細節詳見 `analyzer/CLAUDE.md`。

## 官網直投常駐掃描（2026-07 新增）

`scripts/direct_apply_scan.sh`（launchd `com.jp-pm-jobs.direct-apply`，30 分鐘一輪）對
`recommend_score ≥ 80` 的日本職缺自動生成投遞包：liveness 複查 → 探測應募窗口
（`tools/company_contact.py`）→ 公司 brief ＋ 応募メール ＋ 履歴書志望動機特化 → 高信心 email
自動建 Gmail 草稿（**永不自動寄出**）→ Telegram 推送確認。miko-ws 掛掉時自動 `launchctl
kickstart -k` 自癒。投遞附件固定為 2 份 PDF（職務経歴書原樣附上、履歴書只改志望動機 3 行，
`tools/resume_assets.py` 為路徑單一來源）。選件條件、附件方針細節、安全閘、寄出偵測、
応募経路推定、完整指令詳見 `docs/DIRECT_APPLY.md`（指令參考亦見 `docs/COMMANDS.md`）。

## Inbox 自動掃描（Gmail → 分類 → 日程 → 草稿）

`scripts/inbox_reply_scan.sh`（launchd `com.jp-pm-jobs.inbox-scan`，30 分鐘一輪）：規則分類
（`application_ack` 優先權最高，其次 `schedule_confirmed`）→ 応募受付/日程確定自動入庫（零 LLM
正則）→ 生成草稿 → Telegram 通知 → **面接パック自動生成**（`inbox/prep_trigger.py`：日程登記済み
でパック未生成の面接を 1 本 `prep.py {id} interview`。既存パックは手編集保護のため再生成せず通知
のみ）。分類優先權順序、正則抽取細節、草稿自癒機制、launchd TCC 陷阱詳見 `inbox/CLAUDE.md`。

## LinkedIn Messaging 自動回覆草稿（linkedin_inbox/，2026-08 新增）

`scripts/linkedin_inbox_scan.sh`（launchd `com.jp-pm-jobs.linkedin-inbox`，30 分鐘一輪）：
CDP 連 LinkedIn Messaging（沿用 `linkedin_jp` 職缺爬蟲的 CDP profile，port 9253）→ 抓未讀
對話 → 規則分類（只留招聘相關）→ LLM 生成回覆草稿 → Telegram 通知。**永不自動發送** —
LinkedIn 無官方草稿 API，草稿只存 DB 與 `output/linkedin_drafts/*.txt`，人工複製貼上到
LinkedIn 網頁後手動送出。DOM 選擇器未經實機驗證，首次部署務必先 `--dry-run` 人工核對，
細節見 `linkedin_inbox/CLAUDE.md`。

## Telegram 通知與互動 bot

`.env` 設 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`（**token 永不硬編碼**）。互動 daemon
`python3 -m notify.bot` 需**專案專用 bot**——與 Claude Code telegram plugin 共用同一 token 會
409 Conflict（一個 token 只允許一個 `getUpdates` 消費者）。推送點、callback 指令、Web UI 對應
詳見 `notify/CLAUDE.md`。

## AI 職涯助手（assistant/，2026-08 新增）

對話式問答，事實只能來自 `assistant/context.py` 白名單檢索（漏斗/跟進/面試/Gap 分析），零編
造紀律比照 gap 分析。Dashboard 頁面（nav key `assistant`）與**專用 Telegram bot**（`.env` 設
`ASSISTANT_TELEGRAM_BOT_TOKEN` / `ASSISTANT_TELEGRAM_CHAT_ID`，與上面的通知 bot 分開）都能問答，
每輪落地 `data/practice.sqlite` 供每日/每週總結（`scripts/assistant_daily_digest.sh` /
`assistant_weekly_digest.sh`）推播。細節見 `assistant/CLAUDE.md`。

## 投遞包語言自動偵測

`prep.py {id} apply` 預設 `--lang auto`：JD 的 CJK 字元比例 < 20% → 英文包；JD 為空時 fallback
看 URL（`greenhouse.io`/`lever.co`/`ashbyhq.com`/`workable.com` → 英文）；其餘 → 日文包。
`--lang en/jp` 可強制覆蓋。輸出目錄與 stage 清單見 `docs/COMMANDS.md`。

## Gap Analysis — Critical Distinction

**推薦度報告頁讀的是 `gap_batches` 表，不是 `jobs.gap_analysis`。** `--job-id` 只寫
`jobs.gap_analysis`，**不建 batch**；只有 `--top N` / `--backfill` 才會建 `gap_batch` 記錄。
完整流程、prompt 瘦身細節詳見 `analyzer/CLAUDE.md`。

Dashboard 的「gap 分析」按鈕（`POST /api/gap/{id}`）在 `raw_jd` 空時會先自動補抓一次
（`tools.refetch_jd.fetch_one`：greenhouse/lever 走 API、其餘走既開著的 CDP Chrome，
**不自動啟動 Chrome**），抓不到才回 400 並說明理由（要登入 / 已下架 / 不支援）。
**ログイン壁の本文は JD として保存しない** — 「会員登録が必要です」頁面有 1000+ 字會通過
長度門檻，存進去 gap 分析就用半頁資訊在判斷。

## Database

路徑: `data/jobs.sqlite`。

| 表 | 用途 |
|---|---|
| `jobs` | 職缺（含 score, gap_analysis JSON, tier, salary_min/max） |
| `applications` | 應募追蹤（status 漏斗: applied→recruiter→tech→onsite→offer/rejected） |
| `gap_batches` | 推薦度報告（summary_json 供 dashboard 渲染） |

Dashboard 用唯讀連線 (`?mode=ro`)。寫入時可能遇到 DB locked（殘留 sqlite3 進程），`salary_parser` 已有重試機制。

## Config-Driven 設定（開源改造新增）

`tools/app_config.py` 提供 `load(name)` / `get(name, key, default)`，讀 `config/{name}.yaml`（缺檔 = 用程式碼內建預設值，行為零變化）：

| 檔案 | 覆蓋內容 | 範本 |
|---|---|---|
| `config/scoring.yaml` | `analyzer/jd_scorer.py` 全部權重/關鍵字/企業清單、`analyzer/gap_analyzer.py` 的 `_TIER1/_TIER2/_HARD_STOP/_SIER/_LARGE_CO` | `config/scoring.yaml.example` |
| `config/app.yaml` | internal 閱讀語言（company brief / gap 分析 / README / QA 檢查） | `config/app.yaml.example` |
| `config/scraping.yaml` | CDP port/profile、Chrome 路徑、recruiter_agent sender_query | `config/scraping.yaml.example` |
| `config/llm.yaml` | LLM provider chain | `config/llm.yaml.example` |
| `config/resume.yaml` | 投遞用職務経歴書/履歴書 PDF・基底 HTML 路徑（`tools/resume_assets.py`） | `config/resume.yaml.example` |
| `config/redaction.yaml` | 取引先ブランド名の禁止語と一般語（`tools/redact.py`） | `config/redaction.yaml.example` |
| `config/prep.yaml` | 面接パック自動生成の ON/OFF・stage・本数・再試行（`inbox/prep_trigger.py`） | `config/prep.yaml.example` |

換職種/換求職偏好只需改 `config/scoring.yaml`，不需碰 `analyzer/` 原始碼。

## 開源部署

- **一鍵安裝：** `bash setup.sh`（檢查依賴 → 複製 `.env`/`config/*.yaml`/`data/*.yaml` 範本 → 裝套件 → `tracker.db.init_db()` 建 schema）
- **Docker：** `docker compose up -d`，只跑 Dashboard（不含爬蟲，CDP 需互動登入不適合容器化，爬蟲固定在 host 跑，`data/`/`output`/`config` volume 掛載共用）
- **單人登入保護：** `.env` 設 `DASHBOARD_PASSWORD` 啟用 HTTP Basic Auth（留空 = 不驗證）；`DASHBOARD_CORS_ORIGINS`（逗號分隔）、`DASHBOARD_PORT` 環境變數可覆蓋預設
- **公開匯出（私 repo → 乾淨公開 repo，不改寫私 repo 歷史）：** `bash scripts/export_public.sh [目標目錄]` — 依 `.publicignore` 清單剔除個人資料/個人工具/portfolio/docs wiki，並掃描殘留個資後才 `git init` 首次 commit。私 repo 本身的 `data/candidate_profile.yaml` 等個人檔案原地保留、不受影響。

## Key Data Files

`data/candidate_profile.yaml`（gap 分析唯一真實來源）、`data/cognitive_profile.yaml`、
`data/tech_footprint.yaml`、`resume/jp/data.yaml` **禁止自動改寫**；`interview/companies/*.md`
只增不刪。每個檔案都有對應 `*.example` 範本供 `setup.sh` 初始化。完整 User/System Layer 分類見
`DATA_CONTRACT.md`。

## 文件夾架構與新檔案放置規則

新建檔案一律按下表放置，禁止散落根目錄：

| 要新增的東西 | 位置 | 備註 |
|---|---|---|
| 管線入口 CLI | 根目錄**僅限現有 5 個**（scrape / prep / pipeline / fetch_jd / apply_strategy） | 新功能掛進現有入口或以 `-m` 模組執行，不再新增根目錄腳本 |
| 爬蟲 provider | `scrapers/{site}.py` | 實作 `scrape()` + `PROVIDER_META` 自動註冊 |
| 評分 / 分析邏輯 | `analyzer/` | |
| LLM provider | `llm/providers/` | 繼承 `base.py` |
| 共用可 import 模組、常用 CLI | `tools/` | 例：`deid`、`app_config`、`salary_parser` |
| 獨立功能模組（自帶 CLI + daemon，非爬蟲/分析/純工具） | 新頂層套件（如 `assistant/`/`linkedin_inbox/`、比照既有 `notify/`/`inbox/`/`growth/`） | 各自帶 `CLAUDE.md` |
| 一次性 / 歷史資料修補腳本 | `tools/oneoff/` | 跑完即歸檔；`python3 -m tools.oneoff.<name>` 執行 |
| 操作性 shell / launchd | `scripts/` | |
| 後端 API 端點 | `dashboard/backend/` **新開 router 檔**（照 `applications.py` 模式） | 不要再往 `main.py` 加端點 |
| 前端頁面 / 共用元件 | `dashboard/frontend/src/pages/` / `src/components/` | |
| 開發文檔 | `docs/*.md` | wiki HTML 在 `docs/{產品,技術,流程,運營}/` |
| 面試手寫 source（問答稿・題庫） | `interview/question-bank/core/` | **正典 49 問，同一題的答案只在這裡**。`interview/` 根層只放程式碼。導覽見該目錄 `README.md`；舊 4 檔（`common_qa*.md` / `asked-frequently-answers*.md`）降為素材，`dojo_base_*.md` 仍是 dashboard 讀取來源 |
| 面試生成物（生成 QA / 深掘） | `interview/generated/`（gitignored） | 其他管線生成物一律 `output/` |
| 臨時 / 實驗檔 | 系統暫存目錄 | 禁止進 repo（禁 `tmp-*` 檔落地根目錄） |
| 參考 repo / 資料 | `参考資料/`（gitignored） | `jobfunnel/`、`resume-lm/` 為既有參考 gitlink，不再新增根目錄參考 repo |

新增**含個人資料**的檔案時，同步檢查 `.gitignore` 與 `.publicignore`（公開匯出排除清單）。

## Gotchas

- **背景進程 PATH 丟失:** `llm/providers/cli.py` 在 `run_in_background` 下 PATH 可能丟失（已有 fallback）。全 provider 失敗不要反覆重試，直接用 Claude Code 自己完成 LLM 歸納任務。詳見 `llm/CLAUDE.md`。
- **零編造紀律:** gap 分析、面試準備、履歷 — 候選人沒有的數字/經驗禁止編造。
- **Data Contract:** `data/`, `output/`, `interview/companies/` 是 User Layer；`scrapers/`, `analyzer/`, `tools/` 是 System Layer。完整清單見 `DATA_CONTRACT.md`。
- **PII 去識別化:** 任何送往外部 LLM 的 prompt 必須用 `tools.deid.build_deid_profile()` 去識別化，詳見頂部「PII 去識別化規則」。
