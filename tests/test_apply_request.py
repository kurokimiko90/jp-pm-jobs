"""inbox.apply_request（応募リクエスト受信 → apply_requests）と
app_request_stats（リクエスト ↔ 応募の対応付け・書類通過率）の測試。

社名は架空（実際の応募先を tests に残さない）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from inbox import apply_request as ar

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard" / "backend"))
import app_request_stats as rs  # noqa: E402

_BODY = (
    "山田様\n\n弊社営業担当より個別に「応募リクエスト」をお送りしております。\n"
    "-------------------------------------------\n\n"
    "▼株式会社サンプルテック\n　◆プロダクトマネージャー　0→1の新規事業\n\n"
    "▼求人票URL\nhttps://mypage.r-agent.com/joboffers/108000001?job_referral=recommendPost\n"
)


def _mail(subject, body=_BODY, received_at="2026-09-01"):
    return {"gmail_msg_id": "m1", "subject": subject, "body": body, "received_at": received_at}


# ── メール抽出 ──

def test_extract_fullwidth_subject():
    info = ar.extract(_mail("株式会社サンプルテックへの応募リクエスト【リクルートエージェント】"))
    assert info["company"] == "株式会社サンプルテック"
    assert info["company_norm"] == "サンプルテック"
    assert info["offer_id"] == "108000001"
    assert info["title"] == "プロダクトマネージャー 0→1の新規事業"
    assert info["requested_at"] == "2026-09-01"


def test_extract_skips_own_reply_and_other_mail():
    assert ar.extract(_mail("Re: 株式会社サンプルテックへの応募リクエスト【リクルートエージェント】")) is None
    assert ar.extract(_mail("【リクルートエージェント】求人のご紹介")) is None


def test_extract_without_url_keeps_company():
    info = ar.extract(_mail("サンプル合同会社への応募リクエスト", body="本文なし"))
    assert info["company_norm"] == "サンプル"
    assert info["offer_id"] is None and info["title"] is None


# ── 対応付け ──

def _req(company_norm, requested_at, offer_id=None, company=None):
    return {"company": company or company_norm, "company_norm": company_norm,
            "requested_at": requested_at, "offer_id": offer_id, "title": "PdM"}


def _app(job_id, company_norm, applied_at, url=""):
    return {"job_id": job_id, "company_norm": company_norm, "applied_at": applied_at, "url": url}


def test_match_prefers_offer_id_regardless_of_date():
    reqs = [_req("alpha", "2026-09-05", offer_id="111")]
    apps = [_app(1, "alpha-other-name", "2026-09-01 10:00:00",
                 url="https://mypage.r-agent.com/joboffers/111?job_referral=x")]
    assert rs.match_requests(reqs, apps)[1]["match"] == "offer"


def test_match_company_only_when_request_came_first():
    reqs = [_req("beta", "2026-09-05")]
    apps = [_app(1, "beta", "2026-09-04"),   # 応募が先 → リクエストはきっかけではない
            _app(2, "beta", "2026-09-05T12:00:00")]  # 同日は対象
    m = rs.match_requests(reqs, apps)
    assert 1 not in m and m[2]["match"] == "company"


def test_match_uses_first_request():
    reqs = [_req("gamma", "2026-08-01"), _req("gamma", "2026-08-20")]
    m = rs.match_requests(reqs, [_app(1, "gamma", "2026-08-25")])
    assert m[1]["requested_at"] == "2026-08-01"


def test_match_ignores_empty_company_norm():
    assert rs.match_requests([_req("", "2026-08-01")], [_app(1, "", "2026-08-25")]) == {}


# ── 書類通過率 ──

def _ann(job_id, outcome, channel="r-agent", applied_at="2026-09-01", company="X"):
    return {"job_id": job_id, "company": company, "outcome": outcome, "status": "rejected",
            "rejection_stage": None, "channel": channel, "applied_at": applied_at}


def test_build_apply_request_control_is_same_channel_same_period():
    reqs = [_req("a", "2026-08-01"), _req("b", "2026-08-02"), _req("b", "2026-08-09"),
            _req("c", "2026-08-03")]
    annotated = [
        _ann(1, "pass"), _ann(2, "fail"),               # リクエスト経由
        _ann(3, "pass"), _ann(4, "fail"),               # 対照
        _ann(5, "pass", channel="direct"),              # 経路違い → 対照外
        _ann(6, "fail", applied_at="2026-07-20"),       # 期間外 → 対照外
    ]
    matched = {1: {**reqs[0], "match": "offer"}, 2: {**reqs[1], "match": "company"}}
    out = rs.build_apply_request(annotated, matched, reqs)
    s = out["summary"]
    assert (s["passed"], s["decided"], s["jobs"]) == (1, 2, 2)
    assert (s["mails"], s["companies"], s["not_applied"]) == (4, 3, 1)
    assert out["control"]["jobs"] == 2 and out["control"]["passed"] == 1
    # 応募しなかった会社は社単位で 1 行
    assert out["not_applied"] == [
        {"company": "c", "requested_at": "2026-08-03", "title": "PdM", "mails": 1}]


def test_build_apply_request_empty():
    out = rs.build_apply_request([_ann(1, "fail")], {}, [])
    assert out["summary"]["jobs"] == 0 and out["summary"]["since"] is None
    assert out["control"]["jobs"] == 0
