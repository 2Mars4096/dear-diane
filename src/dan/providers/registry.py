"""Provider registry — resolves model names to LLM provider instances."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dan.providers import LLMProvider

logger = logging.getLogger(__name__)

_DEFAULT_PREFIX_PATTERNS: list[tuple[str, str]] = [
    ("gpt-", "openai"),
    ("kimi-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude-", "anthropic"),
    ("gemini-", "google"),
]


class ProviderRegistry:
    """Routes model names to the correct LLM provider.

    Resolution order:
    1. Exact model → provider override map
    2. Prefix pattern match (gpt-* → openai, claude-* → anthropic, etc.)
    3. "default" provider as final fallback
    """

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._model_overrides: dict[str, str] = {}
        self._prefix_patterns: list[tuple[str, str]] = list(_DEFAULT_PREFIX_PATTERNS)

    def register(self, name: str, provider: LLMProvider) -> None:
        """Register a named provider."""
        self._providers[name] = provider

    def set_model_override(self, model: str, provider_name: str) -> None:
        """Pin a specific model to a specific provider (highest priority)."""
        self._model_overrides[model] = provider_name

    def add_prefix_pattern(self, prefix: str, provider_name: str) -> None:
        """Add a custom prefix → provider mapping."""
        self._prefix_patterns.append((prefix, provider_name))

    def resolve_name(self, model: str) -> str:
        """Resolve a model name to its provider name.

        Mirrors :meth:`resolve` but returns the matching registry key instead of
        the provider instance so callers can derive provider-specific behavior
        (for example tier parameter defaults) from the model they actually plan
        to invoke.
        """
        if model in self._model_overrides:
            provider_name = self._model_overrides[model]
            if provider_name in self._providers:
                return provider_name

        for prefix, provider_name in self._prefix_patterns:
            if model.startswith(prefix) and provider_name in self._providers:
                return provider_name

        if "default" in self._providers:
            return "default"

        raise KeyError(
            f"No provider found for model '{model}'. "
            f"Registered providers: {sorted(self._providers)}. "
            f"Model overrides: {self._model_overrides}"
        )

    def resolve(self, model: str) -> LLMProvider:
        """Resolve a model name to its provider.

        Raises KeyError if no matching provider is found.
        """
        return self._providers[self.resolve_name(model)]

    def has_provider(self, name: str) -> bool:
        return name in self._providers

    def get(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def provider_names(self) -> list[str]:
        return sorted(self._providers)
