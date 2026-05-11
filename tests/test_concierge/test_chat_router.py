from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from dan.server.chat_manager import ChatCompleteEvent
from dan.server.control_plane import DANV2TurnOutcome
from dan.worker.organisms.dan_conversation import (
    DANConversationTurnDecision,
    ReviewDecision,
    SupervisorBrief,
    WorkerReport,
)
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


class _FakeDANV2Runtime:
    def __init__(self, outcome: DANV2TurnOutcome) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def triage_user_turn(self, **kwargs: Any) -> DANV2TurnOutcome:
        self.calls.append(kwargs)
        return self.outcome


class _FakeChatStore:
    def __init__(self) -> None:
        self._threads: dict[tuple[str, str], object] = {}
        self._meta: dict[tuple[str, str], dict[str, Any]] = {}

    def ensure_thread(self, workflow_id: str, thread_id: str) -> None:
        self._threads[(workflow_id, thread_id)] = object()

    def get_thread(self, workflow_id: str, thread_id: str) -> object | None:
        return self._threads.get((workflow_id, thread_id))

    def get_thread_meta(self, workflow_id: str, thread_id: str) -> dict[str, Any]:
        return dict(self._meta.get((workflow_id, thread_id), {}))

    def set_thread_meta(self, workflow_id: str, thread_id: str, meta: dict[str, Any]) -> None:
        self._meta[(workflow_id, thread_id)] = dict(meta)


