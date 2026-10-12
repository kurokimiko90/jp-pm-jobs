"""面接シアター資産生成 — QA md → VNScript JSON + 逐句語音（內容尋址）。

輸入：output/prep/{pack}/01|03_interview_qa.md（現行標準一問一答格式：
`### Q. 質問文` + 結論 1 句 + 番号付き要点）。舊格式（狙い/回答骨子型）不支援 —
公司還在流程中的 pack 重跑一次 qa stage 比維護多代 parser 便宜。

輸出（pack_dir/theater/）：
  script.json    — VNScript 形狀（前端 vn/types.ts 同構）；只由 md 導出，不含音檔資訊
  manifest.json  — line_id → 音檔映射（內容尋址：sha12(text|voice|rate)，逐句寫入 =
                   漸進可用，生成中也能播已完成的句子）
  audio/*.mp3    — 逐句音檔。檔名即 content hash → 改一句只重合成一句、重複句自動去重、
                   HTTP 端可掛 immutable cache

PII：每句合成前過 tools.pii_gate.scrub_for_external()（真名→「本人」後才離開本機）。

用法:
    python3 -m tts.theater --job-id 123            # script + 逐句音檔
    python3 -m tts.theater --job-id 123 --no-audio # 只建 script.json
    python3 -m tts.theater --pack 123_サンプル社 --force
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from interview.qa import keywords
from tools import app_config
from tools.pii_gate import scrub_for_external

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PREP_DIR = PROJECT_ROOT / "output" / "prep"

QA_FILENAMES = ("01_interview_qa.md", "03_interview_qa.md")
SCRIPT_VERSION = 1

# 見出し頭の `[キーワード]` は読み上げに乗せない（「かぎかっこ」と発音される）
_Q_RE = re.compile(
    rf"^### Q[\d\-]*[\.、]?\s*{keywords.OPTIONAL_PREFIX}(.+)$", re.MULTILINE)
_SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_POINT_RE = re.compile(r"^\d+\.\s+")

DEFAULT_VOICE_INTERVIEWER = "ja-JP-KeitaNeural"
DEFAULT_VOICE_CANDIDATE = "ja-JP-NanamiNeural"


# ---------------------------------------------------------------- parse

@dataclass
class QAItem:
    section: str
    question: str
    conclusion: str
    points: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    """行内 markdown 記號剝離（顯示與 TTS 共用；{{要確認}} 類佔位整塊移除）。"""
    text = text.replace("**", "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"\{\{.*?\}\}", "", text)
    return text.strip()


def parse_standard_qa(md_text: str) -> list[QAItem]:
    """現行標準一問一答格式 → QAItem 列。非標準格式回空列（呼叫端判定）。"""
    sections = list(_SECTION_RE.finditer(md_text))

    def section_of(pos: int) -> str:
        title = ""
        for m in sections:
            if m.start() < pos:
                title = m.group(1).strip()
            else:
                break
        return title

    items: list[QAItem] = []
    matches = list(_Q_RE.finditer(md_text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        body = md_text[start:end]
        # 次の section 見出しで打ち切り
        next_section = re.search(r"^## ", body, re.MULTILINE)
        if next_section:
            body = body[:next_section.start()]

        conclusion_parts: list[str] = []
        points: list[str] = []
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line == "---":
                continue
            if _POINT_RE.match(line):
                points.append(_clean(_POINT_RE.sub("", line)))
            elif not points:  # 要点開始前の行のみ結論に（後置註記は捨てる）
                conclusion_parts.append(_clean(line))
        conclusion = "".join(p for p in conclusion_parts if p)
        points = [p for p in points if p]
        question = _clean(m.group(1))
        if question and (conclusion or points):
            items.append(QAItem(
                section=section_of(m.start()),
                question=question,
                conclusion=conclusion,
                points=points,
            ))
    return items


# ---------------------------------------------------------------- script

def _source_path(pack_dir: Path) -> Path | None:
    for name in QA_FILENAMES:
        p = pack_dir / name
        if p.exists():
            return p
    return None


def build_script(pack_dir: Path, items: list[QAItem], job: dict | None = None) -> dict:
    """QAItem 列 → VNScript 形狀 dict（frontend vn/types.ts 同構）。

    zh 欄位鏡射 ja（自動生成 pack 無中文翻譯；前端切中文時顯示同文）。
    favorDelta / emotion 演出一律不編造 — noFavor: true 讓前端隱藏好感度 UI。
    """
    dirname = pack_dir.name
    company = (job or {}).get("company") or (dirname.split("_", 1)[1] if "_" in dirname else dirname)
    title = (job or {}).get("title") or ""

    # section 別に幕を構成（section 無しの質問は「想定問答」幕へ）
    act_order: list[str] = []
    by_section: dict[str, list[QAItem]] = {}
    for item in items:
        key = item.section or "想定問答"
        if key not in by_section:
            act_order.append(key)
            by_section[key] = []
        by_section[key].append(item)

    acts = []
    q_no = 0
    for act_idx, section in enumerate(act_order, start=1):
        lines = []
        for item in by_section[section]:
            q_no += 1
            lines.append({
                "id": f"q{q_no}-q", "speaker": "interviewer", "type": "talk",
                "ja": item.question, "zh": item.question, "emotion": "neutral",
            })
            answer_parts = ([item.conclusion] if item.conclusion else []) + item.points
            for k, part in enumerate(answer_parts, start=1):
                lines.append({
                    "id": f"q{q_no}-a{k}", "speaker": "candidate", "type": "talk",
                    "ja": part, "zh": part, "emotion": "neutral",
                })
        acts.append({
            "id": act_idx,
            "titleJa": section, "titleZh": section,
            "goalJa": None, "goalZh": None,
            "lines": lines,
        })

    overall = (f"{company} 想定問答 全{q_no}問。面接パックから自動生成した台本のため、"
               f"演出・評価は表示しません。要確認事項は面接パック本体を参照してください。")
    return {
        "id": f"pack-{dirname}",
        "titleJa": f"{company} 想定問答", "titleZh": f"{company} 想定問答",
        "interviewerJa": "面接官", "interviewerZh": "面試官",
        "sample": False,
        "noFavor": True,
        "acts": acts,
        "report": {"aspects": [], "overallJa": overall, "overallZh": overall},
        "_meta": {
            "version": SCRIPT_VERSION,
            "dirname": dirname,
            "company": company,
            "jobTitle": title,
            "questions": q_no,
        },
    }


# ---------------------------------------------------------------- assets

def theater_dir(pack_dir: Path) -> Path:
    return pack_dir / "theater"


def load_manifest(pack_dir: Path) -> dict:
    p = theater_dir(pack_dir) / "manifest.json"
    if not p.exists():
        return {"version": 1, "items": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def _save_manifest(pack_dir: Path, manifest: dict) -> None:
    (theater_dir(pack_dir) / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _probe_duration_ms(path: Path) -> int | None:
    """ffprobe で音声の実測時間（ms）— 復習頁の所要時間表示は推測でなくこれを使う。"""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10, check=True)
        return round(float(proc.stdout.strip()) * 1000)
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _voices() -> dict[str, str]:
    cfg = app_config.get("tts", "theater", {}) or {}
    return {
        "interviewer": cfg.get("voice_interviewer", DEFAULT_VOICE_INTERVIEWER),
        "candidate": cfg.get("voice_candidate", DEFAULT_VOICE_CANDIDATE),
    }


def ensure_assets(pack_dir: Path, job: dict | None = None, *,
                  synth_audio: bool = True, force: bool = False,
                  log=print) -> dict:
    """script.json（來源 md 變更時重建）+ 逐句音檔（缺的才合成）。

    回傳 {script_path, questions, lines_total, audio_done, audio_errors, pii_findings}。
    """
    src = _source_path(pack_dir)
    if src is None:
        raise FileNotFoundError(f"{pack_dir.name}: 想定問答 md（01/03_interview_qa.md）が見つかりません")
    md_text = src.read_text(encoding="utf-8")
    source_hash = sha256(md_text.encode("utf-8")).hexdigest()

    tdir = theater_dir(pack_dir)
    tdir.mkdir(parents=True, exist_ok=True)
    script_path = tdir / "script.json"

    script = None
    if script_path.exists() and not force:
        cached = json.loads(script_path.read_text(encoding="utf-8"))
        if cached.get("_meta", {}).get("sourceHash") == source_hash:
            script = cached
    if script is None:
        items = parse_standard_qa(md_text)
        if not items:
            raise ValueError(
                f"{src.name} は標準一問一答形式（### Q. + 結論 + 番号付き要点）ではありません — "
                f"qa stage を再実行して再生成してください")
        script = build_script(pack_dir, items, job)
        script["_meta"]["sourceHash"] = source_hash
        script["_meta"]["sourceFile"] = src.name
        script["_meta"]["generatedAt"] = datetime.now().isoformat(timespec="seconds")
        script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  ✓ theater/script.json（{script['_meta']['questions']}問）")

    result = {
        "script_path": script_path,
        "questions": script["_meta"]["questions"],
        "lines_total": sum(len(a["lines"]) for a in script["acts"]),
        "audio_done": 0, "audio_errors": [], "pii_findings": 0,
    }
    if not synth_audio:
        return result

    from tts import synthesize  # 遅延 import（--no-audio 時 chain 構築不要）

    voices = _voices()
    rate = (app_config.get("tts", "providers", {}) or {}).get("edge_tts", {}).get("rate", "+0%")
    audio_dir = tdir / "audio"
    audio_dir.mkdir(exist_ok=True)
    manifest = load_manifest(pack_dir)
    items_map: dict = manifest.setdefault("items", {})
    manifest["voices"] = voices

    lines = [l for a in script["acts"] for l in a["lines"] if l["type"] == "talk"]
    for line in lines:
        text, findings = scrub_for_external(line["ja"])
        if findings:
            result["pii_findings"] += 1
        voice = voices["interviewer" if line["speaker"] == "interviewer" else "candidate"]
        text_hash = sha256(f"{text}|{voice}|{rate}".encode("utf-8")).hexdigest()[:12]

        cached = items_map.get(line["id"])
        if (not force and cached and cached.get("hash") == text_hash
                and (audio_dir / cached["file"]).exists()):
            result["audio_done"] += 1
            if cached.get("durationMs") is None:   # 旧生成分の後付け実測（再合成不要）
                ms = _probe_duration_ms(audio_dir / cached["file"])
                if ms is not None:
                    cached["durationMs"] = ms
                    _save_manifest(pack_dir, manifest)
            continue
        try:
            resp = synthesize(text, voice=voice, lang="ja")
            filename = f"{text_hash}.{resp.audio_format}"
            (audio_dir / filename).write_bytes(resp.audio_bytes)
            items_map[line["id"]] = {
                "file": filename, "hash": text_hash,
                "provider": resp.provider, "voice": resp.voice,
                "durationMs": _probe_duration_ms(audio_dir / filename),
            }
            _save_manifest(pack_dir, manifest)  # 逐句寫入 = 生成中も既完成分は再生可能
            result["audio_done"] += 1
        except Exception as exc:  # noqa: BLE001 — 単句失敗は記録して続行（純文字降級）
            result["audio_errors"].append({"line_id": line["id"], "error": str(exc)})

    log(f"  ✓ theater/audio: {result['audio_done']}/{len(lines)} 句"
        + (f"（失敗 {len(result['audio_errors'])}）" if result["audio_errors"] else ""))
    return result


def audio_files_in_order(pack_dir: Path) -> list[Path]:
    """台本順の音檔列（concat 用）。未生成句はスキップ。"""
    script_path = theater_dir(pack_dir) / "script.json"
    if not script_path.exists():
        return []
    script = json.loads(script_path.read_text(encoding="utf-8"))
    manifest = load_manifest(pack_dir)
    items_map = manifest.get("items", {})
    audio_dir = theater_dir(pack_dir) / "audio"
    files = []
    for act in script["acts"]:
        for line in act["lines"]:
            entry = items_map.get(line["id"])
            if entry and (audio_dir / entry["file"]).exists():
                files.append(audio_dir / entry["file"])
    return files


def find_pack_dir(job_id: int) -> Path:
    candidates = sorted(PREP_DIR.glob(f"{job_id}_*"))
    if not candidates:
        sys.exit(f"output/prep/{job_id}_* が見つかりません")
    return candidates[0]


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="面接シアター資産生成（script + 逐句語音）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--job-id", type=int)
    g.add_argument("--pack", help="output/prep 配下の pack ディレクトリ名")
    p.add_argument("--no-audio", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    if args.pack:
        pack_dir = PREP_DIR / args.pack
        if not pack_dir.is_dir():
            sys.exit(f"{pack_dir} が存在しません")
        job = None
    else:
        pack_dir = find_pack_dir(args.job_id)
        from tracker.db import connect
        with connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (args.job_id,)).fetchone()
        job = dict(row) if row else None

    result = ensure_assets(pack_dir, job, synth_audio=not args.no_audio, force=args.force)
    if result["audio_errors"]:
        for e in result["audio_errors"][:5]:
            print(f"  ✗ {e['line_id']}: {e['error']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
