"""Concurrent project dispatcher — the single authority for all queueing,
serialization, and cross-project parallelism (29-5 §9, §10)."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncIterator

from dan.server.chat_manager import ChatCompleteEvent, ChatQueuedEvent, ChatStreamEvent

from .command_registry import get_default_registry
from .identity import format_prefix
from .models import ResolvedContext, SurfaceMessage

logger = logging.getLogger(__name__)

_BYPASS_PREFIXES = (
    "/status", "/build-status", "/cancel", "/build-stop",
    "/save", "/build-", "/memory-", "/mcp", "/model", "/cost", "/retry"
)
_BYPASS_EXACT = frozenset({
    "status", "cancel", "what's happening", "what's going on",
})


def _is_bypass_command(msg: SurfaceMessage) -> bool:
    """Status and cancel commands bypass queueing entirely (29-5 §10-4)."""
    text = msg.text.strip().lower()
    if get_default_registry().is_fast_command(text):
        return True
    if any(text.startswith(p) for p in _BYPASS_PREFIXES):
        return True
    return text in _BYPASS_EXACT


class ConcurrentDispatcher:
    """Wraps a ``Concierge`` to add per-project concurrency.

    * Different projects -> processed concurrently (separate asyncio tasks)
    * Same project -> messages queued and drained serially so follow-ups
      see the prior result in the task's turn history.
    * Status/cancel commands bypass queueing entirely.
    * New independent projects start immediately when resources are available.
    """

    def __init__(
        self,
        concierge: Any,
        *,
        max_concurrent_projects: int = 5,
        max_queue_depth: int = 20,
        resource_tracker: Any | None = None,
    ) -> None:
        self._concierge = concierge
        self._max_concurrent = max_concurrent_projects
        self._max_queue_depth = max_queue_depth
        self._resource_tracker = resource_tracker

        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._project_queues: dict[str, asyncio.Queue[tuple[SurfaceMessage, str]]] = {}
        self._response_buses: dict[str, tuple[asyncio.Queue[ChatStreamEvent], float]] = {}
        self._response_bus_pins: dict[str, int] = {}
        self._bus_ttl_seconds = 300.0
        from dan.server.concierge.resources import PriorityQueue, MessagePriority, classify_priority

        self._global_queue: PriorityQueue = PriorityQueue(
            maxsize=max_queue_depth * max_concurrent_projects,
        )
        self._classify_priority = classify_priority
        self._MessagePriority = MessagePriority
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def concierge(self) -> Any:
        return self._concierge

    async def dispatch(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        """Accept a message and either process it immediately or queue it.

        Decision rules (29-5 §10):
        1. Status/cancel commands -> always process immediately (bypass queue).
        2. Project has no active task AND resources available -> process immediately.
        3. Project has an active task -> queue behind it (same-project serialization).
        4. Resources exhausted (max concurrent reached) -> queue with position info.
        """
        self._reap_stale_buses()
        context = self._concierge._resolve_context(msg)
        project_id = context.project.project_id

        if _is_bypass_command(msg):
            async for event in self._process_immediately(msg, project_id):
                yield event
            return

        if project_id in self._active_tasks and not self._active_tasks[project_id].done():
            async for event in self._enqueue_for_project(msg, project_id, context):
                yield event
            return

        has_capacity = (
            self._resource_tracker.available
            if self._resource_tracker is not None
            else len(self._active_tasks) < self._max_concurrent
        )
        if not has_capacity:
            async for event in self._enqueue_overflow(msg, context):
                yield event
            return

        async for event in self._process_immediately(msg, project_id):
            yield event

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    async def _process_immediately(
        self, msg: SurfaceMessage, project_id: str,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Spawn a processing task for a new project and yield its events."""
        if self._resource_tracker is not None:
            await self._resource_tracker.try_acquire("run")

        event_queue: asyncio.Queue[ChatStreamEvent | None] = asyncio.Queue()

        async def _run() -> None:
            try:
                await self._execute_and_forward(msg, event_queue)
            except Exception:
                logger.exception("Dispatcher task crashed for project %s", project_id)
            finally:
                event_queue.put_nowait(None)
            try:
                await self._drain_project_queue(project_id)
            except Exception:
                logger.exception("Drain failed for project %s", project_id)
            finally:
                self._active_tasks.pop(project_id, None)
                if self._resource_tracker is not None:
                    await self._resource_tracker.release("run")
            try:
                await self._drain_global_overflow()
            except Exception:
                logger.exception("Overflow drain failed for project %s", project_id)

        task = asyncio.create_task(_run())
        self._active_tasks[project_id] = task

        while True:
            event = await event_queue.get()
            if event is None:
                break
            yield event

    async def _enqueue_for_project(
        self, msg: SurfaceMessage, project_id: str, context: ResolvedContext,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Enqueue a message behind the active task for the same project."""
        if project_id not in self._project_queues:
            self._project_queues[project_id] = asyncio.Queue(maxsize=self._max_queue_depth)

        q = self._project_queues[project_id]
        if q.full():
            yield ChatCompleteEvent(
                message_id=uuid.uuid4().hex[:12],
                content=f"{format_prefix(context.project.label)} Too many pending messages. Please wait.",
                token_usage={},
                context_window=0,
                graph_revision="",
            )
            return

        channel_id = f"queued-{uuid.uuid4().hex[:10]}"
        correlation_id = uuid.uuid4().hex[:12]
        self._create_response_bus(channel_id)
        await q.put((msg.model_copy(update={
            "metadata": {**msg.metadata, "correlation_id": correlation_id},
        }), channel_id))

        queue_position = q.qsize()
        yield ChatQueuedEvent(
            stream_channel_id=channel_id,
            correlation_id=correlation_id,
            queue_position=queue_position,
        )

    async def _enqueue_overflow(
        self, msg: SurfaceMessage, context: ResolvedContext,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Enqueue when max concurrent projects are reached."""
        if self._global_queue.full():
            yield ChatCompleteEvent(
                message_id=uuid.uuid4().hex[:12],
                content=f"{format_prefix(context.project.label)} System is at capacity. Please try again shortly.",
                token_usage={},
                context_window=0,
                graph_revision="",
            )
            return
        channel_id = f"queued-{uuid.uuid4().hex[:10]}"
        correlation_id = uuid.uuid4().hex[:12]
        self._create_response_bus(channel_id)
        priority = self._classify_priority(
            msg.text,
            getattr(self._concierge, "_concierge_state", None)
            and getattr(self._concierge._concierge_state, "active_goals", None),
        )
        await self._global_queue.put(
            (msg.model_copy(update={
                "metadata": {**msg.metadata, "correlation_id": correlation_id},
            }), channel_id),
            priority=int(priority),
        )

        queue_position = self._global_queue.qsize()
        yield ChatQueuedEvent(
            stream_channel_id=channel_id,
            correlation_id=correlation_id,
            queue_position=queue_position,
        )

    async def _execute_and_forward(
        self,
        msg: SurfaceMessage,
        target: asyncio.Queue[ChatStreamEvent | None],
    ) -> None:
        """Run ``concierge.process()`` and forward events to a queue."""
        try:
            async for event in self._concierge.process(msg):
                await target.put(event)
        except Exception as exc:
            logger.exception("Concierge.process() failed")
            error_kind = type(exc).__name__
            await target.put(ChatCompleteEvent(
                message_id=uuid.uuid4().hex[:12],
                content=f"An error occurred ({error_kind}). Please try again or check server logs.",
                token_usage={},
                context_window=0,
                graph_revision="",
            ))

    # ------------------------------------------------------------------
    # Queue draining
    # ------------------------------------------------------------------

    async def _drain_project_queue(self, project_id: str) -> None:
        """Process queued same-project messages serially."""
        q = self._project_queues.get(project_id)
        if q is None:
            return

        while not q.empty():
            queued_msg, channel_id = await q.get()
            bus = self._get_or_create_bus(channel_id)
            try:
                async for event in self._concierge.process(queued_msg):
                    await bus.put(event)
            except Exception as exc:
                logger.exception("Failed processing queued message for project %s", project_id)
                error_kind = type(exc).__name__
                await bus.put(ChatCompleteEvent(
                    message_id=uuid.uuid4().hex[:12],
                    content=f"An error occurred ({error_kind}). Please try again.",
                    token_usage={},
                    context_window=0,
                    graph_revision="",
                ))
            await bus.put(None)

        self._project_queues.pop(project_id, None)

    async def _drain_global_overflow(self) -> None:
        """Pick up overflow-queued messages once a project slot frees up."""
        while not self._global_queue.empty() and (
            self._resource_tracker.available
            if self._resource_tracker is not None
            else len(self._active_tasks) < self._max_concurrent
        ):
            queued_msg, channel_id = self._global_queue.get_nowait()
            context = self._concierge._resolve_context(queued_msg)
            project_id = context.project.project_id
            bus = self._get_or_create_bus(channel_id)

            if project_id in self._active_tasks and not self._active_tasks[project_id].done():
                if project_id not in self._project_queues:
                    self._project_queues[project_id] = asyncio.Queue(maxsize=self._max_queue_depth)
                try:
                    self._project_queues[project_id].put_nowait((queued_msg, channel_id))
                except asyncio.QueueFull:
                    await bus.put(ChatCompleteEvent(
                        message_id=uuid.uuid4().hex[:12],
                        content="Too many pending messages for this project.",
                        token_usage={}, context_window=0, graph_revision="",
                    ))
                    await bus.put(None)
                continue

            if self._resource_tracker is not None:
                acquired = await self._resource_tracker.try_acquire("run")
                if not acquired:
                    await self._global_queue.put(
                        (queued_msg, channel_id),
                        priority=int(self._classify_priority(queued_msg.text)),
                    )
                    break

            task = asyncio.create_task(
                self._process_overflow_message(project_id, queued_msg, bus),
            )
            self._active_tasks[project_id] = task
            self._track_task(task)

    async def _process_overflow_message(
        self, project_id: str, msg: SurfaceMessage, bus: asyncio.Queue[ChatStreamEvent | None],
    ) -> None:
        """Process a single overflow-queued message, then drain any same-project follow-ups."""
        try:
            async for event in self._concierge.process(msg):
                await bus.put(event)
        except Exception as exc:
            logger.exception("Failed processing overflow message for project %s", project_id)
            error_kind = type(exc).__name__
            await bus.put(ChatCompleteEvent(
                message_id=uuid.uuid4().hex[:12],
                content=f"An error occurred ({error_kind}). Please try again.",
                token_usage={},
                context_window=0,
                graph_revision="",
            ))
        finally:
            await bus.put(None)
        try:
            await self._drain_project_queue(project_id)
        except Exception:
            logger.exception("Drain failed after overflow for project %s", project_id)
        finally:
            self._active_tasks.pop(project_id, None)
            if self._resource_tracker is not None:
                await self._resource_tracker.release("run")
        try:
            await self._drain_global_overflow()
        except Exception:
            logger.exception("Overflow drain failed after project %s", project_id)

    # ------------------------------------------------------------------
    # Response bus management
    # ------------------------------------------------------------------

    def _create_response_bus(self, channel_id: str) -> asyncio.Queue:
        bus: asyncio.Queue = asyncio.Queue()
        self._response_buses[channel_id] = (bus, time.monotonic())
        self._response_bus_pins.setdefault(channel_id, 0)
        return bus

    def _get_or_create_bus(self, channel_id: str) -> asyncio.Queue:
        entry = self._response_buses.get(channel_id)
        if entry is not None:
            bus, _ts = entry
            self._response_buses[channel_id] = (bus, time.monotonic())
            return bus
        return self._create_response_bus(channel_id)

    def get_response_bus(self, channel_id: str) -> asyncio.Queue[ChatStreamEvent | None] | None:
        """Retrieve the response bus for a queued message's stream channel."""
        entry = self._response_buses.get(channel_id)
        if entry is None:
            return None
        bus, _ts = entry
        self._response_buses[channel_id] = (bus, time.monotonic())
        self._response_bus_pins[channel_id] = self._response_bus_pins.get(channel_id, 0) + 1
        return bus

    def cleanup_response_bus(self, channel_id: str) -> None:
        """Remove a response bus after the consumer has drained it."""
        pins = self._response_bus_pins.get(channel_id, 0)
        if pins > 1:
            self._response_bus_pins[channel_id] = pins - 1
            return
        self._response_bus_pins.pop(channel_id, None)
        self._response_buses.pop(channel_id, None)

    def _reap_stale_buses(self) -> None:
        """Remove response buses older than TTL (guards against leaked consumers)."""
        now = time.monotonic()
        stale = [
            k
            for k, (_, ts) in self._response_buses.items()
            if now - ts > self._bus_ttl_seconds
            and self._response_bus_pins.get(k, 0) <= 0
        ]
        for k in stale:
            self._response_buses.pop(k, None)
            self._response_bus_pins.pop(k, None)

    # ------------------------------------------------------------------
    # Background task tracking (prevent GC warnings)
    # ------------------------------------------------------------------

    def _track_task(self, task: asyncio.Task) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Cancel all active tasks and drain queues."""
        for task in self._active_tasks.values():
            if not task.done():
                task.cancel()
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        all_tasks = list(self._active_tasks.values()) + list(self._background_tasks)
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)
        self._active_tasks.clear()
        self._project_queues.clear()
        self._response_buses.clear()
        self._background_tasks.clear()
