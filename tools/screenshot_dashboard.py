"""Capture every visible Dashboard page as a verified full-page PNG.

Usage:
    python3 -m tools.screenshot_dashboard
    python3 -m tools.screenshot_dashboard --url http://127.0.0.1:8000 \
        --output-dir output/screenshots/manual

The Dashboard must already be running.  If Basic Auth is enabled, export
``DASHBOARD_PASSWORD`` before invoking this command; the password is never
written to the manifest or command output.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Locator, Page, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class PageSpec:
    filename: str
    sidebar_index: int
    tab_index: int | None = None


# Sidebar and hub-tab positions mirror dashboard/frontend/src/App.tsx,
# JobsHub.tsx, and ApplicationsHub.tsx.  Indices avoid coupling automation to
# the current zh/ja/en display language or to badges appended to accessible names.
PAGE_SPECS = (
    PageSpec("01-today.png", 0),
    PageSpec("02-jobs-all.png", 1, 0),
    PageSpec("03-jobs-recommendations.png", 1, 1),
    PageSpec("04-jobs-reports.png", 1, 2),
    PageSpec("05-analytics.png", 2),
    PageSpec("06-applications.png", 3, 0),
    PageSpec("07-apply-strategy.png", 3, 1),
    PageSpec("08-direct-apply.png", 3, 2),
    PageSpec("09-inbox.png", 4),
    PageSpec("10-apply-packs.png", 5),
    PageSpec("11-interview-packs.png", 6),
    PageSpec("12-dojo.png", 7),
    PageSpec("13-profile.png", 8),
    PageSpec("14-genki.png", 9),
    PageSpec("15-scoring.png", 10),
)


def parse_args() -> argparse.Namespace:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(
        description="Capture all Dashboard views as full-page PNG screenshots."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Dashboard URL (default: {DEFAULT_URL})")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "output" / "screenshots" / f"dashboard-{stamp}",
        help="Destination directory (default: timestamped folder under output/screenshots)",
    )
    parser.add_argument("--lang", choices=("zh", "ja", "en"), default="zh")
    parser.add_argument("--width", type=int, default=1440, help="Viewport width in CSS pixels")
    parser.add_argument("--height", type=int, default=1000, help="Viewport height in CSS pixels")
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=1200,
        help="Extra wait after page/API activity settles",
    )
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--headed", action="store_true", help="Show the Chromium window while capturing")
    return parser.parse_args()


def _first_line(locator: Locator) -> str:
    text = locator.inner_text().strip()
    return text.splitlines()[0].strip() if text else ""


def _hub_tabs(page: Page) -> Locator:
    # Both hub components render their tab bar as the first direct child of
    # the first div in <main>.
    return page.locator("main > div > div:first-child > button")


def _wait_until_settled(page: Page, settle_ms: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        # Some future page may maintain a background request; the explicit
        # settle delay still provides a deterministic capture point.
        pass
    try:
        page.evaluate("() => document.fonts.ready")
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)


def _prime_full_page(page: Page) -> None:
    """Scroll through the document so lazy content is rendered before capture."""
    previous_height = 0
    step = max(400, page.viewport_size["height"] // 2 if page.viewport_size else 500)
    for _ in range(2):
        height = int(
            page.evaluate(
                "Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"
            )
        )
        for y in range(0, height, step):
            page.evaluate("y => window.scrollTo(0, y)", y)
            page.wait_for_timeout(30)
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(100)
        current_height = int(
            page.evaluate(
                "Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"
            )
        )
        if current_height == previous_height or current_height == height:
            break
        previous_height = current_height
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)


def _png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as file:
        header = file.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"Not a valid PNG: {path}")
    return struct.unpack(">II", header[16:24])


def _document_metrics(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => ({
          scrollWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
          scrollHeight: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
          title: document.title,
        })"""
    )


