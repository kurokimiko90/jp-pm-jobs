"""recruiter_agent のプレーンテキスト ▼ ブロック解析。社名・求人番号は架空。"""
import re

from scrapers import recruiter_agent as ra

_REQUEST_BODY = (
    "弊社営業担当より個別に「応募リクエスト」をお送りしております。\n"
    "▼株式会社サンプルテック\n　◆プロダクトマネージャー　0→1の新規事業\n\n"
    "▼求人票URL\nhttps://mypage.r-agent.com/joboffers/108000001?job_referral=recommendPost\n"
)


def _parse(body):
    blocks = re.split(r"▼", ra._fold_label_headings(body))
    return [j for j in (ra._parse_plain_block("▼" + b) for b in blocks[1:]) if j]


def test_request_mail_label_heading_is_not_company():
    jobs = _parse(_REQUEST_BODY)
    assert len(jobs) == 1
    assert jobs[0]["company"] == "株式会社サンプルテック"
    assert jobs[0]["title"] == "プロダクトマネージャー 0→1の新規事業"
    assert jobs[0]["source_id"] == "108000001"


def test_recommend_mail_format_unchanged():
    body = (
        "▼株式会社サンプル/PdM\n［想定年収］600～900万円\n［勤務地］東京都\n"
        "https://mypage.r-agent.com/joboffers/108000002?job_referral=recommendPost\n"
    )
    jobs = _parse(body)
    assert [(j["company"], j["title"]) for j in jobs] == [("株式会社サンプル", "PdM")]
