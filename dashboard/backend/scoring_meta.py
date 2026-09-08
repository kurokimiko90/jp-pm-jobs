"""從 analyzer/jd_scorer.py 取評分規則常數，供評分基準頁與 rescore 預覽用。"""
import sys

from paths import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT))

from analyzer.jd_scorer import (  # noqa: E402
    ENG_ONLY_PENALTY,
    OVERSEAS_WEIGHTS,
    PM_GATE_PENALTY,
    TIER_PREFERENCE,
    WEIGHTS,
)

DIM_LABELS = {
    "salary_fit": "薪資契合（日本軌主軸，目標帶 900–1800万）",
    "market_keywords": "2026 市場關鍵字（LLM / GenAI / agentic / RAG / MCP…）",
    "role_fit": "PM 職銜真實性（真 PM 核心職 vs PMM/分析/工程）",
    "tier_preference": "公司 tier 偏好（ai_startup 100 > mega 80 > sier 50 > unknown 40）",
    "tech_overlap": "技術詞與個人 tech footprint 重合",
    "domain": "領域加分（Fintech / SaaS / AI）",
    "remote_visa": "遠端/簽證（已併入 salary_fit，權重 0）",
}

ATS_SOURCES = {"greenhouse-api", "ashby-api", "lever-api", "workable-api"}

MIN_SAMPLE = 10
_REJECTED_AT_SHORUI = "shorui"


def tuning_suggestions() -> dict:
    """依應募管線標記的拒絕階段，統計各 tier 書類通過率，樣本足量時給調校建議。

    純規則（分組算通過率 + 對照權重），零 LLM 成本，跟 scorer 的設計哲學一致。
    只納入「已知是否通過書類」的紀錄：未拒絕（仍在流程中/offer）視為通過，
    已拒絕但沒補記拒絕階段的舊資料排除，避免污染分母。
    """
    from db import query

    rows = query(
        "SELECT j.tier AS tier, a.status AS status, a.rejection_stage AS rejection_stage "
        "FROM applications a JOIN jobs j ON j.id = a.job_id "
        "WHERE j.tier IS NOT NULL AND (a.status != 'rejected' OR a.rejection_stage IS NOT NULL)"
    )
    stats: dict[str, dict[str, int]] = {}
    for r in rows:
        s = stats.setdefault(r["tier"], {"total": 0, "passed": 0})
        s["total"] += 1
        if r["status"] != "rejected" or r["rejection_stage"] != _REJECTED_AT_SHORUI:
            s["passed"] += 1

    overall_total = sum(s["total"] for s in stats.values())
    overall_passed = sum(s["passed"] for s in stats.values())
    overall_rate = overall_passed / overall_total if overall_total else 0.0

    suggestions = []
    for tier, s in stats.items():
        if s["total"] < MIN_SAMPLE or overall_rate <= 0:
            continue
        rate = s["passed"] / s["total"]
        if rate >= overall_rate * 0.7:
            continue
        current = TIER_PREFERENCE.get(tier, 40)
        new_weight = max(20, round(current * 0.7 / 10) * 10)
        if new_weight >= current:
            continue
        suggestions.append({
            "tier": tier, "pass_rate": round(rate * 100), "passed": s["passed"], "total": s["total"],
            "overall_rate": round(overall_rate * 100),
            "current_weight": current, "suggested_weight": new_weight,
            "message": (
                f"{tier} 書類通過率 {round(rate * 100)}%（{s['passed']}/{s['total']}），"
                f"低於整體 {round(overall_rate * 100)}% — 建議把 tier_preference.{tier} "
                f"從 {current} 降到 {new_weight}（config/scoring.yaml）"
            ),
        })
    suggestions.sort(key=lambda x: x["pass_rate"])
    return {"suggestions": suggestions, "sample_size": overall_total, "min_sample": MIN_SAMPLE}


def scoring_payload() -> dict:
    return {
        "weights": WEIGHTS,
        "overseas_weights": OVERSEAS_WEIGHTS,
        "dim_labels": DIM_LABELS,
        "penalties": {
            "eng_only": {"factor": ENG_ONLY_PENALTY, "label": "純工程職（標題無 PM 字樣）"},
            "pm_gate": {"factor": PM_GATE_PENALTY, "label": "非 PM 內容閘（営業/コンサル/法務等沉底）"},
        },
        "tier_preference": TIER_PREFERENCE,
        "target_band": {"min": 900, "max": 1800, "unit": "万円"},
    }
