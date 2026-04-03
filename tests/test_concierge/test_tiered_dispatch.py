from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable

import pytest

from dan.server.concierge.autonomy import AutonomyResolution
from dan.server.capability_registry import CapabilityContext
from dan.server.chat_manager import (
    ChatManager,
    ChatCompleteEvent,
    ChatInterruptedEvent,
    ChatQueuedEvent,
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
from dan.server.concierge.dispatcher import ConcurrentDispatcher
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.runtime import Concierge
from dan.server.concierge.session import SessionManager, SessionResult, SessionState, SessionTier
from dan.server.chat.helpers import (
    _missing_action_hints,
    _tool_retry_prompt_for_missing_actions,
)
from dan.server.concierge.tier_executors import (
    InstantExecutor,
    MultiStepExecutor,
    SingleShotExecutor,
    _build_prompt,
    _determine_stage,
    _extract_chat_params,
    _find_synthesis_gap_reason,
    _should_keep_mutation_tool_for_followup,
    _should_promote_ask_mode_for_workflow_followup,
)
from dan.server.concierge.tiering import ConciergeTierResolver
from dan.server.concierge.tiered_dispatch import ContextGatherer, TieredDispatcher
from dan.server.concierge.triage import TriageResult
from dan.server.telemetry import InMemoryTelemetryStore


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

    async def _yield_configured_response(
        self,
        user_text: str,
        *,
        fallback_key: str,
    ) -> AsyncIterator[Any]:
        response = self._responses.get(user_text)
        if response is None:
            response = self._responses.get(fallback_key)
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
            return
        if isinstance(response, str):
            yield ChatCompleteEvent(
                message_id="mock-complete",
                content=response,
                graph_revision="",
            )

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
            "call_type": "tool_loop",
            "workflow_id": workflow_id,
            "message": message,
            "history": list(history or []),
            **kwargs,
        })
        async for event in self._yield_configured_response(
            user_text,
            fallback_key="__send_message_with_tools__",
        ):
            yield event

    async def send_message(
        self,
        workflow_id: str = "",
        message: str = "",
        history: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        user_text = message
        self.call_log.append(user_text)
        self.calls.append({
            "call_type": "text_only",
            "workflow_id": workflow_id,
            "message": message,
            "history": list(history or []),
            **kwargs,
        })
        async for event in self._yield_configured_response(
            user_text,
            fallback_key="__send_message__",
        ):
            yield event


class _FakeProvider:
    def __init__(self, response_text: str, *, error: Exception | None = None) -> None:
        self._response_text = response_text
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                **kwargs,
            }
        )
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            text=self._response_text,
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )


class _FakeProviderRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider
        self.resolved_models: list[str] = []

    def resolve(self, model: str) -> _FakeProvider:
        self.resolved_models.append(model)
        return self._provider


class DummyContextGatherer:
    async def gather(
        self,
        msg: SurfaceMessage,
        triage: Any,
        concierge: Concierge,
        autonomy_resolution: Any | None = None,
    ) -> Any:
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


def _terminal_interrupt_stream(content: str) -> Callable[[], AsyncIterator[Any]]:
    async def _stream() -> AsyncIterator[Any]:
        yield ChatInterruptedEvent(
            message_id="terminal-interrupt",
            content=content,
            token_usage={},
        )

    return _stream


# ---------------------------------------------------------------------------
# Concierge + dispatcher setup
# ---------------------------------------------------------------------------


def _make_concierge(tmp_path: Path, *, telemetry_store: Any | None = None) -> Concierge:
    project_store = ProjectStore(base_dir=tmp_path / "projects")
    chat_manager = MockChatManager()
    concierge = Concierge(
        project_store=project_store,
        chat_manager=chat_manager,
        capability_context=CapabilityContext(workflow_id="_scratch"),
        telemetry_store=telemetry_store,
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
            SurfaceMessage(surface="cli", external_id="cli-user", text="greetings")
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
async def test_fast_social_turn_skips_triage_llm_call(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        raise AssertionError("triage should be bypassed by fast social classifier")

    _install_dispatcher(concierge, triage_fn=triage_fn)

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="thanks!")
        )
    ]
    terminal_events = [
        event
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert [event.content for event in terminal_events] == ["You're welcome."]
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
    # Execution phase uses triage goal as progress detail (not the bare word "Executing").
    assert any("Answer the user directly" in content for content in progress_messages)
    assert concierge.chat_manager.call_log == ["What is the status?"]


@pytest.mark.asyncio
async def test_process_emits_immediate_intake_ack_before_reassurance_timeout(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    concierge._REASSURANCE_INITIAL_DELAY = 60
    concierge._REASSURANCE_REPEAT_INTERVAL = 60

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

    first_complete = next(
        event
        for event in events
        if getattr(event, "type", "") == "chat_complete"
    )
    assert getattr(first_complete, "detected_mode", None) == "progress_ack"
    assert "Understanding your request" in first_complete.content


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
                    "surface_context": {
                        "mode": "development",
                        "workspace_id": "ws-1",
                        "active_file": {
                            "path": "src/report.md",
                            "content": "Draft outline",
                            "language": "markdown",
                        },
                    },
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
    assert call["surface_context"]["active_file"]["path"] == "src/report.md"
    assert call["required_action_hints"] == ["read_file", "write_file"]
    assert call["allow_mutation_tool"] is True
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
async def test_tier1_workflow_query_only_keeps_mutation_tool_disabled(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            goal="Explain the current workflow",
            deliverable="Workflow explanation",
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="workflow",
                action_hints=["workflow_query"],
            ),
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Explain the current workflow"] = "This workflow reviews and summarizes documents."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="editor",
                external_id="editor-user",
                text="Explain the current workflow",
                metadata={
                    "workflow_id": "wf-123",
                    "mode": "agent",
                },
            )
        )
    ]

    assert any(getattr(event, "type", "") == "chat_complete" for event in events)
    assert concierge.chat_manager.call_log == ["Explain the current workflow"]
    call = concierge.chat_manager.calls[-1]
    assert call["allow_mutation_tool"] is False
    assert call["required_action_hints"] == ["workflow_query"]


@pytest.mark.asyncio
async def test_tier2_build_override_skips_decomposition_and_calls_builder_directly(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="agent",
            goal="Build the workflow",
            deliverable="Create the workflow",
            subtasks=["research", "analyze", "summarize"],
            execution_order="serial",
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="general",
                action_hints=[],
            ),
        )

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    prompt = "Build a simple 3-step chain: research, analyze, summarize"
    concierge.chat_manager._responses[prompt] = _complete_stream("Workflow created.")

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text=prompt,
                metadata={
                    "workflow_id": "wf-123",
                    "mode": "agent",
                    "requested_mode": "build",
                },
            )
        )
    ]

    assert any(getattr(event, "type", "") == "chat_complete" for event in events)
    assert concierge.chat_manager.call_log == [prompt]
    call = concierge.chat_manager.calls[-1]
    assert call["workflow_id"] == "wf-123"
    assert call["mode"] == "build"
    assert call["allow_mutation_tool"] is True

    root = dispatcher._session_manager.get_root("cli-user")
    assert root is not None
    assert root.children == []
    assert root.result is not None
    assert root.result.content == "Workflow created."


def test_build_child_session_drops_workflow_route_hints_for_non_workflow_subtasks(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    executor = MultiStepExecutor(concierge, dispatcher=None)
    manager = SessionManager()
    session = manager.create_root(
        SurfaceMessage(
            surface="cli",
            external_id="cli-user",
            text="Investigate and summarize",
            session_id="thread-42",
            metadata={
                "mode": "agent",
                "workflow_id": "wf-123",
                "thread_id": "thread-42",
                "memory_context": "Remember the prior patch failure.",
                "request_history": [{"role": "user", "content": "full parent history"}],
                "surface_context": {
                    "workspace_root": str(tmp_path),
                    "mentioned_files": [{"path": str(tmp_path / "src" / "feature.py")}],
                },
            },
        ),
        triage=TriageResult(
            tier=2,
            intent="agent",
            goal="Investigate and summarize",
            deliverable="Investigate and summarize",
            subtasks=["search docs"],
            execution_order="serial",
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="workflow",
                action_hints=["workflow_query", "search_web"],
            ),
        ),
        tier=SessionTier.MULTI,
    )
    session.task_context = {"mutable": {"a": 1}}

    child = executor._build_child_session(session, manager, "search docs")

    assert child.triage is not None
    assert child.triage.route is not None
    assert child.triage.route.target == "general"
    assert child.triage.route.action_hints == ["search_web"]
    assert child.msg.metadata["allow_mutation_tool"] is False
    assert child.msg.session_id != session.msg.session_id
    assert child.msg.metadata["thread_id"] != session.msg.metadata["thread_id"]
    assert child.msg.metadata["parent_thread_id"] == "thread-42"
    assert child.msg.metadata["tiered_child_session_id"] == child.id
    assert child.task_context["handoff"]["parent_thread_id"] == "thread-42"
    assert child.msg.metadata["tiered_handoff"]["constraints"]["read_only_parent_context"] is True
    assert child.msg.metadata["tiered_handoff"]["return_channel"]["parent_session_id"] == session.id
    assert "request_history" not in child.msg.metadata
    assert "mutable" not in child.task_context["parent_context"]
    child.msg.metadata["surface_context"]["mentioned_files"][0]["path"] = "changed"
    assert session.msg.metadata["surface_context"]["mentioned_files"][0]["path"] != "changed"
    child.task_context["handoff"]["context"]["file_refs"].append("/tmp/extra.py")
    assert "/tmp/extra.py" not in child.task_context["parent_context"].get("file_refs", [])
    assert session.task_context["mutable"]["a"] == 1


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
async def test_tier2_decomposition_uses_llm_when_triage_has_no_subtasks(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="ask",
            goal="Search docs and summarize findings",
            deliverable="Search docs and summarize findings",
            execution_order="serial",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    provider = _FakeProvider('{"subtasks":["search docs","summarize findings"]}')
    concierge.chat_manager._providers = _FakeProviderRegistry(provider)
    concierge.chat_manager._responses["search docs"] = _complete_stream("Search complete.")
    concierge.chat_manager._responses["summarize findings"] = _complete_stream("Summary complete.")

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text="Search docs and summarize findings",
            )
        )
    ]

    assert concierge.chat_manager.call_log == ["search docs", "summarize findings"]
    assert provider.calls
    assert provider.calls[0].get("max_tokens") is None
    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert any("## search docs" in content and "## summarize findings" in content for content in terminal_messages)


