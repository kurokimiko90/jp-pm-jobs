# interview/ — 面試準備管線

**手寫問答稿一律放 `question-bank/`，這一層只放程式碼。** 場合別導覽（面接前 / エージェント
面談前 / 音読練習）與「同一題有多份答案時信哪份」見 `question-bank/README.md`。
生成物在 `generated/`（gitignored）與 `output/prep/{id}_{slug}/`，不與手寫檔混放。

`prep.py {id} interview` 跑 6 個 stage：`qa` → `jikoshoukai` → `checklist` → `slides` → `script`
→ `voice`（`prep.py` 內的 stage runner 依序呼叫，個別 stage 也能單獨重跑）。本檔記錄
jikoshoukai／slides／script／voice 幾個較複雜 stage 的內部機制，以及 qa 的二次審稿管線 `qa_upgrade`。

## qa stage — 想定問答（`interview/qa/`）

**pack を開けば全部そろっている状態を作る。** 定番を外部ファイルへ逃がさない。
`prep.py {id} interview --stage qa` は `interview.qa.build()` 一本だけを呼ぶ。

```
正典 49 問（question-bank/core/）
  → persona_traits   求める人物像を LLM に言語化させる（JD 見出し優先・無ければ推測） … LLM 1 call
  → product_relationships JD・会社事実に登場する製品名を LLM に特定させる       … LLM 1 call
  → tune_core        jd_dependent な 14 問をこの求人向けへ調整      … LLM 1 call
  → jd_specific      この JD でしか出ない 12 問を生成               … LLM 1 call
  → 重複排除          定番の言い換えになっている問を捨てる（零 LLM）
  → drilldown        突かれやすい 6 問への追問（1 段目）            … LLM 1 call
  → drilldown_second その答えをさらに突く（2 段目・4 問）           … LLM 1 call
  → gates            機械閘門（下記 7 種）
  → repair_batch     落ちた問を 1 回にまとめて作り直す（≤2 巡）     … LLM ≤2 call
  → critic.review    面接官の目で採点＋弱い問を指摘                  … LLM 1 call
  → refine_batch     指摘された問だけ書き直す → 再採点（≤2 巡）     … LLM ≤6 call
  → render           純日本語・質問と回答だけの 1 枚へ
```

出力は `01_interview_qa.md`（本体）と `05_qa_audit.md`（監査）。本体には見出し以外の
説明文を書かない。**LLM 呼び出しは問数に比例させない — 1 パック最大 19 回**（修復も
是正も問ごとではなく一括）。批評巡回を切れば従来どおり 8 回
（`build(..., critique=False)` / `--no-critique`）。

| モジュール | 役割 |
|---|---|
| `qa/taxonomy.py` | 正典 49 問の定義と、旧 4 ファイル 75 問からの写像（`sources`） |
| `qa/bank.py` | `question-bank/core/` の読み書き・移行元の解析 |
| `qa/generate.py` | 指揮中心（`tools.miko_llm`）呼び出し 8 種＋共通 `STYLE_RULES` |
| `qa/gates.py` | 機械閘門（零 LLM） |
| `qa/critic.py` | 面接官の目での採点・指摘の機械照合（下記） |
| `qa/render.py` | 最終描画・閘門用の `Labelled`（結論＋要点、質問文は含めない） |
| `qa/build.py` | 上記の統合。単独実行は `python3 -m interview.qa.build --job-id 123` |

### 機械閘門（`qa/gates.py`）

**検査対象は結論文と要点の両方、質問文は含めない。** 質問文まで数えると、JD の語を
質問に書いただけで B が覆蓋済みへ倒れる（自己成就）。

| Gate | 見るもの | 落ちたとき |
|---|---|---|
| A 事実錨定 | 回答中の数字が素材にあるか / 要点に具体物があるか | 一括再生成 |
| B 要件覆蓋 | `gap_analysis.requirements` が最低 1 問で扱われているか | 監査に列挙（再生成しない） |
| C 一貫性 | 経験年数が複数の値で語られていないか | **少数派の値を語る問だけ**再生成 |
| D 素材偏り | 同じ数量表現が何問で使い回されているか | 使い回しだけで出来た問を再生成 |
| E 口語 | 口に出すと浮く書面語が残っていないか | 一括再生成 |
| F 人物像覆蓋 | JD の「求める人物像」の各特性が具体的実績と結び付いているか | 監査に列挙（再生成しない） |
| G 製品覆蓋 | JD・会社事実の各製品が「関係・貢献・次にすること」と結び付いているか | 監査に列挙（再生成しない） |

