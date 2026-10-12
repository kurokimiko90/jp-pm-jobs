"""Chrome-exported cookies → Playwright context loader.

接受兩種格式：
1. EditThisCookie 擴充匯出的陣列（domain/name/value/path/expirationDate）
2. cookies.txt Netscape 格式（標準工具如 yt-dlp 用）

usage:
    from auth.load_cookies import load_into_context
    load_into_context(context, "linkedin")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext

COOKIES_DIR = Path(__file__).parent / "cookies"


def _normalize_same_site(v: Any) -> str:
    """Playwright 要求 'Strict' | 'Lax' | 'None'。"""
    if not v:
        return "Lax"
    s = str(v).lower()
    if "strict" in s:
        return "Strict"
    if "none" in s or s == "no_restriction" or s == "unspecified":
        return "None"
    return "Lax"


def _convert_editthiscookie(raw: list[dict]) -> list[dict]:
    out = []
    for c in raw:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", False)),
            "sameSite": _normalize_same_site(c.get("sameSite")),
        }
        exp = c.get("expirationDate")
        if exp:
            cookie["expires"] = int(exp)
        out.append(cookie)
    return out


def load_into_context(context: BrowserContext, site: str) -> bool:
    """Load cookies for `site` (e.g. 'linkedin', 'wantedly', 'bizreach').

    Returns True if cookies were loaded, False if file missing.
    """
    path = COOKIES_DIR / f"{site}.json"
    if not path.exists():
        return False

    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        cookies = _convert_editthiscookie(raw)
    else:
        raise ValueError(f"Unsupported cookie format in {path}")

    context.add_cookies(cookies)
    return True


def require_cookies(site: str) -> Path:
    """Raise if cookies missing — fail-fast for cookie-required scrapers."""
    path = COOKIES_DIR / f"{site}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. 從 Chrome 用 'EditThisCookie' 擴充匯出該站 cookies 並存到此路徑。"
            f" 見 auth/README.md。"
        )
    return path
