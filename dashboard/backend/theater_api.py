"""面接シアター API — 各社面接パックの台本一覧 / VNScript 配信 / 逐句音檔 / 生成觸發。

台本・音檔生成本体は tts/theater.py（管線側と共用）。本 router は配信と觸發のみ。
音檔はファイル名 = content hash（內容尋址）→ immutable cache 可。
"""
from __future__ import annotations

import json
import sys

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

try:
    from paths import PROJECT_ROOT
except ModuleNotFoundError:
    from dashboard.backend.paths import PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tts.theater import (  # noqa: E402
    PREP_DIR, QA_FILENAMES, ensure_assets, load_manifest, theater_dir,
)

router = APIRouter(prefix="/api/theater")

# 封存目錄：pack 移入後從所有列表（theater / dojo sync）消失，檔案原樣保留可還原
ARCHIVE_DIR = PREP_DIR / "_archive"


def _resolve_pack(dirname: str, root=None):
    root = (root or PREP_DIR).resolve()
    pack_dir = (root / dirname).resolve()
    if pack_dir.parent != root or not dirname or dirname.startswith("_"):
        raise HTTPException(403, "invalid pack dir")
    if not pack_dir.is_dir():
        raise HTTPException(404, f"pack not found: {dirname}")
    return pack_dir


def _job_for(dirname: str) -> dict | None:
    job_id = dirname.split("_", 1)[0]
    if not job_id.isdigit():
        return None
    from tracker.db import connect
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
    return dict(row) if row else None


@router.get("/scripts")
def theater_scripts():
    """QA md を持つ pack の一覧（script/音檔の readiness 付き）。"""
    packs = []
    if not PREP_DIR.is_dir():
        return {"packs": packs, "archived": []}
    for p in sorted(PREP_DIR.iterdir(), reverse=True):
        if not p.is_dir() or p.name.startswith("_"):
            continue
        if not any((p / n).exists() for n in QA_FILENAMES):
            continue
        script_path = theater_dir(p) / "script.json"
        meta: dict = {}
        if script_path.exists():
            try:
                meta = json.loads(script_path.read_text(encoding="utf-8")).get("_meta", {})
            except (json.JSONDecodeError, OSError):
                pass
        manifest = load_manifest(p)
        audio_dir = theater_dir(p) / "audio"
        audio_ready = sum(
            1 for v in manifest.get("items", {}).values()
            if (audio_dir / v["file"]).exists()
        )
        dirname = p.name
        packs.append({
            "dirname": dirname,
            "job_id": int(dirname.split("_", 1)[0]) if dirname.split("_", 1)[0].isdigit() else None,
            "company": meta.get("company") or (dirname.split("_", 1)[1] if "_" in dirname else dirname),
            "job_title": meta.get("jobTitle") or "",
            "questions": meta.get("questions"),
            "script_ready": script_path.exists(),
            "audio_ready": audio_ready,
        })
    archived = []
    if ARCHIVE_DIR.is_dir():
        for p in sorted(ARCHIVE_DIR.iterdir(), reverse=True):
            if p.is_dir():
                archived.append({
                    "dirname": p.name,
                    "company": p.name.split("_", 1)[1] if "_" in p.name else p.name,
                })
    return {"packs": packs, "archived": archived}


@router.post("/archive")
def theater_archive(payload: dict = Body(...)):
    """pack を封存（→ _archive/）/ 還原（restore: true）。刪除はしない — 檔案原樣移動。"""
    dirname = payload.get("dirname") or ""
    restore = bool(payload.get("restore", False))
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if restore:
        src = _resolve_pack(dirname, root=ARCHIVE_DIR)
        dst = PREP_DIR / dirname
    else:
        src = _resolve_pack(dirname)
        dst = ARCHIVE_DIR / dirname
    if dst.exists():
        raise HTTPException(409, f"already exists: {dst.name}")
    src.rename(dst)
    return {"dirname": dirname, "archived": not restore}


@router.get("/script")
def theater_script(dirname: str):
    """VNScript JSON（audioBase + 逐句 audio ファイル名をマージ済み）。"""
    pack_dir = _resolve_pack(dirname)
    script_path = theater_dir(pack_dir) / "script.json"
    if not script_path.exists():
        raise HTTPException(404, "script not built — POST /api/theater/build first")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    items = load_manifest(pack_dir).get("items", {})
    audio_dir = theater_dir(pack_dir) / "audio"
    for act in script["acts"]:
        for line in act["lines"]:
            entry = items.get(line["id"])
            if entry and (audio_dir / entry["file"]).exists():
                line["audio"] = entry["file"]
                if entry.get("durationMs") is not None:
                    line["durationMs"] = entry["durationMs"]
    script["audioBase"] = f"/api/theater/audio/{dirname}"
    return {"script": script}


@router.post("/build")
def theater_build(payload: dict = Body(...)):
    """script + 逐句音檔生成（同期 — 40句で1分弱かかりうる。管線側で事前生成が本線）。"""
    dirname = payload.get("dirname") or ""
    pack_dir = _resolve_pack(dirname)
    try:
        result = ensure_assets(
            pack_dir, _job_for(dirname),
            synth_audio=bool(payload.get("audio", True)),
            force=bool(payload.get("force", False)),
            log=lambda *_: None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "dirname": dirname,
        "questions": result["questions"],
        "lines_total": result["lines_total"],
        "audio_done": result["audio_done"],
        "audio_errors": result["audio_errors"],
    }


@router.get("/audio/{dirname}/{name}")
def theater_audio(dirname: str, name: str):
    pack_dir = _resolve_pack(dirname)
    audio_dir = (theater_dir(pack_dir) / "audio").resolve()
    path = (audio_dir / name).resolve()
    try:
        path.relative_to(audio_dir)
    except ValueError as exc:
        raise HTTPException(403, "invalid audio path") from exc
    if not path.is_file():
        raise HTTPException(404, "audio not found")
    media_type = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
    # ファイル名 = content hash → 中身が変われば URL も変わる。immutable で恒久 cache
    return FileResponse(path, media_type=media_type,
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