@pytest.mark.asyncio
async def test_tier2_decomposition_falls_back_to_split_when_llm_unavailable(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="ask",
            goal="Search docs and summarize findings",
            deliverable="Search docs and summarize findings",
            execution_order="serial",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Search docs"] = _complete_stream("Search complete.")
    concierge.chat_manager._responses["summarize findings"] = _complete_stream("Summary complete.")

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text="Search docs and summarize findings",
            )
        )
    ]

    assert concierge.chat_manager.call_log == ["Search docs", "summarize findings"]
    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert any("## Search docs" in content and "## summarize findings" in content for content in terminal_messages)


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
async def test_tier1_terminal_interrupt_marks_session_cancelled_with_partial_status(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            goal="Continue the migration",
            deliverable="Continue the migration",
        )

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Continue the migration"] = _terminal_interrupt_stream(
        "Partial migration notes"
    )

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="interrupt-user",
                text="Continue the migration",
            )
        )
    ]

    assert any(getattr(event, "type", "") == "chat_interrupted" for event in events)
    root = dispatcher._session_manager.get_root("interrupt-user")
    assert root is not None
    assert root.state == SessionState.CANCELLED
    assert root.result is not None
    assert root.result.content == "Partial migration notes"
    assert root.result.metadata["completion_status"] == "interrupted"


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
    attached_text = "Explain the pending task\n[User confirmation: yes]"
    concierge.chat_manager._responses[attached_text] = "Confirmed execution."

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
    assert concierge.chat_manager.call_log == [attached_text]
    assert "Confirmed execution." in terminal_messages
    assert "Got it." not in terminal_messages
    assert "Pending action attachment" in concierge.chat_manager.calls[-1]["extra_system_instructions"]

    refreshed = concierge.project_store.get_project(project.project_id, "cli-user")
    assert refreshed is not None
    task = refreshed.tasks[-1]
    user_turn = next(turn for turn in reversed(task.turns) if turn.role == "user")
    assert user_turn.content == "yes"
    assert user_turn.metadata["skip_confirm"] is True
    assert user_turn.metadata["pending_route_step"] == "approval_confirmation"
    assert user_turn.metadata["attached_user_reply"] == "yes"
    assert user_turn.metadata["replay_source"] == "pending_attachment"
    assert user_turn.metadata["pending_attachment_source"] == "pending_follow_up"
    assert user_turn.metadata["pending_original_text"] == "Explain the pending task"
    assert user_turn.metadata["pending_effective_text"] == attached_text


@pytest.mark.asyncio
async def test_clarify_resume_persists_attached_reply_metadata(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    project = concierge.project_store.create_project("Pending Project", "cli-user")
    concierge.project_store.add_task(project.project_id, "Pending task", "cli-user")
    concierge.project_store.set_pending_action(
        project.project_id,
        PendingAction(
            kind="clarify",
            intent="ask",
            original_text="Pick a file to continue",
        ),
        "cli-user",
    )

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        raise AssertionError("triage should not run for clarification handling")

    _install_dispatcher(concierge, triage_fn=triage_fn)
    replay_text = "Pick a file to continue\n[User clarification: use README.md]"
    concierge.chat_manager._responses[replay_text] = "Continuing with README."

    async for _event in concierge.process(
        SurfaceMessage(surface="cli", external_id="cli-user", text="use README.md")
    ):
        pass

    assert concierge.chat_manager.call_log == [replay_text]
    refreshed = concierge.project_store.get_project(project.project_id, "cli-user")
    assert refreshed is not None
    task = refreshed.tasks[-1]
    user_turn = next(turn for turn in reversed(task.turns) if turn.role == "user")
    assert user_turn.content == "use README.md"
    assert user_turn.metadata["clarification_answer"] == "use README.md"
    assert user_turn.metadata["pending_route_step"] == "clarification_answer"
    assert user_turn.metadata["attached_user_reply"] == "use README.md"
    assert user_turn.metadata["replay_source"] == "pending_attachment"
    assert user_turn.metadata["pending_attachment_source"] == "pending_follow_up"
    assert user_turn.metadata["pending_original_text"] == "Pick a file to continue"
    assert user_turn.metadata["pending_effective_text"] == replay_text


@pytest.mark.asyncio
async def test_low_confidence_triage_requests_clarification_and_retriages_reply(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    triage_calls: list[str] = []
    original_text = "do it"
    clarification_reply = "update README.md to fix the typo"
    replay_text = f"{original_text}\n[User clarification: {clarification_reply}]"

    async def triage_fn(text: str, *args: Any, **kwargs: Any) -> TriageResult:
        triage_calls.append(text)
        if text == replay_text:
            return TriageResult(
                tier=1,
                intent="agent",
                confidence=0.91,
                goal="Update README",
                deliverable="Update README",
                route=RouteDecision(
                    mode=RouteMode.AGENT,
                    target="file",
                    action_hints=["write_file"],
                ),
            )
        return TriageResult(
            tier=1,
            intent="agent",
            confidence=0.55,
            goal="Do the task",
            deliverable="Do the task",
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="file",
                action_hints=["write_file"],
            ),
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses[replay_text] = "Updated README."

    first_events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text=original_text)
        )
    ]

    first_final = next(
        event for event in reversed(first_events) if isinstance(event, ChatCompleteEvent)
    )
    assert "Which file should I update" in first_final.content
    assert concierge.chat_manager.call_log == []

    pending_project = concierge.project_store.get_pending_project("cli-user")
    assert pending_project is not None
    assert pending_project.pending_action is not None
    assert pending_project.pending_action.metadata["requires_triage"] is True
    current_task = concierge.project_store.get_current_task(
        pending_project.project_id,
        "cli-user",
    )
    assert current_task is not None
    assert current_task.status == "paused"

    second_events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text=clarification_reply,
            )
        )
    ]

    second_final = next(
        event for event in reversed(second_events) if isinstance(event, ChatCompleteEvent)
    )
    assert second_final.content.endswith("Updated README.")
    assert triage_calls == [original_text, replay_text]
    assert concierge.chat_manager.call_log == [replay_text]
    assert concierge.project_store.get_pending_project("cli-user") is None

    project = concierge.project_store.list_projects("cli-user")[0]
    task = project.tasks[-1]
    user_turn = next(turn for turn in reversed(task.turns) if turn.role == "user")
    assert user_turn.content == clarification_reply
    assert user_turn.metadata["pending_requires_triage"] is True
    assert user_turn.metadata["pending_route_step"] == "clarification_answer"
    assert user_turn.metadata["pending_effective_text"] == replay_text


