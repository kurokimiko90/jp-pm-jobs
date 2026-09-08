"""LLM provider 基類 — 所有 provider 實作 call() / is_available()。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    provider: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderError(RuntimeError):
    """單一 provider 呼叫失敗（鏈上會換下一個）。"""


class BaseLLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def call(self, prompt: str, model: str = "", timeout: int = 180,
             accept: dict | None = None) -> LLMResponse:
        """送出 prompt，回傳回應。失敗時 raise ProviderError。

        model:  呼叫端指定的模型偏好；provider 不認得時用自己的預設模型。
        accept: 驗收條件 dict（僅 miko_gateway 支援，其餘 provider 忽略）。
        """

    def is_available(self) -> bool:
        """認證/連線是否就緒（不保證呼叫必成功）。預設樂觀回 True。"""
        return True

    def health(self) -> dict:
        return {"ok": self.is_available(), "provider": self.name}

    def accepts_model(self, model: str) -> bool:
        """呼叫端傳來的 model 名是否屬於本 provider 家族。"""
        return False
