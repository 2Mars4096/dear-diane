"""Multi-provider LLM abstraction — protocol, data classes, and public API."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
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
    finish_reason: str = ""


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


def supports_exact_tool_choice(provider: Any) -> bool:
    """True when the provider can target a specific tool via ``tool_choice``."""
    return bool(getattr(provider, "supports_exact_tool_choice", False))


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


def resolve_provider_timeout(
    config: ProviderConfig,
    *,
    default_seconds: float = 120.0,
) -> float:
    """Resolve the default request timeout for a provider instance.

    Priority:
    1. ``config.extra["timeout_seconds"]``
    2. ``DAN_PROVIDER_TIMEOUT``
    3. ``DAN_LLM_CALL_TIMEOUT``
    4. ``default_seconds``
    """
    raw = (
        (config.extra or {}).get("timeout_seconds")
        or os.environ.get("DAN_PROVIDER_TIMEOUT")
        or os.environ.get("DAN_LLM_CALL_TIMEOUT")
        or default_seconds
    )
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return default_seconds
    return timeout if timeout > 0 else default_seconds


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
