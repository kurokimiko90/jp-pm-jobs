"""集中所有絕對路徑常數。白名單校驗都以 PROJECT_ROOT 為界。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# JOBS_DB 環境變數可覆蓋（demo 截圖 / 測試用替身 DB）
DB_PATH = Path(os.environ.get("JOBS_DB") or PROJECT_ROOT / "data" / "jobs.sqlite")
PREP_DIR = PROJECT_ROOT / "output" / "prep"
# 手書きの面接問答は全部ここ（interview/ 直下にはコードだけ置く）
QBANK_DIR = PROJECT_ROOT / "interview" / "question-bank"
RESUME_OUT = PROJECT_ROOT / "resume" / "jp" / "output"
OUTPUT_DIR = PROJECT_ROOT / "output"
TAILORED_DIR = OUTPUT_DIR / "tailored"
APPLY_DIR = OUTPUT_DIR / "apply"
# 人工整理的職涯助手對話總結（非 digest.py 自動生成，手動落地）
ASSISTANT_SUMMARY_DIR = OUTPUT_DIR / "assistant"

RESUME_VERSIONS = {
    "rirekisho": {"label": "履歴書", "html": RESUME_OUT / "rirekisho.html", "pdf": RESUME_OUT / "rirekisho.pdf"},
    "shokumu": {"label": "職務経歴書", "html": RESUME_OUT / "shokumu-keirekisho.html", "pdf": RESUME_OUT / "shokumu-keirekisho.pdf"},
    "shokumu_zh": {"label": "職経(中文)", "html": RESUME_OUT / "shokumu-keirekisho-zh.html", "pdf": RESUME_OUT / "shokumu-keirekisho-zh.pdf"},
    "web": {"label": "Web版", "html": RESUME_OUT / "shokumu-web.html", "pdf": None},
    "web_light": {"label": "Web版(light)", "html": RESUME_OUT / "shokumu-web-light.html", "pdf": None},
}


def inside_project(p: Path) -> bool:
    try:
        p.resolve().relative_to(PROJECT_ROOT)
        return True
    except ValueError:
        return False
