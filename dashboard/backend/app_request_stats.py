"""応募リクエスト × 書類通過（純函數、FastAPI 非依存）。

応募リクエスト＝r-agent の企業担当が「経歴と親和性が高い」と個別に送る応募の打診
（inbox.apply_request が記録）。企業本人の招待ではない点に注意して読む。

- `match_requests`：リクエスト ↔ 応募の対応付け（一覧のバッジと統計の唯一の入口）
- `build_apply_request`：リクエスト経由の応募の書類通過率と、同時期 r-agent 応募の対照

判定（pass / fail / pending …）は app_analytics.annotate() の outcome をそのまま使う。
"""
from __future__ import annotations

import re
from collections import defaultdict

import app_analytics as aa

_OFFER_ID = re.compile(r"r-agent\.com/joboffers/(\d+)")
# リクエストが来得る応募経路。対照群もこの経路に揃える（官網直投と比べても意味がない）
REQUEST_CHANNEL = "r-agent"


def _offer_id(url: str | None) -> str | None:
    m = _OFFER_ID.search(url or "")
    return m.group(1) if m else None


def match_requests(requests: list, apps: list) -> dict[int, dict]:
    """応募 job_id → 対応するリクエスト（最初に届いたもの）。

    1. **求人番号一致**：リクエスト本文の joboffers/{id} と応募求人の URL が同じ。
       確実なので日付を問わない。
    2. **社名一致**（求人番号で当たらない応募のみ）：company_norm が同じで、
       リクエスト受信日 ≤ 応募日。求人が別経路（green 等）で取り込まれていると
       URL が r-agent ではないので、ここで拾う。応募後に届いたリクエストは
       応募のきっかけではないので対象外。

    `requests` は requested_at 昇順で渡すこと（最初のリクエストを採る）。
    `apps` は job_id / url / company_norm / applied_at を持つ行。
    """
    by_offer: dict[str, dict] = {}
    by_company: dict[str, list[dict]] = defaultdict(list)
    for r in requests:
        r = dict(r)
        if r["offer_id"]:
            by_offer.setdefault(r["offer_id"], r)
        if r["company_norm"]:
            by_company[r["company_norm"]].append(r)

    matched: dict[int, dict] = {}
    for a in apps:
        hit = by_offer.get(_offer_id(a["url"]) or "")
        if hit:
            matched[a["job_id"]] = {**hit, "match": "offer"}
            continue
        applied = (a["applied_at"] or "")[:10]
        for r in by_company.get(a["company_norm"] or "", []):
            if applied and r["requested_at"] <= applied:
                matched[a["job_id"]] = {**r, "match": "company"}
                break
    return matched


def _counts(rows: list[dict]) -> dict:
    c: dict = defaultdict(int)
    for r in rows:
        c[r["outcome"]] += 1
    return c


def build_apply_request(annotated: list[dict], matched: dict[int, dict],
                        requests: list) -> dict:
    """リクエスト経由の応募の書類通過率。

    比較対象は「最初のリクエスト受信日以降に r-agent 経由で応募し、リクエストが
    無かった応募」。全期間と比べると、リクエスト機能を使う前の応募（別の履歴書・
    別の職種）が混ざる。
    """
    requests = [dict(r) for r in requests]
    since = min((r["requested_at"] for r in requests), default=None)
    requested = [
        {**r, "request": matched[r["job_id"]]} for r in annotated if r["job_id"] in matched
    ]
    control = [
        r for r in annotated
        if since and r["job_id"] not in matched and r["channel"] == REQUEST_CHANNEL
        and (r["applied_at"] or "")[:10] >= since
    ]
    control_entry = aa._rate_entry("control", _counts(control), None)
    req_counts = _counts(requested)

    # 応募に至らなかったリクエスト（社単位。同じ会社から複数通来ることがある）
    applied_norms = {m["company_norm"] for m in matched.values()}
    not_applied: dict[str, dict] = {}
    for r in requests:
        if r["company_norm"] in applied_norms:
            continue
        entry = not_applied.setdefault(r["company_norm"], {
            "company": r["company"], "requested_at": r["requested_at"],
            "title": r["title"], "mails": 0,
        })
        entry["mails"] += 1
        entry["requested_at"] = max(entry["requested_at"], r["requested_at"])

    return {
        "summary": {
            **aa._rate_entry("requested", req_counts, control_entry["rate"]),
            "jobs": len(requested), "withdrawn": req_counts["withdrawn"],
            "mails": len(requests),
            "companies": len({r["company_norm"] for r in requests}),
            "not_applied": len(not_applied), "since": since,
        },
        "control": {**control_entry, "jobs": len(control)},
        "items": sorted(
            ({"job_id": r["job_id"], "company": r["company"], "outcome": r["outcome"],
              "status": r["status"], "rejection_stage": r["rejection_stage"],
              "requested_at": r["request"]["requested_at"],
              "applied_at": (r["applied_at"] or "")[:10],
              "match": r["request"]["match"]} for r in requested),
            key=lambda x: x["requested_at"], reverse=True,
        ),
        "not_applied": sorted(not_applied.values(), key=lambda x: x["requested_at"], reverse=True),
    }
