#!/usr/bin/env python3
"""日文履歴書 + 職務経歴書 渲染 — YAML data + Jinja2 template → HTML + PDF。

用法:
    python3 -m resume.jp.render                    # 兩個都出（HTML + PDF）
    python3 -m resume.jp.render --shokumu          # 只出職務経歴書
    python3 -m resume.jp.render --rireki           # 只出履歴書
    python3 -m resume.jp.render --html-only        # 不出 PDF，只看 HTML
    python3 -m resume.jp.render --open             # 出完用瀏覽器打開預覽

輸出位置: resume/jp/output/
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"

import re
from markupsafe import Markup, escape

env = Environment(
    loader=FileSystemLoader(HERE),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def md_bold(text: str) -> Markup:
    """`**xxx**` → <strong>xxx</strong>、其餘 HTML escape。"""
    if not text:
        return Markup("")
    parts = re.split(r"\*\*(.+?)\*\*", str(text))
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(str(escape(part)))
        else:
            out.append(f"<strong>{escape(part)}</strong>")
    return Markup("".join(out))


env.filters["md_bold"] = md_bold


def load_data() -> dict:
    data = yaml.safe_load((HERE / "data.yaml").read_text(encoding="utf-8"))
    data["today"] = date.today().strftime("%Y年%m月%d日")
    return data


def render_html(template_name: str, data: dict, total_pages: int) -> str:
    tmpl = env.get_template(template_name)
    return tmpl.render(total_pages=total_pages, **data)


def write_html(name: str, html: str) -> Path:
    OUT.mkdir(exist_ok=True)
    # copy style.css alongside HTML so 瀏覽器能直接看
    shutil.copy(HERE / "style.css", OUT / "style.css")
    path = OUT / f"{name}.html"
    path.write_text(html, encoding="utf-8")
    return path


def export_pdf(html_path: Path) -> Path:
    """用 Playwright headless Chromium 出 PDF。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ Playwright 未安裝：pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)

    pdf_path = html_path.with_suffix(".pdf")
    url = f"file://{html_path.resolve()}"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1240, "height": 1754})
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle")
        page.emulate_media(media="print")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
        )
        browser.close()
    return pdf_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shokumu", action="store_true", help="只渲染職務経歴書")
    parser.add_argument("--rireki", action="store_true", help="只渲染履歴書")
    parser.add_argument("--html-only", action="store_true", help="不出 PDF")
    parser.add_argument("--open", action="store_true", help="出完用瀏覽器打開")
    args = parser.parse_args()

    data = load_data()
    targets: list[tuple[str, str, int]] = []
    do_all = not (args.shokumu or args.rireki)

    if args.shokumu or do_all:
        # 2 頁固定排版：P1 = 要約+強み+現職+AI実運用 / P2 = 過去経歴+スキル+学歴+PR
        targets.append(("shokumu-keirekisho", "shokumu-keirekisho-2p.html.j2.html", 2))

    if args.rireki or do_all:
        targets.append(("rirekisho", "rirekisho.html.j2.html", 1))

    for name, tmpl_file, total in targets:
        print(f"\n→ {name}")
        html = render_html(tmpl_file, data, total)
        html_path = write_html(name, html)
        print(f"  HTML: {html_path}")
        if not args.html_only:
            pdf_path = export_pdf(html_path)
            print(f"  PDF:  {pdf_path}")
        if args.open:
            subprocess.run(["open", str(html_path)])

    print("\n完成。記得把 data.yaml 內 `{{...}}` placeholders 全部填掉再投出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