加えて「同じ型で締めている問」（`御社でも〜`）が 25 % を超えたら監査に列挙する。
**再生成はしない** — どの問を削るべきかは機械では決められないため、人が読んで落とす。

**F は「条件を満たすか」ではなく「面接官が求めている人物のタイプに見えるか」を見る軸**。
B（MUST/WANT の逐条チェック）とは別観点 — 条件は満たしていても「この人はうちが欲しい
人物か」で落ちる面接があるため、QA 生成前に人物像を言語化し、`generate.jd_specific()`
のプロンプトへ「特性ごとに具体的実績（プロジェクト名・数字）を対応させよ」という
指示として渡す。判定は B と同じキーワード近似で監査に出すだけ、再生成トリガーには
しない（`gates.run` の `persona_traits` 引数、`build()` が毎回自動で渡すので
`prep.py {id} interview` を跑るだけで効く — 個別に呼び出す必要はない）。

- **人物像の抽出は LLM（`generate.persona_traits`、1 call）が本命、正規表現
  （`build._persona_traits_fallback`）は LLM 呼び出し失敗時だけのフォールバック。**
  JD に「求める人物像」という見出しがあるとは限らない（実測: 明記されている求人票は
  一部にとどまる）。見出しが無くても、募集背景・業務内容・企業文化の書きぶりから
  「採りたいタイプ」は読み取れることが多く、これは本質的に規則ではなく読解の仕事
  なので LLM に任せる。応募者プロフィールは渡さない（`_review_context` と同じ
  設計 — 求人側だけで判断させ、応募者に寄せて都合よく書かせない）。

- **G は「製品名を挙げるだけ」で終わらせないための軸。** `generate.product_relationships`
  （1 call、求人側だけの文脈）が JD・会社事実に実際に書かれている製品名だけを拾い
  （架空の名前は作らせない）、その一覧を `jd_specific()` のプロンプトへ渡す。
  regex フォールバックは無い — 製品名の特定は見出しに頼れる性質の情報ではないため、
  LLM が失敗したら該当質問を作らないだけで pack 生成自体は止めない。`jd_specific()`
  は製品ごとに最低 1 問、回答へ①製品の説明（JD・会社事実に書かれている範囲のみ。
  それ以外の知識で仕様を断定しない）②応募者との関係（薄ければ「直接の経験は
  ないが〜」と正直に書く）③貢献できること・次にすること、の 3 点を明示させる —
  これが今回の「まず求める人物像・関わる製品を特定してから、自分の経験を
  対応させて書く」という運用そのものの実装。

- **B が見る要件は JD 本文（日本語）から取る**（`build._jd_requirements`、零 LLM）。
  `gap_analysis.requirements` は内部閲覧用に**中国語**で書かれているため、日本語の
  回答と語が一致せず覆蓋が常に 0 付近へ倒れる（実測: 中国語要件 5 件で 0/5 →
  JD から取り直して 6/8）。JD から拾えなかったときだけ gap へフォールバックする。
- **見出し検出の表記ゆれ**（`_REQ_HEADING_RE` / `_PERSONA_HEADING_RE`）: 日本の求人票は
  「必須要件」「必須（MUST）」「【必須】」など見出し表記が揺れる。行頭アンカー
  `(?m)^` に固定し、括弧書き・単独見出しも拾うよう直したところ、DB 全 2,144 件中の
  非空抽出率が 408 件（19%）→ 1,256 件（59%）に改善（実測）。見出し行末の空白除去に
  `\s*` を使うと `\n` も食ってしまい「見出し行の直後の箇条書き 1 件目」ごと呑み込む
  バグを踏んだため、行内空白限定の `[ \t\r]*` に直してある — 次に見出し検出を触る
  ときは同じ罠を踏まないこと。
- B はキーワード一致による**近似**（要件語 ↔ 答えの略語は `gates._SYNONYMS` で吸収。
  「プロダクトマネジメント経験」↔「PdM」を見ないと常に未覆蓋へ倒れる）、C は
  **年数のみ**が対象。事実の食い違い全般は検出しない — 監査レポートにもその旨を
  明記してあり、網羅を装わない。
- D の再生成対象は LLM 生成分（JD 特化・深掘り）に限る。**正典（C 番号）は手書きなので
  機械では書き換えず、監査に出すだけ**（直すなら人が `question-bank/core/` を直す）。
- C の少数派判定は「その値を語る問数」の多数決。手書き正典側が少数派なら正典側が
  pack 内で直される（`question-bank/` の原文は書き換えない）。
