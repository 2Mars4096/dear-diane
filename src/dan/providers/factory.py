"""Helpers for constructing provider registries from runtime config."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from dan.providers import ProviderConfig
from dan.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from dan.providers import LLMProvider

logger = logging.getLogger(__name__)


def _build_openai_provider(config: ProviderConfig) -> "LLMProvider":
    from dan.providers.openai_provider import OpenAIProvider

    return OpenAIProvider(config)


class ProviderRegistryConfig(Protocol):
    """Structural config shape needed to build a provider registry."""

    llm_api_key: str | None
    llm_base_url: str | None
    providers: dict[str, ProviderConfig]
    model_provider_map: dict[str, str] | None


def create_provider(name: str, config: ProviderConfig) -> "LLMProvider | None":
    """Instantiate a provider by registry name.

    Unknown provider names are treated as OpenAI-compatible endpoints.
    """

    if name in {"default", "openai"}:
        return _build_openai_provider(config)

    if name == "anthropic":
        try:
            from dan.providers.anthropic_provider import AnthropicProvider

            return AnthropicProvider(config)
        except ImportError:
            logger.warning(
                "anthropic package not installed; skipping provider '%s'",
                name,
            )
            return None

    if name == "google":
        try:
            from dan.providers.google_provider import GoogleProvider

            return GoogleProvider(config)
        except ImportError:
            logger.warning(
                "google-generativeai not installed; skipping provider '%s'",
                name,
            )
            return None

    return _build_openai_provider(config)


def build_provider_registry(config: ProviderRegistryConfig) -> ProviderRegistry:
    """Create a chat/provider registry from an engine-like config object."""

    registry = ProviderRegistry()
    default_config = ProviderConfig(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
    )
    registry.register("default", _build_openai_provider(default_config))

    for name, provider_config in config.providers.items():
        if name == "default":
            continue
        provider = create_provider(name, provider_config)
        if provider is not None:
            registry.register(name, provider)

    for model, provider_name in (getattr(config, "model_provider_map", {}) or {}).items():
        registry.set_model_override(model, provider_name)

    return registry
