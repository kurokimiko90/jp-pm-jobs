"""Reader-language helpers for internal-facing outputs.

This controls the language of company briefs, gap analysis text, README files,
and QA quality-check notes. It does not affect submission-language artifacts
such as 志望動機 / cover letter / tailored resume.
"""

from __future__ import annotations

from typing import Literal

from tools.app_config import get as _cfg

ReaderLang = Literal["zh", "ja", "en"]
_ALLOWED = {"zh", "ja", "en"}


def reader_lang() -> ReaderLang:
    raw = str(_cfg("app", "reader_lang", "zh")).strip().lower()
    return raw if raw in _ALLOWED else "zh"  # type: ignore[return-value]


_TEXT = {
    "zh": {
        "gap_block_matched": "**匹配點：**",
        "gap_block_gaps": "**缺口：**",
        "gap_md_title": "# Gap 分析 — [{id}] {title}",
        "gap_md_company": "- **公司**: {company}",
        "gap_md_score": "- **score**: {score}　**推薦度**: {rec}/100",
        "gap_md_url": "- **URL**: {url}",
        "gap_md_requirements": "## JD 要求",
        "gap_md_matched": "## ✅ 符合",
        "gap_md_gaps": "## ⚠️ 缺口",
        "match_brief_title": "# Match Brief — {company} / {title}",
        "match_brief_meta": "job_id: {job_id} ｜ score: {score} ｜ 推薦度: {rec}",
        "match_brief_verdict": "## 判斷: {icon} {verdict}",
        "match_brief_section": "## JD 要求 × 匹配",
        "match_brief_coverage": "## 關鍵詞覆蓋（{coverage}%）",
        "match_brief_covered": "**已覆蓋 ({count})：** {items}",
        "match_brief_uncovered": "**未覆蓋 ({count})：** {items}",
        "qa_check_lang": "請用繁體中文簡潔回答（各 1〜3 行）。",
    },
    "ja": {
        "gap_block_matched": "**一致点：**",
        "gap_block_gaps": "**ギャップ：**",
        "gap_md_title": "# Gap Analysis — [{id}] {title}",
        "gap_md_company": "- **会社**: {company}",
        "gap_md_score": "- **score**: {score}　**推薦度**: {rec}/100",
        "gap_md_url": "- **URL**: {url}",
        "gap_md_requirements": "## JD 要件",
        "gap_md_matched": "## ✅ 満たす点",
        "gap_md_gaps": "## ⚠️ ギャップ",
        "match_brief_title": "# Match Brief — {company} / {title}",
        "match_brief_meta": "job_id: {job_id} ｜ score: {score} ｜ 推薦度: {rec}",
        "match_brief_verdict": "## 判定: {icon} {verdict}",
        "match_brief_section": "## JD 要件 × 適合",
        "match_brief_coverage": "## キーワードカバレッジ（{coverage}%）",
        "match_brief_covered": "**カバー済み ({count})：** {items}",
        "match_brief_uncovered": "**未カバー ({count})：** {items}",
        "qa_check_lang": "日本語で簡潔に回答してください（各 1〜3 行）。",
    },
    "en": {
        "gap_block_matched": "**Matched:**",
        "gap_block_gaps": "**Gaps:**",
        "gap_md_title": "# Gap Analysis — [{id}] {title}",
        "gap_md_company": "- **Company**: {company}",
        "gap_md_score": "- **score**: {score}  **Fit**: {rec}/100",
        "gap_md_url": "- **URL**: {url}",
        "gap_md_requirements": "## JD Requirements",
        "gap_md_matched": "## ✅ Matches",
        "gap_md_gaps": "## ⚠️ Gaps",
        "match_brief_title": "# Match Brief — {company} / {title}",
        "match_brief_meta": "job_id: {job_id} | score: {score} | fit: {rec}",
        "match_brief_verdict": "## Verdict: {icon} {verdict}",
        "match_brief_section": "## JD Requirements × Match",
        "match_brief_coverage": "## Keyword Coverage ({coverage}%)",
        "match_brief_covered": "**Covered ({count}):** {items}",
        "match_brief_uncovered": "**Missing ({count}):** {items}",
        "qa_check_lang": "Answer briefly in English (1-3 lines each).",
    },
}


def text(key: str, **kwargs) -> str:
    value = _TEXT[reader_lang()][key]
    return value.format(**kwargs)


