"""tools.refetch_jd._fetch_ashby / _fetch_workable — board-wide清單からの単筆抽出。

greenhouse/lever は単筆 API だが ashby/workable は board 全体を返す → URL/shortcode
比對のロジックが壊れていないかをここで固定する（実 HTTP は httpx.MockTransport で代替）。
"""
import httpx
import pytest

from tools import refetch_jd


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── ashby ────────────────────────────────────────────────────────────────

def test_ashby_matches_job_by_url_and_strips_html():
    url = "https://jobs.ashbyhq.com/acme/abc-123"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.ashbyhq.com"
        assert "acme" in str(request.url)
        return httpx.Response(200, json={"jobs": [
            {"jobUrl": url, "descriptionPlain": "<p>PM 募集</p>  改行あり"},
            {"jobUrl": "https://jobs.ashbyhq.com/acme/other", "descriptionPlain": "別の求人"},
        ]})

    text, liveness = refetch_jd._fetch_ashby(url, _client(handler))
    assert liveness == "active"
    assert "PM 募集" in text
    assert "<p>" not in text


def test_ashby_not_in_board_list_is_expired():
    url = "https://jobs.ashbyhq.com/acme/gone"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [
            {"jobUrl": "https://jobs.ashbyhq.com/acme/other", "descriptionPlain": "別の求人"},
        ]})

    assert refetch_jd._fetch_ashby(url, _client(handler)) == ("", "expired")


def test_ashby_board_404_is_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    text, liveness = refetch_jd._fetch_ashby("https://jobs.ashbyhq.com/acme/x", _client(handler))
    assert (text, liveness) == ("", "expired")


def test_ashby_non_matching_url_is_uncertain():
    text, liveness = refetch_jd._fetch_ashby("https://example.com/j/1", _client(lambda r: pytest.fail("should not fetch")))
    assert (text, liveness) == ("", "uncertain")


# ── workable ─────────────────────────────────────────────────────────────

def test_workable_matches_job_by_shortcode():
    url = "https://apply.workable.com/acme/j/AB12CD34/"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "apply.workable.com"
        return httpx.Response(200, json={"jobs": [
            {"shortcode": "AB12CD34", "url": url, "description": "<div>PM 募集</div>"},
            {"shortcode": "ZZ99", "url": "https://apply.workable.com/acme/j/ZZ99/", "description": "別の求人"},
        ]})

    text, liveness = refetch_jd._fetch_workable(url, _client(handler))
    assert liveness == "active"
    assert "PM 募集" in text
    assert "<div>" not in text


def test_workable_not_in_account_list_is_expired():
    url = "https://apply.workable.com/acme/j/GONE000/"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [
            {"shortcode": "ZZ99", "url": "https://apply.workable.com/acme/j/ZZ99/", "description": "別の求人"},
        ]})

    assert refetch_jd._fetch_workable(url, _client(handler)) == ("", "expired")


def test_workable_account_404_is_expired():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    text, liveness = refetch_jd._fetch_workable("https://apply.workable.com/acme/j/X/", _client(handler))
    assert (text, liveness) == ("", "expired")
