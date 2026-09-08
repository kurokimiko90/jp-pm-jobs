"""Google Gemini generateContent provider（API key 認證）。"""

from __future__ import annotations

import os

import requests

from .base import BaseLLMProvider, LLMResponse, ProviderError

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        key_env = cfg.get("api_key_env", "GEMINI_API_KEY")
        self.api_key = os.environ.get(key_env, "") or os.environ.get("GOOGLE_API_KEY", "")
        self.default_model = cfg.get("model", DEFAULT_MODEL)

    def is_available(self) -> bool:
        return bool(self.api_key)

    def accepts_model(self, model: str) -> bool:
        return model.startswith("gemini")

    def call(self, prompt: str, model: str = "", timeout: int = 180,
             accept: dict | None = None) -> LLMResponse:
        if not self.api_key:
            raise ProviderError("gemini: GEMINI_API_KEY 未設定")
        m = model if self.accepts_model(model) else self.default_model
        try:
            r = requests.post(
                f"{BASE_URL}/{m}:generateContent",
                params={"key": self.api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise ProviderError(f"gemini: {e}") from e
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError) as e:
            raise ProviderError(f"gemini: 回應格式異常") from e
        if not text.strip():
            raise ProviderError("gemini: 回應為空")
        return LLMResponse(text=text.strip(), model=m, provider=self.name)
