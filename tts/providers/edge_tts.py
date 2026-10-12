from __future__ import annotations

import asyncio

from .base import BaseTTSProvider, ProviderError, TTSResponse


class EdgeTTSProvider(BaseTTSProvider):
    """Microsoft Edge TTS（無料・API key 不要・要網路）。

    無認證端點，Microsoft 隨時可能變更 — 視為 best-effort tier，
    chain 後面務必掛 local_macos 備援。
    """

    name = "edge_tts"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.default_voice_ja = self.config.get("default_voice_ja", "ja-JP-NanamiNeural")
        self.default_voice_en = self.config.get("default_voice_en", "en-US-AriaNeural")
        self.rate = self.config.get("rate", "+0%")

    def _pick_voice(self, voice: str, lang: str) -> str:
        if voice:
            return voice
        return self.default_voice_ja if (lang or "ja").startswith("ja") else self.default_voice_en

    def health(self) -> dict:
        try:
            import edge_tts  # noqa: F401
            ok = True
        except ImportError:
            ok = False
        return {"name": self.name, "ok": ok, "installed": ok, "rate": self.rate}

    def synthesize(self, text: str, *, voice: str = "", lang: str = "ja",
                   audio_format: str = "") -> TTSResponse:
        if not text.strip():
            raise ProviderError(f"{self.name}: empty text")
        if audio_format and audio_format != "mp3":
            raise ProviderError(f"{self.name}: unsupported format '{audio_format}' (mp3 only)")
        try:
            import edge_tts
        except ImportError as exc:
            raise ProviderError(f"{self.name}: edge-tts 未安裝（pip install edge-tts）") from exc

        picked_voice = self._pick_voice(voice, lang)

        async def _run() -> bytes:
            communicate = edge_tts.Communicate(text, voice=picked_voice, rate=self.rate)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        try:
            audio = asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001 — 網路/DRM 變更等一律轉 ProviderError 走 chain 降級
            raise ProviderError(f"{self.name}: {exc}") from exc
        if not audio:
            raise ProviderError(f"{self.name}: empty audio response")
        return TTSResponse(
            audio_bytes=audio,
            audio_format="mp3",
            content_type="audio/mpeg",
            provider=self.name,
            voice=picked_voice,
        )
