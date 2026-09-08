from .anthropic_api import AnthropicProvider
from .base import BaseLLMProvider, LLMResponse, ProviderError
from .cli import CLIProvider
from .gemini_api import GeminiProvider
from .miko_gateway import MikoGatewayProvider
from .openai_api import OpenAIProvider

REGISTRY: dict[str, type[BaseLLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "cli": CLIProvider,
    "miko_gateway": MikoGatewayProvider,
}

__all__ = ["BaseLLMProvider", "LLMResponse", "ProviderError", "REGISTRY",
           "AnthropicProvider", "OpenAIProvider", "GeminiProvider",
           "CLIProvider", "MikoGatewayProvider"]
