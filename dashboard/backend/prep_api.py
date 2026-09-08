"""prep 域端點 — 面接包 / common-qa / 深掘り生成。"""
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from db import query_one
from paths import PREP_DIR, PROJECT_ROOT, QBANK_DIR, inside_project
from queries import _app_status_cols

router = APIRouter()

# ── prep 面接包 ───────────────────────────────────────────────────

# 一層下の子目錄（qa_upgrade/、2ji/ 等）は group として並べる。`_` 始まりと
# theater/ の音檔は audio/ の更に一層下なので、この掃描には掛からない。


def _scan_md(prep_dir: Path) -> list[dict]:
    """pack_dir 直下 + 一層下の子目錄の *.md / *.mp3 を、実際のファイル名で返す（白名單に依存しない）。

    `*.bak.md` は改稿前のバックアップ（例：01_interview_qa.bak.md）。
    一覧に出すと古い問答を開いてしまうので除外する。
    """
    def _entries(d: Path, group: str, prefix: str = "") -> list[dict]:
        out = [
            {"name": f"{prefix}{p.name}", "group": group, "type": "md"}
            for p in sorted(d.glob("*.md"))
            if not p.name.endswith(".bak.md")
        ]
        out += [
            {"name": f"{prefix}{p.name}", "group": group, "type": "mp3"}
            for p in sorted(d.glob("*.mp3"))
        ]
        return out

    files = _entries(prep_dir, "main")
    for sub in sorted(x for x in prep_dir.iterdir()
                      if x.is_dir() and not x.name.startswith((".", "_"))):
        files += _entries(sub, sub.name, prefix=f"{sub.name}/")
    return files