def _direct_v2_outcome(content: str = "Answered from DAN-v2.") -> DANV2TurnOutcome:
    return DANV2TurnOutcome(
        turn_decision=DANConversationTurnDecision(
            action="respond",
            public_response=content,
        ),
        supervisor_brief=None,
        worker_report=WorkerReport(
            lane="controller",
            status="responded",
            summary=content,
        ),
        review_decision=ReviewDecision(
            action="stop",
            public_response=content,
            reason="direct",
        ),
        direct_response=content,
        controller_session_payload={"session": "v2"},
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


@pytest.mark.asyncio
async def test_chat_message_v2_control_plane_direct_response_skips_legacy_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="respond",
                public_response="Answered from DAN-v2.",
            ),
            supervisor_brief=None,
            worker_report=WorkerReport(
                lane="controller",
                status="responded",
                summary="Answered from DAN-v2.",
            ),
            review_decision=ReviewDecision(
                action="stop",
                public_response="Answered from DAN-v2.",
                reason="direct",
            ),
            direct_response="Answered from DAN-v2.",
            controller_session_payload={"session": "v2"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v2")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.delenv("DAN_TELEGRAM_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("DAN_ADAPTERS_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("DAN_EXTERNAL_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="How should we route this?",
        mode="agent",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls
    assert manager.calls == []
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "env"
    assert [event["type"] for event in events] == ["chat_complete"]
    assert events[0]["content"] == "Answered from DAN-v2."


@pytest.mark.asyncio
async def test_chat_message_v2_control_plane_handoff_uses_legacy_runtime_with_supervisor_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="delegate",
                public_response="Routing through DAN Code.",
                selected_lane="code",
                why_now="The user asked for a repo change.",
                desired_delta="Implement the requested repo change.",
                success_criteria=["Make the smallest concrete edit."],
            ),
            supervisor_brief=SupervisorBrief(
                lane="code",
                why_now="The user asked for a repo change.",
                desired_delta="Implement the requested repo change.",
                success_criteria=["Make the smallest concrete edit."],
            ),
            worker_report=WorkerReport(
                lane="code",
                status="ready",
                summary="Routing through DAN Code.",
                objective="Implement the requested repo change.",
                acceptance_criteria=["Make the smallest concrete edit."],
            ),
            review_decision=ReviewDecision(
                action="continue",
                public_response="Routing through DAN Code.",
                reason="handoff",
                next_lane="code",
            ),
            direct_response=None,
            handoff_prompt_context="DAN-v2 supervisor brief:\n- Lane: code",
            handoff_metadata={"selected_lane": "code"},
            controller_session_payload={"session": "v2"},
            code_session_payload={"session": "code"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v2")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Build a simple chain",
        mode="build",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls
    assert manager.calls
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "env"
    assert "DAN-v2 supervisor brief" in manager.calls[0]["prompt_context"]
    assert manager.calls[0]["mode"] == "build"
    assert [event["type"] for event in events] == ["chat_complete"]
    assert events[0]["content"] == "Built directly."


@pytest.mark.asyncio
async def test_chat_message_v2_control_plane_handoff_uses_legacy_runtime_with_research_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="delegate",
                public_response="Routing through DAN Research.",
                selected_lane="research",
                why_now="The user asked for current external grounding.",
                desired_delta="Investigate the current failure rate trend.",
                success_criteria=["Return a grounded answer with explicit caveats."],
            ),
            supervisor_brief=SupervisorBrief(
                lane="research",
                why_now="The user asked for current external grounding.",
                desired_delta="Investigate the current failure rate trend.",
                success_criteria=["Return a grounded answer with explicit caveats."],
            ),
            worker_report=WorkerReport(
                lane="research",
                status="ready",
                summary="Routing through DAN Research.",
                objective="Investigate the current failure rate trend.",
                acceptance_criteria=["Return a grounded answer with explicit caveats."],
            ),
            review_decision=ReviewDecision(
                action="continue",
                public_response="Routing through DAN Research.",
                reason="handoff",
                next_lane="research",
            ),
            direct_response=None,
            handoff_prompt_context=(
                "DAN-v2 supervisor brief:\n"
                "- Lane: research\n"
                "\n"
                "DAN Research handoff:\n"
                "- Objective: Investigate the current failure rate trend.\n"
                "- Delivery target: chat answer"
            ),
            handoff_metadata={
                "selected_lane": "research",
                "delivery_target": "chat answer",
            },
            controller_session_payload={"session": "v2"},
            research_session_payload={"session": "research"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v2")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.delenv("DAN_TELEGRAM_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("DAN_ADAPTERS_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("DAN_EXTERNAL_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Research the current failure trend.",
        mode="agent",
        surface="telegram:ops-bot",
        surface_type="telegram",
        surface_id="ops-bot",
        surface_context={
            "approval_mode": "confirm-risky",
            "available_tool_families": ["browser", "desktop", "adapters"],
            "available_adapters": ["telegram"],
        },
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls
    assert manager.calls
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "surface_default"
    assert "DAN Research handoff" in manager.calls[0]["prompt_context"]
    assert "Delivery target: chat answer" in manager.calls[0]["prompt_context"]
    assert manager.calls[0]["surface"] == "telegram:ops-bot"
    assert manager.calls[0]["surface_context"] == {
        "approval_mode": "confirm-risky",
        "available_tool_families": ["browser", "desktop", "adapters"],
        "available_adapters": ["telegram"],
    }
    assert manager.calls[0]["mode"] == "agent"
    assert [event["type"] for event in events] == ["chat_complete"]
    assert events[0]["content"] == "Built directly."


@pytest.mark.asyncio
async def test_chat_message_request_override_can_force_v2_when_env_is_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="respond",
                public_response="Answered from DAN-v2.",
            ),
            supervisor_brief=None,
            worker_report=WorkerReport(
                lane="controller",
                status="responded",
                summary="Answered from DAN-v2.",
            ),
            review_decision=ReviewDecision(
                action="stop",
                public_response="Answered from DAN-v2.",
                reason="direct",
            ),
            direct_response="Answered from DAN-v2.",
            controller_session_payload={"session": "v2"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v1")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="How should we route this?",
        mode="agent",
        control_plane_mode="v2",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls
    assert manager.calls == []
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "request"
    assert events[0]["content"] == "Answered from DAN-v2."


@pytest.mark.asyncio
async def test_chat_message_request_override_can_force_v1_when_env_is_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="respond",
                public_response="Answered from DAN-v2.",
            ),
            supervisor_brief=None,
            worker_report=WorkerReport(
                lane="controller",
                status="responded",
                summary="Answered from DAN-v2.",
            ),
            review_decision=ReviewDecision(
                action="stop",
                public_response="Answered from DAN-v2.",
                reason="direct",
            ),
            direct_response="Answered from DAN-v2.",
            controller_session_payload={"session": "v2"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v2")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Build a simple chain",
        mode="build",
        control_plane_mode="v1",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls == []
    assert manager.calls
    assert response["control_plane_mode"] == "v1"
    assert response["control_plane_mode_source"] == "request"
    assert [event["type"] for event in events] == ["chat_complete"]
    assert events[0]["content"] == "Built directly."


@pytest.mark.asyncio
async def test_chat_message_surface_env_override_can_force_v2_when_global_is_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="respond",
                public_response="Answered from DAN-v2.",
            ),
            supervisor_brief=None,
            worker_report=WorkerReport(
                lane="controller",
                status="responded",
                summary="Answered from DAN-v2.",
            ),
            review_decision=ReviewDecision(
                action="stop",
                public_response="Answered from DAN-v2.",
                reason="direct",
            ),
            direct_response="Answered from DAN-v2.",
            controller_session_payload={"session": "v2"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v1")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setenv("DAN_EDITOR_CONTROL_PLANE", "v2")

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="How should we route this?",
        mode="agent",
        surface="editor:panel-1",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls
    assert manager.calls == []
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "surface_env"
    assert events[0]["content"] == "Answered from DAN-v2."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("env_updates", "request_kwargs", "expected_mode", "expected_source", "expects_v2"),
    [
        (
            {"DAN_CONTROL_PLANE": "v1"},
            {"mode": "build"},
            "v1",
            "env",
            False,
        ),
        (
            {"DAN_CONTROL_PLANE": "v1"},
            {"mode": "agent", "control_plane_mode": "v2"},
            "v2",
            "request",
            True,
        ),
        (
            {"DAN_CONTROL_PLANE": "v1", "DAN_EDITOR_CONTROL_PLANE": "v2"},
            {"mode": "agent", "surface": "editor:panel-1"},
            "v2",
            "surface_env",
            True,
        ),
        (
            {"DAN_CONTROL_PLANE": "v1", "DAN_INTERNAL_CONTROL_PLANE": "v2"},
            {"mode": "agent", "surface": "editor:panel-1"},
            "v2",
            "surface_group_env",
            True,
        ),
        (
            {"DAN_CONTROL_PLANE": "v2", "DAN_ADAPTERS_CONTROL_PLANE": "v1"},
            {"mode": "build", "surface": "wechat:bot-a"},
            "v1",
            "adapter_group_env",
            False,
        ),
        (
            {"DAN_CONTROL_PLANE": "v1", "DAN_ADAPTERS_CONTROL_PLANE": "v1"},
            {"mode": "build", "surface": "telegram:bot-a"},
            "v2",
            "adapter_group_env",
            True,
        ),
        (
            {"DAN_CONTROL_PLANE": "v1", "DAN_ADAPTERS_CONTROL_PLANE": "v1", "DAN_TELEGRAM_CONTROL_PLANE": "v2"},
            {"mode": "agent", "surface": "telegram:bot-a"},
            "v2",
            "surface_env",
            True,
        ),
    ],
    ids=[
        "global-env-v1",
        "request-override-v2",
        "surface-env-v2",
        "internal-group-v2",
        "adapter-group-v1",
        "telegram-pure-v2-beats-adapter-v1",
        "surface-env-beats-adapter-group",
    ],
)
async def test_chat_message_frozen_control_plane_matrix_persists_selected_mode(
    monkeypatch: pytest.MonkeyPatch,
    env_updates: dict[str, str],
    request_kwargs: dict[str, Any],
    expected_mode: str,
    expected_source: str,
    expects_v2: bool,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    chat_store = _FakeChatStore()
    runtime = _FakeDANV2Runtime(_direct_v2_outcome())
    chat_router._chat_streams.clear()
    chat_store.ensure_thread("wf-1", "thread-1")

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: chat_store)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v1")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)
    for key in (
        "DAN_TELEGRAM_CONTROL_PLANE",
        "DAN_ADAPTERS_CONTROL_PLANE",
        "DAN_EXTERNAL_CONTROL_PLANE",
        "DAN_INTERNAL_CONTROL_PLANE",
        "DAN_EDITOR_CONTROL_PLANE",
        "DAN_TELEGRAM_ALLOW_V1",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env_updates.items():
        monkeypatch.setenv(key, value)

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Route this frozen scenario.",
        thread_id="thread-1",
        session_id="thread-1",
        **request_kwargs,
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    control_plane_meta = chat_store.get_thread_meta("wf-1", "thread-1")["control_plane"]

    assert response["control_plane_mode"] == expected_mode
    assert response["control_plane_mode_source"] == expected_source
    assert control_plane_meta["selected_mode"] == expected_mode
    assert control_plane_meta["selected_mode_source"] == expected_source

    if expects_v2:
        assert runtime.calls
        assert manager.calls == []
        assert control_plane_meta["dan_v2"]["controller_session"] == {"session": "v2"}
        assert events[0]["content"] == "Answered from DAN-v2."
    else:
        assert runtime.calls == []
        assert manager.calls
        assert "dan_v2" not in control_plane_meta
        assert events[0]["content"] == "Built directly."


@pytest.mark.asyncio
async def test_chat_message_internal_surface_group_override_can_force_v2_when_global_is_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="respond",
                public_response="Answered from DAN-v2.",
            ),
            supervisor_brief=None,
            worker_report=WorkerReport(
                lane="controller",
                status="responded",
                summary="Answered from DAN-v2.",
            ),
            review_decision=ReviewDecision(
                action="stop",
                public_response="Answered from DAN-v2.",
                reason="direct",
            ),
            direct_response="Answered from DAN-v2.",
            controller_session_payload={"session": "v2"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v1")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setenv("DAN_INTERNAL_CONTROL_PLANE", "v2")

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="How should we route this?",
        mode="agent",
        surface="editor:panel-1",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls
    assert manager.calls == []
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "surface_group_env"
    assert events[0]["content"] == "Answered from DAN-v2."


@pytest.mark.asyncio
async def test_chat_message_external_surface_group_override_can_force_v1_when_global_is_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="respond",
                public_response="Answered from DAN-v2.",
            ),
            supervisor_brief=None,
            worker_report=WorkerReport(
                lane="controller",
                status="responded",
                summary="Answered from DAN-v2.",
            ),
            review_decision=ReviewDecision(
                action="stop",
                public_response="Answered from DAN-v2.",
                reason="direct",
            ),
            direct_response="Answered from DAN-v2.",
            controller_session_payload={"session": "v2"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v2")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.delenv("DAN_TELEGRAM_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("DAN_ADAPTERS_CONTROL_PLANE", raising=False)
    monkeypatch.delenv("DAN_TELEGRAM_ALLOW_V1", raising=False)
    monkeypatch.setenv("DAN_EXTERNAL_CONTROL_PLANE", "v1")

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Build a simple chain",
        mode="build",
        surface="wechat:bot-a",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls == []
    assert manager.calls
    assert response["control_plane_mode"] == "v1"
    assert response["control_plane_mode_source"] == "surface_group_env"
    assert [event["type"] for event in events] == ["chat_complete"]
    assert events[0]["content"] == "Built directly."


@pytest.mark.asyncio
async def test_chat_message_adapter_group_override_can_force_v1_when_global_is_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="respond",
                public_response="Answered from DAN-v2.",
            ),
            supervisor_brief=None,
            worker_report=WorkerReport(
                lane="controller",
                status="responded",
                summary="Answered from DAN-v2.",
            ),
            review_decision=ReviewDecision(
                action="stop",
                public_response="Answered from DAN-v2.",
                reason="direct",
            ),
            direct_response="Answered from DAN-v2.",
            controller_session_payload={"session": "v2"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v2")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setenv("DAN_ADAPTERS_CONTROL_PLANE", "v1")

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="Build a simple chain",
        mode="build",
        surface="wechat:bot-a",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls == []
    assert manager.calls
    assert response["control_plane_mode"] == "v1"
    assert response["control_plane_mode_source"] == "adapter_group_env"
    assert [event["type"] for event in events] == ["chat_complete"]
    assert events[0]["content"] == "Built directly."


@pytest.mark.asyncio
async def test_chat_message_surface_env_beats_adapter_group_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeChatManager()
    graph_store = _FakeGraphStore()
    runtime = _FakeDANV2Runtime(
        DANV2TurnOutcome(
            turn_decision=DANConversationTurnDecision(
                action="respond",
                public_response="Answered from DAN-v2.",
            ),
            supervisor_brief=None,
            worker_report=WorkerReport(
                lane="controller",
                status="responded",
                summary="Answered from DAN-v2.",
            ),
            review_decision=ReviewDecision(
                action="stop",
                public_response="Answered from DAN-v2.",
                reason="direct",
            ),
            direct_response="Answered from DAN-v2.",
            controller_session_payload={"session": "v2"},
        )
    )
    chat_router._chat_streams.clear()

    monkeypatch.setattr(chat_router, "get_chat_manager", lambda: manager)
    monkeypatch.setattr(chat_router, "get_graph_store", lambda: graph_store)
    monkeypatch.setattr(chat_router, "get_chat_store", lambda: None)
    monkeypatch.setattr(chat_router, "get_concierge", lambda: None)
    monkeypatch.setattr(chat_router, "get_dispatcher", lambda: None)
    monkeypatch.setattr(chat_router, "get_control_plane_mode", lambda *_args, **_kwargs: "v1")
    monkeypatch.setattr(chat_router, "get_dan_v2_runtime", lambda *_args, **_kwargs: runtime)
    monkeypatch.setenv("DAN_ADAPTERS_CONTROL_PLANE", "v1")
    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "v2")

    req = chat_router.ChatMessageRequest(
        workflow_id="wf-1",
        message="How should we route this?",
        mode="agent",
        surface="telegram:bot-a",
    )

    response = await chat_router.chat_message(req, concierge=True)
    channel_id = response["stream_channel_id"]
    queue = chat_router._chat_streams[channel_id][0]

    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)

    assert runtime.calls
    assert manager.calls == []
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "surface_env"
    assert events[0]["content"] == "Answered from DAN-v2."
