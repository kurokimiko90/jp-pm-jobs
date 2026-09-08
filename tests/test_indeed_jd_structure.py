"""scrapers.indeed_jp.structure_indeed_jd の回帰テスト。

Indeed は集約サイトで掲載元ごとに JD の書式が違うため、「壊さない」ことが
一番の要件。本文を落とさない・何度通しても同じ（既存データの一括再整形が
安全）・未知の書式は素通し、をここで固定する。
"""

from __future__ import annotations

import pytest

from scrapers.indeed_jp import structure_indeed_jd


def _strip_markers(text: str) -> str:
    return text.replace("## ", "").replace("**", "")


class TestNoiseRemoval:
    def test_drops_header_search_box(self):
        raw = "キーワード\n勤務地\n求人検索\nPM 募集\n株式会社テスト"
        out = structure_indeed_jd(raw)
        assert "求人検索" not in out
        assert "PM 募集" in out

    def test_only_slices_search_box_near_the_top(self):
        """先頭 6 行より後の「求人検索」で本文を切り落とさない。"""
        raw = "\n".join(["仕事内容"] + [f"本文 {i}" for i in range(8)] + ["求人検索", "末尾"])
        out = structure_indeed_jd(raw)
        assert "本文 0" in out and "末尾" in out

    @pytest.mark.parametrize("noise", [
        "&nbsp;", "応募画面に進む", "問題を報告する", "30+日前", "slide2 of 3", "1 / 3",
        "____________________",
    ])
    def test_drops_ui_noise(self, noise):
        out = structure_indeed_jd(f"## 仕事内容\n本文テキスト\n{noise}")
        assert noise not in out
        assert "本文テキスト" in out


class TestPromotion:
    def test_section_label(self):
        assert "## 仕事内容" in structure_indeed_jd("仕事内容\n本文")

    def test_field_label(self):
        assert "**想定年収**" in structure_indeed_jd("想定年収\n400万円～700万円")

    def test_bracket_heading(self):
        assert "**必須条件**" in structure_indeed_jd("【必須条件】\n・PM 経験 3 年")

    def test_symbol_heading(self):
        assert "**法人営業**" in structure_indeed_jd("■法人営業\n・移転計画のヒアリング")

    def test_symbol_wrapped_both_sides(self):
        assert "**業務内容**" in structure_indeed_jd("◇◇ 業務内容 ◇◇\n・要件定義")

    def test_symbol_bullet_with_colon_stays_body(self):
        """「■ラベル：値」は箇条書きの本文行なので見出しにしない。"""
        raw = "■開発業務：API 設計\n■課題解決：技術検証"
        assert "**" not in structure_indeed_jd(raw)

    def test_symbol_sentence_stays_body(self):
        raw = "■特定技能外国人の業務・生活支援アプリ開発を推進するPM業務を担当。"
        assert "**" not in structure_indeed_jd(raw)

    def test_bracket_heading_not_promoted_to_section(self):
        """【仕事内容】は掲載元が本文に書いた小見出し。大区分に昇格させない。"""
        out = structure_indeed_jd("【仕事内容】\n・顧客プロジェクトの推進")
        assert "**仕事内容**" in out
        assert "## " not in out


class TestPlainFallback:
    def test_promotes_plain_headings_when_nothing_else_found(self):
        raw = "Job Description\n\nWe are hiring.\n\nBusiness Overview\n\nRakuten is large."
        out = structure_indeed_jd(raw)
        assert "**Job Description**" in out
        assert "**Business Overview**" in out

    def test_not_applied_when_markers_exist(self):
        """記号や既知ラベルがある JD では体裁だけの判定を持ち込まない。"""
        raw = "## 仕事内容\n\nProduct Manager\n\nWe are hiring."
        assert "**Product Manager**" not in structure_indeed_jd(raw)

    def test_single_candidate_is_not_a_structure(self):
        raw = "Overview\n\nWe are hiring a product manager for our team."
        assert "**" not in structure_indeed_jd(raw)


class TestContract:
    def test_idempotent(self):
        raw = (
            "キーワード\n勤務地\n求人検索\n仕事の内容\n■特定技能アプリ開発を担当。\n"
            "【仕事内容】\n・推進\n◇◇ 業務内容 ◇◇\n・要件定義\n想定年収\n400万円"
        )
        once = structure_indeed_jd(raw)
        assert structure_indeed_jd(once) == once

    def test_body_text_is_preserved(self):
        raw = "仕事内容\n【具体的には】\n・要件定義\n・進行管理\n想定年収\n400万円～700万円"
        body = [line for line in raw.split("\n") if line.startswith("・") or "万円" in line]
        out = _strip_markers(structure_indeed_jd(raw))
        for line in body:
            assert line in out

    def test_unknown_format_passes_through(self):
        raw = "Some agency posting with no structure at all, all in one line."
        assert structure_indeed_jd(raw) == raw

    def test_empty_input(self):
        assert structure_indeed_jd("") == ""
