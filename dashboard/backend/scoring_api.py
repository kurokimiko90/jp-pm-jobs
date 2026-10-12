"""scoring 域端點 — 評分基準 / 調校建議 / 互動調權預覽。"""
import json

from fastapi import APIRouter, Body, HTTPException

from db import query
from scoring_meta import WEIGHTS, scoring_payload, tuning_suggestions

router = APIRouter()

# ── scoring 基準 + 互動調權預覽 ───────────────────────────────────


@router.get("/api/scoring")
def scoring():
    return scoring_payload()


@router.get("/api/scoring/suggestions")
def scoring_suggestions():
    """拒絕原因碼累積足量後的調校建議（純規則，見 docs/dashboard-uiux-redesign-plan.md §1.4）。"""
    return tuning_suggestions()


@router.post("/api/rescore")
def rescore(weights: dict = Body(...)):
    """以自訂權重重算 top 預覽。近似法：用 score/base 比值還原處罰係數後套新權重。"""
    total_w = sum(float(weights.get(k, 0)) for k in WEIGHTS)
    if total_w <= 0:
        raise HTTPException(400, "weights total must be > 0")
    rows = query(
        "SELECT id, title, company, score, tier, score_breakdown FROM jobs "
        "WHERE score_breakdown IS NOT NULL AND score IS NOT NULL AND COALESCE(blacklisted, 0) = 0")
    out = []
    for r in rows:
        try:
            bd = json.loads(r["score_breakdown"])
        except (json.JSONDecodeError, TypeError):
            continue
        base = sum(float(bd.get(k, 0) or 0) * WEIGHTS[k] for k in WEIGHTS) / 100
        factor = (r["score"] / base) if base > 0 else 1.0
        new = sum(float(bd.get(k, 0) or 0) * float(weights.get(k, 0)) for k in WEIGHTS) / total_w
        out.append({"id": r["id"], "title": r["title"], "company": r["company"],
                    "tier": r["tier"], "old_score": r["score"],
                    "new_score": round(min(new * factor, 100), 1)})
    out.sort(key=lambda x: -x["new_score"])
    return {"items": out[:50], "note": "Penalty factor is approximated from score/base"}
