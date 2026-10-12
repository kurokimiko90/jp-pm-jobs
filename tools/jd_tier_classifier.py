"""JD 企業分類器 — 2026 日本 AI PM 市場三類分流。

分類:
- mega_venture       : メルカリ / サイバーエージェント / LINEヤフー / DeNA / リクルート 等
- traditional_sier   : NTT Data / 野村総研 (NRI) / 富士通 / 日立 / TIS / SCSK 等
- ai_startup         : Preferred Networks (PFN) / Sakana AI / ELYZA / Stability AI Japan / rinna 等
                       或 JD 含「ファウンデーションモデル」「Researcher」「LLM 基盤」等關鍵字
- unknown            : 預設

用法:
    python3 -m tools.jd_tier_classifier --all              # 全表分類
    python3 -m tools.jd_tier_classifier --job-id 123       # 單筆
    python3 -m tools.jd_tier_classifier --stats            # 只看統計
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime

from tracker.db import all_jobs, connect, stats_by_tier, update_tier, update_posting_type

# ── 公司清單（大小寫不敏感，子字串命中即可）────────────────────────
COMPANY_PATTERNS: dict[str, list[str]] = {
    "mega_venture": [
        "メルカリ", "mercari",
        "サイバーエージェント", "cyberagent",
        "linヤフー", "lineヤフー", "line yahoo", "lineヤフ",
        "dena", "ディー・エヌ・エー",
        "リクルート", "recruit",
        "wantedly",
        "smartnews",
        "gunosy",
        "freee",
        "sansan",
        "money forward", "マネーフォワード",
        "rakuten", "楽天",
        "pixiv",
        "smartHR", "smarthr",
        "フリー株式会社", "フリー 株式会社",  # freee の登記社名（カタカナ表記）
        "ラクスル株式会社", "ラクスル 株式会社",
        "ウェルスナビ",
        "アンドパッド",
    ],
    "traditional_sier": [
        "ntt data", "ntt データ", "エヌ・ティ・ティ・データ",
        "野村総研", "野村総合研究所", "nri",
        "富士通", "fujitsu",
        "日立", "hitachi",
        "tis ", "tis株式会社",
        "scsk",
        "ibm japan", "日本ibm", "日本アイ・ビー・エム",
        "アクセンチュア", "accenture",
        "アビーム", "abeam",
        "電通国際", "isid",
        "伊藤忠テクノ", "ctc",
        "nec", "日本電気",
    ],
    "ai_startup": [
        "preferred networks", "pfn",
        "sakana ai",
        "elyza",
        "stability ai",
        "rinna",
        "ubie",
        "exawizards", "エクサウィザーズ",
        "abeja",
        "matrixflow",
        "neural pocket",
        "fastlabel",
        "deepx",
        "ailia",
        "studio ousia",
        "pksha",
    ],
}

# 企業規模啟發式（白名單 + JD 線索都沒命中時的備援）——
# 只有部分來源（目前是 green）在 raw_jd 尾端附帶真實企業資料標籤
# 「【企業データ】従業員数:X名 / 上場:Y / 業種:Z / 設立:N年」，沒有標籤的
# 來源（bizreach/indeed/linkedin/recruiter_agent）不受影響，維持 unknown。
# 格式（scrapers/green.py 組裝）：【企業データ】従業員数:X名 / 上場:Y / 業種:A, B / 設立:N年
# 業種/設立為可選部件；gap（.*?）必須放在 optional 群組「內」——
# 放在群組外時 lazy gap + optional 群組會直接匹配空，業種/設立永遠捕不到。
_COMPANY_DATA_RE = re.compile(
    r"【企業データ】従業員数[:：]?(\d+)名.*?上場[:：]?([^\s/]+)"
    r"(?:.*?業種[:：]?([^/\n]+))?"
    r"(?:.*?設立[:：]?(\d{4})年)?"
)
_SIER_INDUSTRY_HINTS = ("システムインテグレータ", "ITコンサルティング")
_SCALE_MIN_EMPLOYEES = 300
_SIER_MIN_AGE_YEARS = 15


def _scale_heuristic(raw_jd: str | None, current_year: int) -> TierResult | None:
    if not raw_jd:
        return None
    m = _COMPANY_DATA_RE.search(raw_jd)
    if not m:
        return None
    employees = int(m.group(1) or 0)
    stock_name = m.group(2) or "非上場"
    industries = m.group(3) or ""
    est_year = int(m.group(4)) if m.group(4) else None
    is_listed = stock_name not in ("非上場", "")
    is_sier_industry = any(h in industries for h in _SIER_INDUSTRY_HINTS)
    is_old = est_year is not None and (current_year - est_year) >= _SIER_MIN_AGE_YEARS

    if is_sier_industry and (employees >= _SCALE_MIN_EMPLOYEES or is_old):
        return TierResult(tier="traditional_sier", conf=0.5, reason=f"scale:sier_industry+{employees}emp")
    if is_listed and employees >= _SCALE_MIN_EMPLOYEES:
        return TierResult(tier="mega_venture", conf=0.5, reason=f"scale:listed+{employees}emp")
    return None


# AI 新創內容線索（沒命中公司名時用）
AI_STARTUP_JD_HINTS = [
    "ファウンデーションモデル", "foundation model",
    "researcher", "リサーチエンジニア",
    "llm 基盤", "llm基盤", "基盤モデル",
    "事前学習", "pre-training", "pretraining",
    "ファインチューニング",
    "rlhf",
    "agentic",
    "ai エージェント開発", "ai agent development",
]


@dataclass
class TierResult:
    tier: str
    conf: float
    reason: str


def classify(company: str | None, title: str | None, raw_jd: str | None) -> TierResult:
    """規則式分類。公司名命中 → 高信心；JD hint 命中 → 中信心；否則 unknown。

    公司名比對只看 `company` 欄位本身，不混入 title/raw_jd —— 否則 JD 內文
    提到的「客戶案例」「競品」「自家產品名」會被誤判成公司本身命中
    （例如某小新創 JD 寫「客戶包含メルカリ、リクルート」被誤標成 mega_venture；
    產品名「sunrise」「connect」內含 nri/nec 子字串誤觸發 traditional_sier）。
    """
    company_blob = (company or "").lower()

    # 1. 公司名命中（最高優先，只比對 company 欄位本身）
    for tier, patterns in COMPANY_PATTERNS.items():
        for pat in patterns:
            if pat.lower() in company_blob:
                return TierResult(tier=tier, conf=0.95, reason=f"company:{pat}")

    # 2. AI 新創 JD 線索（內容線索，看全文合理）
    blob = " ".join(filter(None, [company, title, raw_jd])).lower()
    for hint in AI_STARTUP_JD_HINTS:
        if hint.lower() in blob:
            return TierResult(tier="ai_startup", conf=0.60, reason=f"jd_hint:{hint}")

    # 3. 企業規模啟發式（白名單/JD 線索都沒命中時的備援，見上方說明）
    scale_result = _scale_heuristic(raw_jd, datetime.now().year)
    if scale_result:
        return scale_result

    return TierResult(tier="unknown", conf=0.0, reason="no_match")


def classify_all() -> dict[str, int]:
    """對 DB 全表分類，寫回 tier + tier_conf。回傳分布統計。"""
    rows = all_jobs()
    updated = 0
    for row in rows:
        result = classify(row["company"], row["title"], row["raw_jd"])
        update_tier(row["id"], result.tier, result.conf)
        updated += 1
    stats = stats_by_tier()
    print(f"[tier_classifier] 已分類 {updated} 筆。分布：")
    for tier, n in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {tier:20s} {n}")
    return stats


# ── 招募類型判斷 ─────────────────────────────────────────────────────
# posting_type 三分類:
#   direct  — 企業直接招募
#   shokai  — 職業紹介（通過中介，你成為目標公司正社員）
#   haken   — 人材派遣（你掛在派遣公司，被派駐至客戶端）

import re as _re

# JD 底部「業種」欄含「人材派遣」→ 發布方是派遣公司
_HAKEN_GYOSHU = _re.compile(r'業種[\s\S]{0,10}?人材派遣')

# 公司名含這些 → 本身是派遣公司
HAKEN_COMPANY_PATTERNS = [
    "akkodis", "adecco", "manpower", "randstad",
    "pasona", "パソナ", "テンプスタッフ", "リクルートスタッフィング",
    "人材派遣",
]

# 職業紹介（中介）信號 — JD 文字
SHOKAI_JD_SIGNALS = [
    "職業紹介事業者による紹介案件",
    "応募情報は職業紹介事業者に送信されます",
    "人材紹介会社",
    "ヘッドハンター案件",
    "転職・求人情報の詳細をご覧になる場合は会員登録",
]

# 職業紹介（中介）— 公司名
SHOKAI_COMPANY_PATTERNS = [
    "talent fit", "hire pundit", "staffing", "recruiting",
    "人材紹介",
]


def classify_posting_type(company: str | None, raw_jd: str | None, source: str | None = None) -> str:
    """判斷 posting_type: 'direct' / 'shokai' / 'haken'"""
    # recruiter_agent 來源全部是仲介推薦郵件
    if source in ("recruiter_agent", "recruit_agent"):
        return "shokai"

    jd = raw_jd or ""
    comp = (company or "").lower()

    # 1. 派遣判斷（最優先）：業種欄含「人材派遣」
    if _HAKEN_GYOSHU.search(jd):
        return "haken"
    for pat in HAKEN_COMPANY_PATTERNS:
        if pat.lower() in comp:
            return "haken"

    # 2. 職業紹介（中介）判斷
    for sig in SHOKAI_JD_SIGNALS:
        if sig in jd:
            return "shokai"
    for pat in SHOKAI_COMPANY_PATTERNS:
        if pat.lower() in comp:
            return "shokai"

    return "direct"


def classify_posting_type_all() -> dict[str, int]:
    """對全表重新判斷 posting_type，寫回 DB。回傳分布統計。"""
    rows = all_jobs()
    counts: dict[str, int] = {}
    for row in rows:
        pt = classify_posting_type(row["company"], row["raw_jd"], row["source"] if "source" in row.keys() else None)
        update_posting_type(row["id"], pt)
        counts[pt] = counts.get(pt, 0) + 1
    print(f"[posting_type] 已分類 {len(rows)} 筆：haken={counts.get('haken', 0)}, shokai={counts.get('shokai', 0)}, direct={counts.get('direct', 0)}")
    return counts


def classify_one(job_id: int) -> TierResult:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise SystemExit(f"job_id {job_id} 不存在")
    result = classify(row["company"], row["title"], row["raw_jd"])
    update_tier(job_id, result.tier, result.conf)
    print(f"job_id={job_id} company={row['company']!r} → tier={result.tier} ({result.conf:.0%}, {result.reason})")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="JD 企業分類器（2026 AI PM 市場）")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="對全表分類")
    g.add_argument("--job-id", type=int, help="只分類單筆")
    g.add_argument("--stats", action="store_true", help="只看當前分布統計")
    args = parser.parse_args()

    if args.stats:
        stats = stats_by_tier()
        print("當前 tier 分布：")
        for tier, n in sorted(stats.items(), key=lambda x: -x[1]):
            print(f"  {tier:20s} {n}")
    elif args.all:
        classify_all()
    elif args.job_id is not None:
        classify_one(args.job_id)


if __name__ == "__main__":
    main()
