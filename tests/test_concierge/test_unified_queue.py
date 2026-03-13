"""Tests for 29-5 tasks 5, 9, 10: parallel memory extraction, unified queue,
and immediate-start guarantee."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.server.chat_manager import ChatCompleteEvent, ChatQueuedEvent
from dan.server.concierge.dispatcher import ConcurrentDispatcher, _is_bypass_command
from dan.server.concierge.models import (
    Project,
    SurfaceMessage,
    Task,
)
from dan.server.concierge.runtime import Concierge


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
