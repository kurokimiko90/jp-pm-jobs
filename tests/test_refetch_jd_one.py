"""tools.refetch_jd.fetch_one — 単一求人の JD 補抓ディスパッチ。

Dashboard の「gap 分析」ボタンは raw_jd が空だと 400 で行き止まりになっていた。
source ごとの取得経路と、ログイン必須サイトで Chrome を勝手に起動しないことを固定する。
"""
import pytest

from tools import refetch_jd


def _job(source: str, url: str = "https://example.com/j/1") -> dict:
    return {"id": 1, "source": source, "url": url, "company": "テスト社", "title": "PM"}


def test_no_url_is_unsupported():
    text, liveness = refetch_jd.fetch_one(_job("manual", ""))
    assert text == ""
    assert liveness == "unsupported"


def test_greenhouse_goes_through_http_api(monkeypatch):
    monkeypatch.setattr(refetch_jd, "_fetch_greenhouse",
                        lambda url, client: ("JD 本文", "active"))
    text, liveness = refetch_jd.fetch_one(_job("greenhouse-api", "https://x/jobs/123"))
    assert text == "JD 本文"
    assert liveness == "active"


def test_lever_goes_through_http_api(monkeypatch):
    monkeypatch.setattr(refetch_jd, "_fetch_lever",
                        lambda url, client: ("JD", "active"))
    assert refetch_jd.fetch_one(_job("lever-api"))[0] == "JD"


def test_ashby_goes_through_http_api(monkeypatch):
    monkeypatch.setattr(refetch_jd, "_fetch_ashby",
                        lambda url, client: ("JD", "active"))
    assert refetch_jd.fetch_one(_job("ashby-api"))[0] == "JD"


def test_workable_goes_through_http_api(monkeypatch):
    monkeypatch.setattr(refetch_jd, "_fetch_workable",
                        lambda url, client: ("JD", "active"))
    assert refetch_jd.fetch_one(_job("workable-api"))[0] == "JD"


def test_indeed_falls_back_to_headless_without_cdp(monkeypatch):
    monkeypatch.setattr(refetch_jd, "_port_open", lambda port: False)
    monkeypatch.setattr(refetch_jd, "_fetch_indeed_batch",
                        lambda jobs, dry_run: {jobs[0]["id"]: ("indeed JD", "active")})
    text, liveness = refetch_jd.fetch_one(_job("indeed_jp"))
    assert (text, liveness) == ("indeed JD", "active")


def test_indeed_prefers_open_cdp_over_headless(monkeypatch):
    """headless は bot 判定で login wall に飛ぶ。CDP が開いていればそちらを使う。"""
    monkeypatch.setattr(refetch_jd, "_port_open", lambda port: True)
    monkeypatch.setattr(refetch_jd, "_fetch_via_cdp",
                        lambda url, port, sel="": ("cdp JD", "active"))
    monkeypatch.setattr(refetch_jd, "_fetch_indeed_batch",
                        lambda jobs, dry_run: pytest.fail("CDP が開いているのに headless を使った"))
    assert refetch_jd.fetch_one(_job("indeed_jp")) == ("cdp JD", "active")


def test_indeed_headless_bot_wall_reports_needs_login(monkeypatch):
    monkeypatch.setattr(refetch_jd, "_port_open", lambda port: False)
    monkeypatch.setattr(refetch_jd, "_fetch_indeed_batch",
                        lambda jobs, dry_run: {jobs[0]["id"]: ("", "error")})
    assert refetch_jd.fetch_one(_job("indeed_jp")) == ("", "needs_login")


def test_login_required_source_does_not_launch_chrome(monkeypatch):
    """CDP ポートが閉じているなら Chrome を起動せず needs_login を返す。"""
    monkeypatch.setattr(refetch_jd, "_port_open", lambda port: False)
    launched = []
    monkeypatch.setattr(refetch_jd, "_fetch_via_cdp",
                        lambda url, port, sel="": launched.append(port) or ("x", "active"))
    text, liveness = refetch_jd.fetch_one(_job("bizreach"))
    assert (text, liveness) == ("", "needs_login")
    assert launched == []


def test_login_required_source_uses_open_cdp_port(monkeypatch):
    monkeypatch.setattr(refetch_jd, "_port_open", lambda port: True)
    seen = {}
    def _fake(url, port, sel=""):
        seen["port"] = port
        return ("bizreach JD", "active")
    monkeypatch.setattr(refetch_jd, "_fetch_via_cdp", _fake)
    text, liveness = refetch_jd.fetch_one(_job("bizreach"))
    assert (text, liveness) == ("bizreach JD", "active")
    assert seen["port"] == 9270


def test_unknown_source_is_unsupported():
    assert refetch_jd.fetch_one(_job("some_new_site"))[1] == "unsupported"


def test_login_wall_text_is_not_saved_as_jd(monkeypatch):
    """ページは開けても中身がログイン壁なら JD として扱わない。"""
    monkeypatch.setattr(refetch_jd, "_port_open", lambda port: True)
    wall = "転職・求人情報の詳細をご覧になる場合は会員登録（無料）が必要です\n新規会員登録"
    monkeypatch.setattr(refetch_jd, "_fetch_via_cdp",
                        lambda url, port, sel="": (wall, "active"))
    assert refetch_jd.fetch_one(_job("bizreach")) == ("", "needs_login")


def test_indeed_headless_login_wall_is_rejected(monkeypatch):
    monkeypatch.setattr(refetch_jd, "_port_open", lambda port: False)
    monkeypatch.setattr(refetch_jd, "_fetch_indeed_batch",
                        lambda jobs, dry_run: {jobs[0]["id"]: ("Authenticating...", "active")})
    assert refetch_jd.fetch_one(_job("indeed_jp")) == ("", "needs_login")
