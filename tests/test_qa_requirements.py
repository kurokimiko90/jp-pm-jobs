"""Gate B が見る「JD 要件」の取り出しのテスト（零 LLM）。

gap_analysis の requirements は内部閲覧用に中国語で書かれている。日本語の回答と
語が一致しないため、そのまま使うと覆蓋が常に 0 付近へ倒れる（実測 0/5）。
JD 本文から日本語で拾えたときはそちらを使う、という切り替えを検査する。
"""

from interview.qa import build

RAGENT_JD = """\
**仕事内容**
データ活用支援を手掛ける当社にて、新規事業の成長を担います。

**求める能力・経験**
【必須】■IT領域のプロダクトマネジメント経験または開発の上流工程（要件定義、基本設計）経験　■プロジェクトにおける関係者との折衝や調整の経験
【求める人物像】■当社が掲げるValueへの共感
■曖昧、抽象的な事象を整理・仕組み化することができる方

**学歴**
大学、大学院
"""


def test_extracts_japanese_requirements_from_jd():
    items = build._jd_requirements({"raw_jd": RAGENT_JD})
    assert any("プロダクトマネジメント経験" in item for item in items)
    assert any("折衝" in item for item in items)
    assert any("仕組み化" in item for item in items)


def test_extraction_strips_bracket_labels_and_stops_at_next_section():
    items = build._jd_requirements({"raw_jd": RAGENT_JD})
    assert all(not item.startswith("【") for item in items)
    # 次の見出し（**学歴**）以降は拾わない
    assert all("大学" not in item for item in items)
    # 見出しより前の本文も拾わない
    assert all("新規事業の成長" not in item for item in items)


def test_extraction_returns_empty_when_no_requirement_section():
    assert build._jd_requirements({"raw_jd": "**仕事内容**\nデータ活用支援です。"}) == []
    assert build._jd_requirements({"raw_jd": None}) == []


def test_extraction_drops_fragments_that_are_too_short():
    jd = "**応募資格**\n■PM\n■3年以上のプロダクトマネジメント経験があること\n"
    items = build._jd_requirements({"raw_jd": jd})
    assert items == ["3年以上のプロダクトマネジメント経験があること"]


def test_extraction_caps_item_count():
    bullets = "\n".join(f"■要件{i}についての十分に長い説明文です" for i in range(20))
    items = build._jd_requirements({"raw_jd": f"**必須条件**\n{bullets}\n"})
    assert len(items) == build.MAX_JD_REQUIREMENTS


def test_requirements_prefers_jd_over_gap_analysis(monkeypatch):
    monkeypatch.setattr(build, "_load", lambda job: {})
    monkeypatch.setattr(build, "_items", lambda data, key: ["中文的產品管理經驗"])
    items = build._requirements({"raw_jd": RAGENT_JD})
    assert any("プロダクトマネジメント経験" in item for item in items)
    assert "中文的產品管理經驗" not in items


def test_requirements_falls_back_to_gap_analysis(monkeypatch):
    monkeypatch.setattr(build, "_load", lambda job: {})
    monkeypatch.setattr(build, "_items", lambda data, key: ["中文的產品管理經驗"])
    assert build._requirements({"raw_jd": ""}) == ["中文的產品管理經驗"]


def test_extraction_drops_recruiting_pitch_lines():
    """≪急成長ベンチャー≫ のような求人票の売り文句は要件ではない。"""
    jd = ("**応募資格**\n■3年以上のプロダクトマネジメント経験\n"
          "≪急成長EVベンチャー≫ 圧倒的成長率で業界をリードしています\n")
    assert build._jd_requirements({"raw_jd": jd}) == ["3年以上のプロダクトマネジメント経験"]
