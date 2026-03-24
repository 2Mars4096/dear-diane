"""Canonical helpers for collecting provider stream chunks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from dan.agent_runtime.mutation_parsing import _normalize_usage


@dataclass(slots=True)
class StreamCollectionState:
    """Final state captured while consuming a provider stream."""

    final_content: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    interrupted: bool = False


class StreamChunkCollector:
    """Wrap ``provider.stream`` and capture the final chunk state.

    The collector preserves the existing runtime behavior:
    - cancellation is checked before yielding the next token event
    - final content and usage come from the last observed chunk
    - interruption captures the accumulated content from the chunk that was
      observed after cancellation was requested
    """

    def __init__(
        self,
        *,
        provider: Any | None = None,
        messages: list[dict[str, str]] | None = None,
        model: str = "",
        temperature: float = 0.7,
        cancel_event: asyncio.Event | None = None,
        stream_factory: Callable[[], AsyncIterator[Any]] | None = None,
    ) -> None:
        self._provider = provider
        self._messages = messages or []
        self._model = model
        self._temperature = temperature
        self._cancel_event = cancel_event
        self._stream_factory = stream_factory
        self.state = StreamCollectionState()

    def _stream(self) -> AsyncIterator[Any]:
        if self._stream_factory is not None:
            return self._stream_factory()
        if self._provider is None:
            raise RuntimeError("StreamChunkCollector requires a provider or stream_factory")
        return self._provider.stream(
            messages=self._messages,
            model=self._model,
            temperature=self._temperature,
        )

    async def __aiter__(self) -> AsyncIterator[Any]:
        async for chunk in self._stream():
            if self._cancel_event and self._cancel_event.is_set():
                self.state.final_content = chunk.accumulated
                self.state.token_usage = _normalize_usage(chunk.usage)
                self.state.interrupted = True
                break
            yield chunk
            if chunk.done:
                self.state.final_content = chunk.accumulated
                self.state.token_usage = _normalize_usage(chunk.usage)