_LANG_DIRECTIVES = {
    "default": {
        "zh": "所有說明性文字一律使用繁體中文。公司名、職稱、產品名、專有名詞可保留原文。",
        "ja": "説明文はすべて日本語で書いてください。会社名・職種名・製品名・固有名詞は原文のままで構いません。",
        "en": "Write all explanatory text in English. Company names, role titles, product names, and proper nouns may stay in the original language.",
    },
    "brief": {
        "zh": "輸出全文一律使用繁體中文；公司名、產品名、專有名詞可保留原文。",
        "ja": "出力本文はすべて日本語で書いてください。会社名・製品名・固有名詞は原文維持で構いません。",
        "en": "Write the entire output in English. Company names, product names, and proper nouns may stay as-is.",
    },
    "jikoshoukai": {
        "zh": "A・D・E・F 的分析與說明文字一律用繁體中文；公司名、職稱、產品名、專有名詞可保留原文。"
              "**B 的自己紹介本文與 F 的回答本文必須是日文**（面接で実際に声に出す内容のため、この設定の影響を受けない）。",
        "ja": "A・D・E・F の分析・説明文はすべて日本語で書いてください。会社名・職種名・製品名・固有名詞は原文維持で構いません。"
              "B の自己紹介本文と F の回答本文はもちろん日本語です。",
        "en": "Write the analysis and explanation text (A, D, E, F) in English. Company names, job titles, product names, "
              "and proper nouns may stay in the original language. Sections B and F must stay in Japanese regardless — "
              "they are spoken content for the actual interview.",
    },
    "qa_supplement": {
        "zh": "A・D・E・F 的分析與說明文字一律用繁體中文；公司名、職稱、產品名、專有名詞可保留原文。"
              "**B 的回答本文與 F 的備用延伸回答必須是日文**（面接で実際に声に出す内容のため、この設定の影響を受けない）。",
        "ja": "A・D・E・F の分析・説明文はすべて日本語で書いてください。会社名・職種名・製品名・固有名詞は原文維持で構いません。"
              "B の回答本文と F の備用延伸回答はもちろん日本語です。",
        "en": "Write the analysis and explanation text (A, D, E, F) in English. Company names, job titles, product names, "
              "and proper nouns may stay in the original language. Sections B and F must stay in Japanese regardless — "
              "they are spoken content for the actual interview.",
    },
    "gap_json": {
        "zh": "JSON 裡的 requirements / matched / gaps / recommend_reason 等描述性欄位一律用繁體中文；公司名、職稱、產品名、專有名詞可保留原文。",
        "ja": "JSON 内の requirements / matched / gaps / recommend_reason など説明的な文字列はすべて日本語で書いてください。会社名・職種名・製品名・固有名詞は原文維持で構いません。",
        "en": "Use English for descriptive strings in requirements / matched / gaps / recommend_reason. Company names, job titles, product names, and proper nouns may remain in the original language.",
    },
    "gap_summary": {
        "zh": "語言（重要）：所有分析敘述（portrait、reason、detail、theme、severity、nature、type、roi、effort、steps、counter_measures、done_criteria）一律用繁體中文撰寫，不得出現日文句子。公司名、職缺名、產品名、專有名詞可保留原文，但描述性文字必須是繁體中文。",
        "ja": "言語（重要）：分析の説明文（portrait、reason、detail、theme、severity、nature、type、roi、effort、steps、counter_measures、done_criteria）はすべて日本語で書いてください。会社名・職種名・製品名・固有名詞は原文維持で構いません。",
        "en": "Language (important): write all analysis text (portrait, reason, detail, theme, severity, nature, type, roi, effort, steps, counter_measures, done_criteria) in English. Company names, job titles, product names, and proper nouns may remain in the original language.",
    },
}


def lang_directive(kind: str = "default") -> str:
    directives = _LANG_DIRECTIVES.get(kind, _LANG_DIRECTIVES["default"])
    return directives[reader_lang()]


