"""Integration tests for the concierge parallelism layer (29-5 §11).

Tests 11-1..11-3 (unit tests for fan_out, ResourceTracker, PriorityQueue) are
embedded here alongside the integration tests so the entire parallelism surface
is validated in one file.

Timing-based tests use generous margins (≥2x expected) to avoid flakiness.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 11-1: fan_out / fan_out_dict unit tests
# ---------------------------------------------------------------------------

from dan.server.concierge.fan_out import fan_out, fan_out_dict


@pytest.mark.asyncio
async def test_fan_out_concurrent_execution():
    """Multiple tasks execute concurrently, not sequentially."""

    async def sleeper(sec: float) -> float:
        await asyncio.sleep(sec)
        return sec

    tasks = [lambda s=s: sleeper(s) for s in [0.05] * 5]
    t0 = time.monotonic()
    results = await fan_out(tasks)
    elapsed = time.monotonic() - t0

    assert results == [0.05] * 5
    assert elapsed < 0.15, f"Expected < 0.15s (concurrent), got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_fan_out_partial_failure():
    """One failing task does not block others."""

    async def ok():
        return "ok"

    async def bad():
        raise ValueError("boom")

    results = await fan_out([ok, bad, ok])
    assert results[0] == "ok"
    assert isinstance(results[1], ValueError)
    assert results[2] == "ok"


@pytest.mark.asyncio
async def test_fan_out_timeout():
    """Tasks exceeding timeout return TimeoutError without blocking others."""

    async def fast():
        return "fast"

    async def slow():
        await asyncio.sleep(5)
        return "slow"

    results = await fan_out([fast, slow], timeout_per=0.05)
    assert results[0] == "fast"
    assert isinstance(results[1], asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_fan_out_dict_keyed_results():
    """Named tasks return a dict of name -> result."""

    async def alpha():
        return 1

    async def beta():
        return 2

    results = await fan_out_dict({"a": alpha, "b": beta})
    assert results == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_fan_out_empty():
    assert await fan_out([]) == []
    assert await fan_out_dict({}) == {}


# ---------------------------------------------------------------------------
# 11-2: ResourceTracker unit tests
# ---------------------------------------------------------------------------

from dan.server.concierge.resources import ResourceBudget, ResourceTracker


@pytest.mark.asyncio
async def test_resource_tracker_budget_accounting():
    """Tracker respects budget limits and releases correctly."""
    tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=2))

    assert await tracker.try_acquire("run") is True
    assert await tracker.try_acquire("run") is True
    assert await tracker.try_acquire("run") is False  # budget exhausted
    assert not tracker.available

    await tracker.release("run")
    assert tracker.available
    assert await tracker.try_acquire("run") is True


@pytest.mark.asyncio
async def test_resource_tracker_backpressure():
    """Snapshot reflects current usage."""
    tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=3, max_concurrent_llm_calls=2))

    await tracker.try_acquire("run")
    await tracker.try_acquire("llm")
    snap = tracker.snapshot()
    assert snap["active_runs"] == 1
    assert snap["active_llm_calls"] == 1
    assert snap["available"] is True


@pytest.mark.asyncio
async def test_resource_tracker_llm_slot():
    tracker = ResourceTracker(ResourceBudget(max_concurrent_llm_calls=1))
    assert await tracker.try_acquire("llm") is True
    assert await tracker.try_acquire("llm") is False
    await tracker.release("llm")
    assert await tracker.try_acquire("llm") is True


# ---------------------------------------------------------------------------
# 11-3: PriorityQueue unit tests
# ---------------------------------------------------------------------------

from dan.server.concierge.resources import MessagePriority, PriorityQueue, classify_priority


@pytest.mark.asyncio
async def test_priority_queue_ordering():
    """Higher priority (lower value) items are dequeued first."""
    pq = PriorityQueue()
    await pq.put("low", priority=MessagePriority.LOW)
    await pq.put("critical", priority=MessagePriority.CRITICAL)
    await pq.put("normal", priority=MessagePriority.NORMAL)

    assert pq.qsize() == 3
    assert await pq.get() == "critical"
    assert await pq.get() == "normal"
    assert await pq.get() == "low"


@pytest.mark.asyncio
async def test_priority_queue_fifo_within_priority():
    """Within the same priority level, FIFO ordering is preserved."""
    pq = PriorityQueue()
    for i in range(5):
        await pq.put(f"msg-{i}", priority=MessagePriority.NORMAL)

    for i in range(5):
        assert await pq.get() == f"msg-{i}"


def test_classify_priority_keywords():
    assert classify_priority("/cancel build") == MessagePriority.CRITICAL
    assert classify_priority("/status") == MessagePriority.CRITICAL
    assert classify_priority("/priority high run this") == MessagePriority.HIGH
    assert classify_priority("urgent: fix the pipeline") == MessagePriority.HIGH
    assert classify_priority("build me a workflow", active_goals=["g1"]) == MessagePriority.HIGH
    assert classify_priority("/priority low summarize") == MessagePriority.LOW
    assert classify_priority("hello there") == MessagePriority.NORMAL


@pytest.mark.asyncio
async def test_priority_queue_full():
    pq = PriorityQueue(maxsize=2)
    await pq.put("a")
    await pq.put("b")
    with pytest.raises(asyncio.QueueFull):
        await pq.put("c")


# ---------------------------------------------------------------------------
# 11-4: Concurrent turn preparation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parallel_prep_faster_than_sequential():
    """Memory retrieval and context resolution run concurrently in prep phase."""
    delay = 0.05

    async def mock_memory(sec: float = delay) -> str:
        await asyncio.sleep(sec)
        return "memory-context"

    async def mock_context(sec: float = delay) -> dict:
        await asyncio.sleep(sec)
        return {"project": "test"}

    t0 = time.monotonic()
    results = await fan_out_dict({
        "memory": lambda: mock_memory(),
        "context": lambda: mock_context(),
    })
    elapsed = time.monotonic() - t0

    assert results["memory"] == "memory-context"
    assert results["context"] == {"project": "test"}
    # Both run concurrently: wall clock should be ~delay, not ~2*delay
    assert elapsed < delay * 1.8, f"Expected < {delay * 1.8:.3f}s, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_parallel_prep_with_reuse_search():
    """Speculative reuse search runs concurrently with memory + context (29-5 §6-2)."""
    delay = 0.04

    async def mock_memory() -> str:
        await asyncio.sleep(delay)
        return "mem"

    async def mock_context() -> dict:
        await asyncio.sleep(delay)
        return {}

    async def mock_reuse() -> tuple:
        await asyncio.sleep(delay)
        return ("generate", None)

    t0 = time.monotonic()
    results = await fan_out_dict({
        "memory": mock_memory,
        "context": mock_context,
        "reuse": mock_reuse,
    })
    elapsed = time.monotonic() - t0

    assert results["reuse"] == ("generate", None)
    # All three concurrent: ~delay, not ~3*delay
    assert elapsed < delay * 2.5, f"Expected < {delay * 2.5:.3f}s, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# 11-5: Multi-file fan-out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multi_file_fan_out_faster():
    """10 file reads at 0.02s each fan out, completing much faster than sequential."""
    per_file = 0.02
    n_files = 10

    async def mock_file_read(path: str) -> str:
        await asyncio.sleep(per_file)
        return f"contents of {path}"

    tasks = [lambda p=f"file_{i}.txt": mock_file_read(p) for i in range(n_files)]

    t0 = time.monotonic()
    results = await fan_out(tasks)
    elapsed = time.monotonic() - t0

    assert len(results) == n_files
    assert all(isinstance(r, str) for r in results)
    # Concurrent: ~0.02s wall clock, not ~0.2s
    assert elapsed < per_file * n_files * 0.5, (
        f"Expected < {per_file * n_files * 0.5:.3f}s (concurrent), got {elapsed:.3f}s"
    )


# ---------------------------------------------------------------------------
# 11-6: Diagnosis fan-out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_diagnosis_fans_out_queries():
    """Diagnosis sources run concurrently via fan_out_dict."""
    per_source = 0.03
    sources = ["memory_repair", "error_patterns", "principles", "similar_workflows"]

    async def mock_source(name: str) -> dict:
        await asyncio.sleep(per_source)
        return {name: [f"result-{name}"]}

    tasks = {name: (lambda n=name: mock_source(n)) for name in sources}

    t0 = time.monotonic()
    results = await fan_out_dict(tasks)
    elapsed = time.monotonic() - t0

    assert len(results) == len(sources)
    for name in sources:
        assert name in results
    # 4 sources concurrent: ~0.03s, not ~0.12s
    assert elapsed < per_source * len(sources) * 0.6, (
        f"Expected < {per_source * len(sources) * 0.6:.3f}s, got {elapsed:.3f}s"
    )


# ---------------------------------------------------------------------------
# 11-7: Independent projects start concurrently
# ---------------------------------------------------------------------------

class _FakeConcierge:
    """Minimal concierge stub for dispatcher tests."""

    def __init__(self, process_delay: float = 0.02) -> None:
        self._delay = process_delay
        self.context_resolver = MagicMock()
        self._concierge_state = MagicMock(active_goals=[])
        self._calls: list[str] = []

    def _resolve_context(self, msg: Any):
        return self.context_resolver.resolve(msg)

    async def process(self, msg: Any) -> AsyncIterator:
        self._calls.append(msg.text if hasattr(msg, "text") else str(msg))
        await asyncio.sleep(self._delay)
        from dan.server.chat_manager import ChatCompleteEvent

        yield ChatCompleteEvent(
            message_id=uuid.uuid4().hex[:12],
            content=f"done-{msg.text}",
            token_usage={},
            context_window=0,
            graph_revision="",
        )


class _ToolHeavyConcierge(_FakeConcierge):
    """Concierge stub that transitions from model work into tool wait."""

    def __init__(self) -> None:
        super().__init__(process_delay=0.0)
        self.started_order: list[str] = []
        self.completed_order: list[str] = []
        self._started: dict[str, asyncio.Event] = {}
        self._allow_tool_start: dict[str, asyncio.Event] = {}
        self._tool_started: dict[str, asyncio.Event] = {}
        self._finish: dict[str, asyncio.Event] = {}

    def _event(self, store: dict[str, asyncio.Event], text: str) -> asyncio.Event:
        return store.setdefault(text, asyncio.Event())

    async def wait_started(self, text: str) -> None:
        await self._event(self._started, text).wait()

    async def wait_tool_started(self, text: str) -> None:
        await self._event(self._tool_started, text).wait()

    def allow_tool_start(self, text: str) -> None:
        self._event(self._allow_tool_start, text).set()

    def finish_tools(self, text: str) -> None:
        self._event(self._finish, text).set()

    async def process(self, msg: Any) -> AsyncIterator:
        from dan.server.chat_manager import (
            ChatCompleteEvent,
            ChatToolCallResultEvent,
            ChatToolCallStartEvent,
        )

        text = msg.text if hasattr(msg, "text") else str(msg)
        self._calls.append(text)
        self.started_order.append(text)
        self._event(self._started, text).set()

        await self._event(self._allow_tool_start, text).wait()
        yield ChatToolCallStartEvent(
            tool_call_id=f"tool-{text}",
            tool_name="mock_tool",
            args_preview=text,
        )
        self._event(self._tool_started, text).set()

        await self._event(self._finish, text).wait()
        yield ChatToolCallResultEvent(
            tool_call_id=f"tool-{text}",
            tool_name="mock_tool",
            status="ok",
            output_preview=f"done-{text}",
            duration_ms=1,
        )
        self.completed_order.append(text)
        yield ChatCompleteEvent(
            message_id=uuid.uuid4().hex[:12],
            content=f"done-{text}",
            token_usage={},
            context_window=0,
            graph_revision="",
        )


async def _drain_bus(dispatcher: Any, channel_id: str) -> list[Any]:
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


def _make_msg(text: str, project_id: str = "") -> Any:
    from dan.server.concierge.models import SurfaceMessage

    return SurfaceMessage(
        text=text,
        surface="test",
        external_id=f"eid-{uuid.uuid4().hex[:8]}",
        metadata={"project_id": project_id or f"proj-{uuid.uuid4().hex[:6]}"},
    )


def _make_context(project_id: str) -> Any:
    from dan.server.concierge.models import Project, ResolvedContext, Task

    project = Project(
        project_id=project_id,
        surface_id="test",
        label=project_id,
    )
    task = Task(
        task_id=f"task-{uuid.uuid4().hex[:6]}",
        label="test-task",
    )
    return ResolvedContext(
        project=project,
        task=task,
        is_new_project=False,
        is_new_task=False,
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_independent_projects_start_concurrently():
    """10 messages to 10 different projects all start immediately with sufficient resources."""
    from dan.server.concierge.dispatcher import ConcurrentDispatcher

    n_projects = 10
    concierge = _FakeConcierge(process_delay=0.03)
    tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=n_projects))
    dispatcher = ConcurrentDispatcher(
        concierge,
        max_concurrent_projects=n_projects,
        resource_tracker=tracker,
    )

    project_ids = [f"proj-{i}" for i in range(n_projects)]
    msgs = [_make_msg(f"msg-{i}", project_id=pid) for i, pid in enumerate(project_ids)]

    for i, msg in enumerate(msgs):
        ctx = _make_context(project_ids[i])
        concierge.context_resolver.resolve = MagicMock(return_value=ctx)

    async def drain(msg: Any, pid: str) -> str:
        ctx = _make_context(pid)
        concierge.context_resolver.resolve = MagicMock(return_value=ctx)
        events = []
        async for event in dispatcher.dispatch(msg):
            events.append(event)
        return events[-1].content if events else ""

    t0 = time.monotonic()
    results = await asyncio.gather(*[
        drain(msgs[i], project_ids[i]) for i in range(n_projects)
    ])
    elapsed = time.monotonic() - t0

    assert len(results) == n_projects
    # All run concurrently: ~0.03s, not ~0.3s
    assert elapsed < 0.03 * n_projects * 0.5, (
        f"Expected all projects concurrent, got {elapsed:.3f}s"
    )

    await dispatcher.close()


# ---------------------------------------------------------------------------
# 12-4: Tool-heavy fairness follow-up
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_heavy_chats_release_run_capacity_between_model_calls():
    """A queued tool-heavy project should start once another project enters tool wait."""
    from dan.server.chat_manager import ChatQueuedEvent, ChatToolCallStartEvent
    from dan.server.concierge.dispatcher import ConcurrentDispatcher

    concierge = _ToolHeavyConcierge()
    tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=1))
    dispatcher = ConcurrentDispatcher(
        concierge,
        max_concurrent_projects=1,
        resource_tracker=tracker,
    )

    msg_a = _make_msg("msg-a", project_id="proj-a")
    msg_b = _make_msg("msg-b", project_id="proj-b")

    concierge.context_resolver.resolve = MagicMock(
        side_effect=lambda msg: _make_context(msg.metadata["project_id"])
    )

    direct_a: list[Any] = []
    direct_b: list[Any] = []

    async def _consume_direct(msg: Any, sink: list[Any]) -> None:
        async for event in dispatcher.dispatch(msg):
            sink.append(event)

    task_a = asyncio.create_task(_consume_direct(msg_a, direct_a))
    await concierge.wait_started("msg-a")

    task_b = asyncio.create_task(_consume_direct(msg_b, direct_b))
    for _ in range(20):
        if direct_b:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("second tool-heavy chat should queue while the first holds the run slot")

    assert isinstance(direct_b[0], ChatQueuedEvent)

    concierge.allow_tool_start("msg-a")
    await concierge.wait_tool_started("msg-a")
    await concierge.wait_started("msg-b")

    assert "msg-a" in concierge.started_order
    assert "msg-b" in concierge.started_order
    assert concierge.completed_order == []

    concierge.allow_tool_start("msg-b")
    await concierge.wait_tool_started("msg-b")
    assert "msg-b" not in concierge.completed_order

    concierge.finish_tools("msg-b")
    bus_events_b = await _drain_bus(dispatcher, direct_b[0].stream_channel_id)

    concierge.finish_tools("msg-a")
    await task_a
    await task_b

    assert any(isinstance(event, ChatToolCallStartEvent) for event in direct_a)
    assert any(isinstance(event, ChatToolCallStartEvent) for event in bus_events_b)
    assert concierge.completed_order == ["msg-b", "msg-a"]
    await dispatcher.close()


# ---------------------------------------------------------------------------
# 11-8: High-priority message jumps queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_high_priority_jumps_queue():
    """Higher-priority messages are dequeued before lower-priority ones."""
    pq = PriorityQueue()

    await pq.put("low-1", priority=MessagePriority.LOW)
    await pq.put("low-2", priority=MessagePriority.LOW)
    await pq.put("high-1", priority=MessagePriority.HIGH)
    await pq.put("normal-1", priority=MessagePriority.NORMAL)
    await pq.put("critical-1", priority=MessagePriority.CRITICAL)

    order = [await pq.get() for _ in range(5)]
    assert order == ["critical-1", "high-1", "normal-1", "low-1", "low-2"]


# ---------------------------------------------------------------------------
# 11-9: Same-project serialization under parallelism
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_project_serializes_under_parallelism():
    """Messages to the same project execute in order, not interleaved."""
    from dan.server.concierge.dispatcher import ConcurrentDispatcher

    execution_order: list[str] = []

    class _OrderedConcierge(_FakeConcierge):
        async def process(self, msg: Any) -> AsyncIterator:
            text = msg.text if hasattr(msg, "text") else str(msg)
            execution_order.append(f"start-{text}")
            await asyncio.sleep(0.03)
            execution_order.append(f"end-{text}")
            from dan.server.chat_manager import ChatCompleteEvent

            yield ChatCompleteEvent(
                message_id=uuid.uuid4().hex[:12],
                content=f"done-{text}",
                token_usage={},
                context_window=0,
                graph_revision="",
            )

    concierge = _OrderedConcierge(process_delay=0.03)
    project_id = "same-project"
    tracker = ResourceTracker(ResourceBudget(max_concurrent_runs=10))
    dispatcher = ConcurrentDispatcher(
        concierge,
        max_concurrent_projects=10,
        resource_tracker=tracker,
    )

    ctx = _make_context(project_id)
    concierge.context_resolver.resolve = MagicMock(return_value=ctx)

    msgs = [_make_msg(f"seq-{i}", project_id=project_id) for i in range(3)]

    async def drain_all(msg: Any) -> list:
        events = []
        async for event in dispatcher.dispatch(msg):
            events.append(event)
        return events

    # Dispatch all 3 concurrently — dispatcher should serialize them
    results = await asyncio.gather(*[drain_all(m) for m in msgs])

    # The first message processes immediately; subsequent ones are queued.
    # Verify the first message completed before the queue was drained.
    assert execution_order[0] == "start-seq-0"
    assert execution_order[1] == "end-seq-0"

    # Queued messages should also execute in order
    start_indices = [
        i for i, e in enumerate(execution_order) if e.startswith("start-")
    ]
    for i in range(len(start_indices) - 1):
        assert start_indices[i] < start_indices[i + 1]

    await dispatcher.close()


# ---------------------------------------------------------------------------
# 5-3 integration: consolidation fan-out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consolidation_async_runs():
    """run_consolidation_async fans out promote + decay, then archive."""
    import tempfile

    from dan.engine.memory_kernel import (
        MemoryItem,
        MemoryKernel,
        MemoryLifecycle,
        MemoryScope,
        MemoryType,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        mk = MemoryKernel(base_dir=tmpdir)

        old_time = time.time() - 200 * 86400  # 200 days ago
        recent_time = time.time() - 48 * 3600  # 48 hours ago (promotable, not archivable)
        mk.store(MemoryItem(
            id="recent-active",
            content="recently created active item",
            memory_type=MemoryType.FACT,
            scope=MemoryScope.USER,
            lifecycle=MemoryLifecycle.ACTIVE,
            created_at=recent_time,
            last_accessed=time.time(),
            importance=0.8,
        ))
        mk.store(MemoryItem(
            id="old-active",
            content="old active item",
            memory_type=MemoryType.FACT,
            scope=MemoryScope.USER,
            lifecycle=MemoryLifecycle.ACTIVE,
            created_at=old_time,
            last_accessed=old_time,
            importance=0.8,
        ))
        mk.store(MemoryItem(
            id="old-durable",
            content="old durable item",
            memory_type=MemoryType.FACT,
            scope=MemoryScope.USER,
            lifecycle=MemoryLifecycle.DURABLE,
            created_at=old_time,
            last_accessed=old_time,
            importance=0.8,
        ))

        result = await mk.run_consolidation_async()

        assert result["promoted"] >= 2  # recent-active + old-active
        assert result["archived"] >= 1  # old-durable (and old-active after promotion)
        assert result["decayed"] >= 1

        # recent-active: promoted to DURABLE but not archived (accessed recently)
        recent = mk.get("recent-active")
        assert recent is not None
        assert recent.lifecycle == MemoryLifecycle.DURABLE

        # old-active: promoted to DURABLE then archived (not accessed for 200 days)
        old_active = mk.get("old-active")
        assert old_active is not None
        assert old_active.lifecycle == MemoryLifecycle.ARCHIVE

        old_durable = mk.get("old-durable")
        assert old_durable is not None
        assert old_durable.lifecycle == MemoryLifecycle.ARCHIVE


# ---------------------------------------------------------------------------
# 5-2 integration: post-run learning fan-out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_learner_async_success_path():
    """extract_run_learnings_async fans out asset + pattern + repair steps."""
    import tempfile

    from dan.engine.memory_kernel import MemoryKernel, MemoryType
    from dan.engine.run_learner import RunLearner

    with tempfile.TemporaryDirectory() as tmpdir:
        mk = MemoryKernel(base_dir=tmpdir)
        learner = RunLearner(mk)

        run_result = {
            "success": True,
            "graph_id": "wf-test-1",
            "run_id": "run-001",
        }
        workflow = {
            "nodes": [
                {"id": "n1", "node_type": "input"},
                {"id": "n2", "node_type": "llm"},
            ],
            "edges": [{"source_node_id": "n1", "target_node_id": "n2"}],
        }

        stored = await learner.extract_run_learnings_async(
            run_result=run_result,
            workflow=workflow,
        )

        assert any(d["action"] in ("created_asset", "updated_asset") for d in stored)


@pytest.mark.asyncio
async def test_run_learner_async_failure_path():
    """extract_run_learnings_async fans out failure pattern + principle boost."""
    import tempfile

    from dan.engine.memory_kernel import MemoryKernel
    from dan.engine.run_learner import RunLearner

    with tempfile.TemporaryDirectory() as tmpdir:
        mk = MemoryKernel(base_dir=tmpdir)
        mk.store_principle("Always validate inputs", confidence=0.5, tags=["validation"])
        learner = RunLearner(mk)

        run_result = {
            "success": False,
            "graph_id": "wf-fail-1",
            "run_id": "run-002",
            "errors": {"node-1": "Validation error: invalid schema"},
        }

        stored = await learner.extract_run_learnings_async(
            run_result=run_result,
            workflow={"nodes": [{"id": "node-1", "node_type": "llm"}], "edges": []},
        )

        assert any(d["action"] == "created_failure_pattern" for d in stored)
