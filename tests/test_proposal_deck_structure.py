"""deck の構造版式（flow / swimlane / structure 線）の回帰テスト。

見出し＋箇条書きだけのページが並ぶ資料は、投影しても論理が読み取れない
（結論だけが出て、根拠と仕組みは口頭補足になる）。構造を持つ版式を最低限
使わせるのが Deck D の役目。実測で 12 枚中 8 枚に構造線が無かったのが発端。
"""

import json

from proposal import deck, deck_render

JOB = {"id": 1, "company": "テスト株式会社", "title": "PdM", "raw_jd": "PdM 募集"}


def _slide(**kw) -> dict:
    base = {"layout": "bullets", "title": "見出しです", "structure": "A → B",
            "lead": "骨格の理由です。", "note": "話す言葉です。"}
    base.update(kw)
    return base


def _deck(*slides) -> dict:
    return {"deck": {"title": "提案", "footer": "X ／ PdM"},
            "slides": [{"layout": "cover", "title": "表紙"}, *slides,
                       {"layout": "closing", "title": "締め", "note": "n"}]}


# ── 描画 ────────────────────────────────────────────────


def test_flow_renders_numbered_steps():
    html = deck_render.render(_deck(_slide(
        layout="flow",
        steps=[{"label": "確かめる", "caption": "離脱地点を特定します",
                "sub": "実現: 計測基盤"},
               {"label": "直す", "caption": "導線を作り直します"}])))
    assert 'class="flow-band"' in html
    assert "確かめる" in html and "離脱地点を特定します" in html
    assert '<span class="fnum">1</span>' in html
    assert '<span class="fnum">2</span>' in html


def test_swimlane_renders_lane_and_cells():
    html = deck_render.render(_deck(_slide(
        layout="swimlane",
        lanes=[{"name": "何が変わるか", "note": "利用者から見える変化",
                "cells": [{"heading": "選択前", "body": "空き状況が分かります",
                           "meta": "打ち手 1"},
                          {"heading": "利用後", "body": "履歴が残ります"}]}])))
    assert 'class="lanes"' in html
    assert "何が変わるか" in html
    assert "空き状況が分かります" in html
    assert "打ち手 1" in html


def test_structure_line_splits_on_arrows():
    html = deck_render.render(_deck(_slide(structure="選択 → 開始 → 完了")))
    assert html.count('class="seg"') == 3
    assert '<span class="arw">→</span>' in html


def test_structure_line_without_arrow_stays_one_segment():
    html = deck_render.render(_deck(_slide(structure="一続きの骨格")))
    assert html.count('class="seg"') == 1


def test_arch_items_accept_name_and_desc():
    """箱の名前だけの図は「それが何をするのか」が読み取れない。"""
    html = deck_render.render(_deck(_slide(
        layout="arch",
        layers=[{"name": "体験層",
                 "items": [{"name": "予約画面", "desc": "空きを見て押さえる"},
                           "既存アプリ"]}])))
    assert "予約画面" in html
    assert "空きを見て押さえる" in html
    assert "既存アプリ" in html          # 文字列のままでも壊れない


def test_footnote_renders_with_label():
    html = deck_render.render(_deck(_slide(
        footnote={"label": "対象外", "text": "課金体系の変更は含みません"})))
    assert 'class="footnote"' in html
    assert "対象外" in html
    assert "課金体系の変更は含みません" in html


def test_blocks_stack_in_one_slide():
    """1 枚に工程帯＋泳道を積めること（密度はこの積み方から出る）。"""
    html = deck_render.render(_deck(_slide(
        layout="flow",
        steps=[{"label": "S1", "caption": "c1"}],
        lanes=[{"name": "L1", "cells": [{"heading": "h", "body": "b"}]}])))
    assert 'class="flow-band"' in html and 'class="lanes"' in html


def test_statement_lead_is_not_duplicated_as_leadin():
    """statement は lead を本文で大きく出す。見出し部で二度出さない。"""
    html = deck_render.render(_deck(_slide(layout="statement", lead="主張です。")))
    assert html.count("主張です。") == 1
    assert 'class="leadin"' not in html


# ── 閘門 ────────────────────────────────────────────────


