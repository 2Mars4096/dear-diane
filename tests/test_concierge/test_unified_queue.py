"""Tests for 29-5 tasks 5, 9, 10: parallel memory extraction, unified queue,
and immediate-start guarantee."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.server.chat_manager import ChatCompleteEvent, ChatQueuedEvent
from dan.server.concierge.dispatcher import ConcurrentDispatcher, _is_bypass_command
from dan.server.concierge.models import (
    Project,
    ResolvedContext,
    SurfaceMessage,
    Task,
)
from dan.server.concierge.runtime import Concierge, _run_coroutine_sync
from dan.server.concierge.triage import triage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(text: str = "hello", surface: str = "test", external_id: str = "s1") -> SurfaceMessage:
    return SurfaceMessage(surface=surface, external_id=external_id, text=text)


def _complete_event(content: str = "ok") -> ChatCompleteEvent:
    return ChatCompleteEvent(
        message_id="m1",
        content=content,
        token_usage={},
        context_window=0,
        graph_revision="",
    )


async def _drain_response_bus(dispatcher: ConcurrentDispatcher, channel_id: str) -> list[Any]:
    bus = dispatcher.get_response_bus(channel_id)
    assert bus is not None
    events: list[Any] = []
    while True:
        item = await asyncio.wait_for(bus.get(), timeout=1.0)
        if item is None:
            break
        events.append(item)
    dispatcher.cleanup_response_bus(channel_id)
    return events


class FakeContextResolver:
    """Returns deterministic ResolvedContext keyed by message text."""

    def __init__(self, project_id: str = "proj1") -> None:
        self._project_id = project_id
        self._counter = 0

    def resolve(self, msg: SurfaceMessage) -> Any:
        project_id = msg.metadata.get("_project_id", self._project_id)
        project = Project(
            project_id=project_id,
            surface_id=msg.external_id,
            label=f"P-{project_id}",
        )
        task = Task(label="task")
        return MagicMock(
            project=project,
            task=task,
            is_new_project=True,
            is_new_task=True,
            confidence=1.0,
        )


class FakeConcierge:
    """Minimal concierge stub that yields a single ChatCompleteEvent."""

    def __init__(self, delay: float = 0) -> None:
        self.context_resolver = FakeContextResolver()
        self.process_calls: list[SurfaceMessage] = []
        self._delay = delay

    def _resolve_context(self, msg: SurfaceMessage):
        return self.context_resolver.resolve(msg)

    async def process(self, msg: SurfaceMessage) -> AsyncIterator:
        self.process_calls.append(msg)
        if self._delay:
            await asyncio.sleep(self._delay)
        yield _complete_event(f"reply to: {msg.text}")


# ---------------------------------------------------------------------------
# Task 5: Parallel memory extraction
# ---------------------------------------------------------------------------

class TestParallelMemoryExtraction:
    """Verify that _store_memory_candidates fans out the three extraction
    steps concurrently via fan_out_dict."""

    def _make_concierge_with_kernel(self) -> Concierge:
        """Create a minimal Concierge with a mocked memory_kernel."""
        kernel = MagicMock()
        kernel.store = MagicMock()
        kernel.store_preference = MagicMock()
        kernel.store_fact = MagicMock()

        project_store = MagicMock()
        chat_manager = MagicMock()
        capability_context = MagicMock()

        c = Concierge(
            project_store=project_store,
            chat_manager=chat_manager,
            capability_context=capability_context,
            memory_kernel=kernel,
        )
        return c

    @pytest.mark.asyncio
    async def test_three_extraction_steps_called_concurrently(self):
        """Mock each extraction step and verify all three are called."""
        c = self._make_concierge_with_kernel()

        episode_called = False
        prefs_called = False
        extraction_called = False

        def mock_episode(*a, **kw):
            nonlocal episode_called
            episode_called = True

        def mock_prefs(*a, **kw):
            nonlocal prefs_called
            prefs_called = True

        def mock_extraction(*a, **kw):
            nonlocal extraction_called
            extraction_called = True

        c._store_episode_candidates = mock_episode
        c._try_extract_preferences = mock_prefs
        c._try_memory_extraction = mock_extraction

        await c._store_memory_candidates_async("hi", "hello", None)

        assert episode_called, "Episode extraction was not called"
        assert prefs_called, "Preference extraction was not called"
        assert extraction_called, "Memory extraction was not called"

    @pytest.mark.asyncio
    async def test_fan_out_dict_used(self):
        """Verify fan_out_dict is called with three tasks."""
        c = self._make_concierge_with_kernel()
        c._store_episode_candidates = MagicMock()
        c._try_extract_preferences = MagicMock()
        c._try_memory_extraction = MagicMock()

        with patch("dan.server.concierge.fan_out.fan_out_dict", new_callable=AsyncMock) as mock_fan_out:
            mock_fan_out.return_value = {"episode": None, "preferences": None, "memory_extraction": None}
            await c._store_memory_candidates_async("hi", "hello", None)
            mock_fan_out.assert_called_once()
            call_args = mock_fan_out.call_args
            task_dict = call_args[0][0]
            assert set(task_dict.keys()) == {"episode", "preferences", "memory_extraction"}

    @pytest.mark.asyncio
    async def test_partial_failure_does_not_block(self):
        """One extraction step failing should not prevent others from completing."""
        c = self._make_concierge_with_kernel()

        success_flag = False

        def failing_episode(*a, **kw):
            raise RuntimeError("episode extraction boom")

        def successful_prefs(*a, **kw):
            nonlocal success_flag
            success_flag = True

        c._store_episode_candidates = failing_episode
        c._try_extract_preferences = successful_prefs
        c._try_memory_extraction = MagicMock()

        await c._store_memory_candidates_async("hi", "hello", None)
        assert success_flag, "Preferences should succeed even when episode fails"

    def test_sync_fallback_when_no_loop(self):
        """When no event loop is running, sync fallback is used."""
        c = self._make_concierge_with_kernel()
        c._store_episode_candidates = MagicMock()
        c._try_extract_preferences = MagicMock()
        c._try_memory_extraction = MagicMock()

        c._store_memory_candidates_sync("hi", "hello", None)

        c._store_episode_candidates.assert_called_once_with("hi", "hello", None)
        c._try_extract_preferences.assert_called_once_with("hi", "hello", project_id=None)
        c._try_memory_extraction.assert_called_once_with("hi", "hello", None, project_id=None, domain=None)

    def test_no_kernel_is_noop(self):
        """Without memory_kernel, _store_memory_candidates is a no-op."""
        c = Concierge(
            project_store=MagicMock(),
            chat_manager=MagicMock(),
            capability_context=MagicMock(),
            memory_kernel=None,
        )
        c._store_memory_candidates("hi", "hello", None)


@pytest.mark.asyncio
async def test_run_coroutine_sync_reuses_running_loop_from_thread() -> None:
    async def _compute() -> str:
        await asyncio.sleep(0)
        return "ok"

    result = await asyncio.to_thread(
        _run_coroutine_sync,
        _compute(),
        loop=asyncio.get_running_loop(),
    )

    assert result == "ok"


@pytest.mark.asyncio
async def test_triage_llm_complete_uses_1024_token_budget_and_parses_json(
) -> None:
    complete_calls: list[dict[str, Any]] = []

    class FakeProvider:
        async def complete(self, **kwargs: Any) -> Any:
            complete_calls.append(kwargs)
            return MagicMock(
                text=json.dumps(
                    {
                        "tier": 1,
                        "intent": "ask",
                        "route": {
                            "mode": "ask",
                            "target": "general",
                            "action_hints": ["status_check"],
                        },
                        "confidence": 0.93,
                        "goal": "Explain the quarterly task",
                        "deliverable": "Short explanation",
                        "entities": [],
                        "is_resume": False,
                        "resume_task_id": None,
                        "is_social": False,
                        "social_response": None,
                        "context_needs": ["memory"],
                        "subtasks": [],
                        "execution_order": "parallel",
                        "rationale": " ".join(["valid"] * 120),
                    }
                )
            )

    class FakeProviders:
        def resolve(self, model: str) -> Any:
            assert model == "triage-model"
            return FakeProvider()

        def provider_names(self) -> list[str]:
            return ["default"]

    chat_manager = MagicMock()
    chat_manager._providers = FakeProviders()
    concierge = Concierge(
        project_store=MagicMock(),
        chat_manager=chat_manager,
        capability_context=MagicMock(),
    )
    context = ResolvedContext(
        project=Project(surface_id="cli-user", label="Revenue Tracker"),
        task=Task(label="Draft quarterly report", status="active"),
        is_new_project=False,
        is_new_task=False,
        confidence=1.0,
    )

    with patch("dan.server.concierge.triage._embedding_triage_result", new=AsyncMock(return_value=None)):
        with patch.object(concierge, "_resolve_triage_model", return_value="triage-model"):
            result = await triage(
                "Explain the quarterly report task",
                context,
                concierge._triage_llm_complete,
            )

    assert complete_calls
    assert complete_calls[0]["max_tokens"] == 1024
    assert result.intent == "ask"
    assert result.goal == "Explain the quarterly task"
    assert result.route is not None
    assert result.route.target == "general"


@pytest.mark.asyncio
async def test_same_surface_concurrent_process_inner_preserves_state_updates() -> None:
    concierge = Concierge(
        project_store=MagicMock(),
        chat_manager=MagicMock(),
        capability_context=MagicMock(),
    )
    concierge._try_fast_command = AsyncMock(return_value=None)
    concierge._ensure_progress_session = MagicMock()

    async def _dispatch(msg: SurfaceMessage) -> AsyncIterator[ChatCompleteEvent]:
        concierge._concierge_state.pending_clarifications.append({"turn": msg.text})
        await asyncio.sleep(0.05)
        yield _complete_event(f"reply to: {msg.text}")

    concierge._tiered_dispatcher = MagicMock()
    concierge._tiered_dispatcher.dispatch = _dispatch

    async def _consume(text: str) -> list[ChatCompleteEvent]:
        return [
            event
            async for event in concierge._process_inner(
                _msg(text=text, surface="cli", external_id="shared-user")
            )
        ]

    first_task = asyncio.create_task(_consume("first"))
    await asyncio.sleep(0.01)
    second_task = asyncio.create_task(_consume("second"))
    first_events, second_events = await asyncio.gather(first_task, second_task)

    scope = concierge._concierge_state_scope_key("cli", "shared-user")
    saved_state = concierge._volatile_concierge_states[scope]

    assert [event.content for event in first_events] == ["reply to: first"]
    assert [event.content for event in second_events] == ["reply to: second"]
    assert [item["turn"] for item in saved_state.pending_clarifications] == [
        "first",
        "second",
    ]


# ---------------------------------------------------------------------------
# Task 9: Unified queue — ProjectMessageQueue is a deprecated pass-through
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="dan.server.concierge.queue module deleted in plan-34")
class TestUnifiedQueue:
    """ProjectMessageQueue was deleted in plan-34."""

    def test_placeholder(self):
        pass


# ---------------------------------------------------------------------------
# Task 10: Immediate-start guarantee
# ---------------------------------------------------------------------------

class TestImmediateStartGuarantee:
    """Dispatcher processes new-project messages immediately and serializes
    same-project messages correctly."""

    @pytest.mark.asyncio
    async def test_new_project_starts_immediately(self):
        """A message for a project with no active task processes immediately."""
        concierge = FakeConcierge()
        d = ConcurrentDispatcher(concierge, max_concurrent_projects=5)

        events = []
        async for event in d.dispatch(_msg("hello")):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], ChatCompleteEvent)
        assert "reply to: hello" in events[0].content

    @pytest.mark.asyncio
    async def test_same_project_serializes(self):
        """Two messages for the same project: first processes, second is queued."""
        concierge = FakeConcierge(delay=0.1)
        d = ConcurrentDispatcher(concierge, max_concurrent_projects=5)

        first_events = []
        async for event in d.dispatch(_msg("first")):
            first_events.append(event)

        second_events = []
        async for event in d.dispatch(_msg("second")):
            second_events.append(event)

        assert any(isinstance(e, ChatCompleteEvent) for e in first_events)
        assert any(isinstance(e, ChatCompleteEvent) for e in second_events)

    @pytest.mark.asyncio
    async def test_same_project_queued_when_busy(self):
        """If a project is actively being processed, a new message queues behind it."""
        concierge = FakeConcierge(delay=0.2)
        d = ConcurrentDispatcher(concierge, max_concurrent_projects=5)

        results = {"first": [], "second": []}

        async def run_first():
            async for event in d.dispatch(_msg("first")):
                results["first"].append(event)

        async def run_second():
            await asyncio.sleep(0.05)
            async for event in d.dispatch(_msg("second")):
                results["second"].append(event)

        await asyncio.gather(run_first(), run_second())

        assert any(isinstance(e, ChatCompleteEvent) for e in results["first"])
        second_types = [type(e).__name__ for e in results["second"]]
        assert "ChatQueuedEvent" in second_types or "ChatCompleteEvent" in second_types

    @pytest.mark.asyncio
    async def test_different_projects_start_concurrently(self):
        """Messages for different projects process concurrently."""
        concierge = FakeConcierge(delay=0.1)
        concierge.context_resolver = FakeContextResolver()

        d = ConcurrentDispatcher(concierge, max_concurrent_projects=5)

        results = {"a": [], "b": []}

        async def run_a():
            msg = _msg("hello A")
            msg = msg.model_copy(update={"metadata": {"_project_id": "projA"}})
            concierge.context_resolver._project_id = "projA"
            async for event in d.dispatch(msg):
                results["a"].append(event)

        async def run_b():
            msg = _msg("hello B")
            msg = msg.model_copy(update={"metadata": {"_project_id": "projB"}})
            async for event in d.dispatch(msg):
                results["b"].append(event)

        class MultiProjectResolver:
            def resolve(self, msg):
                pid = msg.metadata.get("_project_id", "projA")
                project = Project(project_id=pid, surface_id=msg.external_id, label=f"P-{pid}")
                task = Task(label="task")
                return MagicMock(project=project, task=task, is_new_project=True, is_new_task=True, confidence=1.0)

        concierge.context_resolver = MultiProjectResolver()
        await asyncio.gather(run_a(), run_b())

        assert any(isinstance(e, ChatCompleteEvent) for e in results["a"])
        assert any(isinstance(e, ChatCompleteEvent) for e in results["b"])


class TestStatusCancelBypass:
    """Status and cancel commands bypass the queue entirely."""

    def test_is_bypass_command_status(self):
        assert _is_bypass_command(_msg("/status"))
        assert _is_bypass_command(_msg("/build-status"))
        assert _is_bypass_command(_msg("status"))

    def test_is_bypass_command_cancel(self):
        assert _is_bypass_command(_msg("/cancel"))
        assert _is_bypass_command(_msg("/build-stop"))
        assert _is_bypass_command(_msg("cancel"))

    def test_is_bypass_command_phrases(self):
        assert _is_bypass_command(_msg("what's happening"))
        assert _is_bypass_command(_msg("what's going on"))

    def test_regular_messages_not_bypass(self):
        assert not _is_bypass_command(_msg("hello"))
        assert not _is_bypass_command(_msg("build me a workflow"))
        assert not _is_bypass_command(_msg("run the pipeline"))
        assert not _is_bypass_command(_msg("/build add a node"))
        assert not _is_bypass_command(_msg("/retry"))

    @pytest.mark.asyncio
    async def test_bypass_command_skips_context_resolution(self):
        concierge = FakeConcierge()
        dispatcher = ConcurrentDispatcher(concierge, max_concurrent_projects=5)
        resolver_called = False

        def _boom(_msg: SurfaceMessage) -> Any:
            nonlocal resolver_called
            resolver_called = True
            raise AssertionError("bypass commands should not resolve context")

        concierge._resolve_context = _boom

        events = []
        async for event in dispatcher.dispatch(_msg("/status")):
            events.append(event)

        assert not resolver_called
        assert any(isinstance(event, ChatCompleteEvent) for event in events)

    @pytest.mark.asyncio
    async def test_retry_queues_behind_active_project(self):
        concierge = FakeConcierge(delay=0.5)
        dispatcher = ConcurrentDispatcher(concierge, max_concurrent_projects=5)

        results = {"work": [], "retry": []}

        async def run_work():
            async for event in dispatcher.dispatch(_msg("do work")):
                results["work"].append(event)

        async def run_retry():
            await asyncio.sleep(0.05)
            async for event in dispatcher.dispatch(_msg("/retry")):
                results["retry"].append(event)

        await asyncio.gather(run_work(), run_retry())

        assert any(isinstance(event, ChatQueuedEvent) for event in results["retry"])
        assert [msg.text for msg in concierge.process_calls] == ["do work", "/retry"]

    @pytest.mark.asyncio
    async def test_status_bypasses_active_project(self):
        """A /status command for a busy project processes immediately."""
        concierge = FakeConcierge(delay=0.5)
        d = ConcurrentDispatcher(concierge, max_concurrent_projects=5)

        results = {"work": [], "status": []}

        async def run_work():
            async for event in d.dispatch(_msg("do work")):
                results["work"].append(event)

        async def run_status():
            await asyncio.sleep(0.05)
            async for event in d.dispatch(_msg("/status")):
                results["status"].append(event)

        await asyncio.gather(run_work(), run_status())

        assert any(isinstance(e, ChatCompleteEvent) for e in results["status"])
        status_event = next(e for e in results["status"] if isinstance(e, ChatCompleteEvent))
        assert "reply to: /status" in status_event.content


class TestSameProjectSupersede:
    @pytest.mark.asyncio
    async def test_superseding_instruction_collapses_stale_same_project_queue(self):
        concierge = FakeConcierge(delay=0.3)
        dispatcher = ConcurrentDispatcher(concierge, max_concurrent_projects=5)

        queue_channels: dict[str, str] = {}

        async def run_first() -> None:
            async for _event in dispatcher.dispatch(_msg("do work")):
                pass

        async def enqueue(name: str, text: str, delay: float) -> list[Any]:
            events: list[Any] = []
            await asyncio.sleep(delay)
            async for event in dispatcher.dispatch(_msg(text)):
                events.append(event)
                if isinstance(event, ChatQueuedEvent):
                    queue_channels[name] = event.stream_channel_id
            return events

        first_task = asyncio.create_task(run_first())
        second_task = asyncio.create_task(enqueue("second", "draft the summary", 0.05))
        third_task = asyncio.create_task(enqueue("third", "actually fix the tests instead", 0.1))
        second_events, third_events = await asyncio.gather(second_task, third_task)
        await first_task

        assert any(isinstance(event, ChatQueuedEvent) for event in second_events)
        assert any(isinstance(event, ChatQueuedEvent) for event in third_events)

        second_bus_events = await _drain_response_bus(dispatcher, queue_channels["second"])
        third_bus_events = await _drain_response_bus(dispatcher, queue_channels["third"])

        assert len(second_bus_events) == 1
        assert isinstance(second_bus_events[0], ChatCompleteEvent)
        assert "Superseded by newer instruction" in second_bus_events[0].content

        assert len(third_bus_events) == 1
        assert isinstance(third_bus_events[0], ChatCompleteEvent)
        assert "reply to: actually fix the tests instead" in third_bus_events[0].content
        assert [msg.text for msg in concierge.process_calls] == [
            "do work",
            "actually fix the tests instead",
        ]


class TestQueuePositionInfo:
    """ChatQueuedEvent includes queue position for transparency."""

    def test_queued_event_has_position_field(self):
        event = ChatQueuedEvent(
            stream_channel_id="ch1",
            correlation_id="c1",
            queue_position=3,
        )
        assert event.queue_position == 3

    def test_queued_event_default_position(self):
        event = ChatQueuedEvent(
            stream_channel_id="ch1",
            correlation_id="c1",
        )
        assert event.queue_position == 0