- **E を指揮中心の `accept` に入れてはいけない。** 十数問の一括出力では禁止語 1 つで
  全体が差し戻され、brain 総当たりの末に gateway が 500 を返す（実際に踏んだ）。
  accept は形式（`regex` / `minChars`）だけ。「貴社→御社」「結論として、」の剥がし等
  機械で直せるものは `generate._clean()` がその場で直し、残りを E が拾って修復に回す。

### 批評 → 是正の巡回（`qa/critic.py`）

機械閘門は形と事実しか見ない。事実が正しく口語で要件も覆っているのに
「誰でも言える一般論」「質問に答えていない答え」「この質問が本当は何を確かめ
ようとしているか無視した答え」で落ちる面接がある。そこを**面接官役の LLM に
問ごと・カテゴリごとの軸で採点させ、指摘を書き直し指示に変える**のがこの層。

検収は必ず**安いほう（機械閘門）から先に**回す。逆順にすると、事実違反を含んだ
答えを面接官役が褒めることがある。

**採点軸は問ごとに変わる。** 全問共通 2 軸（`具体性`／`質問応答`）＋カテゴリ軸
1〜2 個（`critic.CATEGORY_AXES`、taxonomy のカテゴリキーで決まる）:

| カテゴリ | 追加軸 | 何を確かめる問か |
|---|---|---|
| career（経歴・転職・条件） | `定着可能性` + `意欲` | 転職理由・キャリアビジョン・志望動機系 |
| strength（強み弱み） | `戦力化スピード` | 即戦力の根拠 |
| pm / ai / agent / client | `専門性` | 実務の判断ロジックが通用するか |
| closing（逆質問） | `意欲` | 志望度の深化 |
| jd / drilldown（生成物） | `専門性` | taxonomy のカテゴリを持たないため一律専門性 |

`定着可能性`/`戦力化スピード`/`意欲` の 3 軸は「長く働くか／5 年後に中核人材と
して活躍しているか／意欲は高いか」という役員面接の評価基準を元にしている
（全問へ一律には掛けない — どの問がどの基準の材料になるかはカテゴリで決まる）。

| 縛り | なぜ |
|---|---|
| 指摘に**回答本文からの逐語引用を要求**し、在るかを文字列比較で照合 | LLM 判定を LLM で検証しない。引用が本文に無い指摘＝作り話なので捨てる（却下数は監査に出す） |
| ラベルが食い違う指摘は**引用が在る問へ引き当て直す** | LLM はラベルをよく取り違える。中身が正しい指摘を捨てない |
| `fix` は「素材の別の場面へ差し替えよ」までしか言えない | 零編造。持っていない実績を書けと指示させない |
| 出てこなかった問・軸は **0 点** | 満点扱いにすると、問や軸を書き落としただけで合格線を越える |
| 合否は**問ごと**（`Critique.passed`） | 平均で判定すると、1 問が壊滅的でも他の問が高得点なら隠れる |
| 書き直して**対象問の合計点が下がった巡は捨てて前の版へ戻す**（`round_delta`） | 迭代は良くなる方向にしか進めない |
| 改善が `STALL_ROUNDS`（2）回続けて止まったら `MAX_ROUNDS` 前でも打ち切る | 90 点は全軸 9 点以上を要求する厳しい基準で、回しても届かない問は普通に残る。上限まで空回りしない |
| `review()` が失敗（None）＝**合格ではなく打ち切り** | 「批評できなかった」を「通った」と読み替えない |

各軸 0〜10 → 0〜100 換算、**問ごとの合格線 90**。巡回は最大 5 巡（1 巡 = 批評 +
是正で LLM 2 call）。監査 `05_qa_audit.md` の末尾に各巡の平均点・合格した問の
割合・採用した版・合格線に届いていない問の一覧・却下した指摘数が出る。**採点は
LLM の自己判断で実際の合否ではない**旨も併記される。

### 見出しキーワード（`qa/keywords.py`）

**見出しは `### Q. [キーワード] 質問文`。** 当日 90 問超を上から読み返すため、質問文を
最後まで読まなくても話題が掴めるようにする。キーワードは **5 文字以内**（`keywords.MAX_LEN`）。

- 正典 49 問は `taxonomy.CORE` の `keyword` に固定で持つ（LLM に作らせない）
- JD 特化・深掘りは既存の生成呼び出しの中で一緒に出させる（**呼び出し回数は増やさない**）。
  LLM が付け忘れた問だけ `keywords.fallback()` が規則で埋める