def test_gate_d_rejects_deck_without_structured_layouts():
    fields = _deck(*[_slide(bullets=[{"text": f"点 {i}"}]) for i in range(9)])
    errs = deck.check(fields, JOB, "")
    assert any("[Deck D] 構造を持つページ" in e for e in errs)


def test_gate_d_rejects_missing_structure_line():
    fields = _deck(*[_slide(layout="cards", structure=None,
                            cards=[{"heading": "h", "body": "b"}])
                     for _ in range(9)])
    errs = deck.check(fields, JOB, "")
    assert any("structure" in e for e in errs)


def test_gate_d_allows_structureless_cover_closing_qa():
    """表紙・締め・想定質問には構造線を求めない。"""
    fields = _deck(*[_slide(layout="flow",
                            steps=[{"label": "S", "caption": "c"}])
                     for _ in range(8)],
                   {"layout": "qa", "title": "想定質問",
                    "qa": [{"q": "q", "a": "a"}], "note": "n"})
    errs = deck.check(fields, JOB, "")
    assert not any("[Deck D]" in e for e in errs), errs


def test_structured_deck_passes_gate_d():
    fields = _deck(
        _slide(layout="flow", steps=[{"label": "S1", "caption": "c1"}]),
        _slide(layout="swimlane",
               lanes=[{"name": "L", "cells": [{"heading": "h", "body": "b"}]}]),
        _slide(layout="arch", layers=[{"name": "層", "items": ["箱"]}]),
        _slide(layout="table",
               table={"columns": ["a", "b"], "rows": [["1", "2"]]}),
        _slide(layout="tree", kgi="K",
               drivers=[{"name": "d", "leading": "l"}]),
        _slide(layout="timeline",
               phases=[{"period": "0-30", "title": "t", "items": ["i"]}]),
        _slide(layout="cards", cards=[{"heading": "h", "body": "b"}]),
        _slide(layout="bullets", bullets=[{"text": "点"}]),
    )
    errs = [e for e in deck.check(fields, JOB, "") if "[Deck D]" in e]
    assert not errs, errs


def test_layouts_registered():
    for lay in ("flow", "swimlane"):
        assert lay in deck_render.LAYOUTS


def test_plain_text_covers_new_blocks():
    """Deck B（字数）と Gate B（数字錨定）が新版式の中身も見ていること。"""
    text = deck_render.plain_text(_deck(_slide(
        layout="flow",
        steps=[{"label": "ラベル語", "caption": "説明語"}],
        lanes=[{"name": "レーン語", "cells": [{"heading": "見出語",
                                               "body": "本文語"}]}],
        footnote={"label": "脚注ラベル", "text": "脚注本文"})))
    for word in ("ラベル語", "説明語", "レーン語", "見出語", "本文語",
                 "脚注ラベル", "脚注本文"):
        assert word in text, word


def test_existing_fields_still_render(tmp_path):
    """既存パックの fields（構造線なし）でも描画は壊れない。"""
    fields = _deck({"layout": "cards", "title": "旧形式",
                    "cards": [{"heading": "h", "body": "b"}], "note": "n"})
    html = deck_render.render(fields)
    assert "旧形式" in html
    json.loads(json.dumps(fields))  # シリアライズ可能であること


# ------------------------------------------------- 高さ予算（Deck B）の頭の分
# 見出しが折り返すと本文領域はその分狭くなる。この補正が無いと「概算 340px <
# 予算 370px」で通ったページの一番下が投影で切れる（実測: 数字カードの meta が
# 丸ごと消え、そのページで一番見せたい実績値だけが落ちた）

def _slide_with(title: str, lead: str = "") -> dict:
    return {"layout": "flow", "role": "design_detail", "title": title,
            "lead": lead, "note": "話す", "structure": "A → B",
            "steps": [{"label": "入力", "caption": "ログ"}],
            "lanes": [{"name": "最小実装", "cells": [{"heading": "台帳",
                                                      "body": "結合"}]}],
            "footnote": {"label": "対象外", "text": "画面開発は含まない"}}


def test_long_title_shrinks_the_height_budget():
    short, long = _slide_with("短い見出し"), _slide_with("あ" * 62)
    assert deck.height_budget(long) < deck.height_budget(short)
    # 本体は同じなのに、見出しが 2 行分伸びた側だけ詰め込み判定になる
    assert deck._estimated_height(long) == deck._estimated_height(short)
    assert deck._estimated_height(short) <= deck.height_budget(short)
    assert deck._estimated_height(long) > deck.height_budget(long)


