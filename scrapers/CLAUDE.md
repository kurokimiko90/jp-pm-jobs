# scrapers/ — Scraper Architecture

`scrapers/` 下是插件式 provider（實作 `scrape()` + `PROVIDER_META`，自動註冊）：
- `indeed_jp` — 公開，不需 cookie
- `green` — 公開，不需 cookie；JD 全文直接解析 SSR 頁面內嵌的 `__NEXT_DATA__` JSON（比 DOM 選擇器穩定，不需 CDP）
- `linkedin_jp` — 需 cookie 或 CDP (port 9253)
- `bizreach` — 需 cookie 或 CDP (port 9270)
- `wantedly` — 需 cookie
- `recruiter_agent` — Gmail API 讀取仲介推薦郵件，需 OAuth（`python3 -m inbox.auth`）+ `config/scraping.yaml` 設 `recruiter_agent.sender_query`（未設定則自動 skip）
- `ragent_search` — r-agent マイページ「求人検索」を CDP で直接爬取（詳細後述）
- `ragent_interests` — r-agent マイページ「気になる」登録求人一覧を CDP で直接爬取（詳細後述）
- `jac_recruitment` — Gmail API 讀 JAC「求人情報のご案内」（1 通 1 求人）。本文 `【社名】/【職種】/【年収（想定額）】…` 標籤解析 + 添付「求人票.pdf」取応募条件（pdfplumber，缺套件則只用本文）。source_id = 求人No.（NJB…）。非公開求人無公開 URL → `mypage/?jobNo=` 佔位。單獨跑：`python3 -m scrapers.jac_recruitment --dry-run`

**郵件來源（`recruiter_agent` / `jac_recruitment`）不實作 `scrape()`**，不進 provider registry；由 `scrape.py` 的 `EMAIL_SOURCES` 表 + `run_email_source()` 直接呼叫 `fetch_from_gmail()`。新增郵件來源＝加一個 `fetch_from_gmail()` 模組 + 在 `EMAIL_SOURCES` 註冊一行。

去重（郵件來源共通）：run 內同一求人號只留 1 筆 → DB `UNIQUE(source, source_id)` 讓再送郵件只更新 `last_seen` → `upsert_job` 的 `company_norm` 合併同公司（跨來源）。故重複跑完全冪等。`jac_recruitment` 另外對「既有 JD 已 ≥1500 字」的求人跳過附件下載並回傳 `raw_jd=None`，避免用較短的本文覆蓋既有完整 JD。

**`ragent_search`（r-agent 求人検索、CDP）**：`recruiter_agent` と同じ `/joboffers/{id}`
空間なので **DB 書き込み source は `recruiter_agent` に統一**（UNIQUE(source, source_id)
でメール推薦と自動マージ、`keyword` に `search:{条件名}` を入れて由来を区別）。
`scrape()` は実装せず registry に載らない — `scrape.py` の `run_source` が
`search_all(page, seen_ids)` を直接呼ぶ（`recruiter_agent_cdp` と同じ特判方式）。

実測した r-agent 側の仕様（2026-07、変わったらここを直す）：
- 検索条件＝保存済み条件の URL そのもの。`?page=` 等のページ送りは**効かない**
  （無視されて 1 ページ目）。初期 100 件 →「さらに表示する」1 クリック +100 件が
  同一 DOM に追記。`max_load_clicks` で制御
- **裸 URL（`/joboffers/{id}` のみ）は必ず「エラーが発生しました」**（DB の url は裸で保存）。
  ただし必要なのは求人ごとのトークンではなく**流入元タグ**で、
  `?job_referral=jobSearch` を付ければ全求人が開く（2026-08 実測 16/16、メール由来か
  求人検索由来かを問わない）。単一の出所は `recruiter_agent.joboffer_url()`、値は
  `config/scraping.yaml` の `recruiter_agent.joboffer_referral` で差し替え可
  - 以前は推薦メールの URL（`job_referral=recommendPost` / `aiScout` / `i2aJob*`）を
    Gmail から毎回引いていた。これは (a) 1 回 70 秒超 (b) メール由来でない求人（約 6 割）
    が開けない (c) スカウト系タグは `/onboarding/scout_acceptance` に飛ばされることがある、
    で破綻していた。**Gmail を読むのは「未登録オファーの列挙」だけ**
    （`tools/open_ragent_jd.py --new-offers`）