@pytest.mark.asyncio
async def test_low_confidence_clarification_falls_through_when_pending_action_persist_fails(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    concierge.chat_manager._responses["do it"] = "Executed without clarification."

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            confidence=0.55,
            goal="Do the task",
            deliverable="Do the task",
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="file",
                action_hints=["write_file"],
            ),
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)

    original_set_pending_action = concierge.project_store.set_pending_action

    def _broken_set_pending_action(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("disk write failed")

    concierge.project_store.set_pending_action = _broken_set_pending_action  # type: ignore[assignment]
    try:
        events = [
            event
            async for event in concierge.process(
                SurfaceMessage(surface="cli", external_id="cli-user", text="do it")
            )
        ]
    finally:
        concierge.project_store.set_pending_action = original_set_pending_action  # type: ignore[assignment]

    final = next(
        event for event in reversed(events) if isinstance(event, ChatCompleteEvent)
    )
    assert final.content.endswith("Executed without clarification.")
    assert concierge.chat_manager.call_log == ["do it"]
    assert concierge.project_store.get_pending_project("cli-user") is None


@pytest.mark.asyncio
async def test_low_confidence_clarification_pauses_same_project_queue_until_reply(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    dispatcher = ConcurrentDispatcher(concierge, max_concurrent_projects=5)
    triage_calls: list[str] = []
    queue_channel_id = ""
    original_text = "do it"
    queued_text = "also check the tests"
    clarification_reply = "update README.md to fix the typo"
    replay_text = f"{original_text}\n[User clarification: {clarification_reply}]"
    first_triage_started = asyncio.Event()
    allow_first_triage = asyncio.Event()

    async def triage_fn(text: str, *args: Any, **kwargs: Any) -> TriageResult:
        triage_calls.append(text)
        if text == original_text:
            first_triage_started.set()
            await allow_first_triage.wait()
            return TriageResult(
                tier=1,
                intent="agent",
                confidence=0.55,
                goal="Do the task",
                deliverable="Do the task",
                route=RouteDecision(
                    mode=RouteMode.AGENT,
                    target="file",
                    action_hints=["write_file"],
                ),
            )
        if text == replay_text:
            return TriageResult(
                tier=1,
                intent="agent",
                confidence=0.91,
                goal="Update README",
                deliverable="Update README",
                route=RouteDecision(
                    mode=RouteMode.AGENT,
                    target="file",
                    action_hints=["write_file"],
                ),
            )
        return TriageResult(
            tier=1,
            intent="ask",
            confidence=0.91,
            goal="Check the tests",
            deliverable="Check the tests",
            route=RouteDecision(
                mode=RouteMode.ASK,
                target="general",
                action_hints=["status_check"],
            ),
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses[replay_text] = "Updated README."
    concierge.chat_manager._responses[queued_text] = "Checked tests."

    async def _run_first() -> list[Any]:
        return [
            event
            async for event in dispatcher.dispatch(
                SurfaceMessage(surface="cli", external_id="cli-user", text=original_text)
            )
        ]

    async def _run_second() -> list[Any]:
        nonlocal queue_channel_id
        events: list[Any] = []
        async for event in dispatcher.dispatch(
            SurfaceMessage(surface="cli", external_id="cli-user", text=queued_text)
        ):
            events.append(event)
            if isinstance(event, ChatQueuedEvent):
                queue_channel_id = event.stream_channel_id
        return events

    first_task = asyncio.create_task(_run_first())
    await first_triage_started.wait()
    second_events = await _run_second()
    assert any(isinstance(event, ChatQueuedEvent) for event in second_events)
    assert queue_channel_id

    allow_first_triage.set()
    first_events = await first_task
    first_final = next(
        event for event in reversed(first_events) if isinstance(event, ChatCompleteEvent)
    )
    assert "Which file should I update" in first_final.content

    queued_bus = dispatcher.get_response_bus(queue_channel_id)
    assert queued_bus is not None
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queued_bus.get(), timeout=0.1)

    clarification_events = [
        event
        async for event in dispatcher.dispatch(
            SurfaceMessage(surface="cli", external_id="cli-user", text=clarification_reply)
        )
    ]
    clarification_final = next(
        event
        for event in reversed(clarification_events)
        if isinstance(event, ChatCompleteEvent)
    )
    assert clarification_final.content.endswith("Updated README.")

    queued_bus_events: list[Any] = []
    try:
        while True:
            item = await asyncio.wait_for(queued_bus.get(), timeout=1.0)
            if item is None:
                break
            queued_bus_events.append(item)
    finally:
        dispatcher.cleanup_response_bus(queue_channel_id)
    queued_final = next(
        event
        for event in reversed(queued_bus_events)
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) != "progress_ack"
    )
    assert queued_final.content.endswith("Checked tests.")
    assert concierge.chat_manager.call_log == [replay_text, queued_text]
    assert triage_calls == [original_text, replay_text, queued_text]
    assert concierge.project_store.get_pending_project("cli-user") is None


@pytest.mark.asyncio
async def test_low_confidence_plain_ask_does_not_force_clarification(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="ask",
            confidence=0.55,
            goal="Explain the task",
            deliverable="Explanation",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["What does this task mean?"] = "It means you should review the report."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text="What does this task mean?",
            )
        )
    ]

    final_event = next(
        event for event in reversed(events) if isinstance(event, ChatCompleteEvent)
    )
    assert final_event.content.endswith("It means you should review the report.")
    assert concierge.chat_manager.call_log == ["What does this task mean?"]
    assert concierge.project_store.get_pending_project("cli-user") is None


@pytest.mark.asyncio
async def test_clarify_choice_attaches_selected_option_without_stale_user_turn(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    project = concierge.project_store.create_project("Pending Project", "cli-user")
    concierge.project_store.add_task(project.project_id, "Pending task", "cli-user")
    concierge.project_store.set_pending_action(
        project.project_id,
        PendingAction(
            kind="clarify",
            intent="ask",
            original_text="Pick a file to continue",
            options=["/tmp/a.md", "/tmp/b.md"],
        ),
        "cli-user",
    )

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        raise AssertionError("triage should not run for clarification handling")

    _install_dispatcher(concierge, triage_fn=triage_fn)
    attached_text = "Pick a file to continue\n[User selected option: /tmp/b.md]"
    concierge.chat_manager._responses[attached_text] = "Continuing with /tmp/b.md."

    async for _event in concierge.process(
        SurfaceMessage(surface="cli", external_id="cli-user", text="2")
    ):
        pass

    assert concierge.chat_manager.call_log == [attached_text]
    refreshed = concierge.project_store.get_project(project.project_id, "cli-user")
    assert refreshed is not None
    task = refreshed.tasks[-1]
    user_turn = next(turn for turn in reversed(task.turns) if turn.role == "user")
    assert user_turn.content == "2"
    assert user_turn.metadata["selected_option"] == 1
    assert user_turn.metadata["selected_path"] == "/tmp/b.md"
    assert user_turn.metadata["pending_route_step"] == "clarification_choice"
    assert user_turn.metadata["pending_original_text"] == "Pick a file to continue"
    assert user_turn.metadata["pending_effective_text"] == attached_text
    assert user_turn.metadata["pending_attachment_source"] == "pending_follow_up"


@pytest.mark.asyncio
async def test_clarify_unmatched_reply_requests_explicit_number(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    project = concierge.project_store.create_project("Pending Project", "cli-user")
    concierge.project_store.add_task(project.project_id, "Pending task", "cli-user")
    concierge.project_store.set_pending_action(
        project.project_id,
        PendingAction(
            kind="clarify",
            intent="ask",
            original_text="Pick a file to continue",
            options=["/tmp/a.md", "/tmp/b.md"],
        ),
        "cli-user",
    )

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        raise AssertionError("triage should not run for clarification handling")

    _install_dispatcher(concierge, triage_fn=triage_fn)

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="something else")
        )
    ]
    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert any("Please reply with a number" in content for content in terminal_messages)
    refreshed = concierge.project_store.get_project(project.project_id, "cli-user")
    assert refreshed is not None
    assert refreshed.pending_action is not None


