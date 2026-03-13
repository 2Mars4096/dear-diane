from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Callable

import pytest

from dan.server.capability_registry import CapabilityContext
from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatInterruptedEvent,
    ChatToolCallResultEvent,
    ChatToolCallStartEvent,
)
from dan.server.concierge.models import (
    IntentCategory,
    PendingAction,
    RouteDecision,
    RouteMode,
    SurfaceMessage,
)
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.runtime import Concierge
from dan.server.concierge.session import SessionManager, SessionState
from dan.server.concierge.tier_executors import (
    InstantExecutor,
    MultiStepExecutor,
    SingleShotExecutor,
)
from dan.server.concierge.tiered_dispatch import TieredDispatcher
from dan.server.concierge.triage import TriageResult


# ---------------------------------------------------------------------------
# Mock chat manager (replaces RecordingHandler)
# ---------------------------------------------------------------------------


class MockChatManager:
    """Mock chat_manager whose send_message_with_tools yields pre-configured events."""

    def __init__(self) -> None:
        self._responses: dict[str, Any] = {}
        self.call_log: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self._providers = None
        self._chat_model = "test-model"

    async def send_message_with_tools(
        self,
        workflow_id: str = "",
        message: str = "",
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        user_text = message
        self.call_log.append(user_text)
        self.calls.append({
            "workflow_id": workflow_id,
            "message": message,
            "history": list(history or []),
            **kwargs,
        })

        response = self._responses.get(user_text)
        if response is None:
            yield ChatCompleteEvent(
                message_id="no-response",
                content=f"No response configured for: {user_text}",
                graph_revision="",
            )
            return

        if callable(response):
            async for event in response():
                yield event
        elif isinstance(response, str):
            yield ChatCompleteEvent(
                message_id="mock-complete",
                content=response,
                graph_revision="",
            )


class DummyContextGatherer:
    async def gather(self, msg: SurfaceMessage, triage: Any, concierge: Concierge) -> Any:
        return concierge._resolve_context(msg)


# ---------------------------------------------------------------------------
# Response stream factories
# ---------------------------------------------------------------------------


def _complete_stream(content: str) -> Callable[[], AsyncIterator[Any]]:
    async def _stream() -> AsyncIterator[Any]:
        yield ChatCompleteEvent(
            message_id=f"complete-{content[:8]}",
            content=content,
            graph_revision="",
        )

    return _stream


def _tool_stream(
    *,
    tool_name: str,
    output_preview: str,
    content: str,
) -> Callable[[], AsyncIterator[Any]]:
    async def _stream() -> AsyncIterator[Any]:
        tool_call_id = f"{tool_name}-call"
        yield ChatToolCallStartEvent(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args_preview="",
        )
        yield ChatToolCallResultEvent(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status="success",
            output_preview=output_preview,
            duration_ms=5,
        )
        yield ChatCompleteEvent(
            message_id=f"done-{tool_call_id}",
            content=content,
            graph_revision="",
        )

    return _stream


def _unterminated_tool_stream(
    *,
    tool_name: str,
    output_preview: str,
) -> Callable[[], AsyncIterator[Any]]:
    async def _stream() -> AsyncIterator[Any]:
        tool_call_id = f"{tool_name}-call"
        yield ChatToolCallStartEvent(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args_preview="",
        )
        yield ChatToolCallResultEvent(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            status="success",
            output_preview=output_preview,
            duration_ms=5,
        )

    return _stream


def _interruptible_stream(cancel_event: asyncio.Event) -> Callable[[], AsyncIterator[Any]]:
    async def _stream() -> AsyncIterator[Any]:
        yield ChatToolCallStartEvent(
            tool_call_id="cancel-search",
            tool_name="web_search",
            args_preview="",
        )
        while not cancel_event.is_set():
            await asyncio.sleep(0)
        yield ChatInterruptedEvent(
            message_id="cancelled-child",
            content="partial child output",
            token_usage={},
        )

    return _stream


# ---------------------------------------------------------------------------
# Concierge + dispatcher setup
# ---------------------------------------------------------------------------


def _make_concierge(tmp_path: Path) -> Concierge:
    project_store = ProjectStore(base_dir=tmp_path / "projects")
    chat_manager = MockChatManager()
    concierge = Concierge(
        project_store=project_store,
        chat_manager=chat_manager,
        capability_context=CapabilityContext(workflow_id="_scratch"),
    )
    concierge._REASSURANCE_INITIAL_DELAY = 0
    concierge._REASSURANCE_REPEAT_INTERVAL = 0
    return concierge


def _install_dispatcher(
    concierge: Concierge,
    *,
    triage_fn: Callable[..., Any],
) -> TieredDispatcher:
    session_manager = SessionManager()
    multi = MultiStepExecutor(concierge, dispatcher=None)
    dispatcher = TieredDispatcher(
        session_manager=session_manager,
        triage_fn=triage_fn,
        executors={
            0: InstantExecutor(concierge),
            1: SingleShotExecutor(concierge),
            2: multi,
        },
        context_gatherer=DummyContextGatherer(),
        concierge=concierge,
    )
    multi._dispatcher = dispatcher
    concierge._tiered_dispatcher = dispatcher
    return dispatcher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier0_social_path_returns_immediate_response(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=0,
            intent="ask",
            is_social=True,
            social_response="Hi there!",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="hello")
        )
    ]

    terminal_events = [
        event
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert [event.content for event in terminal_events] == ["Hi there!"]
    assert concierge.chat_manager.call_log == []


@pytest.mark.asyncio
async def test_tier1_path_emits_phase_events(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="ask",
            goal="Answer the user directly",
            deliverable="Answer the user directly",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["What is the status?"] = "All systems nominal."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text="What is the status?",
            )
        )
    ]

    progress_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) == "progress_ack"
    ]
    assert any("Understanding your request" in content for content in progress_messages)
    assert any("Gathering relevant context" in content for content in progress_messages)
    assert any("Executing" in content for content in progress_messages)
    assert concierge.chat_manager.call_log == ["What is the status?"]


