"""投遞策略域端點 — 策略設定 / 波次列表。"""
from fastapi import APIRouter, HTTPException

from db import query, query_one
from paths import APPLY_DIR, PROJECT_ROOT

router = APIRouter()

# ── 投遞策略設定 ─────────────────────────────────────────────────

STRATEGY_YAML = PROJECT_ROOT / "apply_strategy.yaml"


@router.get("/api/strategy-config")
def strategy_config():
    if not STRATEGY_YAML.exists():
        raise HTTPException(404, "apply_strategy.yaml not found")
    import yaml
    with open(STRATEGY_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── 投遞策略 ─────────────────────────────────────────────────────


def _live_pack_ids() -> set[int]:
    """掃描 output/apply/ 目錄，回傳已有投遞包的 job_id 集合。"""
    import re as _re
    ids: set[int] = set()
    if APPLY_DIR.exists():
        for d in APPLY_DIR.iterdir():
            if d.is_dir():
                m = _re.match(r"^(\d+)_", d.name)
                if m:
                    ids.add(int(m.group(1)))
    return ids


@router.get("/api/strategy")
def strategy(source: str = "", posting_type: str = ""):
    extra_where = []
    extra_params: list = []
    if source:
        ss = source.split(",")
        extra_where.append(f"j.source IN ({','.join('?' * len(ss))})")
        extra_params += ss
    if posting_type:
        extra_where.append("COALESCE(j.posting_type,'direct') = ?")
        extra_params.append(posting_type)
    extra = (" AND " + " AND ".join(extra_where)) if extra_where else ""
    waves = query(
        "SELECT w.id, w.wave, w.job_id, w.rank, w.weighted, w.pack_ready, w.status, w.created_at, "
        "j.title, j.company, j.score, j.tier, j.posting_type, j.location, j.source, "
        "j.employee_count, j.mentions_ai, cr.openwork_score, cr.openwork_url, "
        "CAST(json_extract(j.gap_analysis, '$.recommend_score') AS INTEGER) recommend_score "
        "FROM apply_waves w JOIN jobs j ON j.id = w.job_id "
        "LEFT JOIN company_ratings cr ON cr.company_name = j.company "
        f"WHERE COALESCE(j.liveness_status, 'active') != 'expired' AND COALESCE(j.blacklisted, 0) = 0{extra} "
        "ORDER BY w.wave, w.rank",
        tuple(extra_params) if extra_params else ()
    )
    today_applied = query_one(
        "SELECT COUNT(*) n FROM applications WHERE applied_at = date('now')"
    )["n"]
    total_applied = query_one("SELECT COUNT(*) n FROM applications")["n"]
    replied = query_one(
        "SELECT COUNT(*) n FROM applications WHERE status != 'applied'"
    )["n"]

    live_packs = _live_pack_ids()

    by_wave: dict[int, list] = {}
    for w in waves:
        row = dict(w)
        row["pack_ready"] = row["job_id"] in live_packs
        by_wave.setdefault(row["wave"], []).append(row)

    return {
        "waves": by_wave,
        "today_applied": today_applied,
        "total_applied": total_applied,
        "replied": replied,
    }
