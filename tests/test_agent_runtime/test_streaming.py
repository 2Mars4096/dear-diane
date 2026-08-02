from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dan.agent_runtime.streaming import StreamChunkCollector


class _Chunk:
    def __init__(
        self,
        delta: str,
        accumulated: str,
        done: bool,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.delta = delta
        self.accumulated = accumulated
        self.done = done
        self.usage = usage or {}


class _Provider:
    async def stream(self, *args: Any, **kwargs: Any):
        yield _Chunk("hello", "hello", False)
        yield _Chunk("", "hello world", True, {"prompt_tokens": 2, "completion_tokens": 3})


@pytest.mark.asyncio
async def test_stream_chunk_collector_tracks_completion_state() -> None:
    collector = StreamChunkCollector(
        provider=_Provider(),
        messages=[{"role": "user", "content": "hi"}],
        model="test-model",
    )

    chunks = []
    async for chunk in collector:
        chunks.append(chunk.accumulated)

    assert chunks == ["hello", "hello world"]
    assert collector.state.final_content == "hello world"
    assert collector.state.token_usage["prompt_tokens"] == 2
    assert collector.state.token_usage["completion_tokens"] == 3
    assert collector.state.interrupted is False


@pytest.mark.asyncio
async def test_stream_chunk_collector_tracks_interruption_state() -> None:
    cancel_event = asyncio.Event()
    collector = StreamChunkCollector(
        provider=_Provider(),
        messages=[{"role": "user", "content": "hi"}],
        model="test-model",
        cancel_event=cancel_event,
    )

    chunks = []
    async for chunk in collector:
        chunks.append(chunk.accumulated)
        cancel_event.set()

    assert chunks == ["hello"]
    assert collector.state.final_content == "hello world"
    assert collector.state.token_usage["prompt_tokens"] == 2
    assert collector.state.token_usage["completion_tokens"] == 3
    assert collector.state.interrupted is True


@pytest.mark.asyncio
async def test_stream_chunk_collector_accepts_stream_factory() -> None:
    async def _stream():
        yield _Chunk("hello", "hello", False)
        yield _Chunk("", "hello world", True, {"prompt_tokens": 4, "completion_tokens": 5})

    collector = StreamChunkCollector(
        cancel_event=None,
        stream_factory=_stream,
    )

    chunks = []
    async for chunk in collector:
        chunks.append(chunk.accumulated)

    assert chunks == ["hello", "hello world"]
    assert collector.state.final_content == "hello world"
    assert collector.state.token_usage["prompt_tokens"] == 4
    assert collector.state.token_usage["completion_tokens"] == 5
    assert collector.state.interrupted is False