@router.get("/api/prep")
def prep_list(page: int = 1, size: int = 9):
    dirs = sorted([p for p in PREP_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")])
    items = []
    for p in dirs:
        job_id = int(p.name.split("_")[0]) if p.name[:1].isdigit() else None
        job = query_one(
            f"SELECT jobs.title, jobs.company, jobs.score, jobs.salary_min, jobs.salary_max, "
            f"jobs.employee_count, jobs.mentions_ai, cr.openwork_score, cr.openwork_url, {_app_status_cols('jobs')} "
            f"FROM jobs LEFT JOIN company_ratings cr ON cr.company_name = jobs.company "
            f"WHERE jobs.id = ? AND COALESCE(jobs.blacklisted, 0) = 0", (job_id,)) if job_id else None
        if job_id and not job:
            continue  # 黑名單企業（或 job 不存在）— 不顯示
        items.append({
            "dir": p.name, "path": str(p), "job_id": job_id, "job": job,
            "app_status": job["app_status"] if job else None,
            "company_app_status": job["company_app_status"] if job else None,
            "company_applied_job_id": job["company_applied_job_id"] if job else None,
            "files": _scan_md(p), "has_slides": (p / "04_slides.html").exists(),
        })
    total = len(items)
    items = items[(page - 1) * size: page * size]
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/api/prep/{dirname}/file")
def prep_file(dirname: str, name: str):
    pack_dir = (PREP_DIR / dirname).resolve()
    p = (pack_dir / name).resolve()
    try:
        p.relative_to(pack_dir)
    except ValueError:
        raise HTTPException(403)
    if not inside_project(p) or not p.is_file() or p.suffix not in (".md", ".html", ".json"):
        raise HTTPException(403)
    return {"content": p.read_text(encoding="utf-8")}


@router.get("/api/prep/{dirname}/audio")
def prep_audio(dirname: str, name: str):
    """pack 内の mp3（面接音声・ロールプレイ音声）をそのまま返す。"""
    pack_dir = (PREP_DIR / dirname).resolve()
    p = (pack_dir / name).resolve()
    try:
        p.relative_to(pack_dir)
    except ValueError:
        raise HTTPException(403)
    if not inside_project(p) or not p.is_file() or p.suffix != ".mp3":
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mpeg")


@router.get("/api/prep/{dirname}/slides")
def prep_slides(dirname: str):
    p = (PREP_DIR / dirname / "04_slides.html").resolve()
    if not inside_project(p) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@router.post("/api/prep/{job_id}/generate")
def generate_prep(job_id: int):
    """面試包生成（想定問答 + checklist）— 收到面試邀請後才跑。"""
    from prep import _load_job, _slug, _write_interview_readme, INTERVIEW_STAGES, INTERVIEW_DIR, ROOT
    from tracker.db import update_pack_status

    try:
        job = _load_job(job_id)
    except SystemExit:
        raise HTTPException(404, f"job_id {job_id} not found")

    prep_dir = INTERVIEW_DIR / f"{job['id']}_{_slug(job.get('company') or '')}"
    prep_dir.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    for name, stage_fn in INTERVIEW_STAGES.items():
        try:
            stage_fn(job, "", prep_dir, False)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")

    _write_interview_readme(job, prep_dir)
    update_pack_status(job_id, "interview", str(prep_dir.relative_to(ROOT)))

    result = {"ok": len(errors) == 0, "prep_dir": str(prep_dir.relative_to(ROOT))}
    if errors:
        result["errors"] = errors
    return result


# ── common-qa ────────────────────────────────────────────────────


def _core_sections() -> list[dict] | None:
    """正典題庫（question-bank/core/）を section 形状で返す。未生成なら None。"""
    from interview.qa import bank
    from interview.qa.taxonomy import CATEGORIES

    answers = bank.load_core()
    if not answers:
        return None
    sections: list[dict] = []
    for answer in answers:
        sections.append({
            "title": f"{answer.qid}. {answer.question}",
            "body": answer.body,
            "num": answer.qid,
            "category": CATEGORIES.get(answer.category, answer.category),
            "drilldown": [],
        })
    return sections


@router.get("/api/common-qa")
def common_qa():
    """正典題庫を返す。未移行の環境では旧 common_qa.md へ落ちる。"""
    import re
    core = _core_sections()
    if core is not None:
        return {"sections": core, "source": "core"}

    p = QBANK_DIR / "common_qa.md"
    if not p.exists():
        raise HTTPException(404, "common_qa.md not found")
    text = p.read_text(encoding="utf-8")
    sections: list[dict] = []
    parts = re.split(r"^## (\d+\.\s.+)$", text, flags=re.MULTILINE)
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        num_match = re.match(r"(\d+)", title)
        sec_num = num_match.group(1) if num_match else ""
        sections.append({"title": title, "body": body, "num": sec_num, "drilldown": []})

    dd_path = QBANK_DIR / "common_qa_drilldown.md"
    if dd_path.exists():
        dd_text = dd_path.read_text(encoding="utf-8")
        dd_sections = re.split(r"^## (\d+)\s*$", dd_text, flags=re.MULTILINE)
        dd_map: dict[str, list[dict]] = {}
        for j in range(1, len(dd_sections), 2):
            sec_num = dd_sections[j].strip()
            body = dd_sections[j + 1] if j + 1 < len(dd_sections) else ""
            items: list[dict] = []
            dd_parts = re.split(r"^### (D\d+\.\s.+)$", body, flags=re.MULTILINE)
            for k in range(1, len(dd_parts), 2):
                q_title = dd_parts[k].strip()
                q_body = dd_parts[k + 1].strip() if k + 1 < len(dd_parts) else ""
                items.append({"question": q_title, "answer": q_body})
            dd_map[sec_num] = items
        for sec in sections:
            sec["drilldown"] = dd_map.get(sec["num"], [])

    return {"sections": sections, "source": "legacy"}


# ── drilldown generate ───────────────────────────────────────────


@router.post("/api/drilldown/generate")
def drilldown_generate(body: dict = Body(...)):
    """LLM 生成深掘り反問。source=common_qa | prep, prep_dir=105_VOLTMIND"""
    from tools.drilldown_gen import run as drilldown_run

    source = body.get("source", "common_qa")
    force = body.get("force", False)

    if source == "common_qa":
        input_path = QBANK_DIR / "common_qa.md"
    elif source == "prep":
        prep_dir = body.get("prep_dir", "")
        if not prep_dir:
            raise HTTPException(400, "prep_dir required")
        input_path = PREP_DIR / prep_dir / "03_interview_qa.md"
    else:
        raise HTTPException(400, f"unknown source: {source}")

    if not input_path.exists():
        raise HTTPException(404, f"{input_path.name} not found")

    out = drilldown_run(input_path, force=force)
    return {"ok": True, "output": str(out)}
