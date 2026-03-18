"""Tests for resource-based concurrency (Task 7) and priority queuing (Task 8)."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest import mock

import pytest

from dan.server.chat_manager import ChatCompleteEvent, ChatQueuedEvent
from dan.server.concierge.models import ResolvedContext
from dan.server.concierge.dispatcher import ConcurrentDispatcher
from dan.server.concierge.models import Project, SurfaceMessage, Task
from dan.server.concierge.resources import (
    MessagePriority,
    PriorityQueue,
    ResourceBudget,
    ResourceTracker,
    classify_priority,
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _make_msg(text: str, external_id: str = "surface-1") -> SurfaceMessage:
    return SurfaceMessage(surface="test", external_id=external_id, text=text)


def _make_context(project_id: str, label: str = "test-project") -> ResolvedContext:
    project = Project(project_id=project_id, surface_id="surface-1", label=label)
    task = Task(task_id="task-1", label="task")
    project.tasks.append(task)
    return ResolvedContext(
        project=project, task=task,
        is_new_project=True, is_new_task=False, confidence=0.9,
    )


class _FakeConcierge:
    """Minimal concierge mock for dispatcher tests."""

    def __init__(self, delay: float = 0.0) -> None:
        self._delay = delay
        self.process_calls: list[str] = []
        self.context_resolver = mock.MagicMock()
        self._contexts: dict[str, ResolvedContext] = {}

    def set_context(self, text_prefix: str, ctx: ResolvedContext) -> None:
        self._contexts[text_prefix] = ctx
        self.context_resolver.resolve = mock.MagicMock(side_effect=self._resolve)

    def _resolve_context(self, msg: SurfaceMessage) -> ResolvedContext:
        return self._resolve(msg)

    def _resolve(self, msg: SurfaceMessage) -> ResolvedContext:
        for prefix, ctx in self._contexts.items():
            if msg.text.startswith(prefix):
                return ctx
        return list(self._contexts.values())[0]

    async def process(self, msg: SurfaceMessage) -> AsyncIterator[Any]:
        self.process_calls.append(msg.text)
        if self._delay:
            await asyncio.sleep(self._delay)
        yield ChatCompleteEvent(
            message_id="m1",
            content=f"Reply to: {msg.text}",
            token_usage={},
            context_window=0,
            graph_revision="",
        )


# =======================================================================
# ResourceBudget tests
# =======================================================================

class TestResourceBudget:
    def test_defaults(self):
        budget = ResourceBudget()
        assert budget.max_concurrent_llm_calls == 10
        assert budget.max_concurrent_runs == 5
        assert budget.max_memory_mb is None

    def test_custom_values(self):
        budget = ResourceBudget(max_concurrent_llm_calls=3, max_concurrent_runs=2, max_memory_mb=512)
        assert budget.max_concurrent_llm_calls == 3
        assert budget.max_concurrent_runs == 2
        assert budget.max_memory_mb == 512

    def test_from_env_overrides(self):
        with mock.patch.dict(os.environ, {
            "DAN_MAX_CONCURRENT_LLM": "20",
            "DAN_MAX_CONCURRENT_RUNS": "8",
            "DAN_MAX_MEMORY_MB": "1024",
        }):
            budget = ResourceBudget.from_env()
        assert budget.max_concurrent_llm_calls == 20
        assert budget.max_concurrent_runs == 8
        assert budget.max_memory_mb == 1024

    def test_from_env_partial_override(self):
        with mock.patch.dict(os.environ, {"DAN_MAX_CONCURRENT_RUNS": "3"}, clear=False):
            env_before = os.environ.get("DAN_MAX_CONCURRENT_LLM")
            if env_before is not None:
                os.environ.pop("DAN_MAX_CONCURRENT_LLM")
            budget = ResourceBudget.from_env()
        assert budget.max_concurrent_runs == 3
        assert budget.max_concurrent_llm_calls == 10  # default

    def test_from_env_no_vars(self):
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("DAN_MAX_CONCURRENT_LLM", "DAN_MAX_CONCURRENT_RUNS", "DAN_MAX_MEMORY_MB")}
        with mock.patch.dict(os.environ, clean, clear=True):
            budget = ResourceBudget.from_env()
        assert budget == ResourceBudget()


# =======================================================================
# ResourceTracker tests
# =======================================================================

class TestResourceTracker:
    @pytest.mark.asyncio
    async def test_acquire_release_run(self):
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=2))
        assert await tracker.try_acquire("run") is True
        assert await tracker.try_acquire("run") is True
        assert await tracker.try_acquire("run") is False  # exhausted
        await tracker.release("run")
        assert await tracker.try_acquire("run") is True

    @pytest.mark.asyncio
    async def test_acquire_release_llm(self):
        tracker = ResourceTracker(ResourceBudget(max_concurrent_llm_calls=2))
        assert await tracker.try_acquire("llm") is True
        assert await tracker.try_acquire("llm") is True
        assert await tracker.try_acquire("llm") is False
        await tracker.release("llm")
        assert await tracker.try_acquire("llm") is True

    @pytest.mark.asyncio
    async def test_backpressure_returns_false(self):
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=1))
        assert await tracker.try_acquire("run") is True
        assert tracker.available is False
        assert await tracker.try_acquire("run") is False

    @pytest.mark.asyncio
    async def test_available_property(self):
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=2))
        assert tracker.available is True
        await tracker.try_acquire("run")
        assert tracker.available is True
        await tracker.try_acquire("run")
        assert tracker.available is False
        await tracker.release("run")
        assert tracker.available is True

    @pytest.mark.asyncio
    async def test_available_false_when_llm_budget_exhausted(self):
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=2, max_concurrent_llm_calls=1))
        assert tracker.available is True
        await tracker.try_acquire("llm")
        assert tracker.available is False

    @pytest.mark.asyncio
    async def test_snapshot(self):
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=3, max_concurrent_llm_calls=5))
        await tracker.try_acquire("run")
        await tracker.try_acquire("llm")
        await tracker.try_acquire("llm")
        snap = tracker.snapshot()
        assert snap["active_runs"] == 1
        assert snap["max_runs"] == 3
        assert snap["active_llm_calls"] == 2
        assert snap["max_llm_calls"] == 5
        assert snap["available"] is True

    @pytest.mark.asyncio
    async def test_release_below_zero_clamped(self):
        tracker = ResourceTracker()
        await tracker.release("run")
        await tracker.release("llm")
        snap = tracker.snapshot()
        assert snap["active_runs"] == 0
        assert snap["active_llm_calls"] == 0

    @pytest.mark.asyncio
    async def test_default_budget(self):
        tracker = ResourceTracker()
        assert tracker.budget.max_concurrent_runs == 5
        assert tracker.budget.max_concurrent_llm_calls == 10

    @pytest.mark.asyncio
    async def test_concurrent_acquire_thread_safety(self):
        """Multiple concurrent acquires should not over-allocate."""
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=3))
        results = await asyncio.gather(
            *[tracker.try_acquire("run") for _ in range(10)]
        )
        assert sum(results) == 3  # exactly 3 acquired
        assert tracker.snapshot()["active_runs"] == 3


def test_build_concierge_wires_resource_tracker_from_env(monkeypatch):
    from dan.server.concierge.runtime import build_concierge

    monkeypatch.setenv("DAN_MAX_CONCURRENT_RUNS", "2")
    monkeypatch.setenv("DAN_MAX_CONCURRENT_LLM", "7")

    result = build_concierge(
        chat_manager=mock.MagicMock(),
        capability_context=SimpleNamespace(run_manager=None, activity_tracker=None),
        enable_dispatcher=True,
    )

    assert isinstance(result, tuple)
    _concierge, dispatcher = result
    tracker = dispatcher._resource_tracker
    assert tracker is not None
    assert tracker.budget.max_concurrent_runs == 2
    assert tracker.budget.max_concurrent_llm_calls == 7


# =======================================================================
# classify_priority tests
# =======================================================================

class TestClassifyPriority:
    def test_cancel_is_critical(self):
        assert classify_priority("/cancel") == MessagePriority.CRITICAL

    def test_status_is_critical(self):
        assert classify_priority("/status") == MessagePriority.CRITICAL

    def test_priority_critical_prefix(self):
        assert classify_priority("/priority critical do this now") == MessagePriority.CRITICAL

    def test_priority_high_prefix(self):
        assert classify_priority("/priority high fix the bug") == MessagePriority.HIGH

    def test_urgent_keyword(self):
        assert classify_priority("this is urgent, fix now") == MessagePriority.HIGH

    def test_active_goals_promotes_to_high(self):
        assert classify_priority("continue with task", active_goals=["goal-1"]) == MessagePriority.HIGH

    def test_no_active_goals_is_normal(self):
        assert classify_priority("summarize this article") == MessagePriority.NORMAL

    def test_empty_goals_list_is_normal(self):
        assert classify_priority("hello", active_goals=[]) == MessagePriority.NORMAL

    def test_priority_low_prefix(self):
        assert classify_priority("/priority low consolidate memories") == MessagePriority.LOW

    def test_whitespace_handling(self):
        assert classify_priority("  /cancel  ") == MessagePriority.CRITICAL

    def test_case_insensitive(self):
        assert classify_priority("This is URGENT please") == MessagePriority.HIGH


# =======================================================================
# PriorityQueue tests
# =======================================================================

class TestPriorityQueue:
    @pytest.mark.asyncio
    async def test_high_before_normal(self):
        pq = PriorityQueue()
        await pq.put("normal-1", priority=MessagePriority.NORMAL)
        await pq.put("high-1", priority=MessagePriority.HIGH)
        await pq.put("normal-2", priority=MessagePriority.NORMAL)

        assert pq.qsize() == 3
        assert await pq.get() == "high-1"
        assert await pq.get() == "normal-1"
        assert await pq.get() == "normal-2"

    @pytest.mark.asyncio
    async def test_same_priority_fifo(self):
        pq = PriorityQueue()
        await pq.put("a", priority=MessagePriority.NORMAL)
        await pq.put("b", priority=MessagePriority.NORMAL)
        await pq.put("c", priority=MessagePriority.NORMAL)

        assert await pq.get() == "a"
        assert await pq.get() == "b"
        assert await pq.get() == "c"

    @pytest.mark.asyncio
    async def test_full_priority_ordering(self):
        pq = PriorityQueue()
        await pq.put("low", priority=MessagePriority.LOW)
        await pq.put("critical", priority=MessagePriority.CRITICAL)
        await pq.put("normal", priority=MessagePriority.NORMAL)
        await pq.put("high", priority=MessagePriority.HIGH)

        results = [await pq.get() for _ in range(4)]
        assert results == ["critical", "high", "normal", "low"]

    @pytest.mark.asyncio
    async def test_bounded_queue_raises_on_full(self):
        pq = PriorityQueue(maxsize=2)
        await pq.put("a")
        await pq.put("b")
        assert pq.full() is True
        with pytest.raises(asyncio.QueueFull):
            await pq.put("c")

    @pytest.mark.asyncio
    async def test_empty_get_blocks_then_resolves(self):
        pq = PriorityQueue()
        assert pq.empty() is True

        result: list[str] = []

        async def _delayed_put():
            await asyncio.sleep(0.02)
            await pq.put("delayed")

        async def _get():
            item = await pq.get()
            result.append(item)

        await asyncio.gather(_delayed_put(), _get())
        assert result == ["delayed"]

    def test_put_nowait_and_get_nowait(self):
        pq = PriorityQueue()
        pq.put_nowait("b", priority=MessagePriority.NORMAL)
        pq.put_nowait("a", priority=MessagePriority.HIGH)
        assert pq.get_nowait() == "a"
        assert pq.get_nowait() == "b"

    def test_get_nowait_empty_raises(self):
        pq = PriorityQueue()
        with pytest.raises(asyncio.QueueEmpty):
            pq.get_nowait()

    def test_put_nowait_full_raises(self):
        pq = PriorityQueue(maxsize=1)
        pq.put_nowait("x")
        with pytest.raises(asyncio.QueueFull):
            pq.put_nowait("y")


# =======================================================================
# Dispatcher integration: resource-based concurrency
# =======================================================================

class TestDispatcherResourceConcurrency:
    @pytest.mark.asyncio
    async def test_resource_tracker_replaces_hard_cap(self):
        """With a ResourceTracker(max_runs=2), 3rd project overflows (not the hard cap)."""
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=2))
        concierge = _FakeConcierge(delay=0.1)
        ctx_a = _make_context("proj-a", "A")
        ctx_b = _make_context("proj-b", "B")
        ctx_c = _make_context("proj-c", "C")
        concierge.set_context("A", ctx_a)
        concierge.set_context("B", ctx_b)
        concierge.set_context("C", ctx_c)

        dispatcher = ConcurrentDispatcher(
            concierge,
            max_concurrent_projects=100,  # high hard cap — should be irrelevant
            resource_tracker=tracker,
        )

        overflow_seen = False

        async def _dispatch(text: str):
            nonlocal overflow_seen
            async for event in dispatcher.dispatch(_make_msg(text)):
                p = event.model_dump()
                if p.get("type") == "chat_queued":
                    overflow_seen = True

        async def _dispatch_c():
            await asyncio.sleep(0.02)
            await _dispatch("C: task")

        await asyncio.gather(
            _dispatch("A: task"),
            _dispatch("B: task"),
            _dispatch_c(),
        )

        assert overflow_seen, "C should overflow because ResourceTracker max_runs=2"

    @pytest.mark.asyncio
    async def test_no_tracker_uses_hard_cap(self):
        """Without a ResourceTracker the legacy max_concurrent_projects still works."""
        concierge = _FakeConcierge(delay=0.1)
        ctx_a = _make_context("proj-a", "A")
        ctx_b = _make_context("proj-b", "B")
        ctx_c = _make_context("proj-c", "C")
        concierge.set_context("A", ctx_a)
        concierge.set_context("B", ctx_b)
        concierge.set_context("C", ctx_c)

        dispatcher = ConcurrentDispatcher(concierge, max_concurrent_projects=2)

        overflow_seen = False

        async def _dispatch(text: str):
            nonlocal overflow_seen
            async for event in dispatcher.dispatch(_make_msg(text)):
                if event.model_dump().get("type") == "chat_queued":
                    overflow_seen = True

        async def _dispatch_c():
            await asyncio.sleep(0.02)
            await _dispatch("C: task")

        await asyncio.gather(
            _dispatch("A: task"),
            _dispatch("B: task"),
            _dispatch_c(),
        )

        assert overflow_seen, "Legacy hard cap should still trigger overflow"

    @pytest.mark.asyncio
    async def test_dispatcher_honors_llm_budget(self):
        """With max_llm_calls=1, a second project should overflow even if runs are available."""
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=5, max_concurrent_llm_calls=1))
        concierge = _FakeConcierge(delay=0.1)
        ctx_a = _make_context("proj-a", "A")
        ctx_b = _make_context("proj-b", "B")
        concierge.set_context("A", ctx_a)
        concierge.set_context("B", ctx_b)

        dispatcher = ConcurrentDispatcher(
            concierge,
            max_concurrent_projects=5,
            resource_tracker=tracker,
        )

        overflow_seen = False

        async def _dispatch(text: str):
            nonlocal overflow_seen
            async for event in dispatcher.dispatch(_make_msg(text)):
                if event.model_dump().get("type") == "chat_queued":
                    overflow_seen = True

        async def _dispatch_b():
            await asyncio.sleep(0.02)
            await _dispatch("B: task")

        await asyncio.gather(
            _dispatch("A: task"),
            _dispatch_b(),
        )

        assert overflow_seen, "LLM budget should trigger overflow when saturated"

    @pytest.mark.asyncio
    async def test_resource_released_after_completion(self):
        """After a project finishes, the tracker slot is released."""
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=1))
        concierge = _FakeConcierge(delay=0.01)
        ctx = _make_context("proj-a", "A")
        concierge.set_context("", ctx)

        dispatcher = ConcurrentDispatcher(concierge, resource_tracker=tracker)

        async for _ in dispatcher.dispatch(_make_msg("A: task")):
            pass

        await asyncio.sleep(0.1)
        snap = tracker.snapshot()
        assert snap["active_runs"] == 0, "Slot should be released after processing completes"


# =======================================================================
# Dispatcher integration: priority queuing
# =======================================================================

class TestDispatcherPriorityQueuing:
    @pytest.mark.asyncio
    async def test_high_priority_processed_before_normal(self):
        """When multiple messages overflow, HIGH should be dequeued before NORMAL."""
        tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=1))
        concierge = _FakeConcierge(delay=0.05)
        ctx_a = _make_context("proj-a", "A")
        ctx_b = _make_context("proj-b", "B")
        ctx_c = _make_context("proj-c", "C")
        concierge.set_context("normal", ctx_b)
        concierge.set_context("urgent", ctx_c)
        concierge.set_context("A", ctx_a)

        dispatcher = ConcurrentDispatcher(concierge, resource_tracker=tracker)

        async def _dispatch_first():
            async for _ in dispatcher.dispatch(_make_msg("A: first (blocks slot)")):
                pass

        async def _queue_normal_then_urgent():
            await asyncio.sleep(0.01)
            async for _ in dispatcher.dispatch(_make_msg("normal task")):
                pass
            async for _ in dispatcher.dispatch(_make_msg("urgent fix needed")):
                pass

        await asyncio.gather(_dispatch_first(), _queue_normal_then_urgent())

        await asyncio.sleep(0.3)

        a_idx = concierge.process_calls.index("A: first (blocks slot)")
        remaining = concierge.process_calls[a_idx + 1:]
        if len(remaining) >= 2:
            assert remaining.index("urgent fix needed") < remaining.index("normal task"), \
                f"Urgent should be processed before normal: {remaining}"

    @pytest.mark.asyncio
    async def test_same_project_queue_respects_priority(self):
        """Within same-project queue, higher priority messages go first."""
        concierge = _FakeConcierge(delay=0.05)
        ctx = _make_context("proj-1", "P1")
        concierge.set_context("", ctx)

        dispatcher = ConcurrentDispatcher(concierge)

        async def _dispatch_first():
            async for _ in dispatcher.dispatch(_make_msg("first message")):
                pass

        async def _queue_two():
            await asyncio.sleep(0.01)
            async for _ in dispatcher.dispatch(_make_msg("second normal")):
                pass
            async for _ in dispatcher.dispatch(_make_msg("/status check")):
                pass

        await asyncio.gather(_dispatch_first(), _queue_two())

        await asyncio.sleep(0.3)

        idx_first = concierge.process_calls.index("first message")
        remaining = concierge.process_calls[idx_first + 1:]
        if len(remaining) >= 2:
            assert remaining.index("/status check") < remaining.index("second normal"), \
                f"/status (CRITICAL) should be processed before normal: {remaining}"
