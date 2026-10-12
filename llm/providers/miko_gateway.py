"""miko-ws LLM 指揮中心 provider（自建 gateway，可選）。

包裝 tools.miko_llm。任何自建的 brain-pool gateway 都可仿照此檔接入。
"""

from __future__ import annotations

from .base import BaseLLMProvider, LLMResponse, ProviderError


class MikoGatewayProvider(BaseLLMProvider):
    name = "miko_gateway"

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        import os
        if cfg.get("base_url"):
            os.environ.setdefault("LLM_GATEWAY_URL", cfg["base_url"])
        self.engine = cfg.get("engine", "codex")

    def _mod(self):
        from tools import miko_llm
        return miko_llm

    def is_available(self) -> bool:
        try:
            return bool(self._mod().is_available())
        except Exception:
            return False

    def accepts_model(self, model: str) -> bool:
        return True  # gateway 自行調度 brain，忽略 model

    def call(self, prompt: str, model: str = "", timeout: int = 180,
             accept: dict | None = None) -> LLMResponse:
        try:
            out = self._mod().text(
                prompt, timeout=timeout,
                opts={"accept": accept} if accept else None,
                engine=self.engine)
        except Exception as e:
            raise ProviderError(f"miko_gateway: {e}") from e
        out = (out or "").strip()
        if not out:
            raise ProviderError("miko_gateway: 回應為空")
        return LLMResponse(text=out, provider=self.name)