- **読む側は必ず `keywords.split()` で剥がす** — 剥がさないと TTS が「かぎかっこ」を
  読み上げる。`tts/theater.py` は見出し正規表現へ `keywords.OPTIONAL_PREFIX` を埋め込み、
  `qa_quality.QAItem.keyword` は保持して `render_qa()` で戻す（qa_upgrade の promote で消えない）
- キーワードは見出しだけの情報。閘門の検査対象（`Labelled`）にも回答本文にも入れない
- キーワード導入前に生成した pack は
  `python3 -m tools.oneoff.add_qa_keywords output/prep/{id}_{slug} [--apply]` で後付けできる
  （見出し以外が 1 字でも変わったら書き込まず中断する）

### 会社事実の自動拾い上げ

`prep.py` に `--facts` を渡さなくても、pack 内の `_facts.md` → `00_company_brief.md` の
順で拾う（`interview.qa.build` の単独実行時も同じ）。ここを拾わないと QA が
「会社事実なし」で生成され、JD の再話に寄った答えになる。

### 回答の型（`generate.STYLE_RULES`）

一問一答の中身は prompt 側で型を固定している。ここを緩めると、3 点それぞれに別
プロジェクトを 1 行ずつ並べた「職務経歴書の朗読」に戻る:

- 1 文目は前置き語（「結論として」等）を置かず答えから入る — accept の `notIncludes` で機械検収
- 要点 1 = 実際にあった一場面（状況→判断→結果、2 文まで）
- 要点 2 = その判断の理由・再現性の根拠
- 要点 3 = この会社での効き方（自然に繋がらない問では書かない）
- 「御社でも〜したいです」で締める問は全体の半分以下（`tune_core` の prompt で制限）

### 正典題庫の再生成

`common_qa.md` 等 4 ファイル（75 問・重複あり）から 49 問へ畳む一次移行:

```bash
python3 -m tools.oneoff.build_qa_core --dry-run    # 素材の対応だけ確認
python3 -m tools.oneoff.build_qa_core              # 未生成の問だけ（再開可）
```

`company_brief.py`（公司調研）と `mock.py`（LLM 模擬面接官）は従来どおり別建て。

### qa_upgrade — QA 二次審稿

第一次生成的想定問答常有三個毛病：寫得像書面文章（唸出來很怪）、數字沒有出處、追問只有
一層深就接不下去。`qa_upgrade.py` 是獨立的第二輪，讀既有問答檔重新加工：

- **逐題審稿改寫** — 分批送審，改成能直接開口講的です・ます調
- **高風險題三層深掘** — 挑出最容易被追問的幾題，各生成 3 層追問與回答（HR / PdM / 技術三種視角）
- **機械檢查（`qa_quality.py`，零 LLM）** — 數字必須能在素材中找到出處（`unsupported_numbers`）、
  書面語用詞偵測（`oral_lints`）、佔位符殘留檢查
- **監査報告** — 彙整成 `04_audit.md`，人工過目後可只指定幾題重跑修復

產物全部寫在 `output/prep/{id}_{slug}/qa_upgrade/`（`01_review.md` / `02_interview_qa_upgraded.md` /
`03_drilldown_qa.md` / `04_audit.md`），**預設非破壞**，原始問答檔不動。只有明確加 `--promote`
才會備份後覆蓋原檔；審稿結果存 checkpoint，之後補跑個別題目不必整份重來。

```bash
python3 -m interview.qa_upgrade --job-id 123              # 全題審稿，產出在 qa_upgrade/ 子目錄
python3 -m interview.qa_upgrade --job-id 123 --questions 3,5   # 只修特定幾題
python3 -m interview.qa_upgrade --job-id 123 --promote     # 確認品質後才覆蓋原始問答檔（先自動備份）
python3 -m interview.qa_upgrade --job-id 123 --no-llm      # 只輸出去識別化 prompts
```

⚠ **promote 後如果該 job 已有面接シアター/語音資產，需手動重跑同步**（promote 完會印提醒）：
```bash
python3 -m interview.theater_script --job-id 123   # 不加 --force，幕級 hash 快取只重跑真的變動的幕
python3 -m tools.interview_voice --job-id 123       # zero-token TTS，內容尋址快取只補新句子
```

## jikoshoukai stage — 自己紹介（`prep.py` 內建，`interview_jikoshoukai()`）

