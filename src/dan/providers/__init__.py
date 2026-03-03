"""Multi-provider LLM abstraction — protocol, data classes, and public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from dan.providers.capabilities import ModelCapabilityRegistry  # noqa: F401
from dan.providers.cost_tracker import BudgetExceededError, CostTracker  # noqa: F401
from dan.providers.model_selector import CascadeHandler, ModelSelector  # noqa: F401


@dataclass
class CompletionResult:
    """Result of a non-streaming LLM completion."""

    text: str
    usage: dict[str, int] | None = None
    model: str = ""
    tool_calls: list[dict[str, Any]] | None = None
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0


def apply_cache_hints(
    provider: Any, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply provider-specific cache hints to messages.

    Falls back to returning messages unchanged if the provider doesn't
    support caching.
    """
    if hasattr(provider, "apply_cache_hints"):
        return provider.apply_cache_hints(messages)
    return messages


@dataclass
class StreamChunk:
    """Single chunk from a streaming LLM response."""

    delta: str
    accumulated: str
    done: bool = False
    usage: dict[str, int] | None = None


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider instance."""

    api_key: str = ""
    base_url: str | None = None
    default_model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol that all LLM providers must satisfy."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult: ...

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]: ...
