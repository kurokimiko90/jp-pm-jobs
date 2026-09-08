"""resume 域端點 — 履歷版本 / profile 摘要 / tailored 定製化文件 / 投遞包。"""
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from db import query
from paths import APPLY_DIR, PROJECT_ROOT, RESUME_VERSIONS, TAILORED_DIR, inside_project
from queries import _app_status_cols

router = APIRouter()

# ── resume ────────────────────────────────────────────────────────


@router.get("/api/resume")
def resume():
    return {"versions": [
        {"key": k, "label": v["label"],
         "html_url": f"/api/resume/{k}/html" if v["html"].exists() else None,
         "pdf": str(v["pdf"]) if v["pdf"] and v["pdf"].exists() else None,
         "folder": str(v["html"].parent)}
        for k, v in RESUME_VERSIONS.items()]}


@router.get("/api/resume/{key}/html")
def resume_html(key: str):
    v = RESUME_VERSIONS.get(key)
    if not v or not v["html"].exists():
        raise HTTPException(404)
    return FileResponse(v["html"])


# ── profile-summary ──────────────────────────────────────────────


@router.get("/api/profile-summary")
def profile_summary():
    """candidate_profile.yaml から基礎情報を返す。"""
    import yaml
    p = PROJECT_ROOT / "data" / "candidate_profile.yaml"
    if not p.exists():
        raise HTTPException(404, "candidate_profile.yaml not found")
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    identity = data.get("identity", {})
    positioning = data.get("positioning", {})
    compensation = data.get("compensation", {})
    languages = data.get("languages", {})
    education = data.get("education", [])
    certifications = data.get("certifications", [])
    domains = data.get("domains", {})

    from tools.deid import load_resume_contact
    rc = load_resume_contact()
    return {
        "name_ja": rc.get("name_ja"),
        "name_romaji": rc.get("name_romaji"),
        "base": identity.get("base"),
        "visa_status": identity.get("visa_status"),
        "years_in_product": identity.get("years_in_product"),
        "years_total_career": identity.get("years_total_career"),
        "title": positioning.get("title"),
        "tagline": positioning.get("tagline"),
        "one_liner": positioning.get("one_liner"),
        "seniority_fit": positioning.get("seniority_fit", []),
        "desired_annual": compensation.get("desired_annual"),
        "desired_min": compensation.get("desired_min"),
        "scorer_target_min": compensation.get("scorer_target_min"),
        "scorer_target_max": compensation.get("scorer_target_max"),
        "languages": languages,
        "education": education,
        "certifications": certifications,
        "domains_strong": domains.get("strong", []),
        "domains_solid": domains.get("solid", []),
    }


# ── tailored 定製化文件 ──────────────────────────────────────────


@router.get("/api/tailored")
def tailored_list():
    """列出 output/tailored/ 下的定製化文件，按職缺分組。"""
    if not TAILORED_DIR.exists():
        return {"groups": []}
    import re
    files = sorted(TAILORED_DIR.glob("*"), key=lambda p: p.name)
    groups: dict[str, dict] = {}
    for f in files:
        if f.suffix not in (".md", ".html"):
            continue
        # 新格式: 014_recruit_cover.md → prefix=014, type=cover
        # 舊格式: 0145_株式会社サンプル_mega_venture.md → prefix=0145
        m = re.match(r"^(\d+)_(.+?)_(summary|cover|slides)\.", f.name)
        if m:
            prefix = m.group(1)
            company = m.group(2)
            ftype = m.group(3)
        else:
            prefix = f.name.split("_")[0]
            company = f.stem.split("_", 1)[1] if "_" in f.stem else f.stem
            ftype = "doc"
        if prefix not in groups:
            groups[prefix] = {"prefix": prefix, "company": company, "files": [], "has_trio": False}
        kind = "slides" if f.suffix == ".html" else "md"
        groups[prefix]["files"].append({"name": f.name, "kind": kind, "label": ftype})
        if ftype in ("summary", "cover", "slides"):
            groups[prefix]["has_trio"] = True
            groups[prefix]["company"] = company
    # 三件套的排前面
    result = sorted(groups.values(), key=lambda g: (not g["has_trio"], g["prefix"]))
    return {"groups": result}


@router.get("/api/tailored/{filename}")
def tailored_file(filename: str):
    p = (TAILORED_DIR / filename).resolve()
    if not inside_project(p) or not p.is_file():
        raise HTTPException(404)
    if p.suffix == ".html":
        return FileResponse(p)
    return {"content": p.read_text(encoding="utf-8")}


# ── 投遞準備（apply pack + tailored 統合） ─────────────────────────


