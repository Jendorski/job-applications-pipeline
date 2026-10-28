from jap.config import ConfigError, Settings
from jap.llm.base import LLMError, LLMProvider, LLMRefusal, Usage

__all__ = ["LLMError", "LLMProvider", "LLMRefusal", "Usage", "get_provider"]


def get_provider(settings: Settings) -> LLMProvider:
    if settings.provider == "anthropic":
        from jap.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(fallbacks=settings.fallbacks)
    if settings.provider == "openai_compat":
        from jap.llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(base_url=settings.openai_base_url)
    raise ConfigError(f"unknown LLM_PROVIDER '{settings.provider}' (anthropic | openai_compat)")
