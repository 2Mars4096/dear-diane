"""Multi-provider LLM abstraction — protocol, data classes, and public API."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, AsyncIterator, Literal, Protocol, cast, runtime_checkable

from dan.providers.capabilities import ModelCapabilityRegistry  # noqa: F401
from dan.providers.cost_tracker import BudgetExceededError, CostTracker  # noqa: F401
from dan.providers.model_selector import CascadeHandler, ModelSelector  # noqa: F401


class LLMAuthenticationError(Exception):
    """Raised when the LLM provider returns a 401/403 authentication or permission error."""
    pass


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
    raw_assistant_message: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelBehaviorProfile:
    """Model-level behavior flags for provider-specific chat/runtime quirks."""

    supports_tool_calls: bool = True
    supports_exact_tool_choice: bool = False
    supports_required_tool_choice: bool = True
    assistant_replay_mode: Literal["reconstruct", "raw"] = "reconstruct"


def _unwrap_provider(provider: Any, *, _max_depth: int = 5) -> Any:
    """Peel back lightweight wrappers to the underlying provider object."""

    current = provider
    seen: set[int] = set()
    depth = 0
    while current is not None and depth < _max_depth:
        marker = id(current)
        if marker in seen:
            break
        seen.add(marker)
        try:
            next_provider = object.__getattribute__(current, "_provider")
        except AttributeError:
            break
        if next_provider is None or next_provider is current:
            break
        current = next_provider
        depth += 1
    return current


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
    raw_provider = _unwrap_provider(provider)
    return bool(getattr(raw_provider, "supports_exact_tool_choice", False))


def supports_tool_calls(provider: Any) -> bool:
    """True when the provider can execute OpenAI-style tool-calling requests."""
    raw_provider = _unwrap_provider(provider)
    return bool(getattr(raw_provider, "supports_tool_calls", True))


def get_model_behavior(provider: Any, model: str) -> ModelBehaviorProfile:
    """Resolve model-specific behavior, falling back to provider-wide flags."""

    raw_provider = _unwrap_provider(provider)
    getter = getattr(raw_provider, "get_model_behavior", None)
    if callable(getter):
        try:
            profile = getter(model)
        except Exception:
            profile = None
        if isinstance(profile, ModelBehaviorProfile):
            return profile
        if isinstance(profile, dict):
            _KNOWN_FIELDS = frozenset(ModelBehaviorProfile.__dataclass_fields__)
            try:
                return ModelBehaviorProfile(
                    **{k: v for k, v in profile.items() if k in _KNOWN_FIELDS}
                )
            except (TypeError, ValueError):
                pass  # fall through to attribute introspection

    tool_calls_supported = bool(getattr(raw_provider, "supports_tool_calls", True))
    assistant_replay_mode = str(
        getattr(raw_provider, "assistant_replay_mode", "reconstruct") or "reconstruct"
    ).strip().lower()
    if assistant_replay_mode not in {"reconstruct", "raw"}:
        assistant_replay_mode = "reconstruct"
    return ModelBehaviorProfile(
        supports_tool_calls=tool_calls_supported,
        supports_exact_tool_choice=bool(
            getattr(raw_provider, "supports_exact_tool_choice", False)
        ),
        supports_required_tool_choice=bool(
            getattr(raw_provider, "supports_required_tool_choice", tool_calls_supported)
        ),
        assistant_replay_mode=cast(
            Literal["reconstruct", "raw"],
            assistant_replay_mode,
        ),
    )


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