@router.get("/api/apply-packs")
def apply_packs(source: str = "", posting_type: str = ""):
    """統合 output/apply/ と output/tailored/ を job_id ベースで一覧。"""
    import re
    packs: dict[int, dict] = {}

    if APPLY_DIR.exists():
        for d in sorted(APPLY_DIR.iterdir()):
            if not d.is_dir():
                continue
            m = re.match(r"^(\d+)_(.+)$", d.name)
            if not m:
                continue
            jid = int(m.group(1))
            files = []
            for f in sorted(d.iterdir()):
                if f.name.startswith("_") or f.name == "00_README.md":
                    continue
                if f.suffix in (".md", ".html", ".json"):
                    label = f.stem.split("_", 1)[1] if "_" in f.stem else f.stem
                    files.append({"name": f.name, "path": str(f.relative_to(PROJECT_ROOT)), "label": label, "kind": "html" if f.suffix == ".html" else "md"})
            if files:
                mtimes = [f.stat().st_mtime for f in d.iterdir() if f.is_file()]
                created_ts = d.stat().st_birthtime if hasattr(d.stat(), "st_birthtime") else (min(mtimes) if mtimes else d.stat().st_mtime)
                updated_ts = max(mtimes) if mtimes else d.stat().st_mtime
                packs[jid] = {
                    "job_id": jid, "slug": m.group(2), "apply_files": files, "tailored_files": [],
                    "created_at": datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M"),
                    "updated_at": datetime.fromtimestamp(updated_ts).strftime("%Y-%m-%d %H:%M"),
                }

    if TAILORED_DIR.exists():
        for f in sorted(TAILORED_DIR.glob("*")):
            if f.suffix not in (".md", ".html"):
                continue
            m = re.match(r"^(\d+)_", f.name)
            if not m:
                continue
            jid = int(m.group(1))
            if jid not in packs:
                slug = f.stem.split("_", 1)[1] if "_" in f.stem else ""
                ft = f.stat().st_mtime
                ts = datetime.fromtimestamp(ft).strftime("%Y-%m-%d %H:%M")
                packs[jid] = {"job_id": jid, "slug": slug, "apply_files": [], "tailored_files": [],
                              "created_at": ts, "updated_at": ts}
            label = "cover note"
            packs[jid]["tailored_files"].append({"name": f.name, "path": str(f.relative_to(PROJECT_ROOT)), "label": label, "kind": "html" if f.suffix == ".html" else "md"})

    if not packs:
        return {"packs": []}

    jids = list(packs.keys())
    placeholders = ",".join("?" * len(jids))
    rows = query(
        f"SELECT jobs.id, jobs.title, jobs.company, jobs.score, jobs.tier, jobs.recommend_score, jobs.posting_type, jobs.location, jobs.source, "
        f"jobs.employee_count, jobs.mentions_ai, cr.openwork_score, cr.openwork_url, "
        f"liveness_status, "
        f"json_extract(gap_analysis, '$.verdict') as gap_verdict, "
        f"json_extract(gap_analysis, '$.matched') as gap_matched, "
        f"json_extract(gap_analysis, '$.gaps') as gap_gaps, "
        f"json_extract(gap_analysis, '$.recommend_reason') as gap_reason, "
        f"{_app_status_cols('jobs')} "
        f"FROM jobs LEFT JOIN company_ratings cr ON cr.company_name = jobs.company WHERE jobs.id IN ({placeholders}) "
        f"AND COALESCE(jobs.liveness_status, 'active') != 'expired' AND COALESCE(jobs.blacklisted, 0) = 0", jids)
    job_map = {r["id"]: r for r in rows}

    src_filter = set(source.split(",")) if source else set()
    result = []
    for jid, pack in sorted(packs.items(), key=lambda x: -(job_map.get(x[0], {}).get("score", 0) or 0)):
        if jid not in job_map:
            continue  # expired または DB 不在
        j = job_map.get(jid, {})
        if src_filter and j.get("source", "") not in src_filter:
            continue
        if posting_type and (j.get("posting_type") or "direct") != posting_type:
            continue
        pack["title"] = j.get("title", "")
        pack["company"] = j.get("company", pack["slug"])
        pack["score"] = j.get("score")
        pack["tier"] = j.get("tier", "unknown")
        pack["recommend_score"] = j.get("recommend_score")
        pack["posting_type"] = j.get("posting_type")
        pack["source"] = j.get("source")
        pack["location"] = j.get("location")
        pack["employee_count"] = j.get("employee_count")
        pack["mentions_ai"] = j.get("mentions_ai")
        pack["openwork_score"] = j.get("openwork_score")
        pack["openwork_url"] = j.get("openwork_url")
        pack["app_status"] = j.get("app_status")
        pack["company_app_status"] = j.get("company_app_status")
        pack["company_applied_job_id"] = j.get("company_applied_job_id")
        pack["gap_verdict"] = j.get("gap_verdict")
        pack["gap_reason"] = j.get("gap_reason")
        try:
            pack["gap_matched"] = json.loads(j["gap_matched"]) if j.get("gap_matched") else None
        except (json.JSONDecodeError, TypeError):
            pack["gap_matched"] = None
        try:
            pack["gap_gaps"] = json.loads(j["gap_gaps"]) if j.get("gap_gaps") else None
        except (json.JSONDecodeError, TypeError):
            pack["gap_gaps"] = None
        total = len(pack["apply_files"]) + len(pack["tailored_files"])
        has_brief = any("brief" in f["label"] for f in pack["apply_files"])
        has_shibou = any("shibou" in f["label"] for f in pack["apply_files"])
        pack["completeness"] = {"total": total, "has_brief": has_brief, "has_shibou": has_shibou}
        result.append(pack)

    return {"packs": result}


@router.get("/api/apply-file")
def apply_file(path: str = Query(...)):
    p = (PROJECT_ROOT / path).resolve()
    if not inside_project(p) or not p.is_file():
        raise HTTPException(404)
    if p.suffix == ".html":
        return FileResponse(p)
    return {"content": p.read_text(encoding="utf-8")}
