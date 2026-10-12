"""3D 面接ルーム API — 面接パック／提案パックの各成果物を「部屋の展示物」として束ねて配信。

部屋構成（受付 → 待合室 → 面接室 → 振り返り室）と展示物の対応は STATIONS が唯一の定義。
台本・逐句音檔は /api/theater/* をそのまま使う（本 router は読み取り専用・0 LLM）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

try:
    from paths import PROJECT_ROOT
except ModuleNotFoundError:
    from dashboard.backend.paths import PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

router = APIRouter(prefix="/api/room3d")

PREP_DIR = PROJECT_ROOT / "output" / "prep"
PROPOSAL_DIR = PROJECT_ROOT / "output" / "proposal"

# 1 展示物の本文上限（巨大 md で画面とレスポンスが重くならないように）
MAX_BODY_CHARS = 60_000

# (station id, 部屋, 出所, ファイル名 glob, 見出し ja, 見出し zh)
# 出所: prep = output/prep/{dirname}、proposal = output/proposal/{dirname}
STATIONS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("company", "lobby", "proposal", "01_company.md", "会社研究", "公司研究"),
    ("product", "lobby", "proposal", "02_product.md", "プロダクト構造", "產品結構"),
    ("persona", "lobby", "proposal", "03_persona.md", "求める人物像", "求才人物像"),
    ("readme", "lobby", "prep", "00_README.md", "パック案内", "準備包導覽"),
    ("checklist", "waiting", "prep", "02_checklist.md", "当日チェックリスト", "當日檢查清單"),
    ("jikoshoukai", "waiting", "prep", "06_jikoshoukai.md", "自己紹介", "自我介紹"),
    ("qa_audit", "review", "prep", "05_qa_audit.md", "QA 監査", "QA 審查"),
    ("hire_audit", "review", "prep", "hire_audit_*.md", "採用競争力審査", "錄取競爭力審查"),
    ("theater_review", "review", "prep", "theater/review_report.md", "台本レビュー", "台本審查"),
    ("proposal_review", "review", "proposal", "12_review_report.md", "提案レビュー", "提案審查"),
)

# 面接室スクリーンに映す提案スライド（proposal 各 stage の png、番号順）
SCREEN_GLOB = "[0-9][0-9]_*.png"


def _resolve(root: Path, dirname: str) -> Path | None:
    """root 直下の dirname を返す（traversal・_archive 等は拒否、無ければ None）。"""
    if not dirname or dirname.startswith(("_", ".")) or "/" in dirname or "\\" in dirname:
        raise HTTPException(403, "invalid pack dir")
    root = root.resolve()
    path = (root / dirname).resolve()
    if path.parent != root:
        raise HTTPException(403, "invalid pack dir")
    return path if path.is_dir() else None


def _pick(base: Path | None, pattern: str) -> Path | None:
    """glob に当たる最新（名前順の最後 = 日付入り名なら最新日）のファイル。"""
    if base is None:
        return None
    hits = sorted(p for p in base.glob(pattern) if p.is_file())
    return hits[-1] if hits else None


def _read(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS] + "\n\n…（以下省略）"
    return text


def build_content(dirname: str) -> dict:
    prep = _resolve(PREP_DIR, dirname)
    if prep is None:
        raise HTTPException(404, f"pack not found: {dirname}")
    proposal = _resolve(PROPOSAL_DIR, dirname)
    bases = {"prep": prep, "proposal": proposal}

    stations = []
    for sid, room, origin, pattern, title_ja, title_zh in STATIONS:
        path = _pick(bases[origin], pattern)
        stations.append({
            "id": sid,
            "room": room,
            "titleJa": title_ja,
            "titleZh": title_zh,
            "available": path is not None,
            "source": f"{origin}/{path.name}" if path else None,
            "body": _read(path) if path else "",
        })

    slides = []
    if proposal is not None:
        slides = [
            f"/api/room3d/slide/{dirname}/{p.name}"
            for p in sorted(proposal.glob(SCREEN_GLOB)) if p.is_file()
        ]

    company = dirname.split("_", 1)[1] if "_" in dirname else dirname
    return {"dirname": dirname, "company": company, "stations": stations, "slides": slides}


@router.get("/content/{dirname}")
def room3d_content(dirname: str):
    return build_content(dirname)


@router.get("/slide/{dirname}/{name}")
def room3d_slide(dirname: str, name: str):
    proposal = _resolve(PROPOSAL_DIR, dirname)
    if proposal is None:
        raise HTTPException(404, "proposal not found")
    path = (proposal / name).resolve()
    if path.parent != proposal.resolve() or not path.match(SCREEN_GLOB) or not path.is_file():
        raise HTTPException(404, "slide not found")
    return FileResponse(path, media_type="image/png")
