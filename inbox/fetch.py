"""從 Gmail 拉郵件 → 結構化 dict。

fetch 只取原文；招聘方 PII 遮罩在送 LLM 前由 inbox._redact 處理（classify/draft 階段）。
"""
from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta
from email.utils import parseaddr

from inbox.auth import get_service

BODY_MAX = 8000  # 截斷，控 LLM token


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_body(payload: dict) -> str:
    """遞迴抽 text/plain（優先），fallback text/html 去標籤。"""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if mime == "text/plain" and data:
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text
    if mime == "text/html" and data:
        html = base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        return _strip_html(html)
    return ""


def _epoch_to_date(internal_date: str | None) -> str:
    if not internal_date:
        return ""
    return datetime.fromtimestamp(int(internal_date) / 1000).strftime("%Y-%m-%d")


def _epoch_to_seconds(internal_date: str | None) -> int | None:
    return int(internal_date) // 1000 if internal_date else None


def _within_hours(internal_date: str | None, hours: int, *, now: datetime | None = None) -> bool:
    """Return whether a Gmail internalDate falls within the requested rolling window."""
    if not internal_date:
        return False
    received = datetime.fromtimestamp(int(internal_date) / 1000)
    return received >= (now or datetime.now()) - timedelta(hours=hours)


def _to_mail(full: dict) -> dict:
    """Gmail API full message → 結構化 dict（fetch_recent 元素格式）。"""
    payload = full["payload"]
    sender_raw = _header(payload, "From")
    _name, email = parseaddr(sender_raw)
    return {
        "gmail_msg_id": full["id"],
        "thread_id": full.get("threadId"),
        "sender": sender_raw,
        "sender_email": email,
        "sender_domain": email.split("@")[-1].lower() if "@" in email else "",
        "subject": _header(payload, "Subject"),
        "received_at": _epoch_to_date(full.get("internalDate")),
        "gmail_received_at": _epoch_to_seconds(full.get("internalDate")),
        "body": _extract_body(payload)[:BODY_MAX],
        "snippet": full.get("snippet", ""),
    }


def fetch_by_id(msg_id: str) -> dict:
    """按 Gmail message id 抓單封（草稿補生時重建郵件正文用）。"""
    svc = get_service()
    full = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
    return _to_mail(full)


def fetch_query(q: str, max_results: int = 200) -> list[dict]:
    """任意の Gmail 検索クエリで取得（ページング対応）。過去分の一括取り込み用。

    fetch_recent が扱わない条件（件名検索・特定送信者の全期間など）を使う場面向け。
    """
    svc = get_service()
    out: list[dict] = []
    token = None
    while len(out) < max_results:
        resp = svc.users().messages().list(
            userId="me", q=q, maxResults=min(100, max_results - len(out)),
            pageToken=token,
        ).execute()
        for m in resp.get("messages", []):
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="full"
            ).execute()
            out.append(_to_mail(full))
        token = resp.get("nextPageToken")
        if not token:
            break
    return out


def fetch_recent(
    days: int = 7,
    hours: int | None = None,
    max_results: int = 30,
    unread_only: bool = True,
    starred_only: bool = False,
    restrict_domains: bool = True,
) -> list[dict]:
    """拉近期郵件（預設近 days 天且僅未讀），回傳結構化 dict list。

    ``hours`` is an exact rolling window. Gmail's search syntax only supports
    day-level relative windows, so we fetch the surrounding day and apply the
    precise cutoff using Gmail's ``internalDate`` locally.

    starred_only=True 時改用 is:starred（不限 in:inbox，星號郵件可能已封存）。
    restrict_domains=True（預設）時，Gmail 查詢層直接限定寄件者為
    inbox.rule_classify._AGENT_DOMAINS 已知招募平台網域，個人生活雜訊
    （購物/票券/健檢/社區信等）不會被拉下來，不進 DB 也不占 LLM/規則判斷成本。
    設 False 可拉全部（例如做網域特徵調查用）。
    """
    svc = get_service()
    search_days = max(1, (hours + 23) // 24) if hours is not None else days
    if starred_only:
        q = f"is:starred newer_than:{search_days}d"
    else:
        q = f"in:inbox newer_than:{search_days}d"
    if unread_only:
        q += " is:unread"
    if restrict_domains:
        from inbox.rule_classify import _AGENT_DOMAINS

        domains_q = " OR ".join(f"from:{d}" for d in _AGENT_DOMAINS)
        q += f" ({domains_q})"
    resp = svc.users().messages().list(userId="me", q=q, maxResults=max_results).execute()

    out: list[dict] = []
    for m in resp.get("messages", []):
        full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        if hours is not None and not _within_hours(full.get("internalDate"), hours):
            continue
        out.append(_to_mail(full))
    return out
