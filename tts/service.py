from __future__ import annotations

import json
import uuid
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from . import synthesize
from .content import NarrationSection, collect_sections

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREP_DIR = PROJECT_ROOT / "output" / "prep"
APPLY_DIR = PROJECT_ROOT / "output" / "apply"
ROOTS = {"prep": PREP_DIR, "apply": APPLY_DIR}


def resolve_pack_dir(kind: str, dirname: str) -> Path:
    root = ROOTS.get(kind)
    if root is None:
        raise ValueError(f"unsupported kind: {kind}")
    pack_dir = (root / dirname).resolve()
    try:
        pack_dir.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("pack dir escapes root") from exc
    if not pack_dir.is_dir():
        raise FileNotFoundError(f"pack not found: {dirname}")
    return pack_dir


def audio_dir_for(pack_dir: Path) -> Path:
    return pack_dir / "audio"


def manifest_path_for(pack_dir: Path) -> Path:
    return audio_dir_for(pack_dir) / "manifest.json"


def load_manifest(pack_dir: Path) -> dict:
    path = manifest_path_for(pack_dir)
    if not path.exists():
        return {"version": 1, "items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def list_content(kind: str, dirname: str, preset: str = "core") -> dict:
    pack_dir = resolve_pack_dir(kind, dirname)
    sections = [section.to_dict() for section in collect_sections(kind, pack_dir, preset=preset)]
    return {"kind": kind, "dirname": dirname, "preset": preset, "sections": sections}


def list_audio(kind: str, dirname: str) -> dict:
    pack_dir = resolve_pack_dir(kind, dirname)
    manifest = load_manifest(pack_dir)
    return {
        "kind": kind,
        "dirname": dirname,
        "audio_dir": str(audio_dir_for(pack_dir).relative_to(PROJECT_ROOT)),
        "items": manifest.get("items", []),
    }


def _select_sections(sections: list[NarrationSection], section_ids: list[str] | None) -> list[NarrationSection]:
    if not section_ids:
        return sections
    index = {section.id: section for section in sections}
    missing = [section_id for section_id in section_ids if section_id not in index]
    if missing:
        raise ValueError(f"unknown section ids: {', '.join(missing)}")
    return [index[section_id] for section_id in section_ids]


def generate_audio(kind: str, dirname: str, *, preset: str = "core",
                   section_ids: list[str] | None = None, voice: str = "",
                   lang: str = "ja", audio_format: str = "", force: bool = False) -> dict:
    pack_dir = resolve_pack_dir(kind, dirname)
    all_sections = collect_sections(kind, pack_dir, preset=preset)
    selected = _select_sections(all_sections, section_ids)
    out_dir = audio_dir_for(pack_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = load_manifest(pack_dir)
    existing_by_id = {item["section_id"]: item for item in existing.get("items", [])}
    items: list[dict] = []
    errors: list[dict] = []
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    task_id = f"tts-{uuid.uuid4().hex[:12]}"

    for section in selected:
        effective_hash = sha256(
            f"{section.text}|{voice}|{lang}|{audio_format}".encode("utf-8")
        ).hexdigest()
        cached = existing_by_id.get(section.id)
        cached_path = out_dir / cached["filename"] if cached else None
        if (
            not force and cached and cached.get("text_hash") == effective_hash
            and cached_path and cached_path.exists()
        ):
            items.append({**cached, "cached": True})
            continue
        try:
            result = synthesize(section.text, voice=voice, lang=lang, audio_format=audio_format)
            filename = f"{section.ordinal:02d}-{section.id}.{result.audio_format}"
            audio_path = out_dir / filename
            audio_path.write_bytes(result.audio_bytes)
            item = {
                "section_id": section.id,
                "title": section.title,
                "category": section.category,
                "source_file": section.source_file,
                "filename": filename,
                "path": str(audio_path.relative_to(PROJECT_ROOT)),
                "voice": result.voice,
                "lang": lang,
                "provider": result.provider,
                "format": result.audio_format,
                "content_type": result.content_type,
                "text_hash": effective_hash,
                "generated_at": now,
                "bytes": len(result.audio_bytes),
                "cached": False,
            }
            items.append(item)
            existing_by_id[section.id] = item
        except Exception as exc:  # noqa: BLE001
            errors.append({"section_id": section.id, "title": section.title, "error": str(exc)})

    manifest = {
        "version": 1,
        "kind": kind,
        "dirname": dirname,
        "preset": preset,
        "generated_at": now,
        "items": sorted(existing_by_id.values(), key=lambda item: item.get("filename", "")),
    }
    manifest_path_for(pack_dir).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "task_id": task_id,
        "status": "completed" if not errors else ("failed" if not items else "partial"),
        "kind": kind,
        "dirname": dirname,
        "preset": preset,
        "items": items,
        "errors": errors,
        "audio_dir": str(out_dir.relative_to(PROJECT_ROOT)),
    }
