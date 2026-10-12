"""公司／產品階段訊號偵測。

抽取規模、成熟產品／穩定客戶、AI 導入／產品升級三項事實，供
gap_analyzer 的 weighted_v2「company_product_stage（15%）」維度使用。
此模組仍保留 bonus 欄位，讓既有 JSON／舊工具可讀；正式推薦分不再把它
當成獨立大額加成。
"""

from __future__ import annotations

import re

from tools.app_config import get as _cfg

_SS = (_cfg("scoring", "gap", {}) or {}).get("sweet_spot", {}) or {}

SIZE_BONUS = int(_SS.get("size_bonus", 20))
MATURITY_BONUS = int(_SS.get("maturity_bonus", 15))
AI_UPGRADE_BONUS = int(_SS.get("ai_upgrade_bonus", 15))
EMPLOYEES_MIN = int(_SS.get("employees_min", 100))
# JD 抽不到従業員數 → bonus −2（資訊缺失的輕度懲罰）
SIZE_MISSING_PENALTY = int(_SS.get("size_missing_penalty", 2))
# 三條件全不命中 → recommend_score 乘此係數（甜蜜點外的職缺整體降 10%）
NO_HIT_PENALTY = float(_SS.get("no_hit_penalty", 0.9))

# 成熟產品/穩定客戶訊號。「上場」單獨使用會被「未上場」誤觸發，
# 一律用帶修飾的組合詞（東証/プライム上場 等）。
MATURITY_KEYWORDS = _SS.get("maturity_keywords", [
    "導入実績", "導入社数", "導入企業", "導入事例", "取引実績",
    "顧客基盤", "継続率", "解約率", "チャーンレート",
    "arr", "mrr", "シェアno.1", "シェアno1", "トップシェア", "業界シェア",
    "東証", "プライム上場", "グロース上場", "上場企業", "マザーズ",
    "大手企業", "有料契約", "黒字",
])

# AI 導入/產品升級「進行中」訊號 —— 與 tier1/tier2 領域關鍵字（比對 LLM
# matched 文字）不同，這裡比對 raw_jd 原文，抓的是轉型動作而非領域標籤。
AI_UPGRADE_KEYWORDS = _SS.get("ai_upgrade_keywords", [
    "ai導入", "aiの導入", "ai活用", "aiを活用", "ai機能",
    "生成ai活用", "生成aiを", "生成aiの活用", "llm活用", "aiエージェント",
    "dx推進", "リプレイス", "リアーキテク", "リニューアル", "刷新",
    "モダナイズ", "レガシー", "第二創業", "プロダクト再構築",
])

_TAG_RE = re.compile(r"【企業データ】従業員数[:：]?(\d+)名")
_LOOSE_RE = re.compile(r"(?:従業員数?|社員数)[^\d]{0,6}([0-9,，]{2,7})\s*(?:名|人)")


def _normalize(text: str) -> str:
    """全角英数字→半角 + 小文字化（jd_scorer と同方針）。"""
    out = []
    for ch in text:
        cp = ord(ch)
        out.append(chr(cp - 0xFEE0) if 0xFF01 <= cp <= 0xFF5E else ch)
    return "".join(out).lower()


def _hit(blob: str, keyword: str) -> bool:
    """ASCII 詞用單詞邊界比對（防 arr 誤中 arrange），CJK 詞用子字串。"""
    kw = keyword.lower()
    if re.fullmatch(r"[a-z0-9.]+", kw):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", blob))
    return kw in blob


def extract_employees(raw_jd: str | None) -> int | None:
    """従業員數抽取：green 的【企業データ】標籤優先，退寬鬆正則（取首個命中）。"""
    if not raw_jd:
        return None
    text = _normalize(raw_jd)
    m = _TAG_RE.search(text) or _LOOSE_RE.search(text)
    if not m:
        return None
    return int(m.group(1).replace(",", "").replace("，", ""))


def evaluate(raw_jd: str | None) -> dict:
    """三條件分別評分。回傳 dict 直接存入 gap_analysis JSON 供 dashboard 溯源。"""
    blob = _normalize(raw_jd or "")
    employees = extract_employees(raw_jd)

    size = employees is not None and employees >= EMPLOYEES_MIN
    maturity = any(_hit(blob, k) for k in MATURITY_KEYWORDS)
    ai_upgrade = any(_hit(blob, k) for k in AI_UPGRADE_KEYWORDS)

    bonus = (
        (SIZE_BONUS if size else 0)
        + (MATURITY_BONUS if maturity else 0)
        + (AI_UPGRADE_BONUS if ai_upgrade else 0)
        - (SIZE_MISSING_PENALTY if employees is None else 0)
    )
    return {
        "employees": employees,
        "size": size,
        "maturity": maturity,
        "ai_upgrade": ai_upgrade,
        "bonus": bonus,
    }
