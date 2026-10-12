from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import BaseTTSProvider, ProviderError, TTSResponse


class LocalMacOSTTSProvider(BaseTTSProvider):
    name = "local_macos"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.say_bin = self.config.get("say_bin", "say")
        self.afconvert_bin = self.config.get("afconvert_bin", "afconvert")
        self.default_format = self.config.get("output_format", "wav")
        self.default_voice_ja = self.config.get("default_voice_ja", "Kyoko")
        self.default_voice_en = self.config.get("default_voice_en", "Samantha")

    def _pick_voice(self, voice: str, lang: str) -> str:
        # 含 "-" 的語音名（ja-JP-KeitaNeural 等）是 edge_tts 的 neural voice，
        # `say` 不認識 — chain 降級到本 provider 時改用 lang 預設聲
        if voice and "-" not in voice:
            return voice
        return self.default_voice_ja if (lang or "ja").startswith("ja") else self.default_voice_en

    def health(self) -> dict:
        say_ok = shutil.which(self.say_bin) is not None
        afconvert_ok = shutil.which(self.afconvert_bin) is not None
        return {
            "name": self.name,
            "ok": say_ok and afconvert_ok,
            "say": say_ok,
            "afconvert": afconvert_ok,
            "default_format": self.default_format,
        }

    def synthesize(self, text: str, *, voice: str = "", lang: str = "ja",
                   audio_format: str = "") -> TTSResponse:
        if not text.strip():
            raise ProviderError(f"{self.name}: empty text")
        if shutil.which(self.say_bin) is None:
            raise ProviderError(f"{self.name}: missing '{self.say_bin}'")
        if shutil.which(self.afconvert_bin) is None:
            raise ProviderError(f"{self.name}: missing '{self.afconvert_bin}'")

        fmt = (audio_format or self.default_format or "m4a").lower()
        if fmt not in {"wav", "aiff"}:
            raise ProviderError(f"{self.name}: unsupported format '{fmt}'")
        picked_voice = self._pick_voice(voice, lang)

        with tempfile.TemporaryDirectory(prefix="tts-") as td:
            temp_dir = Path(td)
            aiff_path = temp_dir / "speech.aiff"
            cmd = [self.say_bin, "-v", picked_voice, "-o", str(aiff_path), text]
            try:
                subprocess.run(cmd, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                raise ProviderError(f"{self.name}: say failed: {detail or exc}") from exc

            if fmt == "aiff":
                return TTSResponse(
                    audio_bytes=aiff_path.read_bytes(),
                    audio_format="aiff",
                    content_type="audio/aiff",
                    provider=self.name,
                    voice=picked_voice,
                )

            out_path = temp_dir / "speech.wav"
            cmd = [
                self.afconvert_bin,
                "-f", "WAVE",
                "-d", "LEI16",
                str(aiff_path),
                str(out_path),
            ]
            try:
                subprocess.run(cmd, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                raise ProviderError(f"{self.name}: afconvert failed: {detail or exc}") from exc

            return TTSResponse(
                audio_bytes=out_path.read_bytes(),
                audio_format="wav",
                content_type="audio/wav",
                provider=self.name,
                voice=picked_voice,
            )
