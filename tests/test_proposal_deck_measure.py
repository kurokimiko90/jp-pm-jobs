"""deck の実測閘門と印刷出力の回帰テスト。

きっかけは実測。全 gate を通過した 4752 のパックを headless Chromium で測ると、
17 枚中 2 枚の本文が溢れていた（11 枚目 +112px / 14 枚目 +31px）。11 枚目は
指標定義の表で、5 行のうち 1 行が投影・PDF から丸ごと落ちていた。Python 側の
概算 `_estimated_height` は同じページを「376px（予算 370px）＝ 6px 超過」と
見積もっており、実測との差はおよそ 20 倍。**概算だけを閘門にすると、
面接資料から行が黙って消える。**

もう 1 つは印刷。`print-color-adjust` が無いと Chrome の「背景のグラフィック」は
既定 OFF なので、白抜き文字の要素（KGI の箱・確認済みバッジ）が白地に白で消える。
"""

import pytest

from proposal import deck_measure, deck_render

pytest.importorskip("playwright")


def _deck(*slides) -> dict:
    return {"deck": {"title": "提案", "footer": "X ／ PdM"},
            "slides": [{"layout": "cover", "title": "表紙"}, *slides,
                       {"layout": "closing", "title": "締め", "note": "n"}]}


def _table_slide(nrows: int) -> dict:
    return {"layout": "table", "title": "指標の定義",
            "structure": "定義 → 取得元 → 頻度", "lead": "先に固定します。",
            "note": "話す言葉です。",
            "table": {"columns": ["指標", "定義式", "取得元", "頻度"],
                      "rows": [["指標" + str(i), "分子 / 分母",
                                "価値判定台帳・契約情報", "月次"]
                               for i in range(nrows)]}}


def test_reasonable_slide_reports_no_overflow():
    assert deck_measure.overflow_errors(_deck(_table_slide(3))) == []


def test_overstuffed_slide_is_caught_by_measurement():
    errs = deck_measure.overflow_errors(_deck(_table_slide(9)))
    assert errs, "9 行の表は本文領域に収まらないので実測で検出されるはず"
    assert "[Deck B]" in errs[0] and "実測" in errs[0]


def test_measure_reports_one_row_per_slide():
    rows = deck_measure.measure(_deck(_table_slide(3)))
    assert [r["n"] for r in rows] == [1, 2, 3]


def test_pdf_is_written_with_one_page_per_slide(tmp_path):
    out = tmp_path / "deck.pdf"
    assert deck_measure.export_pdf(_deck(_table_slide(3)), out)
    assert out.stat().st_size > 1000
    assert out.read_bytes().startswith(b"%PDF")


def test_print_css_forces_background_colors():
    """塗りつぶしを印刷へ持ち込まないと、白抜き文字が白地に白で消える。"""
    css = deck_render._CSS
    print_block = css.split("@media print")[1]
    assert "print-color-adjust:exact" in print_block
    assert "-webkit-print-color-adjust:exact" in print_block