@pytest.mark.asyncio
async def test_superseding_pending_confirm_retriages_against_resolved_project(tmp_path: Path) -> None:
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
    other_project = concierge.project_store.create_project("Other Project", "cli-user")
    concierge.project_store.add_task(other_project.project_id, "Other task", "cli-user")

    triage_calls: list[tuple[str, str]] = []

    async def triage_fn(
        text: str,
        context: Any,
        *_args: Any,
        **_kwargs: Any,
    ) -> TriageResult:
        triage_calls.append((text, context.project.project_id))
        return TriageResult(
            tier=1,
            intent="agent",
            goal=text,
            deliverable=text,
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    attached_text = (
        "actually fix the tests instead\n"
        "[Supersedes pending confirm: Explain the pending task]"
    )
    concierge.chat_manager._responses[attached_text] = "Switching to the test fix."

    async for _event in concierge.process(
        SurfaceMessage(
            surface="cli",
            external_id="cli-user",
            text="actually fix the tests instead",
        )
    ):
        pass

    assert triage_calls == [(attached_text, project.project_id)]
    assert concierge.chat_manager.call_log == [attached_text]

    refreshed = concierge.project_store.get_project(project.project_id, "cli-user")
    assert refreshed is not None
    assert refreshed.pending_action is None
    task = refreshed.tasks[-1]
    user_turn = next(turn for turn in reversed(task.turns) if turn.role == "user")
    assert user_turn.content == "actually fix the tests instead"
    assert user_turn.metadata["pending_route_step"] == "superseding_instruction"
    assert user_turn.metadata["pending_requires_triage"] is True
    assert user_turn.metadata["pending_original_text"] == "Explain the pending task"
    assert user_turn.metadata["pending_effective_text"] == attached_text


@pytest.mark.asyncio
async def test_mixed_execution_order_preserved_on_root_session(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="agent",
            goal="Run mixed plan",
            deliverable="Run mixed plan",
            subtasks=["search docs", "summarize findings"],
            execution_order="mixed",
        )

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["search docs"] = _complete_stream("Search complete.")
    concierge.chat_manager._responses["summarize findings"] = _complete_stream("Summary complete.")

    async for _event in concierge.process(
        SurfaceMessage(surface="cli", external_id="cli-user", text="Run mixed plan")
    ):
        pass

    root = dispatcher._session_manager.get_root("cli-user")
    assert root is not None
    assert root.child_execution == "mixed"


@pytest.mark.asyncio
async def test_mixed_execution_runs_independent_group_in_parallel(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="agent",
            goal="Run mixed plan",
            deliverable="Run mixed plan",
            subtasks=["search docs", "inspect repo", "summarize findings"],
            execution_order="mixed",
        )

    def _delayed_stream(content: str, delay: float) -> Callable[[], AsyncIterator[Any]]:
        async def _stream() -> AsyncIterator[Any]:
            await asyncio.sleep(delay)
            yield ChatCompleteEvent(
                message_id=f"complete-{content[:8]}",
                content=content,
                graph_revision="",
            )

        return _stream

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["search docs"] = _delayed_stream("Search complete.", 0.15)
    concierge.chat_manager._responses["inspect repo"] = _delayed_stream("Repo complete.", 0.15)
    concierge.chat_manager._responses["summarize findings"] = _complete_stream("Summary complete.")

    start = time.monotonic()
    async for _event in concierge.process(
        SurfaceMessage(surface="cli", external_id="cli-user", text="Run mixed plan")
    ):
        pass
    elapsed = time.monotonic() - start

    root = dispatcher._session_manager.get_root("cli-user")
    assert root is not None
    assert root.child_execution == "mixed"
    assert elapsed < 0.26, f"expected hybrid grouping, got {elapsed:.3f}s"

    summary_child = dispatcher._session_manager.get(root.children[-1])
    assert summary_child is not None
    previous_result = summary_child.task_context.get("previous_result", "")
    assert "Search complete." in previous_result
    assert "Repo complete." in previous_result


@pytest.mark.asyncio
async def test_mixed_execution_runs_dependent_stage_in_parallel_after_independent_group(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="agent",
            goal="Run mixed plan",
            deliverable="Run mixed plan",
            subtasks=[
                "search docs",
                "inspect repo",
                "summarize docs findings",
                "summarize repo findings",
                "compile final answer",
            ],
            execution_order="mixed",
        )

    def _delayed_stream(content: str, delay: float) -> Callable[[], AsyncIterator[Any]]:
        async def _stream() -> AsyncIterator[Any]:
            await asyncio.sleep(delay)
            yield ChatCompleteEvent(
                message_id=f"complete-{content[:8]}",
                content=content,
                graph_revision="",
            )

        return _stream

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["search docs"] = _delayed_stream("Search complete.", 0.15)
    concierge.chat_manager._responses["inspect repo"] = _delayed_stream("Repo complete.", 0.15)
    concierge.chat_manager._responses["summarize docs findings"] = _delayed_stream("Docs summary complete.", 0.15)
    concierge.chat_manager._responses["summarize repo findings"] = _delayed_stream("Repo summary complete.", 0.15)
    concierge.chat_manager._responses["compile final answer"] = _complete_stream("Final answer complete.")

    start = time.monotonic()
    async for _event in concierge.process(
        SurfaceMessage(surface="cli", external_id="cli-user", text="Run mixed plan")
    ):
        pass
    elapsed = time.monotonic() - start

    root = dispatcher._session_manager.get_root("cli-user")
    assert root is not None
    assert root.child_execution == "mixed"
    assert elapsed < 0.42, f"expected dependent-stage fan-out, got {elapsed:.3f}s"

    assert set(concierge.chat_manager.call_log[:2]) == {"search docs", "inspect repo"}
    assert set(concierge.chat_manager.call_log[2:4]) == {
        "summarize docs findings",
        "summarize repo findings",
    }
    assert concierge.chat_manager.call_log[-1] == "compile final answer"

    docs_summary_child = dispatcher._session_manager.get(root.children[2])
    repo_summary_child = dispatcher._session_manager.get(root.children[3])
    final_child = dispatcher._session_manager.get(root.children[4])
    assert docs_summary_child is not None
    assert repo_summary_child is not None
    assert final_child is not None
    assert "Search complete." in docs_summary_child.task_context.get("previous_result", "")
    assert "Repo complete." in docs_summary_child.task_context.get("previous_result", "")
    assert "Search complete." in repo_summary_child.task_context.get("previous_result", "")
    assert "Repo complete." in repo_summary_child.task_context.get("previous_result", "")
    assert "Docs summary complete." in final_child.task_context.get("previous_result", "")
    assert "Repo summary complete." in final_child.task_context.get("previous_result", "")


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
    root_trace = assistant_turn.metadata["session_tree"][0]
    assert root_trace["concierge_stage"] == "conversation"
    assert root_trace["route_source"] == "llm"
    assert root_trace["autonomy_level"] == "balanced"


# ---------------------------------------------------------------------------
# _determine_stage tests
# ---------------------------------------------------------------------------


class _FakeMsg:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = metadata or {}
        self.surface = "cli"
        self.external_id = "test"
        self.text = "hello"


class _FakeRoute:
    def __init__(
        self,
        target: str = "general",
        action_hints: list[str] | None = None,
    ) -> None:
        self.target = target
        self.action_hints = action_hints or []


class _FakeTriage:
    def __init__(
        self,
        intent: str = "ask",
        route: Any = None,
    ) -> None:
        self.intent = intent
        self.route = route
        self.goal = "test"
        self.deliverable = "test"


class _FakeSession:
    def __init__(
        self,
        triage: Any = None,
        msg: Any = None,
        autonomy_resolution: Any | None = None,
    ) -> None:
        self.triage = triage
        self.msg = msg or _FakeMsg()
        self.id = "sess-1"
        self.root_id = "sess-1"
        self.context = None
        self.task = "test task"
        self.tier = 1
        self.autonomy_resolution = autonomy_resolution


def test_determine_stage_default_conversation() -> None:
    session = _FakeSession(triage=_FakeTriage())
    assert _determine_stage(session) == "conversation"


def test_determine_stage_plan_intent() -> None:
    session = _FakeSession(triage=_FakeTriage(intent="plan"))
    assert _determine_stage(session) == "conversation_plan"


def test_determine_stage_plan_mode() -> None:
    session = _FakeSession(
        triage=_FakeTriage(),
        msg=_FakeMsg(metadata={"mode": "plan"}),
    )
    assert _determine_stage(session) == "conversation_plan"


def test_determine_stage_debug_mode() -> None:
    session = _FakeSession(
        triage=_FakeTriage(),
        msg=_FakeMsg(metadata={"mode": "debug"}),
    )
    assert _determine_stage(session) == "conversation_debug"


def test_determine_stage_build_mode() -> None:
    session = _FakeSession(
        triage=_FakeTriage(),
        msg=_FakeMsg(metadata={"mode": "build"}),
    )
    assert _determine_stage(session) == "workflow_build"


def test_determine_stage_requested_build_mode_override() -> None:
    session = _FakeSession(
        triage=_FakeTriage(),
        msg=_FakeMsg(metadata={"mode": "agent", "requested_mode": "build"}),
    )
    assert _determine_stage(session) == "workflow_build"


def test_determine_stage_workflow_edit_action_hint() -> None:
    route = _FakeRoute(action_hints=["workflow_edit"])
    session = _FakeSession(triage=_FakeTriage(route=route))
    assert _determine_stage(session) == "workflow_build"


def test_determine_stage_workflow_build_action_hint() -> None:
    route = _FakeRoute(action_hints=["workflow_build"])
    session = _FakeSession(triage=_FakeTriage(route=route))
    assert _determine_stage(session) == "workflow_build"


def test_determine_stage_workflow_target() -> None:
    route = _FakeRoute(target="workflow")
    session = _FakeSession(triage=_FakeTriage(route=route))
    assert _determine_stage(session) == "workflow_build"


def test_determine_stage_workflow_query_only_stays_conversation() -> None:
    route = _FakeRoute(target="workflow", action_hints=["workflow_query"])
    session = _FakeSession(triage=_FakeTriage(route=route))
    assert _determine_stage(session) == "conversation"


def test_determine_stage_file_target() -> None:
    route = _FakeRoute(target="file")
    session = _FakeSession(triage=_FakeTriage(route=route))
    assert _determine_stage(session) == "file_review"


def test_determine_stage_no_triage() -> None:
    session = _FakeSession(triage=None)
    assert _determine_stage(session) == "conversation"


def test_determine_stage_experience_lookup() -> None:
    route = _FakeRoute(action_hints=["experience_lookup"])
    session = _FakeSession(triage=_FakeTriage(route=route))
    assert _determine_stage(session) == "experience_fallback"


def test_determine_stage_direct_task_run() -> None:
    route = _FakeRoute(target="run")
    session = _FakeSession(triage=_FakeTriage(intent="agent", route=route))
    assert _determine_stage(session) == "direct_task"


def test_determine_stage_direct_task_memory() -> None:
    route = _FakeRoute(target="memory")
    session = _FakeSession(triage=_FakeTriage(intent="agent", route=route))
    assert _determine_stage(session) == "direct_task"


def test_determine_stage_direct_task_web() -> None:
    route = _FakeRoute(target="web")
    session = _FakeSession(triage=_FakeTriage(intent="agent", route=route))
    assert _determine_stage(session) == "direct_task"


def test_determine_stage_run_target_ask_intent_is_conversation() -> None:
    """run target with 'ask' intent should be conversation, not direct_task."""
    route = _FakeRoute(target="run")
    session = _FakeSession(triage=_FakeTriage(intent="ask", route=route))
    assert _determine_stage(session) == "conversation"


def test_workflow_followup_promotes_ask_mode_and_keeps_mutation_tool() -> None:
    history = [
        {"role": "user", "content": "build a workflow for quarterly reports"},
        {"role": "assistant", "content": "I created a workflow with 3 nodes."},
    ]

    assert _should_promote_ask_mode_for_workflow_followup(
        "also make it weekly and send email updates",
        history,
    )
    assert _should_keep_mutation_tool_for_followup(
        "also make it weekly and send email updates",
        history,
    )


# ---------------------------------------------------------------------------
# _extract_chat_params model_override tests
# ---------------------------------------------------------------------------


def test_extract_chat_params_includes_model_override() -> None:
    session = _FakeSession(triage=_FakeTriage())
    params = _extract_chat_params(session, "system prompt", model_override="test-model")
    assert params["model_override"] == "test-model"


def test_extract_chat_params_omits_model_override_when_none() -> None:
    session = _FakeSession(triage=_FakeTriage())
    params = _extract_chat_params(session, "system prompt", model_override=None)
    assert "model_override" not in params


def test_extract_chat_params_omits_model_override_when_empty() -> None:
    session = _FakeSession(triage=_FakeTriage())
    params = _extract_chat_params(session, "system prompt", model_override="")
    assert "model_override" not in params


def test_extract_chat_params_carries_project_memory_hints() -> None:
    session = _FakeSession(
        triage=_FakeTriage(),
        msg=_FakeMsg(metadata={"memory_context": "Relevant memory:\n- papers directory: /tmp/papers"}),
    )
    session.context = SimpleNamespace(project=SimpleNamespace(project_id="proj-123"))
    params = _extract_chat_params(session, "system prompt", model_override=None)
    assert params["memory_project_id"] == "proj-123"
    assert params["include_memory_kernel_context"] is False


def test_extract_chat_params_adjusts_max_tool_turns_for_careful() -> None:
    session = _FakeSession(
        triage=_FakeTriage(),
        autonomy_resolution=AutonomyResolution(
            preferred_level="careful",
            effective_level="careful",
            source="explicit",
            reason="session preference",
        ),
    )
    params = _extract_chat_params(session, "system prompt", model_override=None)
    assert params["max_tool_turns"] == 12


def test_extract_chat_params_adjusts_max_tool_turns_for_aggressive() -> None:
    session = _FakeSession(
        triage=_FakeTriage(),
        autonomy_resolution=AutonomyResolution(
            preferred_level="aggressive",
            effective_level="aggressive",
            source="explicit",
            reason="session preference",
        ),
    )
    params = _extract_chat_params(session, "system prompt", model_override=None)
    assert params["max_tool_turns"] == 36


def test_build_prompt_includes_aggressive_recent_turn_context() -> None:
    session = _FakeSession(
        triage=_FakeTriage(),
        msg=_FakeMsg(metadata={
            "autonomy_recent_turns": ["user: first", "assistant: second"],
            "autonomy_task_snapshot": "Project summary: finish hardening",
            "autonomy_repo_snapshot": "Modified files: src/app.py",
            "auto_read_content": {
                "/tmp/example.py": "def example():\n    return True\n",
            },
        }),
        autonomy_resolution=AutonomyResolution(
            preferred_level="auto",
            effective_level="aggressive",
            source="inferred",
            reason="clear low-risk directive",
        ),
    )

    prompt = _build_prompt(session)
    assert "Recent task turns:" in prompt
    assert "user: first" in prompt
    assert "Task snapshot:" in prompt
    assert "Project summary: finish hardening" in prompt
    assert "Repo snapshot:" in prompt
    assert "Modified files: src/app.py" in prompt
    assert "Relevant file content:" in prompt


def test_extract_chat_params_includes_stage_overlay_and_audit_metadata() -> None:
    route = _FakeRoute(target="workflow", action_hints=["workflow_edit"])
    triage = _FakeTriage(intent="agent", route=route)
    triage.route_source = "fast_lexical"
    triage.scenario_id = "workflow_followup_apply"
    triage.scenario_confidence = 0.96
    session = _FakeSession(
        triage=triage,
        msg=_FakeMsg(metadata={"mode": "build"}),
    )

    params = _extract_chat_params(session, "system prompt")

    assert "## Concierge stage: workflow_build" in params["extra_system_instructions"]
    assert "canonical node kinds" not in params["extra_system_instructions"]
    assert params["audit_metadata"]["concierge_stage"] == "workflow_build"
    assert params["audit_metadata"]["concierge_prompt_overlay"] == "concierge_stage:workflow_build"
    assert params["audit_metadata"]["route_source"] == "fast_lexical"
    assert params["audit_metadata"]["scenario_id"] == "workflow_followup_apply"


def test_should_decompose_aggressive_single_subtask() -> None:
    executor = MultiStepExecutor(concierge=SimpleNamespace(), dispatcher=None)
    session = SimpleNamespace(
        tier=2,
        triage=SimpleNamespace(subtasks=["only step"]),
        task_context={},
        max_depth=4,
        depth=0,
        id="sess-1",
        autonomy_resolution=AutonomyResolution(
            preferred_level="aggressive",
            effective_level="aggressive",
            source="explicit",
            reason="session preference",
        ),
    )
    manager = SimpleNamespace(can_spawn_child=lambda session_id: True)

    assert executor._should_decompose(session, manager) is True


def test_find_synthesis_gap_reason_reviews_original_goal_terms() -> None:
    manager = SessionManager()
    session = manager.create_root(
        SurfaceMessage(
            surface="cli",
            external_id="goal-gap-user",
            text="Update docs and run regression tests for autonomy",
        ),
        triage=TriageResult(
            tier=2,
            intent="agent",
            goal="Update docs and run regression tests for autonomy",
            deliverable="Docs update plus verified regression tests",
            subtasks=["Update docs", "Write release summary"],
            execution_order="serial",
        ),
        tier=SessionTier.MULTI,
        autonomy_resolution=AutonomyResolution(
            preferred_level="aggressive",
            effective_level="aggressive",
            source="explicit",
            reason="session preference",
        ),
    )
    manager.update_state(session.id, SessionState.RUNNING)

    docs_child = manager.create_child(session.id, "Update docs", SessionTier.SINGLE)
    notes_child = manager.create_child(session.id, "Write release summary", SessionTier.SINGLE)
    for child, content in (
        (docs_child, "Updated docs and command help."),
        (notes_child, "Wrote release notes for the docs refresh."),
    ):
        manager.update_state(child.id, SessionState.RUNNING)
        manager.set_result(child.id, SessionResult(content=content))
        manager.update_state(child.id, SessionState.COMPLETED)

    child_results = {
        docs_child.id: manager.get(docs_child.id).result,
        notes_child.id: manager.get(notes_child.id).result,
    }
    gap_reason = _find_synthesis_gap_reason(session, child_results, manager)

    assert gap_reason is not None
    assert "original goal" in gap_reason
    assert "keyword coverage" in gap_reason


def test_synthesize_careful_surfaces_uncertainties_from_followup_markers() -> None:
    manager = SessionManager()
    session = manager.create_root(
        SurfaceMessage(surface="cli", external_id="careful-user", text="Ship the patch carefully"),
        triage=TriageResult(
            tier=2,
            intent="agent",
            goal="Ship the patch carefully",
            deliverable="Patched code with validation",
            subtasks=["Patch code", "Validate changes"],
            execution_order="serial",
        ),
        tier=SessionTier.MULTI,
        autonomy_resolution=AutonomyResolution(
            preferred_level="careful",
            effective_level="careful",
            source="explicit",
            reason="session preference",
        ),
    )
    manager.update_state(session.id, SessionState.RUNNING)

    patch_child = manager.create_child(session.id, "Patch code", SessionTier.SINGLE)
    validate_child = manager.create_child(session.id, "Validate changes", SessionTier.SINGLE)
    for child, content in (
        (patch_child, "Patched the executor review flow."),
        (
            validate_child,
            "Validation note:\n- [ ] rerun the full integration suite when CI is available",
        ),
    ):
        manager.update_state(child.id, SessionState.RUNNING)
        manager.set_result(child.id, SessionResult(content=content))
        manager.update_state(child.id, SessionState.COMPLETED)

    child_results = {
        patch_child.id: manager.get(patch_child.id).result,
        validate_child.id: manager.get(validate_child.id).result,
    }
    synthesized = MultiStepExecutor._synthesize(session, child_results, manager)

    assert "## Uncertainties" in synthesized
    assert "pending checklist items" in synthesized


def test_synthesize_balanced_preserves_plain_concatenation() -> None:
    manager = SessionManager()
    session = manager.create_root(
        SurfaceMessage(surface="cli", external_id="balanced-user", text="Summarize the work"),
        triage=TriageResult(
            tier=2,
            intent="agent",
            goal="Summarize the work",
            deliverable="Plain combined report",
            subtasks=["Part one", "Part two"],
            execution_order="serial",
        ),
        tier=SessionTier.MULTI,
        autonomy_resolution=AutonomyResolution(
            preferred_level="balanced",
            effective_level="balanced",
            source="explicit",
            reason="session preference",
        ),
    )
    manager.update_state(session.id, SessionState.RUNNING)

    first_child = manager.create_child(session.id, "Part one", SessionTier.SINGLE)
    second_child = manager.create_child(session.id, "Part two", SessionTier.SINGLE)
    for child, content in (
        (first_child, "Completed the first part."),
        (second_child, "Next steps:\n- [ ] optional follow-up"),
    ):
        manager.update_state(child.id, SessionState.RUNNING)
        manager.set_result(child.id, SessionResult(content=content))
        manager.update_state(child.id, SessionState.COMPLETED)

    child_results = {
        first_child.id: manager.get(first_child.id).result,
        second_child.id: manager.get(second_child.id).result,
    }
    synthesized = MultiStepExecutor._synthesize(session, child_results, manager)

    assert "## Part one" in synthesized
    assert "## Part two" in synthesized
    assert "## Uncertainties" not in synthesized


@pytest.mark.asyncio
async def test_balanced_synthesis_uses_llm_for_three_or_more_children(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    provider = _FakeProvider("Unified summary across all child tasks.")
    concierge.chat_manager._providers = _FakeProviderRegistry(provider)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="agent",
            goal="Summarize all work",
            deliverable="Unified summary",
            subtasks=["Part one", "Part two", "Part three"],
            execution_order="serial",
        )

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Part one"] = _complete_stream("Completed the first part.")
    concierge.chat_manager._responses["Part two"] = _complete_stream("Completed the second part.")
    concierge.chat_manager._responses["Part three"] = _complete_stream("Completed the third part.")

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="balanced-user", text="Summarize all work")
        )
    ]

    assert provider.calls
    assert provider.calls[0].get("max_tokens") is None
    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert terminal_messages[-1] == "Unified summary across all child tasks."

    root = dispatcher._session_manager.get_root("balanced-user")
    assert root is not None
    assert root.result is not None
    assert root.result.content == "Unified summary across all child tasks."