- カード class は CSS module ハッシュ（`JobCard_jobOfferWrapper__xxxxx`）→ 前方一致
  ＋ `a[href*="/joboffers/"]` で兜底。パースはカードの innerText 行ベース
- 詳細ページはタブを切り替えても innerText がページ全体を返す → 「選考・企業概要」は
  **差分行だけ**追記（`_clean_detail` でナビ行も除去）
- コスト最小化：**一覧段階で** 既知 id / 工程職 / 年収下限を落としてから詳細を開く。
  1 回の実行は `detail_limit`（既定 60 件）まで。数回走らせれば一覧分は収束
- **取得済み id は `output/ragent_search_seen.json` にも記録する**（append-only）。
  DB 由来の seen だけでは不足：`upsert_job` の「同公司併入」分支（1 社 1 レコード方針）
  は既存 row を更新するだけで新しい source_id を残さないため、その求人を毎回
  「未取得」と誤認して開き直してしまう。このファイルを消すと再取得が走る
- 一覧に出るのは `max_load_clicks` で決まる件数（既定 2 = 300 件）だけ。条件の
  総件数（例 2,143 件）を掘るならクリック数を上げる（DOM 蓄積のためメモリ増に注意）

設定は `config/scraping.yaml` の `ragent_search`（`search_urls` 未設定なら自動 skip）。

**raw_jd の構造化（2026-08）**：r-agent 詳細ページは固定テンプレートの dt/dd（ラベル行→値行）
が innerText でフラットに並ぶため、そのまま保存すると「職務内容/勤務条件/勤務地/選考・企業概要」
が全部 1 本の壁になって読めない。`recruiter_agent.py` の `structure_jd_lines()` が既知ラベル
（`_JD_SECTION_LABELS` = 大区分／`_JD_FIELD_LABELS` = 個別項目）を境界に `## 見出し` /
`**項目**` を差し込んで整形する（意味解析ではなく行境界の機械的な分割、情報の欠落なし）。
同時に `NAV_LINES`（ホーム/求人検索/応募する/募集要項/受付終了 等）を落とす。

- **`NAV_LINES` の定義は `recruiter_agent.py` が唯一の出所**。`ragent_search._NAV_LINES` は
  それを import して「選考・企業概要」（タブ見出し）だけ足す — 区分マーカーは
  `_fetch_detail` が `【選考・企業概要】` として別途入れるため両者を混同しないこと
- **冪等**（`_delabel()` が既存マーカーを外してから判定）なので、ラベル定義を足した後に
  同じ行へ何度通しても安全。既存データの一括再整形は
  `python3 -m tools.oneoff.restructure_ragent_jd --apply`（dry-run が既定、差分が出る行だけ更新）
- 一覧カード由来の要約（先頭の年収/勤務地/休日）は `職務内容` より前にあるため、同名でも
  `##` に昇格させない（`_JD_BODY_START`）。これが無いと `## 勤務地` が 2 回出て目次が壊れる
- 描画側は `dashboard/frontend/src/components/JdViewer.tsx`（目次チップ + 折りたたみ）。
  **Markdown ではなく `pre-wrap` で描画する** — 本文は「【必須】」「・」「■」で改行が意味を
  持つのに、Markdown だと単一改行が潰れて 1 段落に繋がってしまうため

**Indeed 側の raw_jd 構造化（2026-08）**：`indeed_jp.py` の `structure_indeed_jd()`。
出力する記法（`## 大区分` / `**項目**`）は r-agent と同じで `JdViewer` を共用するが、
**ラベル定義は共有しない** — Indeed は集約サイトで掲載元ごとに語彙が違い、r-agent 転載分
（全体の約 4 割、「リクルートエージェント」を含む求人）も Web 版とは別テンプレートのため。
昇格の判定は 4 段（上から優先）：

