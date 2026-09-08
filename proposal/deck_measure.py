"""deck の実測 — 描いてから測る（推定しない）。

`deck._estimated_height()` は Python 側で描画高さを概算する。速いし LLM への
是正指示も書けるが、**実測とはずれる**。ずれたときに起きるのは「gate は全部
通ったのに、投影すると表の最終行が消えている」という、一番気づけない壊れ方
（`.body` が `overflow:hidden` なので、切れても何のエラーも出ない）。

実測でのみ捕まえた例（4752 のパック、全 gate 通過後）:

    8 枚目 +30px / 9 枚目 +16px / 10 枚目 +90px / 11 枚目 +10px

10 枚目は JD 対応表（必須 role）で、6 行のうち最終行「データ活用への意欲」が
丸ごと落ちていた。**面接官に見せる資料から、JD 要件が 1 つ黙って消える。**

ここは推定の置き換えではなく後ろ盾。概算は LLM への即時フィードバックとして
残し（1 スライドごとに理由を書ける）、実測は「最終的に切れていないこと」を
保証する。playwright が無い環境では静かに諦める（研究層と同じ方針 —
機能が減るだけで、パック生成は止めない）。
"""

from __future__ import annotations

from pathlib import Path

from . import deck_render

# 実測の許容。フォントのヒンティングで 1〜2px は揺れるので、その分は見逃す
TOLERANCE_PX = 4

_MEASURE_JS = """() => [...document.querySelectorAll('.slide')].map((s, i) => {
  const b = s.querySelector('.body');
  return {
    n: i + 1,
    title: (s.querySelector('h1')?.textContent || '').slice(0, 30),
    overflow: b ? b.scrollHeight - b.clientHeight : 0,
  };
})"""


def _launch(html: str, fn):
    """印刷相当のレイアウト（全スライドが 1280x720・変形なし）で fn(page) を呼ぶ。

    画面表示は `transform:scale()` で viewport に合わせて縮むため、そのままだと
    測定値が viewport サイズに依存する。print メディアを模すと縮小が外れ、
    実際に投影・PDF 化されるときの版面で測れる。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                page.set_content(html, wait_until="load")
                page.emulate_media(media="print")
                page.wait_for_timeout(150)
                return fn(page)
            finally:
                browser.close()
    except Exception:
        return None


def measure(fields: dict) -> list[dict] | None:
    """各スライドの本文溢れ量（px）。測れなければ None。"""
    return _launch(deck_render.render(fields),
                   lambda page: page.evaluate(_MEASURE_JS))


def overflow_errors(fields: dict, *, tolerance: int = TOLERANCE_PX) -> list[str]:
    """実測で切れているスライドを Deck B の是正指示に翻訳する。

    測れなかった場合は空（playwright が無い環境で gate を厳しくしない）。
    """
    rows = measure(fields)
    if not rows:
        return []
    errs = []
    for r in rows:
        over = int(r.get("overflow") or 0)
        if over <= tolerance:
            continue
        errs.append(
            f"[Deck B] {r['n']} 枚目「{r.get('title', '')}」: 実測で本文が {over}px "
            f"溢れており、下端のブロックが投影・PDF で切れる。ブロックを 1 つ "
            f"減らすか、行数を削るか、2 枚に割ること")
    return errs


def export_pdf(fields: dict, pdf_file: Path) -> bool:
    """投影 HTML と同じ版面の PDF を書き出す。成否を返す。

    面接で実際に渡す/画面共有するのは PDF なので、人が Chrome の印刷ダイアログで
    「背景のグラフィック」に気づけるかどうかに成果物を賭けない。
    `print_background=True` を固定し、`@page` の 1280x720 をそのまま使う。
    """
    def _run(page):
        page.pdf(path=str(pdf_file), print_background=True,
                 prefer_css_page_size=True)
        return True

    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    return bool(_launch(deck_render.render(fields), _run))