@pytest.mark.asyncio
async def test_balanced_synthesis_falls_back_to_concatenation_when_llm_fails(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    concierge.chat_manager._providers = _FakeProviderRegistry(
        _FakeProvider("", error=RuntimeError("provider down"))
    )

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="agent",
            goal="Summarize all work",
            deliverable="Unified summary",
            subtasks=["Part one", "Part two", "Part three"],
            execution_order="serial",
        )

    dispatcher = _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Part one"] = _complete_stream("Completed the first part.")
    concierge.chat_manager._responses["Part two"] = _complete_stream("Completed the second part.")
    concierge.chat_manager._responses["Part three"] = _complete_stream("Completed the third part.")

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="balanced-fallback-user", text="Summarize all work")
        )
    ]

    terminal_messages = [
        event.content
        for event in events
        if getattr(event, "type", "") == "chat_complete"
        and getattr(event, "detected_mode", None) != "progress_ack"
    ]
    assert "## Part one" in terminal_messages[-1]
    assert "## Part two" in terminal_messages[-1]
    assert "## Part three" in terminal_messages[-1]

    root = dispatcher._session_manager.get_root("balanced-fallback-user")
    assert root is not None
    assert root.result is not None
    assert "## Part one" in root.result.content


@pytest.mark.asyncio
async def test_aggressive_synthesis_spawns_single_remediation_child_for_goal_gap(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    executor = MultiStepExecutor(concierge, dispatcher=None)
    executor._child_progress_event = lambda session, child: None
    concierge.chat_manager._responses["__send_message__"] = (
        '{"decision":"remediate","reason":"Regression tests are still missing from the combined result."}'
    )

    manager = SessionManager()
    session = manager.create_root(
        SurfaceMessage(
            surface="cli",
            external_id="aggressive-user",
            text="Update docs and run regression tests for autonomy",
        ),
        triage=TriageResult(
            tier=2,
            intent="agent",
            goal="Update docs and run regression tests for autonomy",
            deliverable="Docs update plus verified regression tests",
            subtasks=["Update docs", "Write release summary"],
            execution_order="serial",
        ),
        tier=SessionTier.MULTI,
        autonomy_resolution=AutonomyResolution(
            preferred_level="aggressive",
            effective_level="aggressive",
            source="explicit",
            reason="session preference",
        ),
    )
    session.context = SimpleNamespace(project=SimpleNamespace(project_id="proj-review"))
    session.child_execution = "serial"
    manager.update_state(session.id, SessionState.RUNNING)

    async def fake_run_child(child: Any, current_manager: Any) -> AsyncIterator[Any]:
        current_manager.update_state(child.id, SessionState.RUNNING)
        if child.task == "Update docs":
            current_manager.set_result(child.id, SessionResult(content="Updated the docs and command help."))
        elif child.task == "Write release summary":
            current_manager.set_result(child.id, SessionResult(content="Wrote release notes for the docs refresh."))
        else:
            assert child.task.startswith("Review the child results against the original goal")
            assert "original goal" in child.task_context.get("remediation_reason", "")
            current_manager.set_result(
                child.id,
                SessionResult(content="Ran the missing regression tests and confirmed the autonomy docs update."),
            )
        current_manager.update_state(child.id, SessionState.COMPLETED)
        yield ChatCompleteEvent(
            message_id=f"{child.id}-done",
            content=current_manager.get(child.id).result.content,
            graph_revision="",
        )

    executor._run_child = fake_run_child

    events = [
        event
        async for event in executor._decompose_and_execute(session, manager, start=0.0)
    ]

    remediation_children = [
        child
        for child in manager.children_of(session.id)
        if child.task.startswith("Review the child results against the original goal")
    ]
    review_calls = [
        call for call in concierge.chat_manager.calls
        if call.get("call_type") == "text_only"
    ]
    assert len(remediation_children) == 1
    assert len(review_calls) == 1
    assert review_calls[0]["memory_project_id"] == "proj-review"
    assert review_calls[0]["record_summary"] is False
    assert isinstance(events[-1], ChatCompleteEvent)
    assert "Ran the missing regression tests" in events[-1].content
    assert manager.get(session.id).result is not None
    assert "## Review the child results against the original goal" in manager.get(session.id).result.content


@pytest.mark.asyncio
async def test_aggressive_synthesis_skips_remediation_when_llm_review_accepts(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)
    executor = MultiStepExecutor(concierge, dispatcher=None)
    executor._child_progress_event = lambda session, child: None
    concierge.chat_manager._responses["__send_message__"] = (
        '{"decision":"accept","reason":"The child outputs satisfy the requested work despite the wording mismatch."}'
    )

    manager = SessionManager()
    session = manager.create_root(
        SurfaceMessage(
            surface="cli",
            external_id="aggressive-accept-user",
            text="Update docs and run regression tests for autonomy",
        ),
        triage=TriageResult(
            tier=2,
            intent="agent",
            goal="Update docs and run regression tests for autonomy",
            deliverable="Docs update plus verified regression tests",
            subtasks=["Update docs", "Write release summary"],
            execution_order="serial",
        ),
        tier=SessionTier.MULTI,
        autonomy_resolution=AutonomyResolution(
            preferred_level="aggressive",
            effective_level="aggressive",
            source="explicit",
            reason="session preference",
        ),
    )
    session.context = SimpleNamespace(project=SimpleNamespace(project_id="proj-accept"))
    session.child_execution = "serial"
    manager.update_state(session.id, SessionState.RUNNING)

    async def fake_run_child(child: Any, current_manager: Any) -> AsyncIterator[Any]:
        current_manager.update_state(child.id, SessionState.RUNNING)
        if child.task.startswith("Review the child results against the original goal"):
            pytest.fail("LLM accepted the result, but remediation child still spawned")
        if child.task == "Update docs":
            current_manager.set_result(child.id, SessionResult(content="Updated the docs and command help."))
        else:
            current_manager.set_result(child.id, SessionResult(content="Wrote release notes for the docs refresh."))
        current_manager.update_state(child.id, SessionState.COMPLETED)
        yield ChatCompleteEvent(
            message_id=f"{child.id}-done",
            content=current_manager.get(child.id).result.content,
            graph_revision="",
        )

    executor._run_child = fake_run_child

    events = [
        event
        async for event in executor._decompose_and_execute(session, manager, start=0.0)
    ]

    remediation_children = [
        child
        for child in manager.children_of(session.id)
        if child.task.startswith("Review the child results against the original goal")
    ]
    review_calls = [
        call for call in concierge.chat_manager.calls
        if call.get("call_type") == "text_only"
    ]
    assert len(remediation_children) == 0
    assert len(review_calls) == 1
    assert review_calls[0]["memory_project_id"] == "proj-accept"
    assert isinstance(events[-1], ChatCompleteEvent)
    assert "## Update docs" in events[-1].content
    assert "## Write release summary" in events[-1].content


@pytest.mark.asyncio
async def test_context_gatherer_refreshes_memory_with_project_id() -> None:
    gatherer = ContextGatherer()
    msg = SurfaceMessage(surface="cli", external_id="cli-user", text="where are my papers?")
    triage = SimpleNamespace(context_needs=["memory"])

    class _FakeConcierge:
        def _resolve_context(self, incoming: SurfaceMessage) -> Any:
            return SimpleNamespace(project=SimpleNamespace(project_id="proj-1"))

        def _retrieve_memory_context(
            self,
            message: str,
            *,
            project_id: str | None = None,
            **_: Any,
        ) -> str:
            return "scoped memory" if project_id == "proj-1" else "unscoped memory"

    context = await gatherer.gather(msg, triage, _FakeConcierge())
    assert context.project.project_id == "proj-1"
    assert msg.metadata["memory_context"] == "scoped memory"


@pytest.mark.asyncio
async def test_context_gatherer_skips_project_refresh_after_memory_timeout(monkeypatch) -> None:
    gatherer = ContextGatherer()
    msg = SurfaceMessage(surface="cli", external_id="cli-user", text="where are my papers?")
    triage = SimpleNamespace(context_needs=["memory"])
    calls: list[str | None] = []

    class _FakeConcierge:
        def _resolve_context(self, incoming: SurfaceMessage) -> Any:
            return SimpleNamespace(project=SimpleNamespace(project_id="proj-1"))

        def _retrieve_memory_context(
            self,
            message: str,
            *,
            project_id: str | None = None,
            **_: Any,
        ) -> str:
            calls.append(project_id)
            time.sleep(2.0)
            return "slow scoped memory" if project_id == "proj-1" else "slow unscoped memory"

    monkeypatch.setenv("DAN_CONCIERGE_PREP_TIMEOUT", "0.05")
    started = time.monotonic()
    context = await gatherer.gather(msg, triage, _FakeConcierge())
    elapsed = time.monotonic() - started

    assert context.project.project_id == "proj-1"
    assert elapsed < 1.0
    assert calls == [None]
    assert "memory_context" not in msg.metadata


@pytest.mark.asyncio
async def test_context_gatherer_aggressive_adds_recent_turns() -> None:
    gatherer = ContextGatherer()
    msg = SurfaceMessage(surface="cli", external_id="cli-user", text="keep going")
    triage = SimpleNamespace(context_needs=[])

    class _FakeConcierge:
        def _resolve_context(self, incoming: SurfaceMessage) -> Any:
            return SimpleNamespace(
                project=SimpleNamespace(project_id="proj-1"),
                task=SimpleNamespace(
                    turns=[
                        SimpleNamespace(role="user", content="first step"),
                        SimpleNamespace(role="assistant", content="done first step"),
                    ],
                ),
            )

    await gatherer.gather(
        msg,
        triage,
        _FakeConcierge(),
        autonomy_resolution=AutonomyResolution(
            preferred_level="auto",
            effective_level="aggressive",
            source="inferred",
            reason="clear low-risk directive",
        ),
    )
    assert msg.metadata["autonomy_recent_turns"]


@pytest.mark.asyncio
async def test_context_gatherer_aggressive_adds_related_files_and_task_snapshot(
    tmp_path: Path,
) -> None:
    gatherer = ContextGatherer()
    subprocess.run(
        ["git", "init"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    related_file = tmp_path / "src" / "feature.py"
    related_file.parent.mkdir(parents=True, exist_ok=True)
    related_file.write_text("def feature_flag() -> bool:\n    return True\n", encoding="utf-8")
    artifact_file = tmp_path / "notes" / "next-step.md"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text("- [ ] add regression coverage\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "."],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    related_file.write_text(
        "def feature_flag() -> bool:\n    return True\n\n\ndef needs_followup() -> bool:\n    return False\n",
        encoding="utf-8",
    )
    msg = SurfaceMessage(
        surface="cli",
        external_id="cli-user",
        text="keep going",
        metadata={
            "surface_context": {
                "workspace_root": str(tmp_path),
                "mentioned_files": [{"path": str(related_file)}],
                "import_neighbors": ["notes/next-step.md"],
            },
        },
    )
    triage = SimpleNamespace(context_needs=[])

    class _FakeConcierge:
        def _resolve_context(self, incoming: SurfaceMessage) -> Any:
            return SimpleNamespace(
                project=SimpleNamespace(project_id="proj-1", summary="Finish the autonomy hardening pass."),
                task=SimpleNamespace(
                    turns=[],
                    pending_steps=["add regression coverage", "write summary"],
                    current_blocker="Need one more prompt regression.",
                    artifacts={"notes": str(artifact_file)},
                ),
            )

    await gatherer.gather(
        msg,
        triage,
        _FakeConcierge(),
        autonomy_resolution=AutonomyResolution(
            preferred_level="auto",
            effective_level="aggressive",
            source="inferred",
            reason="clear low-risk directive",
        ),
    )

    assert str(related_file.resolve()) in msg.metadata["auto_read_content"]
    assert "Project summary: Finish the autonomy hardening pass." in msg.metadata["autonomy_task_snapshot"]
    assert "Pending steps: add regression coverage; write summary" in msg.metadata["autonomy_task_snapshot"]
    assert "Current blocker: Need one more prompt regression." in msg.metadata["autonomy_task_snapshot"]
    assert str(artifact_file) in msg.metadata["autonomy_task_snapshot"]
    assert "Modified files: src/feature.py" in msg.metadata["autonomy_repo_snapshot"]


@pytest.mark.asyncio
async def test_context_gatherer_careful_skips_aggressive_extra_context(
    tmp_path: Path,
) -> None:
    gatherer = ContextGatherer()
    related_file = tmp_path / "src" / "feature.py"
    related_file.parent.mkdir(parents=True, exist_ok=True)
    related_file.write_text("def feature_flag() -> bool:\n    return True\n", encoding="utf-8")
    msg = SurfaceMessage(
        surface="cli",
        external_id="cli-user",
        text="keep going",
        metadata={
            "surface_context": {
                "workspace_root": str(tmp_path),
                "mentioned_files": [{"path": str(related_file)}],
            },
        },
    )
    triage = SimpleNamespace(context_needs=[])

    class _FakeConcierge:
        def _resolve_context(self, incoming: SurfaceMessage) -> Any:
            return SimpleNamespace(
                project=SimpleNamespace(project_id="proj-1", summary="Finish the autonomy hardening pass."),
                task=SimpleNamespace(
                    turns=[],
                    pending_steps=["add regression coverage"],
                    current_blocker="Need one more prompt regression.",
                    artifacts={},
                ),
            )

    await gatherer.gather(
        msg,
        triage,
        _FakeConcierge(),
        autonomy_resolution=AutonomyResolution(
            preferred_level="auto",
            effective_level="careful",
            source="inferred",
            reason="risky or irreversible wording",
        ),
    )

    assert "auto_read_content" not in msg.metadata
    assert "autonomy_task_snapshot" not in msg.metadata
    assert "autonomy_repo_snapshot" not in msg.metadata


@pytest.mark.asyncio
async def test_auto_inference_announces_only_on_effective_level_change(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            goal="Handle the request",
            deliverable="Handle the request",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Please implement and test this patch."] = "Applied the patch."
    concierge.chat_manager._responses["Delete the generated file and post the results."] = "Deleted the file."
    concierge.chat_manager._responses["Delete the backup file and post the results."] = "Deleted the backup."

    first_events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="auto-user",
                text="Please implement and test this patch.",
            )
        )
    ]
    second_events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="auto-user",
                text="Delete the generated file and post the results.",
            )
        )
    ]
    third_events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="auto-user",
                text="Delete the backup file and post the results.",
            )
        )
    ]

    first_final = next(event for event in reversed(first_events) if isinstance(event, ChatCompleteEvent))
    second_final = next(event for event in reversed(second_events) if isinstance(event, ChatCompleteEvent))
    third_final = next(event for event in reversed(third_events) if isinstance(event, ChatCompleteEvent))

    assert "Autonomy: switched to" not in first_final.content
    assert "Autonomy: switched to `careful`" in second_final.content
    assert second_final.content.count("Autonomy: switched to") == 1
    assert "Autonomy: switched to" not in third_final.content


