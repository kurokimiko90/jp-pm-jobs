"""TTS provider chain.

Default behavior is local-only; external providers must be explicitly configured.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from tools import app_config

from .providers import REGISTRY, BaseTTSProvider, ProviderError, TTSResponse

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get("TTS_CONFIG", ROOT / "config" / "tts.yaml"))
_CHAIN: list[BaseTTSProvider] | None = None


def _build_chain() -> list[BaseTTSProvider]:
    cfg = app_config.load("tts") if CONFIG_PATH.exists() else {}
    names = cfg.get("chain") or ["local_macos"]
    provider_cfgs = cfg.get("providers", {}) or {}
    chain: list[BaseTTSProvider] = []
    for name in names:
        cls = REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"config/tts.yaml: unknown provider '{name}' (available: {sorted(REGISTRY)})")
        chain.append(cls(provider_cfgs.get(name, {})))
    return chain


def get_chain(refresh: bool = False) -> list[BaseTTSProvider]:
    global _CHAIN
    if _CHAIN is None or refresh:
        _CHAIN = _build_chain()
    return _CHAIN


def synthesize(text: str, *, voice: str = "", lang: str = "ja",
               audio_format: str = "") -> TTSResponse:
    errors: list[str] = []
    for provider in get_chain():
        try:
            return provider.synthesize(text, voice=voice, lang=lang, audio_format=audio_format)
        except ProviderError as exc:
            errors.append(str(exc))
    raise RuntimeError("all TTS providers failed:\n  - " + "\n  - ".join(errors))


def health() -> dict:
    try:
        chain = get_chain()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    providers = [provider.health() for provider in chain]
    return {"ok": any(p.get("ok") for p in providers), "chain": providers}


__all__ = ["ProviderError", "TTSResponse", "get_chain", "health", "synthesize"]
