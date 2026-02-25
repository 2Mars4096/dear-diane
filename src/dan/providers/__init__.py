"""Multi-provider LLM abstraction — protocol, data classes, and public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass
class CompletionResult:
    """Result of a non-streaming LLM completion."""

    text: str
    usage: dict[str, int] | None = None
    model: str = ""
    tool_calls: list[dict[str, Any]] | None = None


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
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult: ...

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]: ...