@pytest.mark.asyncio
async def test_telemetry_events_include_autonomy_resolution_metadata(
    tmp_path: Path,
) -> None:
    telemetry_store = InMemoryTelemetryStore()
    concierge = _make_concierge(tmp_path, telemetry_store=telemetry_store)

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="agent",
            goal="Handle the request",
            deliverable="Handle the request",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Please implement and test this patch."] = "Applied the patch."

    _ = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="telemetry-user",
                text="Please implement and test this patch.",
            )
        )
    ]

    telemetry_events = await telemetry_store.query()
    turn_events = [event for event in telemetry_events if event.event_type == "chat_turn"]
    session_complete = [event for event in telemetry_events if event.event_type == "session_complete"]
    dispatch_complete = [event for event in telemetry_events if event.event_type == "tiered_dispatch_complete"]

    assert turn_events
    assert session_complete
    assert dispatch_complete
    assert turn_events[-1].metadata["concierge_stage"] == "conversation"
    assert turn_events[-1].metadata["session_tier"] == 1
    assert turn_events[-1].metadata["prompt_overlay"] == "concierge_stage:conversation"
    assert session_complete[-1].metadata["autonomy_resolution"]["effective_level"] == "aggressive"
    assert dispatch_complete[-1].metadata["autonomy_resolution"]["effective_level"] == "aggressive"
    assert session_complete[-1].metadata["concierge_stage"] == "conversation"
    assert session_complete[-1].metadata["session_tier"] == 1
    assert dispatch_complete[-1].metadata["prompt_overlay"] == "concierge_stage:conversation"
    assert dispatch_complete[-1].metadata["session_tier"] == 1