@pytest.mark.asyncio
async def test_tier1_forwards_request_metadata_and_action_hints(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            goal="Draft the report",
            deliverable="Draft the report",
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="file",
                action_hints=["read_file", "write_file"],
            ),
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Draft the report"] = "Done."
    mentions = [{"kind": "file", "label": "notes.tex"}]
    cancel_event = asyncio.Event()

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="editor",
                external_id="editor-user",
                session_id="thread-42",
                text="Draft the report",
                metadata={
                    "workflow_id": "wf-123",
                    "request_history": [{"role": "assistant", "content": "Prior context"}],
                    "client_graph_revision": "rev-7",
                    "mode": "build",
                    "debug_context": "debug details",
                    "mentions": mentions,
                    "cancel_event": cancel_event,
                },
            )
        )
    ]

    assert any(getattr(event, "type", "") == "chat_complete" for event in events)
    assert concierge.chat_manager.call_log == ["Draft the report"]
    call = concierge.chat_manager.calls[-1]
    assert call["workflow_id"] == "wf-123"
    assert call["message"] == "Draft the report"
    assert call["history"] == [{"role": "assistant", "content": "Prior context"}]
    assert call["thread_id"] == "thread-42"
    assert call["client_graph_revision"] == "rev-7"
    assert call["mode"] == "build"
    assert call["debug_context"] == "debug details"
    assert call["mentions"] == mentions
    assert call["required_action_hints"] == ["read_file", "write_file"]
    assert call["allow_mutation_tool"] is False
    assert call["cancel_event"] is cancel_event


@pytest.mark.asyncio
async def test_tier1_enables_mutation_tool_only_for_workflow_edits(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            goal="Add a review node",
            deliverable="Update the workflow",
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="workflow",
                action_hints=["workflow_edit"],
            ),
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Add a review node"] = "Updated."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="editor",
                external_id="editor-user",
                text="Add a review node",
                metadata={
                    "workflow_id": "wf-123",
                    "mode": "build",
                },
            )
        )
    ]

    assert any(getattr(event, "type", "") == "chat_complete" for event in events)
    assert concierge.chat_manager.call_log == ["Add a review node"]
    assert concierge.chat_manager.calls[-1]["allow_mutation_tool"] is True
    assert concierge.chat_manager.calls[-1]["required_action_hints"] == ["workflow_edit"]


@pytest.mark.asyncio
async def test_tier1_synthesizes_terminal_response_when_handler_stream_ends_early(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            goal="Write the report",
            deliverable="Write the report",
        )

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Write the report"] = _unterminated_tool_stream(
        tool_name="file_read",
        output_preview="Read the source file",
    )

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="Write the report")
        )
    ]

    assert concierge.chat_manager.call_log == ["Write the report"]
    assert any(getattr(event, "type", "") == "chat_tool_call_result" for event in events)

    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert terminal_messages == [
        "The response stream ended before a final answer was produced. Please ask me to continue from the latest progress."
    ]

    root = dispatcher._session_manager.get_root("cli-user")
    assert root is not None
    assert root.result is not None
    assert root.result.content == terminal_messages[0]


