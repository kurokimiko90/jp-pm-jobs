"""scrapers/ragent_search — 純関数パース（ブラウザを起動しない部分）。"""

import scrapers.ragent_search as rs
from scrapers.ragent_search import _clean_detail, _load_seen, _parse_card, _save_seen

# 実際の一覧カード innerText（全角社名・タグ行・ボタン行込み）
CARD_TEXT = """株式会社ＺＯＺＯ　ＮＥＸＴ
【新規事業企画〜PM業務】◆ZOZOグループ◆在宅勤務を推奨！フルフレックス◎
年収
679〜1152万
勤務地
千葉県千葉市稲毛区、東京都千代田区
休日
126日
業界未経験OK
気になる"""


def _card(text: str, sid: str = "108009088") -> dict:
    return {"source_id": sid, "href": f"/joboffers/{sid}?tracking_id=abc", "text": text}


class TestParseCard:
    def test_company_and_title(self):
        c = _parse_card(_card(CARD_TEXT))
        assert c["company"] == "株式会社ＺＯＺＯ　ＮＥＸＴ"
        assert c["title"].startswith("【新規事業企画〜PM業務】")

    def test_salary_range_parsed(self):
        c = _parse_card(_card(CARD_TEXT))
        assert (c["salary_min"], c["salary_max"]) == (679, 1152)

    def test_location(self):
        c = _parse_card(_card(CARD_TEXT))
        assert c["location"] == "千葉県千葉市稲毛区、東京都千代田区"

    def test_button_lines_not_treated_as_title(self):
        """「気になる」等のボタン行が title/location に混ざらない。"""
        c = _parse_card(_card("株式会社テスト\n気になる\n【PdM】募集\n年収\n800〜1200万"))
        assert c["title"] == "【PdM】募集"

    def test_salary_single_value(self):
        c = _parse_card(_card("株式会社テスト\n【PdM】\n年収\n800万"))
        assert (c["salary_min"], c["salary_max"]) == (800, None)

    def test_missing_salary_is_none(self):
        c = _parse_card(_card("株式会社テスト\n【PdM】\n勤務地\n東京都渋谷区"))
        assert c["salary_min"] is None and c["salary_max"] is None
        assert c["location"] == "東京都渋谷区"

    def test_too_short_returns_none(self):
        assert _parse_card(_card("株式会社テスト")) is None
        assert _parse_card(_card("")) is None

    def test_href_and_source_id_kept(self):
        c = _parse_card(_card(CARD_TEXT, sid="12345"))
        assert c["source_id"] == "12345"
        assert "tracking_id" in c["href"]  # 詳細取得には tracking 付き href が必須


class TestCleanDetail:
    def test_nav_lines_removed(self):
        lines = _clean_detail(
            "ホーム\n求人ポスト\n求人検索\n気になる\nメッセージ\n"
            "株式会社テスト\n仕事内容\nプロダクト戦略の立案\n"
            "応募する\n興味なし\n募集要項\n選考・企業概要"
        )
        assert lines == ["株式会社テスト", "仕事内容", "プロダクト戦略の立案"]

    def test_blank_lines_dropped(self):
        assert _clean_detail("A\n\n  \nB") == ["A", "B"]

    def test_footer_removed(self):
        lines = _clean_detail("仕事内容\nヘルプ・お問い合わせ\n利用規約\nプライバシーポリシー")
        assert lines == ["仕事内容"]


class TestSeenStore:
    """取得済み id の永続化 — DB の「同公司併入」で source_id が消える分を補う。"""

    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rs, "_SEEN_PATH", tmp_path / "seen.json")
        _save_seen({"111", "222"})
        assert _load_seen() == {"111", "222"}

    def test_missing_file_is_empty_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rs, "_SEEN_PATH", tmp_path / "nope.json")
        assert _load_seen() == set()

    def test_corrupt_file_is_empty_set(self, tmp_path, monkeypatch):
        p = tmp_path / "seen.json"
        p.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(rs, "_SEEN_PATH", p)
        assert _load_seen() == set()

    def test_save_creates_parent_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rs, "_SEEN_PATH", tmp_path / "sub" / "seen.json")
        _save_seen({"999"})
        assert _load_seen() == {"999"}
