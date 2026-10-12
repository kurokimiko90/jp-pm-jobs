"""Provider 插件架構 — 統一 scraper interface + 自動發現。

所有 scraper 都必須實作 `scrape(page, keyword, max_pages) -> list[dict]`。
本模組提供：
  1. ProviderMeta — 每個 provider 的元數據
  2. discover_providers() — 自動掃描 scrapers/*.py 找出所有 provider
  3. get_provider(name) — 按名稱取 provider
  4. run_provider(name, page, keyword, max_pages) — 統一呼叫入口

新增 ATS 只需在 scrapers/ 下加一個 .py，實作 scrape() 函數 + PROVIDER_META dict。
不需要修改任何其他文件。
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Page


@dataclass(frozen=True)
class ProviderMeta:
    id: str
    name: str
    requires_login: bool = False
    base_url: str = ""
    description: str = ""


_registry: dict[str, tuple[ProviderMeta, Callable]] = {}


def _load_providers() -> None:
    if _registry:
        return
    pkg_dir = Path(__file__).parent
    for finder, module_name, _ in pkgutil.iter_modules([str(pkg_dir)]):
        if module_name.startswith("_") or module_name == "provider":
            continue
        try:
            mod = importlib.import_module(f"scrapers.{module_name}")
        except Exception as e:
            print(f"  [provider] 載入 scrapers.{module_name} 失敗: {e}")
            continue
        if not hasattr(mod, "scrape"):
            continue
        meta_dict = getattr(mod, "PROVIDER_META", {})
        meta = ProviderMeta(
            id=meta_dict.get("id", module_name),
            name=meta_dict.get("name", module_name),
            requires_login=meta_dict.get("requires_login", False),
            base_url=meta_dict.get("base_url", ""),
            description=meta_dict.get("description", ""),
        )
        _registry[meta.id] = (meta, mod.scrape)


def discover_providers() -> list[ProviderMeta]:
    _load_providers()
    return [meta for meta, _ in _registry.values()]


def get_provider(name: str) -> tuple[ProviderMeta, Callable] | None:
    _load_providers()
    return _registry.get(name)


def run_provider(
    name: str, page: Page, keyword: str, max_pages: int = 5, seen_ids: set | None = None
) -> list[dict]:
    entry = get_provider(name)
    if not entry:
        raise ValueError(f"Provider '{name}' not found. Available: {list(_registry.keys())}")
    meta, scrape_fn = entry
    # 只有支援 seen_ids 的 provider（如 linkedin_jp 跨 keyword 去重）才傳入，
    # 其他 provider 簽名不變、行為不受影響。
    if seen_ids is not None and "seen_ids" in inspect.signature(scrape_fn).parameters:
        return scrape_fn(page, keyword, max_pages, seen_ids=seen_ids)
    return scrape_fn(page, keyword, max_pages)


def list_providers_table() -> str:
    providers = discover_providers()
    lines = [f"{'ID':<15} {'名稱':<20} {'登入':<6} {'說明'}"]
    lines.append("-" * 60)
    for p in sorted(providers, key=lambda x: x.id):
        login = "要" if p.requires_login else "不要"
        lines.append(f"{p.id:<15} {p.name:<20} {login:<6} {p.description}")
    return "\n".join(lines)
