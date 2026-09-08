"""OpenAI Chat Completions provider（API key 認證）。

base_url 可設定 → 同一支程式碼支援 OpenRouter / Ollama / LM Studio /
任何 OpenAI 相容端點（自建 gateway 也適用）。
"""

from __future__ import annotations

import os

import requests

from .base import BaseLLMProvider, LLMResponse, ProviderError

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"

_MODEL_PREFIXES = ("gpt", "o1", "o3", "o4", "chatgpt")


class OpenAIProvider(BaseLLMProvider):
    name = "openai"

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        key_env = cfg.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = os.environ.get(key_env, "")
        self.base_url = cfg.get("base_url", DEFAULT_BASE_URL).rstrip("/")
        self.default_model = cfg.get("model", DEFAULT_MODEL)
        # Ollama 等本機端點不需要 key
        self.require_key = bool(cfg.get("require_key", True))

    def is_available(self) -> bool:
        return bool(self.api_key) or not self.require_key

    def accepts_model(self, model: str) -> bool:
        return model.startswith(_MODEL_PREFIXES)

    def call(self, prompt: str, model: str = "", timeout: int = 180,
             accept: dict | None = None) -> LLMResponse:
        if self.require_key and not self.api_key:
            raise ProviderError("openai: API key 未設定")
        m = model if self.accepts_model(model) else self.default_model
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        try:
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={"model": m,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=timeout,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise ProviderError(f"openai: {e}") from e
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise ProviderError(f"openai: 回應格式異常 {data}") from e
        if not text.strip():
            raise ProviderError("openai: 回應為空")
        usage = data.get("usage", {})
        return LLMResponse(text=text.strip(), model=m, provider=self.name,
                           input_tokens=usage.get("prompt_tokens"),
                           output_tokens=usage.get("completion_tokens"))