def _capture(page: Page, output_dir: Path, spec: PageSpec, label: str) -> dict[str, Any]:
    _prime_full_page(page)
    metrics = _document_metrics(page)
    target = output_dir / spec.filename
    page.screenshot(path=str(target), full_page=True)
    screenshot_width, screenshot_height = _png_size(target)

    if (
        screenshot_width < int(metrics["scrollWidth"])
        or screenshot_height < int(metrics["scrollHeight"])
    ):
        raise RuntimeError(
            f"Incomplete screenshot for {label}: image={screenshot_width}x{screenshot_height}, "
            f"document={metrics['scrollWidth']}x{metrics['scrollHeight']}"
        )

    print(
        f"{spec.filename}\t{label}\t{screenshot_width}x{screenshot_height}\t"
        f"document={metrics['scrollWidth']}x{metrics['scrollHeight']}",
        flush=True,
    )
    return {
        "file": spec.filename,
        "page": label,
        "screenshot_width": screenshot_width,
        "screenshot_height": screenshot_height,
        **metrics,
    }


def run(args: argparse.Namespace) -> Path:
    if args.width < 800 or args.height < 500:
        raise SystemExit("--width must be >= 800 and --height must be >= 500")
    if args.settle_ms < 0:
        raise SystemExit("--settle-ms must be >= 0")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": args.width, "height": args.height},
            "locale": {"zh": "zh-TW", "ja": "ja-JP", "en": "en-US"}[args.lang],
            "color_scheme": "light",
            "device_scale_factor": 1,
            "reduced_motion": "reduce",
        }
        password = os.environ.get("DASHBOARD_PASSWORD")
        if password:
            context_kwargs["http_credentials"] = {
                "username": os.environ.get("DASHBOARD_USERNAME", "dashboard"),
                "password": password,
            }
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(args.timeout_ms)
        page.add_init_script(
            f"localStorage.setItem('jp-pm-jobs.lang', {json.dumps(args.lang)})"
        )
        response = page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout_ms)
        if response is None:
            raise RuntimeError(f"No response from {args.url}")
        if response.status == 401:
            raise RuntimeError(
                "Dashboard returned HTTP 401. Export DASHBOARD_PASSWORD and run again."
            )
        if not response.ok:
            raise RuntimeError(f"Dashboard returned HTTP {response.status}")

        page.add_style_tag(
            content="""
              *, *::before, *::after {
                animation: none !important;
                transition: none !important;
                caret-color: transparent !important;
              }
            """
        )
        _wait_until_settled(page, args.settle_ms)

        sidebar_buttons = page.locator("aside nav button")
        sidebar_buttons.first.wait_for(state="visible")
        required_sidebar_count = max(spec.sidebar_index for spec in PAGE_SPECS) + 1
        actual_sidebar_count = sidebar_buttons.count()
        if actual_sidebar_count < required_sidebar_count:
            raise RuntimeError(
                f"Expected at least {required_sidebar_count} sidebar pages, found {actual_sidebar_count}"
            )

        active_sidebar_index: int | None = None
        for spec in PAGE_SPECS:
            if active_sidebar_index != spec.sidebar_index:
                sidebar_button = sidebar_buttons.nth(spec.sidebar_index)
                label = _first_line(sidebar_button)
                sidebar_button.click()
                active_sidebar_index = spec.sidebar_index
                _wait_until_settled(page, args.settle_ms)
            else:
                label = _first_line(sidebar_buttons.nth(spec.sidebar_index))

            if spec.tab_index is not None:
                tabs = _hub_tabs(page)
                required_tab_count = spec.tab_index + 1
                if tabs.count() < required_tab_count:
                    raise RuntimeError(
                        f"Expected at least {required_tab_count} hub tabs for {label}, found {tabs.count()}"
                    )
                tab = tabs.nth(spec.tab_index)
                label = _first_line(tab)
                tab.click()
                _wait_until_settled(page, args.settle_ms)

            manifest.append(_capture(page, output_dir, spec, label))

        context.close()
        browser.close()

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "base_url": args.url,
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "language": args.lang,
                "viewport": {"width": args.width, "height": args.height},
                "full_page": True,
                "count": len(manifest),
                "screenshots": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if len(manifest) != len(PAGE_SPECS):
        raise RuntimeError(f"Expected {len(PAGE_SPECS)} screenshots, got {len(manifest)}")
    print(f"Saved {len(manifest)} full-page screenshots to {output_dir}")
    return output_dir


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
