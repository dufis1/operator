from brainchild.pipeline.providers.base import (
    LLMProvider,
    ContextOverflowError,
    ToolCall,
    ProviderResponse,
)
from brainchild.pipeline.providers.openai import OpenAIProvider
from brainchild.pipeline.providers.anthropic import AnthropicProvider


def build_provider():
    """Build the LLMProvider selected by config.LLM_PROVIDER.

    Called by the app-level entry points (__main__, runner, docker entrypoint)
    so the choice of backend lives in one place.
    """
    from brainchild import config
    name = config.LLM_PROVIDER
    if name == "openai":
        from openai import OpenAI
        if not config.OPENAI_API_KEY:
            raise RuntimeError(
                "llm.provider is 'openai' but OPENAI_API_KEY is not set in .env"
            )
        return OpenAIProvider(OpenAI(api_key=config.OPENAI_API_KEY))
    if name == "anthropic":
        from anthropic import Anthropic
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError(
                "llm.provider is 'anthropic' but ANTHROPIC_API_KEY is not set in .env"
            )
        return AnthropicProvider(Anthropic(api_key=config.ANTHROPIC_API_KEY))
    raise ValueError(f"unknown llm.provider: {name!r} (expected 'openai' or 'anthropic')")


__all__ = [
    "LLMProvider",
    "ContextOverflowError",
    "ToolCall",
    "ProviderResponse",
    "OpenAIProvider",
    "AnthropicProvider",
    "build_provider",
]