# ---------------------------------------------------------------------------
# SingleShotExecutor model_override wiring test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_shot_passes_model_override_with_tier_resolver(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    tier_map = {"micro": "fast-model", "routine": "mid-model", "reasoning": "big-model", "critical": "big-model"}
    concierge._tier_resolver = ConciergeTierResolver(tier_map, "fallback-model")

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="ask",
            goal="Answer",
            deliverable="Answer",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["What time is it?"] = "It is noon."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="What time is it?")
        )
    ]

    assert concierge.chat_manager.call_log == ["What time is it?"]
    call = concierge.chat_manager.calls[-1]
    assert call["model_override"] == "mid-model"


@pytest.mark.asyncio
async def test_single_shot_plan_mode_uses_reasoning_model(
    tmp_path: Path,
) -> None:
    concierge = _make_concierge(tmp_path)

    tier_map = {"micro": "fast-model", "routine": "mid-model", "reasoning": "big-model", "critical": "big-model"}
    concierge._tier_resolver = ConciergeTierResolver(tier_map, "fallback-model")

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="plan",
            goal="Plan the project",
            deliverable="Plan the project",
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Plan the project"] = "Here is the plan."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text="Plan the project",
                metadata={"mode": "plan"},
            )
        )
    ]

    assert concierge.chat_manager.call_log == ["Plan the project"]
    call = concierge.chat_manager.calls[-1]
    assert call["model_override"] == "big-model"


# ---------------------------------------------------------------------------
# MultiStepExecutor model_override wiring test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_step_direct_passes_model_override(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)

    tier_map = {"micro": "fast-model", "routine": "mid-model", "reasoning": "big-model", "critical": "big-model"}
    concierge._tier_resolver = ConciergeTierResolver(tier_map, "fallback-model")

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=2,
            intent="agent",
            goal="Build the workflow",
            deliverable="Build the workflow",
            subtasks=["Build the workflow"],
            execution_order="serial",
            route=RouteDecision(
                mode=RouteMode.AGENT,
                target="workflow",
                action_hints=["workflow_edit"],
            ),
        )

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Build the workflow"] = "Workflow built."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(
                surface="cli",
                external_id="cli-user",
                text="Build the workflow",
                metadata={"mode": "build"},
            )
        )
    ]

    assert concierge.chat_manager.call_log == ["Build the workflow"]
    call = concierge.chat_manager.calls[-1]
    assert call["model_override"] == "big-model"


@pytest.mark.asyncio
async def test_no_model_override_without_tier_resolver(tmp_path: Path) -> None:
    """When no tier resolver is configured, model_override should not be in params."""
    concierge = _make_concierge(tmp_path)
    concierge._tier_resolver = None

    async def triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(tier=1, intent="ask", goal="Hello", deliverable="Hello")

    _install_dispatcher(concierge, triage_fn=triage_fn)
    concierge.chat_manager._responses["Hello world"] = "Hi."

    events = [
        event
        async for event in concierge.process(
            SurfaceMessage(surface="cli", external_id="cli-user", text="Hello world")
        )
    ]

    assert concierge.chat_manager.call_log == ["Hello world"]
    call = concierge.chat_manager.calls[-1]
    assert "model_override" not in call


@pytest.mark.asyncio
async def test_run_children_parallel_does_not_use_legacy_busy_poll_sleep(
    tmp_path: Path,
) -> None:
    import dan.server.concierge.tier_executors as tier_executors

    concierge = _make_concierge(tmp_path)
    executor = MultiStepExecutor(concierge, dispatcher=None)
    executor._child_progress_event = lambda session, child: None

    async def fake_run_child(child: Any, manager: Any) -> AsyncIterator[Any]:
        yield ChatCompleteEvent(
            message_id=f"{child.id}-done",
            content=f"{child.task} complete",
            graph_revision="",
        )

    executor._run_child = fake_run_child

    original_sleep = tier_executors.asyncio.sleep

    async def guarded_sleep(delay: float, *args: Any, **kwargs: Any) -> Any:
        assert delay != 0.01
        return await original_sleep(delay, *args, **kwargs)

    class _Manager:
        def update_state(self, *_: Any) -> None:
            return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tier_executors.asyncio, "sleep", guarded_sleep)
    try:
        children = [
            SimpleNamespace(id="child-1", task="task 1"),
            SimpleNamespace(id="child-2", task="task 2"),
        ]
        session = _FakeSession(triage=_FakeTriage())
        events = [
            event
            async for event in executor._run_children_parallel(children, session, _Manager())
        ]
    finally:
        monkeypatch.undo()

    assert len(events) == 2
    assert all(isinstance(event, ChatCompleteEvent) for event in events)


