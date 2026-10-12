from __future__ import annotations

from dataclasses import dataclass


class ProviderError(RuntimeError):
    """Raised when a TTS provider cannot synthesize audio."""


@dataclass
class TTSResponse:
    audio_bytes: bytes
    audio_format: str
    content_type: str
    provider: str
    voice: str


class BaseTTSProvider:
    name = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def synthesize(self, text: str, *, voice: str = "", lang: str = "ja",
                   audio_format: str = "") -> TTSResponse:
        raise NotImplementedError

    def health(self) -> dict:
        return {"name": self.name, "ok": True}
