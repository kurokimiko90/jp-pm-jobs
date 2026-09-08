---
name: job-prep
description: 指定 job_id 一次性產出面接準備パック（公司調研/志望動機/想定問答/面試slides/checklist）。session 內用並行 sub-agent 版本，比 prep.py CLI 快且穩。用法：/job-prep 105
---

# job-prep — 並行 sub-agent 版面接準備

對 `$ARGUMENTS` 中的 job_id 執行。輸出目錄：`output/prep/{id}_{公司slug}/`（與 prep.py 同結構）。

## 步驟

1. **讀素材**（直接做，不派 agent）：
   - `sqlite3 data/jobs.sqlite "SELECT * FROM jobs WHERE id=<id>"` 取 JD/公司/薪資/tier
   - `data/cognitive_bullets.md`（JP 條目）、`resume/jp/data.yaml`（careers + position）
   - 若 `interview/companies/{slug}_facts.md` 已存在 → 跳過步驟 2

2. **調研 agent**（general-purpose + WebSearch，先行）：
   調研公司基本面/產品/近6個月動向/文化評價/競品/選考情報，每條附來源 URL，
   查不到標「未確認」禁編造。結果存 `interview/companies/{slug}_facts.md`。

2.5 **Gap 矩陣（主 session 直接做，先於生成）**：
   逐條對照 JD 必須/歓迎要件 × data.yaml 実績，寫進 `01_company_brief.md` 開頭：
   「充足 ◎／部分 △／無支撐 ✗」。任何**必須要件 = ✗** 視為 go/no-go 級風險：
   置頂標紅 + 附一段 honest 回答話術（不盛る、用相鄰實績換算），不准只埋成 {{要確認}}。

3. **三個生成 agent 並行**（單一 message 內同時派出，prompt 各自注入：JD + facts + bullets + careers + gap 矩陣 + 嚴守事項「禁止捏造數字、不明寫 {{要確認}}、全文日語、**年資/領域表述必須可由 data.yaml period 逐年驗算**（PdM 年數 = 各 PdM 職 period 合計；不得把プロダクト職年數冒充 PdM 年數、非該領域職歷不得冒充該領域）」）：
   - **brief agent**：按 `interview/companies/_template.md` 結構產 `01_company_brief.md`（含預想質問5 + 逆質問 HR/HM/Exec 各3 + applying angle）
   - **qa agent**：產 `03_interview_qa.md` — 20問（日本定番8 + JD特化12），**一問一答**：每問只有「### Q. 質問文」+ 回答本文，狙い/NG/解説/前後語等其它內容一律不出現。**回答格式（強制）**：結論先行（1 行目に結論 1 文）→ 要点「1. / 2. / 3.」番号付き最大 3 点 → 各点 1〜2 文の話し言葉（面接でそのまま口に出せる平易な表現）＋証拠（実績・数字・プロジェクト名）必附；**最重要 5 問（必須要件・AI実運用・失敗経験・志望動機・強み）各附「深掘り3連問」**（なぜ？→あなた個人の貢献は？→やり直すなら？），深掘り同樣一問一答（追問 + 可直接口說的回答）
   - **shibou agent**：產 `02_shibou_doki.md` — 志望動機300字/自己PR300字/転職理由200字 + 各45秒口頭版

3.5 **成果因果反推與反證 QA**：
   依 `$interview-qa-deepdive` 的流程，從履歷中選 5〜8 個高風險成果，追加：
   `確認済み事実 → 再構成仮説 → 60秒回答 → 深掘り3問以上`。
   必問「根因／本人貢獻／數字怎麼量」，倍率、比例、用戶數、導入數若缺分母或期間，
   必須加 `{{要確認}}` 並降低口頭表述強度。最後執行該 Skill 的 `audit_qa.py`，
   errors=0 才能進入 slides。

4. **zero-token 部分**（直接跑）：
   - `python3 prep.py <id> --stage checklist`（checklist 本身 zero-token）
   - tailored 履歷：`python3 -m tools.resume_tailor --job-id <id>`

5. **slides**（主 session 自己寫，品質要求高不外包）：
   - 按 `prep.py` 的 SLIDE_SPEC 7枚結構寫 `04_slides.json`（title/bullets/note，note=話し言葉原稿）
   - `python3 prep.py <id> --render-only` 渲染 `04_slides.html`

5.5 **數字暗記カード**：產 `06_numbers_card.md` — 全 pack 出現的所有數字（実績・認知データ・年資）一頁彙總，每個數字附：出處檔案 + 「どう測ったか」30秒口頭說明（特別是認知統計 49%/77%/5.3% 類，面試官第一反應是『どう測った？』，沒準備反成扣分）。

6. **一致性審計（主 session 直接做，最後一道關）**：
   - 交叉比對 01/02/03/04/06 中所有年資・領域・規模表述 vs data.yaml period，逐項驗算；不一致 = 修正後才算完成（這是「編造數字 = 事故」的執行機制，不是建議）
   - `03_interview_qa.md` 的成果反推區必須通過 `$interview-qa-deepdive` 的 deterministic audit；QA 本身不可當 evidence
   - 90日プラン等承諾型內容，逐條自問「面試官追問實現手段時答得出嗎」，答不出就改保守
   - **機械 gate（必跑，不可省略）**：`grep -nE "狙い|回答骨子|回答例|NG回答|NG例" 03_interview_qa.md`。
     有命中 = qa agent 沒遵守一問一答格式，把命中的問題編號整理出來直接刪掉該行或重派 qa agent 只修這幾題，
     絕不能讓「狙い/回答例/NG回答」這類舊格式殘留進最終檔案（曾在實際案例中發生過）

7. **驗收**：確認 7 檔齊全；向用戶回報：① `{{要確認}}` 清單（面試前宿題）② gap 矩陣的 ✗ 項與應對話術 ③ 提醒面試後跑 `interview/retro.py` 並把被問到的新問題還流 question-bank。

## 鐵則
- 編造數字 = 事故。所有實績只能來自 data.yaml / cognitive_bullets / facts 檔
- 自稱文案（「日本初」等）標註為自稱，不當事實
- 失敗的 agent 不重派超過 1 次，缺的部分主 session 自己補