@pytest.mark.asyncio
async def test_tier2_leaf_synthesizes_terminal_response_when_handler_stream_ends_early(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="agent",
            goal="Write the report",
            deliverable="Write the report",
            subtasks=["Write the report"],
            execution_order="serial",
        )

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Write the report"] = _unterminated_tool_stream(
        tool_name="file_read",
        output_preview="Read the source file",
    )

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="Write the report")
        )
    ]

    assert concierge.chat_manager.call_log == ["Write the report"]
    assert any(getattr(event, "type", "") == "chat_tool_call_result" for event in events)

    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert terminal_messages == [
        "The response stream ended before a final answer was produced. Please ask me to continue from the latest progress."
    ]

    root = dispatcher._session_manager.get_root("cli-user")
    assert root is not None
    assert root.result is not None
    assert root.result.content == terminal_messages[0]


@pytest.mark.asyncio
async def test_tier2_decomposition_bubbles_child_events(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="ask",
            goal="Prepare a short brief",
            deliverable="Prepare a short brief",
            subtasks=["search docs", "summarize findings"],
            execution_order="serial",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["search docs"] = _tool_stream(
        tool_name="web_search",
        output_preview="Found 3 sources",
        content="Search complete.",
    )
    concierge.chat_manager._responses["summarize findings"] = _complete_stream(
        "Summary complete.",
    )

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text="Prepare a short brief",
            )
        )
    ]

    assert concierge.chat_manager.call_log == ["search docs", "summarize findings"]
    event_types = [getattr(event, "type", "") for event in events]
    assert "chat_tool_call_start" in event_types
    assert "chat_tool_call_result" in event_types

    progress_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) == "progress_ack"
    ]
    assert any("search docs" in content for content in progress_messages)
    assert any("summarize findings" in content for content in progress_messages)

    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert any(content == "Search complete." for content in terminal_messages)
    assert any(content == "Summary complete." for content in terminal_messages)
    assert any(
        "## search docs" in content and "## summarize findings" in content
        for content in terminal_messages
    )


@pytest.mark.asyncio
async def test_cancellation_stops_serial_child_fan_out_and_marks_tree_cancelled(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    cancel_event = asyncio.Event()

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="ask",
            goal="Do the multi-step task",
            deliverable="Do the multi-step task",
            subtasks=["search docs", "summarize findings"],
            execution_order="serial",
        )

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["search docs"] = _interruptible_stream(cancel_event)
    concierge.chat_manager._responses["summarize findings"] = _complete_stream(
        "This should never run.",
    )

    events = []
    async for event in concierge.process(
        SurfaceMessage(
            surface="cli",
            external_id="cli-user",
            text="Do the multi-step task",
            metadata={"cancel_event": cancel_event},
        )
    ):
        events.append(event)
        if getattr(event, "type", "") == "chat_tool_call_start":
            cancel_event.set()

    assert concierge.chat_manager.call_log == ["search docs"]
    assert any(getattr(event, "type", "") == "chat_interrupted" for event in events)

    root = dispatcher._session_manager.get_root("cli-user")
    assert root is not None
    assert root.state == SessionState.CANCELLED
    assert len(root.children) == 1


@pytest.mark.asyncio
async def test_confirm_yes_replays_pending_action_instead_of_falling_back_to_got_it(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    project = concierge.project_store.create_project("Pending Project", "cli-user")
    concierge.project_store.add_task(project.project_id, "Pending task", "cli-user")
    concierge.project_store.set_pending_action(
        project.project_id,
        PendingAction(
            kind="confirm",
            intent="ask",
            original_text="Explain the pending task",
        ),
        "cli-user",
    )

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        raise AssertionError("triage should not run for confirm replay")

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Explain the pending task"] = "Confirmed execution."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="yes")
        )
    ]

    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert concierge.chat_manager.call_log == ["Explain the pending task"]
    assert "Confirmed execution." in terminal_messages
    assert "Got it." not in terminal_messages


@pytest.mark.asyncio
async def test_root_assistant_turn_persists_session_tree_metadata(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="ask",
            goal="Answer the user directly",
            deliverable="Answer the user directly",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Where are we?"] = "You are on the tiered path."

    async for _event in concierge.process(
        SurfaceMessage(
            surface="cli",
            external_id="cli-user",
            text="Where are we?",
        )
    ):
        pass

    projects = concierge.project_store.list_projects("cli-user")
    assert len(projects) == 1
    stored_task = projects[0].tasks[-1]
    assistant_turn = next(turn for turn in reversed(stored_task.turns) if turn.role == "assistant")
    assert assistant_turn.content == "You are on the tiered path."
    assert isinstance(getattr(assistant_turn, "metadata", None), dict)
    assert assistant_turn.metadata["session_tree"]
