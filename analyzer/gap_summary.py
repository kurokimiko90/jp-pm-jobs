"""批次推薦度報告生成器 — 把一批 gap 分析橫向聚合成結構化 summary_json。

每次 gap_analyzer 跑完一批，呼叫 summarize_batch() 生成一份報告 JSON 落庫
（gap_batches.summary_json），dashboard「推薦度報告」頁直接渲染、可切換歷史批次。

固定 schema（前端依賴，勿隨意改欄位名）：
{
  "portrait": "markdown 整體畫像",
  "common_gaps": [{"theme","count","severity","nature"}],
  "tiers": {"go":[{id,company,title,rec,reason}], "improve":[...], "skip":[...]},
  "actions": [{"title","detail"}]
}
"""

from __future__ import annotations

import json
import re
import time

from interview._llm import call
from tools.locale import lang_directive, reader_lang

MODEL = "claude-sonnet-4-6"

SCHEMA_HINT = """{
  "portrait": "整體畫像（markdown，含推薦度/來源/年收分布、被低估與被高估的職缺）",
  "common_gaps": [{"theme": "主題名", "count": 數字, "severity": "高|中|低", "nature": "真短板|敘事問題|混合"}],
  "tiers": {
    "go":      [{"id": 數字, "company": "", "title": "", "rec": 數字, "reason": "一句話"}],
    "improve": [{"id": 數字, "company": "", "title": "", "rec": 數字, "reason": "一句話"}],
    "skip":    [{"id": 數字, "company": "", "title": "", "rec": 數字, "reason": "一句話"}]
  },
  "actions": [{
    "title": "行動標題",
    "detail": "一句話總述：怎麼做 + 解哪些 gap",
    "type": "敘事優化|能力補強|談判準備|投遞策略",
    "roi": "高|中|低",
    "effort": "預估投入（如：1天 / 1週 / 1-3個月）",
    "steps": ["具體可執行步驟1", "步驟2", "步驟3"],
    "counter_measures": ["可能遇到的阻力/風險 → 對策"],
    "target_job_ids": [輸入中真實的職缺 id],
    "done_criteria": "完成判定標準（可驗證）"
  }]
}"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("summarizer 回傳非 JSON")


def build_prompt(profile_summary: str, items: list[dict]) -> str:
    schema_lang = "Traditional Chinese" if reader_lang() == "zh" else "Japanese" if reader_lang() == "ja" else "English"
    return f"""你是求職策略分析師。候選人畫像：
{profile_summary}

以下是一批已做 gap 分析的職缺（JSON）。每筆含 id/company/title/score(市場分)/rec(推薦度)/sal(年收)/gaps(短板清單)/reason(推薦理由)：

{json.dumps(items, ensure_ascii=False)}

Please synthesize across the batch and output only JSON that matches the schema below (content language: {schema_lang}), with no extra text:
{SCHEMA_HINT}

要求：
- portrait：點出兩極化、最佳適配層、被低估（低市場分高推薦度）與被高估（高市場分低推薦度）的職缺
- common_gaps：把所有 gaps 聚成 5-8 個主題，count=出現次數，nature 判定是「真短板」還是「敘事問題（履歷沒寫好）」
- tiers：go=推薦度≥75且無 hard blocker、推薦投遞，improve=60-74需補強再投，skip=<60或有硬性排除；每層列代表性職缺，id 必須真實來自輸入
- actions：3-5 條按投報率排序的具體行動，每條必須含詳細執行規劃：steps（3-5 個可執行步驟）、counter_measures（阻力與對策）、target_job_ids（受惠的真實職缺 id）、done_criteria（可驗證的完成標準）、type/roi/effort 分類
- 零編造，只用輸入的真實 id/數字。

{lang_directive("gap_summary")}"""


def summarize_batch(profile_summary: str, items: list[dict],
                    dry_run: bool = False) -> dict:
    """items: [{id, company, title, score, rec, sal, gaps, reason}, ...]"""
    if not items:
        return {"portrait": "（本批次無職缺）", "common_gaps": [],
                "tiers": {"go": [], "improve": [], "skip": []}, "actions": []}
    prompt = build_prompt(profile_summary, items)
    if dry_run:
        print(prompt)
        return {}
    # accept：actions 是 schema 最後一個 key，缺 = 輸出截斷 → gateway 自動換 brain
    accept = {"includesAll": ['"actions"']}
    last_err: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(5)  # gateway 偶發 500 留時間恢復，同 provider 內重試
        try:
            raw = call(prompt, timeout=240, model=MODEL, accept=accept)
        except RuntimeError as e:  # 所有 provider 失敗（如 gateway 500），非 JSON 問題
            last_err = e
            continue
        try:
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    raise last_err
