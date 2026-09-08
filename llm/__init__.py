"""統一 LLM 入口 — config/llm.yaml 驅動的多 provider 鏈。

    from llm import call
    text = call("prompt", timeout=180, model="claude-haiku-4-5")

Provider 鏈依 config 順序嘗試，首個成功者勝出；全部失敗才拋錯。
config/llm.yaml 不存在時走自動偵測：有哪家的 API key 就用哪家，最後試本機 CLI。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .providers import REGISTRY, BaseLLMProvider, LLMResponse, ProviderError

ROOT = Path(__file__).parent.parent
CONFIG_PATH = Path(os.environ.get("LLM_CONFIG", ROOT / "config" / "llm.yaml"))

_ENV_LOADED = False
_CHAIN: list[BaseLLMProvider] | None = None


def _load_dotenv() -> None:
    """讀專案根目錄 .env（不覆蓋已存在的環境變數）。"""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _ENV_LOADED = True


def _auto_chain_names() -> list[str]:
    """無 config 時的自動偵測順序：有 key 的 API → 本機 CLI。"""
    names: list[str] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        names.append("anthropic")
    if os.environ.get("OPENAI_API_KEY"):
        names.append("openai")
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        names.append("gemini")
    names.append("cli")
    return names


def _build_chain() -> list[BaseLLMProvider]:
    _load_dotenv()
    cfg: dict = {}
    if CONFIG_PATH.exists():
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    names = cfg.get("chain") or _auto_chain_names()
    provider_cfgs = cfg.get("providers", {}) or {}
    chain: list[BaseLLMProvider] = []
    for name in names:
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"config/llm.yaml: 未知 provider '{name}'（可用: {sorted(REGISTRY)}）")
        chain.append(cls(provider_cfgs.get(name, {})))
    return chain


def get_chain(refresh: bool = False) -> list[BaseLLMProvider]:
    global _CHAIN
    if _CHAIN is None or refresh:
        _CHAIN = _build_chain()
    return _CHAIN


def call(prompt: str, timeout: int = 180, model: str = "",
         accept: dict | None = None) -> str:
    """送 prompt 給 provider 鏈，回傳文字。全鏈失敗 raise RuntimeError。"""
    errors: list[str] = []
    for provider in get_chain():
        try:
            return provider.call(prompt, model=model, timeout=timeout, accept=accept).text
        except ProviderError as e:
            errors.append(str(e))
    raise RuntimeError(
        "所有 LLM provider 都失敗：\n  - " + "\n  - ".join(errors) +
        f"\n請檢查 {CONFIG_PATH}（或 .env 的 API key / 本機 CLI 登入狀態）。")


def health() -> dict:
    """回報整條鏈的就緒狀態（dashboard 啟動檢查用）。"""
    try:
        chain = get_chain()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    providers = [p.health() for p in chain]
    return {"ok": any(p["ok"] for p in providers), "chain": providers}


__all__ = ["call", "health", "get_chain", "LLMResponse", "ProviderError"]
