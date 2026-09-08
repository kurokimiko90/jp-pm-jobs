"""把郵件對應到已投職缺（applications）的 job_id。對不到回 None（仍生成通用草稿）。

v1 規則：normalize 後比對 sender domain / 主旨 / 本文前段是否含公司名。
"""
from __future__ import annotations

import re

from tracker.db import connect


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_job(mail: dict) -> int | None:
    """優先比對「已投職缺」的公司；對不到回 None。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT j.id, j.company FROM jobs j "
            "JOIN applications a ON a.job_id = j.id"
        ).fetchall()

    domain = _norm(mail.get("sender_domain", ""))
    haystack = _norm(mail.get("subject", "") + " " + (mail.get("body", "") or "")[:500])

    for r in rows:
        comp = _norm(r["company"])
        if len(comp) < 3:
            continue
        if comp in domain or comp in haystack or (len(comp) >= 6 and domain.startswith(comp[:6])):
            return r["id"]
    return None