_TPL = {
    "apply_readme_en": {
        "zh": """# 投遞包（EN） — {company} / {title}

生成日: {date} ｜ job_id: {job_id} ｜ score: {score}
URL: {url}

| 檔案 | 內容 | 確認 |
|---|---|---|
| 01_company_brief.md | 公司研究 + 投遞角度 + Go/No-Go | ☐ 核對事實，決定是否投遞 |
| 03_cover_letter.md | 英文 Cover Letter（約 280 字，可直接微調） | ☐ 朗讀一遍，調整語氣 |
| 04_resume_tailored.md | 依 JD 客製的英文履歷（Markdown） | ☐ 檢查所有事實，移除 {{{{to_verify}}}} |

再生成: `python3 prep.py {job_id} apply --lang en`
Base resume: `resume/en/master.md`
{gap}""",
        "ja": """# 応募パック（EN） — {company} / {title}

生成日: {date} ｜ job_id: {job_id} ｜ score: {score}
URL: {url}

| ファイル | 内容 | 確認 |
|---|---|---|
| 01_company_brief.md | 企業調査 + 応募角度 + Go/No-Go | ☐ 事実確認・応募判断 |
| 03_cover_letter.md | 英文カバーレター（約 280 words、送付前提） | ☐ 音読して語気を調整 |
| 04_resume_tailored.md | JD 特化の英語履歴書（Markdown） | ☐ 事実確認・{{{{to_verify}}}} 除去 |

再生成: `python3 prep.py {job_id} apply --lang en`
Base resume: `resume/en/master.md`
{gap}""",
        "en": """# Application Pack (EN) — {company} / {title}

Generated: {date} | job_id: {job_id} | score: {score}
URL: {url}

| File | Content | Action |
|---|---|---|
| 01_company_brief.md | Company research + applying angle + Go/No-Go | ☐ Verify facts and decide Go/No-Go |
| 03_cover_letter.md | English cover letter (~280 words, ready to tune/send) | ☐ Read aloud and adjust tone |
| 04_resume_tailored.md | JD-tailored English resume (Markdown) | ☐ Check facts and remove {{{{to_verify}}}} |

Regenerate: `python3 prep.py {job_id} apply --lang en`
Base resume: `resume/en/master.md`
{gap}""",
    },
    "apply_readme_jp": {
        "zh": """# 投遞包 — {company} / {title}

生成日: {date} ｜ job_id: {job_id} ｜ score: {score}
求人URL: {url}

| 檔案 | 內容 | 確認 |
|---|---|---|
| 01_company_brief.md | 公司調研 — 投遞判斷材料 | ☐ 事實確認、決定 Go/No-Go |
| 02_documents.md | 提出書類連結（cover note + 職務経歴書 + 履歴書） | ☐ 確認 PDF 為最新版本 |
| 03_shibou_doki.md | 志望動機・自己PR・轉職理由 | ☐ 朗讀並壓進 45 秒 |
| 04_shokumu.html | 客製職務経歴書（JD 最適化、2 頁） | ☐ 檢查數字與專有名詞 |

再生成: `python3 prep.py {job_id} apply`
{gap}""",
        "ja": """# 応募パック — {company} / {title}

生成日: {date} ｜ job_id: {job_id} ｜ score: {score}
求人URL: {url}

| ファイル | 内容 | 確認 |
|---|---|---|
| 01_company_brief.md | 会社調査 — 応募判断の材料 | ☐ 事実確認・Go/No-Go 判断 |
| 02_documents.md | 提出書類リンク（cover note + 職務経歴書 + 履歴書） | ☐ PDF が最新版か確認 |
| 03_shibou_doki.md | 志望動機・自己PR・転職理由 | ☐ 音読して 45 秒に収める |
| 04_shokumu.html | カスタム職務経歴書（JD 最適化・2頁） | ☐ 数字・固有名詞を確認 |

再生成: `python3 prep.py {job_id} apply`
{gap}""",
        "en": """# Application Pack — {company} / {title}

Generated: {date} | job_id: {job_id} | score: {score}
Job URL: {url}

| File | Content | Action |
|---|---|---|
| 01_company_brief.md | Company brief for apply/no-apply judgment | ☐ Verify facts and decide Go/No-Go |
| 02_documents.md | Submission document links (cover note + shokumu + rirekisho) | ☐ Confirm the latest PDF versions |
| 03_shibou_doki.md | Motivation, self-PR, and reason for change | ☐ Read aloud and fit within 45 seconds |
| 04_shokumu.html | Tailored shokumu (JD-optimized, 2 pages) | ☐ Check numbers and proper nouns |

Regenerate: `python3 prep.py {job_id} apply`
{gap}""",
    },
    "interview_readme": {
        "zh": """# 面接包 — {company} / {title}

生成日: {date} ｜ job_id: {job_id} ｜ score: {score}
求人URL: {url}

| 檔案 | 內容 | 確認 |
|---|---|---|
| 01_interview_qa.md | 想定問答（公司連結 5 題 + JD 特化 12 題） | ☐ 補上 {{{{自分の事例}}}} |
| 02_checklist.md | 面試前一天 checklist | ☐ 全項目確認 |
| 03_slides.pptx | 面接スライド（slide15/16 為本 JD 特化） | ☐ 投影前過一遍內容 |

再生成: `python3 prep.py {job_id} interview`
""",
        "ja": """# 面接パック — {company} / {title}

生成日: {date} ｜ job_id: {job_id} ｜ score: {score}
求人URL: {url}

| ファイル | 内容 | 確認 |
|---|---|---|
| 01_interview_qa.md | 想定問答（会社接続 5 問 + JD 特化 12 問） | ☐ {{{{自分の事例}}}} を埋める |
| 02_checklist.md | 面接前日チェックリスト | ☐ 全項目確認 |
| 03_slides.pptx | 面接スライド（slide15/16 が本求人特化） | ☐ 投影前に内容確認 |

再生成: `python3 prep.py {job_id} interview`
""",
        "en": """# Interview Pack — {company} / {title}

Generated: {date} | job_id: {job_id} | score: {score}
Job URL: {url}

| File | Content | Action |
|---|---|---|
| 01_interview_qa.md | Interview Q&A (5 company-link questions + 12 JD-specific questions) | ☐ Fill in {{{{自分の事例}}}} |
| 02_checklist.md | Day-before interview checklist | ☐ Review every item |
| 03_slides.pptx | Interview deck (slides 15/16 tailored to this JD) | ☐ Review before presenting |

Regenerate: `python3 prep.py {job_id} interview`
""",
    },
}


def tpl(key: str, **kwargs) -> str:
    return _TPL[key][reader_lang()].format(**kwargs)
