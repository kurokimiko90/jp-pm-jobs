"""副作用端點集中檔 — 批量開 JD / open-folder / open-file / 受限檔案讀取。

僅在後端與瀏覽器同一台機器（本機 self-host）時有意義；
路徑一律 resolve 後以 inside_project 限定在 PROJECT_ROOT 內。
"""
import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query

from db import query
from paths import PROJECT_ROOT, inside_project

router = APIRouter()

# ── batch open JD pages in browser ───────────────────────────────

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# 一度に開くタブ数の上限（既定 20／リクエストで上書き可、ハードキャップ 50）。
# 上限なしだと score 範囲だけで全 DB 分（数百件）が一気に開いてブラウザが固まる。
DEFAULT_OPEN_LIMIT = 20
MAX_OPEN_LIMIT = 50
SOURCE_CHROME_MAP: dict[str, list[str]] = {
    "indeed_jp":       [CHROME, "--profile-directory=Profile 2"],
    "linkedin_jp":     [CHROME, f"--user-data-dir={Path.home() / '.chrome-chatgpt4'}"],
    "bizreach":        [CHROME, f"--user-data-dir={Path.home() / '.chrome-bizreach'}"],
    # r-agent はログイン済みの bizreach profile を共用
    "recruiter_agent": [CHROME, f"--user-data-dir={Path.home() / '.chrome-bizreach'}"],
}


@router.post("/api/open-jds")
def open_jds(body: dict = Body(...)):
    """批量在對應 Chrome profile 開啟 JD 原始頁面。

    r-agent の裸 URL（jobs.url）は必ずエラーになるため、
    `scrapers.recruiter_agent.joboffer_url()` の流入元タグ付き URL に差し替える。
    """
    source = body.get("source", "")
    score_min = int(body.get("score_min", 0))
    score_max = int(body.get("score_max", 100))
    job_ids: list[int] | None = body.get("job_ids")
    limit = min(int(body.get("limit") or DEFAULT_OPEN_LIMIT), MAX_OPEN_LIMIT)

    if job_ids:
        # 呼び出し側（画面に見えている順）を保つ。上限超過分は開かない。
        wanted = list(dict.fromkeys(int(i) for i in job_ids))
        placeholders = ",".join("?" * len(wanted))
        rows = query(
            f"SELECT id, url, source, source_id FROM jobs WHERE id IN ({placeholders}) "
            f"AND url IS NOT NULL AND url != ''",
            wanted,
        )
        by_id = {r["id"]: r for r in rows}
        rows = [by_id[i] for i in wanted if i in by_id]
    else:
        where = ["url IS NOT NULL", "url != ''",
                 "COALESCE(liveness_status, 'active') != 'closed'",
                 "score >= ?", "score <= ?"]
        params: list = [score_min, score_max]
        if source:
            where.append("source = ?")
            params.append(source)
        rows = query(
            f"SELECT id, url, source, source_id FROM jobs WHERE {' AND '.join(where)} ORDER BY score DESC",
            params,
        )

    matched = len(rows)
    rows = rows[:limit]

    from scrapers.recruiter_agent import joboffer_url

    by_source: dict[str, list[str]] = {}
    skipped_ragent = 0
    for r in rows:
        if r["source"] == "recruiter_agent":
            if not r["source_id"]:
                skipped_ragent += 1
                continue
            by_source.setdefault(r["source"], []).append(joboffer_url(r["source_id"]))
        elif r["url"].startswith("http"):
            by_source.setdefault(r["source"], []).append(r["url"])

    opened = 0
    for src, urls in by_source.items():
        chrome_cmd = SOURCE_CHROME_MAP.get(src)
        if not chrome_cmd:
            chrome_cmd = [CHROME, "--profile-directory=Profile 2"]
        subprocess.Popen(chrome_cmd + urls,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        opened += len(urls)

    result = {
        "opened": opened,
        "by_source": {s: len(u) for s, u in by_source.items()},
        "matched": matched,
        "limit": limit,
        "skipped_over_limit": max(matched - limit, 0),
    }
    if skipped_ragent:
        result["skipped_ragent_no_source_id"] = skipped_ragent
    return result


# ── fs：open-folder / 受限檔案讀取 ────────────────────────────────
# 跨平台開啟指令：macOS → open   Linux → xdg-open   Windows → explorer
_OPEN_CMD = {"Darwin": "open", "Linux": "xdg-open", "Windows": "explorer"}.get(
    platform.system())


def _open_with_os(p: Path) -> None:
    if not _OPEN_CMD:
        raise HTTPException(501, f"此作業系統（{platform.system()}）不支援開啟本機檔案/資料夾")
    subprocess.run([_OPEN_CMD, str(p)], check=False)


def _resolve_project_path(raw: object) -> Path:
    """Resolve dashboard paths against PROJECT_ROOT, not the backend cwd.

    Many API payloads store paths as project-relative values, e.g.
    ``output/apply/123_company``.  The backend may be launched from
    ``dashboard/backend`` via run.sh, so resolving those paths directly would
    incorrectly point to ``dashboard/backend/output/...`` and produce 404.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(400, "缺少 path")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


@router.post("/api/open-folder")
def open_folder(body: dict = Body(...)):
    p = _resolve_project_path(body.get("path"))
    if not inside_project(p):
        raise HTTPException(403, "路徑不在項目內")
    if p.is_file():
        p = p.parent
    if not p.is_dir():
        raise HTTPException(404, "資料夾不存在")
    _open_with_os(p)
    return {"ok": True}


@router.post("/api/open-file")
def open_file(body: dict = Body(...)):
    p = _resolve_project_path(body.get("path"))
    if not inside_project(p):
        raise HTTPException(403, "路徑不在項目內")
    if not p.is_file():
        raise HTTPException(404, "檔案不存在")
    _open_with_os(p)
    return {"ok": True}


@router.get("/api/file")
def read_file(path: str = Query(...)):
    p = _resolve_project_path(path)
    if not inside_project(p) or not p.is_file() or p.suffix not in (".md", ".html", ".json", ".txt", ".yaml"):
        raise HTTPException(403)
    return {"content": p.read_text(encoding="utf-8")}
