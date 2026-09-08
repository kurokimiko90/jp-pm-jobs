"""Anthropic Messages API provider（API key 認證）。"""

from __future__ import annotations

import os

import requests

from .base import BaseLLMProvider, LLMResponse, ProviderError

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 8192


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        key_env = cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        self.api_key = os.environ.get(key_env, "")
        self.default_model = cfg.get("model", DEFAULT_MODEL)
        self.max_tokens = int(cfg.get("max_tokens", MAX_TOKENS))

    def is_available(self) -> bool:
        return bool(self.api_key)

    def accepts_model(self, model: str) -> bool:
        return model.startswith("claude")

    def call(self, prompt: str, model: str = "", timeout: int = 180,
             accept: dict | None = None) -> LLMResponse:
        if not self.api_key:
            raise ProviderError("anthropic: ANTHROPIC_API_KEY 未設定")
        m = model if self.accepts_model(model) else self.default_model
        try:
            r = requests.post(
                API_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": API_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": m,
                    "max_tokens": self.max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise ProviderError(f"anthropic: {e}") from e
        text = "".join(b.get("text", "") for b in data.get("content", []))
        if not text.strip():
            raise ProviderError("anthropic: 回應為空")
        usage = data.get("usage", {})
        return LLMResponse(text=text.strip(), model=m, provider=self.name,
                           input_tokens=usage.get("input_tokens"),
                           output_tokens=usage.get("output_tokens"))
