from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from dan.agent_runtime.text_runtime import stream_text_response as canonical_stream_text_response
from dan.server.chat.text_runtime import stream_text_response as legacy_stream_text_response


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
        yield _Chunk("", "hello world", True, {"prompt_tokens": 1, "completion_tokens": 2})


class _GraphStore:
    def get_graph(self, workflow_id: str) -> dict[str, Any]:
        return {
            "nodes": [],
            "edges": [],
            "metadata": {"name": "wf", "description": ""},
            "entry_points": [],
            "exit_points": [],
        }


class _Manager:
    _chat_model = "test-model"

    def __init__(self) -> None:
        self._graph_store = _GraphStore()
        self._providers = SimpleNamespace(get=lambda _name: _Provider())

    async def _build_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, str]]:
        return [{"role": "user", "content": "hi"}]

    def _resolve_provider(self, **kwargs: Any) -> _Provider:
        return _Provider()

    def _wrap_provider_for_pii(self, provider: Any, **kwargs: Any) -> Any:
        return provider

    def _record_conversation_summary(self, **kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_canonical_text_runtime_matches_compatibility_import() -> None:
    assert canonical_stream_text_response is legacy_stream_text_response


@pytest.mark.asyncio
async def test_canonical_text_runtime_streams_complete_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    manager = _Manager()
    events = []

    async for event in canonical_stream_text_response(
        manager=manager,
        workflow_id="wf-1",
        message="hello",
        history=[],
        persist_audit=lambda **kwargs: None,
        friendly_chat_error=lambda exc: str(exc),
    ):
        events.append(event.model_dump())

    assert events[-1]["type"] == "chat_complete"
    assert events[-1]["content"] == "hello world"
    assert events[-1]["token_usage"]["prompt_tokens"] == 1
    assert events[-1]["token_usage"]["completion_tokens"] == 2


@pytest.mark.asyncio
async def test_canonical_text_runtime_interrupts_with_final_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    manager = _Manager()
    cancel_event = asyncio.Event()
    events = []

    async for event in canonical_stream_text_response(
        manager=manager,
        workflow_id="wf-1",
        message="hello",
        history=[],
        cancel_event=cancel_event,
        persist_audit=lambda **kwargs: None,
        friendly_chat_error=lambda exc: str(exc),
    ):
        events.append(event.model_dump())
        if event.type == "chat_token":
            cancel_event.set()

    assert events[-1]["type"] == "chat_interrupted"
    assert events[-1]["content"] == "hello world"
    assert events[-1]["token_usage"]["prompt_tokens"] == 1
    assert events[-1]["token_usage"]["completion_tokens"] == 2


@pytest.mark.asyncio
async def test_canonical_text_runtime_uses_model_gateway_stream_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    manager = _Manager()
    gateway_calls: list[dict[str, Any]] = []

    class _Gateway:
        async def stream(self, **kwargs: Any):
            gateway_calls.append(kwargs)
            yield _Chunk("hello", "hello", False)
            yield _Chunk("", "hello world", True, {"prompt_tokens": 3, "completion_tokens": 4})

    manager.model_gateway = _Gateway()
    events = []

    async for event in canonical_stream_text_response(
        manager=manager,
        workflow_id="wf-1",
        message="hello",
        history=[],
        persist_audit=lambda **kwargs: None,
        friendly_chat_error=lambda exc: str(exc),
    ):
        events.append(event.model_dump())

    assert events[-1]["type"] == "chat_complete"
    assert events[-1]["content"] == "hello world"
    assert gateway_calls == [
        {
            "messages": [{"role": "user", "content": "hi"}],
            "model": "test-model",
            "temperature": 0.7,
            "pii_session": gateway_calls[0]["pii_session"],
        }
    ]
