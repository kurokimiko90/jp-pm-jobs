"""context.jd_text() が JD 全文ではなく職務内容/求める能力・経験だけを拾うことの回帰テスト。

prompt に勤務地/給与/社会保険などの無関係項目を混ぜないための絞り込み。見出しは
ソースによって表記揺れがある（`## x` / `**x**`、日本語/英語）ので別名を吸収しつつ、
JAC 系ソースが要件を 1 行ずつ `**要件**` と太字強調する書式で本文を丸ごと落とさない
ことを確認する。
"""

from __future__ import annotations

from proposal import context


def test_extracts_only_target_sections():
    raw = (
        "**勤務地**\n東京都\n\n"
        "**仕事内容**\nプロダクト企画と要件定義。\n\n"
        "**給与**\n600万円\n\n"
        "**求める能力・経験**\nPM経験3年以上。\n\n"
        "**社会保険**\n雇用保険\n"
    )
    out = context._extract_jd_sections(raw)
    assert "## 職務内容" in out
    assert "プロダクト企画と要件定義。" in out
    assert "## 求める能力・経験" in out
    assert "PM経験3年以上。" in out
    assert "東京都" not in out
    assert "600万円" not in out
    assert "雇用保険" not in out


def test_bold_bullet_items_inside_target_section_are_kept():
    """JAC 系ソースは要件を 1 行ずつ `**要件**` で太字強調する。

    太字＝見出しと決め打ちすると対象節の本文が全滅する（実測: id 5079 で
    「求める能力・経験」が空になった）。
    """
    raw = (
        "## 必要な経験・能力等\n\n"
        "**必須**\n\n"
        "**システム開発の要件定義経験**\n\n"
        "**ステークホルダーとの各種調整業務経験**\n\n"
        "**学歴・資格**\n学歴：大学\n"
    )
    out = context._extract_jd_sections(raw)
    assert "システム開発の要件定義経験" in out
    assert "ステークホルダーとの各種調整業務経験" in out
    assert "大学" not in out  # 学歴・資格は対象外の見出しで打ち切られる


def test_header_aliases_are_normalized():
    raw = "## 仕事の内容\n本文A\n\n**必要な経験・能力等**\n本文B\n"
    out = context._extract_jd_sections(raw)
    assert out.count("## 職務内容") == 1
    assert "本文A" in out
    assert "## 求める能力・経験" in out
    assert "本文B" in out


def test_unknown_format_falls_back_to_full_jd():
    """既知の見出しが無い書式は安全側で全文へ fallback する。"""
    job = {"raw_jd": "こんな求人があります。詳細は面談で。"}
    assert context.jd_text(job) == "こんな求人があります。詳細は面談で。"


def test_jd_text_drops_unrelated_fields_when_sections_match():
    job = {
        "raw_jd": (
            "**勤務地**\n東京都\n\n"
            "**職務内容**\n要件定義から設計まで一貫担当。\n\n"
            "**求める能力・経験**\nSaaS開発の経験。\n\n"
            "**受動喫煙対策**\nあり\n"
        )
    }
    out = context.jd_text(job)
    assert "要件定義から設計まで一貫担当。" in out
    assert "SaaS開発の経験。" in out
    assert "受動喫煙対策" not in out
    assert "東京都" not in out