面試開場「簡単に自己紹介をお願いします」的専用產物，跟 qa 的 49 問正典不是同一件事
（C01「これまでのご経歴」偏經歴陳述，這裡要的是 1 分鐘、JD 優先度排序、且刻意加入
「履歴書に出ない情報」的開場稿）。單一 LLM call，走面試教練手法：JD 優先度分析 →
1 分鐘日文台本 → 翻譯 → 逐段對應 JD 的理由 → 面試官可能形成的印象 → 「強み」追問的
接續答案，六段輸出成一份 `06_jikoshoukai.md`。

- 素材沿用 `_context_block(job, facts, with_gap=True)`（JD + 會社事實 + gap 分析的
  要件対位メモ + 去識別化 profile），不另外重建 context
- 「履歴書以外の情報」的唯一依據是 profile 的 `differentiators`
  （cognitive_data / narrative / career_vision / self_pr_jp）— prompt 明文限定根拠，
  不讓 LLM 自己編性格描述
- 志望動機/転職理由只能點到為止（prompt 明文禁止講完），留給後面單獨的志望動機提問
- 零 LLM 檢查：B 段自己紹介字數落在 220〜340 字外只印警告不阻擋（比照 apply 系列的
  輕量風格，不套 qa 的多輪機械閘門/critic），輸出後跑 `tools.redact.redact()` 掃殘留
  取引先ブランド名（比照 `apply_mail` 的最終關門）
- 說明文語言（A/D/E/F）跟著 `config/app.yaml` 的 `reader_lang` 走（`locale.lang_directive
  ("jikoshoukai")`），B 段自己紹介本文與 F 段回答**固定日文**不受影響；`reader_lang=ja`
  時省略 C 段中文翻譯

```bash
python3 prep.py 123 interview --stage jikoshoukai
```

## slides stage — 面接スライド

母版 `interview/slides_master.pptx`（gitignored，17 枚個人 deck）的 slide15「入社後の展開」+
slide16 結句共 11 欄位由 LLM 依 JD 特化，其餘頁固定不動；prompt 僅送
`tools.deid.build_deid_profile()` 白名單，姓名只在母版本地換回。

- 母版重建：`python3 -m tools.oneoff.make_slides_master <deck.pptx>`
- 欄位微調：改 `03_slides.fields.json` 後用 `tools.interview_slides.render()` 重渲染，
  不必重呼叫 LLM

## script stage — 面接シアター 9 幕互動台本

`theater_script.py` 讀 QA md + company brief + raw_jd，生成 9 幕互動台本（比照手寫
`SCRIPT_MAIN` 骨架：挨拶→自己紹介→志望動機→意思決定→ケース→深掘り→逆質問→年収→結び，
含面試官追問／候選人 quoteFrom 反問鏈）。

LLM 呼叫最小化：缺快取的幕合併成 1 次呼叫一括生成，只有沒過機械閘門的幕才單獨修復
（新 pack 最佳 1 call、素材沒變 0 call、改一題 1 call）。品質靠三種機械閘門：

| Gate | 檢查什麼 |
|---|---|
| A 結構 | `quoteFrom` 必須是前句面試官原文的子字串 |
| B 事實錨定 | 候選人提到的數字必須 ⊆ 素材，防編造 |
| C 口語敬語 | です・ます調、句長 ≤ 90、提問必附クッション言葉、「貴社」→「御社」自動修正 |

不過閘重生成 ≤ 2 次，仍敗則該幕降級平板版並記入 `theater/review_report.md`（模擬回答清單也在
此，voice stage 前建議人工過目）。幕級內容尋址快取（`script_gen_cache.json`），素材沒變不重呼叫。

```bash
python3 -m interview.theater_script --job-id 123 [--force]
```

## voice stage — 面接語音化（TTS）

`01_interview_qa.md` 一問一答 → `tts/theater.py` 逐句合成（內容尋址快取，面試官/候選人分聲，
provider chain 見 `config/tts.yaml`：`edge_tts` → `local_macos`）→ `theater/script.json` +
`audio/*.mp3`（面接シアター `#/theater` 逐句播放用）+ ffmpeg concat 整包 MP3 → Telegram
`sendAudio`。**zero-token**，不呼叫文字生成模型。每句合成前過 `tools/pii_gate.py`（真名→本人）。

```bash
python3 -m tools.interview_voice --job-id 123          # 整包生成 + Telegram 推送
python3 -m tts.theater --job-id 123 [--no-audio|--force]   # 只建 theater 資產（不含推送）
```

## 面試後覆盤

`retro.py` 面試結束後手動跑，LLM 結構化分析面試表現、提取改進行動。

```bash
python3 -m interview.retro --job-id 123
```