1. 既にマーカーが付いている行 → **その階層のまま素通し**（`_delabel()` が階層も返す）。
   これが冪等性の要。素の文字列だけで再判定すると `■課題解決` は昇格後にパターンから
   外れ、逆に `【仕事内容】`（掲載元の小見出し）が `## 仕事内容`（大区分）に化ける
2. `【…】` / `■…` の独立行 → `**項目**`。ただし句読点と `：` を含む行は除く
   （`■開発業務：API設計` は箇条書きの本文であって見出しではない）
3. `_SECTION_LABELS` / `_FIELD_LABELS` の既知ラベル → `## ` / `**…**`
4. 1〜3 が 1 つも当たらなかった文書だけ、体裁のみの兜底（`_plain_headings()`：空行の
   直後に来る 40 字以内・句点で終わらない行）。英文の自由記述求人（実測 663 件中 8 件）
   を救うためで、**記号やラベルがある文書には持ち込まない**（誤検出が本文を壊すため）

`_NOISE_LINES` / `_NOISE_RE` で検索窓（キーワード/勤務地/求人検索）・ボタン・「30+日前」・
`slide2 of 3` 等を落とす。先頭の検索窓ブロックは「先頭 6 行以内の `求人検索`」までを
スライスして捨てる（残すと本文の `勤務地` より前に同名項目が立って目次が二重になる）。
既存データの一括再整形は `python3 -m tools.oneoff.restructure_indeed_jd --apply`
（dry-run が既定）。回帰テストは `tests/test_indeed_jd_structure.py`。

なお `JdViewer` は「見出しの無い先頭ブロック」をカード要約とみなして小さい灰字で畳むが、
**大区分が 1 つも無い JD では畳まない**（英文求人が全部メタ表示に潰れるため）。

**`ragent_interests`（r-agent「気になる」一覧、CDP）**：自分でチェックした求人だけの
一覧（`/interests`）。カード DOM / href 仕様は `ragent_search` と完全に同一（同じ
JobCard コンポーネント）なので、`_collect_cards` / `_parse_card` / `_fetch_detail` /
`_load_more` をそのまま import して再利用（`ragent_search.py` 内の private helper）。
書き込み source も同じく `recruiter_agent` に統一、`keyword='mypage_interest'` で由来を
区別。実測（2026-08）では「さらに表示する」ボタンは出ない＝27件が1ページに全表示（気に
なる登録数がそもそも少ないため）。取得済み id は `output/ragent_interests_seen.json`
に記録（`ragent_search` と同じ「同公司併入で seen が失われる」問題への対策）。
単独で走らせる場合も `scrape.py --source ragent_interests` から（`fetch_all()` は
provider registry に載らない特判呼び出し）。`受付終了` のカードは行構成がずれる
（1 行目が会社名ではなく「受付終了」）＝ JD も開けないので一覧段階で落とす。

**星付きは「1 社 1 レコード」の例外**（2026-08-23）。本人が選んだ求人なので、
他サイト経由で同じ会社が既に在庫にあっても**併入せず独立 row を作る**
（`upsert_job(..., allow_company_dup=True)`）。併入すると r-agent の source_id /
URL / JD が残らず、**r-agent 経由で応募できなくなる**のが理由。後処理の
`dedup_fuzzy` も星付き（`keyword='mypage_interest'`）を無条件で keep 側に回し、
消す側から raw_jd（長い方）と score / recommend_score / gap_analysis（keep が空のとき）
を引き継いでから消す — そうしないと「JD が長い方を残す」既定ルールで
入れたばかりの星付きが即座に消える（実測：25 件入れて 4 件が消えた）。
`output/ragent_interests_seen.json` に旧挙動の名残（DB に row が無いのに
seen 扱い）がある場合は `python3 -m tools.oneoff.prune_ragent_interests_seen --apply`。

CDP port / Chrome profile 路徑可由 `config/scraping.yaml` 覆蓋（`tools/app_config.py` 統一載入，缺檔則用 `scrape.py` / `inbox/_cdp.py` 內建預設值，本人環境行為不變）。
