"""公司黑名單 — 現職 / 利害衝突公司的投遞攔截。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_BLACKLIST_PATH = Path(__file__).parent.parent / "data" / "blacklist.yaml"

_NORMALIZE_RE = re.compile(r"株式会社|有限会社|合同会社|Inc\.|Corp\.|Ltd\.|Co\.,? ?Ltd\.?|\s+", re.IGNORECASE)


def _normalize(name: str) -> str:
    return _NORMALIZE_RE.sub("", name).strip().lower()


def _load() -> list[dict]:
    if not _BLACKLIST_PATH.exists():
        return []
    data = yaml.safe_load(_BLACKLIST_PATH.read_text(encoding="utf-8"))
    return data.get("companies", []) if data else []


_MIN_SUBSTRING_LEN = 4  # 短於此長度的字串（如 dip/ORO/log）禁止 substring 比對，只能完全相等，防誤傷不相關公司


def is_blacklisted(company_name: str) -> str | None:
    """命中黑名單則回傳 reason，否則回傳 None。

    substring 雙向比對僅在較短字串長度 ≥ _MIN_SUBSTRING_LEN 時才生效，
    否則要求完全相等 —— 避免「ORO」誤中「Photoroom」、「LOG」誤中
    「Terilogy Holdings」這類短字巧合命中。

    entry 可選 `exclude` 清單：子公司名單會 substring 命中母公司（如
    「○○データ ○○支店」包含「○○データ」），exclude 讓母公司精確名稱跳過
    這條規則，不影響其他條目對同一名稱的判斷。"""
    if not company_name:
        return None
    norm = _normalize(company_name)
    for entry in _load():
        entry_norm = _normalize(entry["name"])
        if not entry_norm:
            continue
        if entry_norm == norm:
            return entry.get("reason", "黑名單")
        if min(len(entry_norm), len(norm)) >= _MIN_SUBSTRING_LEN and (
            entry_norm in norm or norm in entry_norm
        ):
            excludes = {_normalize(x) for x in entry.get("exclude", [])}
            if norm in excludes:
                continue
            return entry.get("reason", "黑名單")
    return None


def guard(company_name: str) -> None:
    """prep.py 用：命中即 sys.exit。"""
    reason = is_blacklisted(company_name)
    if reason:
        sys.exit(f"⛔ 黑名單攔截: {company_name}（{reason}）— 不可投遞")


def flag_blacklisted() -> None:
    """postprocess 用：掃全表，標記黑名單職缺。"""
    from tracker.db import connect
    with connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()]
        if "blacklisted" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN blacklisted INTEGER DEFAULT 0")
    _flag_rows()


def _flag_rows() -> None:
    from tracker.db import connect
    with connect() as conn:
        rows = conn.execute("SELECT id, company FROM jobs WHERE company IS NOT NULL").fetchall()
        for row in rows:
            bl = 1 if is_blacklisted(row["company"]) else 0
            conn.execute("UPDATE jobs SET blacklisted = ? WHERE id = ?", (bl, row["id"]))
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM jobs WHERE blacklisted = 1").fetchone()[0]
    if count:
        print(f"[黑名單] {count} 筆職缺已標記")