# ---------------------------------------------------------------------------
# Triage model uses tier resolver
# ---------------------------------------------------------------------------


def test_resolve_triage_model_uses_tier_resolver(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)
    tier_map = {"micro": "cheap-classifier", "routine": "mid", "reasoning": "big", "critical": "big"}
    concierge._tier_resolver = ConciergeTierResolver(tier_map, "fallback")
    concierge._triage_model = ""

    assert concierge._resolve_triage_model() == "cheap-classifier"


def test_resolve_triage_model_explicit_override_wins(tmp_path: Path) -> None:
    concierge = _make_concierge(tmp_path)
    tier_map = {"micro": "cheap-classifier", "routine": "mid", "reasoning": "big", "critical": "big"}
    concierge._tier_resolver = ConciergeTierResolver(tier_map, "fallback")
    concierge._triage_model = "explicit-model"

    assert concierge._resolve_triage_model() == "explicit-model"


def test_missing_action_hints_workflow_edit_unsatisfied() -> None:
    assert _missing_action_hints(["workflow_edit"], set()) == ["workflow_edit"]


def test_missing_action_hints_workflow_edit_satisfied_by_mutation_tool() -> None:
    assert _missing_action_hints(["workflow_edit"], {"plan_graph_mutations"}) == []


def test_retry_prompt_for_workflow_edit_mentions_mutation_tool() -> None:
    prompt = _tool_retry_prompt_for_missing_actions(["workflow_edit"])
    assert "plan_graph_mutations" in prompt
    assert "workflow" in prompt.lower()


def test_extract_chat_params_mutation_tool_true_for_workflow_route_without_edit_hint() -> None:
    """mode=agent + route_target=workflow (no workflow_edit hint) → allow_mutation_tool=True."""
    route = _FakeRoute(target="workflow", action_hints=["search_web"])
    session = _FakeSession(
        triage=_FakeTriage(intent="agent", route=route),
        msg=_FakeMsg(metadata={"mode": "agent"}),
    )
    params = _extract_chat_params(session, "system prompt")
    assert params["allow_mutation_tool"] is True


def test_extract_chat_params_mutation_tool_true_for_workflow_followup_history() -> None:
    session = _FakeSession(
        triage=_FakeTriage(intent="agent", route=_FakeRoute(target="general", action_hints=["run_control"])),
        msg=_FakeMsg(metadata={"mode": "agent"}),
    )
    session.msg.text = "so is it gonna work? I have the watchlist.csv set with 1 ticker for test"
    session.context = SimpleNamespace(
        project=SimpleNamespace(linked_workflow_ids=["_scratch"]),
        task=SimpleNamespace(
            turns=[
                SimpleNamespace(role="user", content="Build the workflow around watchlist.csv"),
                SimpleNamespace(role="assistant", content="Prepared a workflow change preview for the watchlist workflow."),
            ],
        ),
    )

    params = _extract_chat_params(session, "system prompt")
    assert params["allow_mutation_tool"] is True
    assert "workflow_run" in params["required_action_hints"]
    assert "workflow_edit" not in params["required_action_hints"]
    assert "run_control" not in params["required_action_hints"]


def test_extract_chat_params_mutation_tool_false_for_workflow_query_only() -> None:
    route = _FakeRoute(target="workflow", action_hints=["workflow_query"])
    session = _FakeSession(
        triage=_FakeTriage(intent="agent", route=route),
        msg=_FakeMsg(metadata={"mode": "agent"}),
    )
    params = _extract_chat_params(session, "system prompt")
    assert params["allow_mutation_tool"] is False


def test_extract_chat_params_mutation_tool_true_for_build_mode_no_route() -> None:
    """mode=build with no route or hints → allow_mutation_tool=True."""
    session = _FakeSession(
        triage=_FakeTriage(intent="ask"),
        msg=_FakeMsg(metadata={"mode": "build"}),
    )
    params = _extract_chat_params(session, "system prompt")
    assert params["allow_mutation_tool"] is True


def test_extract_chat_params_requested_build_mode_override() -> None:
    """requested_mode=build should restore build semantics even when mode was normalized."""
    session = _FakeSession(
        triage=_FakeTriage(intent="ask"),
        msg=_FakeMsg(metadata={"mode": "agent", "requested_mode": "build"}),
    )
    params = _extract_chat_params(session, "system prompt")
    assert params["mode"] == "build"
    assert params["allow_mutation_tool"] is True


def test_extract_chat_params_promotes_ask_mode_for_workflow_action_followup() -> None:
    session = _FakeSession(
        triage=_FakeTriage(intent="ask", route=_FakeRoute(target="general")),
        msg=_FakeMsg(metadata={"mode": "ask"}),
    )
    session.msg.text = "Sounds good, can you run this workflow to test ?"
    session.context = SimpleNamespace(
        project=SimpleNamespace(linked_workflow_ids=["_scratch"]),
        task=SimpleNamespace(
            turns=[
                SimpleNamespace(role="user", content="Build the workflow around watchlist.csv"),
                SimpleNamespace(role="assistant", content="Prepared a workflow change preview for the watchlist workflow."),
            ],
        ),
    )

    params = _extract_chat_params(session, "system prompt")
    assert params["mode"] == "agent"
    assert params["allow_mutation_tool"] is True
    assert "workflow_run" in params["required_action_hints"]
    assert "workflow_edit" not in params["required_action_hints"]
    assert "run_control" not in params["required_action_hints"]


def test_extract_chat_params_promotes_ask_mode_for_workflow_apply_followup() -> None:
    session = _FakeSession(
        triage=_FakeTriage(intent="ask", route=_FakeRoute(target="general")),
        msg=_FakeMsg(metadata={"mode": "ask"}),
    )
    session.msg.text = "good please apply"
    session.context = SimpleNamespace(
        project=SimpleNamespace(linked_workflow_ids=["_scratch"]),
        task=SimpleNamespace(
            turns=[
                SimpleNamespace(role="user", content="Build the workflow around watchlist.csv"),
                SimpleNamespace(role="assistant", content="Prepared a workflow change preview for the watchlist workflow."),
            ],
        ),
    )

    params = _extract_chat_params(session, "system prompt")
    assert params["mode"] == "agent"
    assert params["allow_mutation_tool"] is True


def test_extract_chat_params_keeps_furnace_run_control_when_explicit() -> None:
    session = _FakeSession(
        triage=_FakeTriage(intent="agent", route=_FakeRoute(target="general", action_hints=["run_control"])),
        msg=_FakeMsg(metadata={"mode": "agent"}),
    )
    session.msg.text = "please start the furnace session again"
    session.context = SimpleNamespace(
        project=SimpleNamespace(linked_workflow_ids=["_scratch"]),
        task=SimpleNamespace(
            turns=[
                SimpleNamespace(role="user", content="Build the workflow around watchlist.csv"),
                SimpleNamespace(role="assistant", content="Prepared a workflow change preview for the watchlist workflow."),
            ],
        ),
    )

    params = _extract_chat_params(session, "system prompt")
    assert "run_control" in params["required_action_hints"]
    assert "workflow_run" not in params["required_action_hints"]


def test_extract_chat_params_mutation_tool_false_for_agent_mode_file_route() -> None:
    """mode=agent + route_target=file → allow_mutation_tool=False (no broadened gate)."""
    route = _FakeRoute(target="file", action_hints=["read_file"])
    session = _FakeSession(
        triage=_FakeTriage(intent="agent", route=route),
        msg=_FakeMsg(metadata={"mode": "agent"}),
    )
    params = _extract_chat_params(session, "system prompt")
    assert params["allow_mutation_tool"] is False


def test_extract_chat_params_metadata_override_takes_precedence() -> None:
    """Explicit metadata['allow_mutation_tool']=False overrides the broadened gate."""
    route = _FakeRoute(target="workflow", action_hints=["workflow_edit"])
    session = _FakeSession(
        triage=_FakeTriage(intent="agent", route=route),
        msg=_FakeMsg(metadata={"mode": "build", "allow_mutation_tool": False}),
    )
    params = _extract_chat_params(session, "system prompt")
    assert params["allow_mutation_tool"] is False


def test_extract_chat_params_prefers_session_id_over_metadata_thread_id() -> None:
    msg = _FakeMsg(metadata={"thread_id": "conversation-1"})
    msg.session_id = "lane-1"
    session = _FakeSession(triage=_FakeTriage(), msg=msg)

    params = _extract_chat_params(session, "system prompt")

    assert params["thread_id"] == "lane-1"


def test_format_surface_context_truncates_large_payloads() -> None:
    block = ChatManager._format_surface_context({
        "mode": "development",
        "workspace_id": "ws-1",
        "active_file": {
            "path": "src/big.ts",
            "language": "typescript",
            "content": "A" * 20_000,
        },
        "selection_text": "B" * 5_000,
        "mentioned_files": [
            {"path": "src/huge.ts", "content": "C" * 8_000, "lines": 500},
        ],
    })

    assert block.startswith("## Surface context\n")
    assert len(block) <= 10_000 + len("## Surface context\n")
    assert "truncated" in block.lower()
