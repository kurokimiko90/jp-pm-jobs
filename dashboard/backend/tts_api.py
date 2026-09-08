from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse

try:
    from paths import PROJECT_ROOT
except ModuleNotFoundError:
    from dashboard.backend.paths import PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tts import health
from tts.service import generate_audio, list_audio, list_content, resolve_pack_dir

router = APIRouter(prefix="/api/tts")


@router.get("/providers")
def tts_providers():
    return health()


@router.get("/content")
def tts_content(kind: str = Query(..., pattern="^(prep|apply)$"),
                dirname: str = Query(...),
                preset: str = Query("core")):
    try:
        return list_content(kind, dirname, preset=preset)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/list")
def tts_list(kind: str = Query(..., pattern="^(prep|apply)$"),
             dirname: str = Query(...)):
    try:
        return list_audio(kind, dirname)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/file")
def tts_file(kind: str = Query(..., pattern="^(prep|apply)$"),
             dirname: str = Query(...),
             name: str = Query(...)):
    try:
        pack_dir = resolve_pack_dir(kind, dirname)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    path = (pack_dir / "audio" / name).resolve()
    try:
        path.relative_to((pack_dir / "audio").resolve())
    except ValueError as exc:
        raise HTTPException(403, "invalid audio path") from exc
    if not path.is_file():
        raise HTTPException(404, "audio file not found")
    return FileResponse(path)


@router.post("/generate")
def tts_generate(payload: dict = Body(...)):
    try:
        kind = payload["kind"]
        dirname = payload["dirname"]
        preset = payload.get("preset", "core")
        section_ids = payload.get("section_ids")
        voice = payload.get("voice", "")
        lang = payload.get("lang", "ja")
        audio_format = payload.get("audio_format", "")
        force = bool(payload.get("force", False))
        return generate_audio(
            kind,
            dirname,
            preset=preset,
            section_ids=section_ids,
            voice=voice,
            lang=lang,
            audio_format=audio_format,
            force=force,
        )
    except KeyError as exc:
        raise HTTPException(400, f"missing field: {exc.args[0]}") from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