def test_wide_table_rows_count_as_wrapped():
    """欄が増えるとセル幅が狭くなり、同じ文字数でも折り返して行が高くなる。"""
    cell = "あ" * 18
    narrow = {"layout": "table", "title": "表", "note": "話す",
              "table": {"columns": ["a", "b"], "rows": [[cell, cell]] * 5}}
    wide = {"layout": "table", "title": "表", "note": "話す",
            "table": {"columns": list("abcde"),
                      "rows": [[cell] * 5] * 5}}
    assert deck._estimated_height(wide) > deck._estimated_height(narrow)


# ------------------------------------------------------- data_case の数字（Deck C）

def _data_case(metas: list[str]) -> dict:
    return {"layout": "cards", "role": "data_case", "title": "実例", "note": "話す",
            "cards": [{"heading": "見出し", "body": "本文", "meta": m}
                      for m in metas]}


def _wrap(slide: dict) -> dict:
    """cover / closing で挟む。role 欠落エラーで 12 件打ち切りに埋もれるのを防ぐ。"""
    return {"slides": [{"layout": "cover", "title": "表紙"}, slide,
                       {"layout": "closing", "title": "締め", "note": "話す"}]}


def test_data_case_without_numbers_is_rejected():
    errs = deck.check(_wrap(_data_case(["定量的に改善", "大幅に削減"])),
                      JOB, corpus="")
    assert any("data_case" in e and "数字" in e for e in errs)


def test_data_case_with_numbers_passes():
    errs = deck.check(_wrap(_data_case(["合格率85%未満で自動停止",
                                        "12回→1回", "生産6指標"])),
                      JOB, corpus="")
    assert not [e for e in errs if "data_case" in e]


def test_overlong_table_row_is_named_with_char_count():
    """溢れの原因が表の折り返しなら、行と字数を名指しして「語を短く」と言う。

    原因を書かずに「2 枚に割る」だけを選ばせると、LLM は表を分割して
    両方すかすかのページにする（実測: 5 行の指標定義が 2 行＋3 行に割れた）。
    """
    row = ["あ" * 20, "い" * 28, "う" * 11, "月次"]     # 合計 61 字（4 欄）
    slide = {"layout": "table", "role": "metrics_spec", "title": "指標",
             "structure": "A → B", "lead": "理由。", "note": "話す",
             "table": {"columns": ["指標", "定義式", "取得元", "頻度"],
                       "rows": [row]}}
    errs = deck.check(_wrap(slide), JOB, corpus="")
    table_errs = [e for e in errs if "枚目の表" in e]
    assert len(table_errs) == 1
    assert "1 行目 61 字" in table_errs[0]
    assert "語を短く" in table_errs[0]


def test_short_table_rows_are_not_flagged():
    row = ["あ" * 12, "い" * 16, "う" * 8, "月次"]        # 合計 40 字
    slide = {"layout": "table", "role": "metrics_spec", "title": "指標",
             "structure": "A → B", "lead": "理由。", "note": "話す",
             "table": {"columns": ["指標", "定義式", "取得元", "頻度"],
                       "rows": [row] * 5}}
    errs = deck.check(_wrap(slide), JOB, corpus="")
    assert not [e for e in errs if "枚目の表" in e]


def test_six_row_table_is_told_to_split_not_trim():
    """6 行の表は最終行が切れる。要件を落とさせず、ページを割らせる。

    prompt に「6 行まで」と書いていたのが誤りで、JD 対応表の 6 行目
    （社外折衝＝必須要件）が投影・PDF から消えかけた。
    """
    row = ["あ" * 8, "い" * 10, "う" * 8]
    slide = {"layout": "table", "role": "jd_map", "title": "対応",
             "structure": "A → B", "lead": "理由。", "note": "話す",
             "table": {"columns": ["要件", "場所", "実例"], "rows": [row] * 6}}
    errs = [e for e in deck.check(_wrap(slide), JOB, corpus="") if "行。" in e]
    assert len(errs) == 1 and "2 枚に" in errs[0]
    # 5 行なら通る
    slide["table"]["rows"] = [row] * 5
    assert not [e for e in deck.check(_wrap(slide), JOB, corpus="") if "行。" in e]
