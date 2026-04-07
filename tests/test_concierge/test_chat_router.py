from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from dan.server.chat_manager import ChatCompleteEvent
import dan.server.routers.chat as chat_router


class _FakeGraphStore:
    def __init__(self) -> None:
        self._graphs = {
            "wf-1": {"nodes": [], "edges": []},
        }

    def get_graph(self, workflow_id: str) -> dict[str, Any] | None:
        graph = self._graphs.get(workflow_id)
        return dict(graph) if graph is not None else None

    def save_graph(self, workflow_id: str, graph: dict[str, Any]) -> None:
        self._graphs[workflow_id] = dict(graph)


class _FakeChatManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def register_stream(self, _channel_id: str) -> asyncio.Event:
        return asyncio.Event()

    def unregister_stream(self, _channel_id: str) -> None:
        return None

    async def send_message_with_tools(self, **kwargs: Any) -> AsyncIterator[ChatCompleteEvent]:
        self.calls.append(kwargs)
        yield ChatCompleteEvent(
            message_id="router-complete",
            content="Built directly.",
            token_usage={},
            context_window=0,
            graph_revision="",
            detected_mode=kwargs.get("mode"),
        )

    async def send_message(self, **kwargs: Any) -> AsyncIterator[ChatCompleteEvent]:
        self.calls.append(kwargs)
        yield ChatCompleteEvent(
            message_id="router-complete",
            content="Built directly.",
            token_usage={},
            context_window=0,
            graph_revision="",
            detected_mode=kwargs.get("mode"),
        )


class _ProgressAckChatManager(_FakeChatManager):
    async def send_message_with_tools(self, **kwargs: Any) -> AsyncIterator[ChatCompleteEvent]:
        self.calls.append(kwargs)
        yield ChatCompleteEvent(
            message_id="router-progress",
            content="",
            token_usage={},
            context_window=0,
            graph_revision="",
            detected_mode="progress_ack",
        )
        yield ChatCompleteEvent(
            message_id="router-complete",
            content="Built directly.",
            token_usage={},
            context_window=0,
            graph_revision="",
        )


@pytest.mark.asyncio
async def test_chat_message_non_concierge_preserves_build_mode_and_surface_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Build a simple chain",
        mode="build",
        surface_context={"workspace_root": "/tmp/demo"},
    )

    response = await chat_router.chat_message(req, concierge=False)
    channel_id = response["stream_channel_id"]

    queue = chat_router._chat_streams[channel_id][0]
    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert manager.calls
    assert manager.calls[0]["mode"] == "build"
    assert manager.calls[0]["surface_context"] == {"workspace_root": "/tmp/demo"}
    assert [event["type"] for event in events] == ["chat_complete"]
    assert events[0]["content"] == "Built directly."


@pytest.mark.asyncio
async def test_chat_message_auto_mode_preserves_progress_ack_detected_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _ProgressAckChatManager()
    graph_store = _FakeGraphStore()
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "detect_chat_mode", lambda *_args, **_kwargs: "agent")

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Build a simple chain",
        mode="auto",
    )

    response = await chat_router.chat_message(req, concierge=False)
    channel_id = response["stream_channel_id"]

    queue = chat_router._chat_streams[channel_id][0]
    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert manager.calls
    assert [event["type"] for event in events] == ["chat_complete", "chat_complete"]
    assert events[0]["detected_mode"] == "progress_ack"
    assert events[0]["content"] == ""
    assert events[1]["detected_mode"] == "agent"
    assert events[1]["content"] == "Built directly."


def test_chat_message_request_allows_distinct_session_and_thread_ids() -> None:
    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="hello",
        session_id="lane-1",
        thread_id="conversation-1",
    )

    assert req.session_id == "lane-1"
    assert req.thread_id == "conversation-1"


@pytest.mark.asyncio
async def test_chat_message_non_concierge_prefers_session_id_for_chat_manager_thread_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Build a simple chain",
        mode="build",
        session_id="lane-1",
        thread_id="conversation-1",
    )

    response = await chat_router.chat_message(req, concierge=False)
    channel_id = response["stream_channel_id"]

    queue = chat_router._chat_streams[channel_id][0]
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break

    assert manager.calls
    assert manager.calls[0]["thread_id"] == "lane-1"


@pytest.mark.asyncio
async def test_chat_message_non_concierge_passes_attachment_context_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Build from the attachment",
        mode="build",
        attachment_path="/tmp/spec.pdf",
    )

    response = await chat_router.chat_message(req, concierge=False)
    channel_id = response["stream_channel_id"]

    queue = chat_router._chat_streams[channel_id][0]
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break

    assert manager.calls
    call = manager.calls[0]
    assert "Primary attached file path" in call["prompt_context"]
    assert "extra_system_instructions" not in call
