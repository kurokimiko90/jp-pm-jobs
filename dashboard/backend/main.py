"""jp-pm-jobs dashboard — FastAPI 唯讀後端。

業務資料只讀；副作用端點（open-folder / open-file / open-jds）集中在 files_api.py，
路徑一律 resolve 後限定在 PROJECT_ROOT 內。

端點按域拆在 *_api.py 等 router 檔（見 CLAUDE.md「文件夾架構與新檔案放置規則」），
本檔只負責 app 組裝：CORS / Basic Auth / router 掛載 / 前端靜態檔。
"""
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from paths import PROJECT_ROOT

# 讓 analyzer/ 等專案頂層模組可被 import
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from actions_api import router as actions_router
from applications import router as applications_router
from assistant_api import router as assistant_router
from direct_apply_api import router as direct_apply_router
from dojo import router as dojo_router
from files_api import router as files_router
from gap_api import router as gap_router
from genki import router as genki_router
from inbox_api import router as inbox_router
from jobs_api import router as jobs_router
from prep_api import router as prep_router
from resume_api import router as resume_router
from scoring_api import router as scoring_router
from strategy_api import router as strategy_router
from theater_api import router as theater_router
from tts_api import router as tts_router

app = FastAPI(title="jp-pm-jobs dashboard")

# CORS 允許來源：DASHBOARD_CORS_ORIGINS 環境變數逗號分隔，
# 未設定則沿用本機開發預設值（vite dev server）。
_cors_origins = [
    o.strip() for o in
    os.environ.get("DASHBOARD_CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware, allow_origins=_cors_origins,
    allow_methods=["*"], allow_headers=["*"],
)

# 單一使用者登入保護：設 DASHBOARD_PASSWORD 後，所有請求需帶 HTTP Basic Auth
# （帳號任意，密碼需符合）。留空 = 不驗證，僅限本機使用時可接受。
_DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
if _DASHBOARD_PASSWORD:
    import base64
    import secrets

    from starlette.requests import Request
    from starlette.responses import Response

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.startswith("Basic "):
            try:
                _, pw = base64.b64decode(auth[6:]).decode().split(":", 1)
                ok = secrets.compare_digest(pw, _DASHBOARD_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})
        return await call_next(request)

app.include_router(actions_router)
app.include_router(applications_router)
app.include_router(assistant_router)
app.include_router(direct_apply_router)
app.include_router(dojo_router)
app.include_router(genki_router)
app.include_router(inbox_router)
app.include_router(theater_router)
app.include_router(tts_router)
app.include_router(jobs_router)
app.include_router(gap_router)
app.include_router(scoring_router)
app.include_router(prep_router)
app.include_router(resume_router)
app.include_router(files_router)
app.include_router(strategy_router)

# ── 託管前端 build ────────────────────────────────────────────────

DIST = Path(__file__).parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
