from .base import BaseTTSProvider, ProviderError, TTSResponse
from .edge_tts import EdgeTTSProvider
from .local_macos import LocalMacOSTTSProvider

REGISTRY = {
    "edge_tts": EdgeTTSProvider,
    "local_macos": LocalMacOSTTSProvider,
}

__all__ = ["BaseTTSProvider", "EdgeTTSProvider", "LocalMacOSTTSProvider",
           "ProviderError", "REGISTRY", "TTSResponse"]
