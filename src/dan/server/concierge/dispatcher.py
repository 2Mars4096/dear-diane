"""Concurrent project dispatcher — processes independent projects in parallel,
serializes messages within the same project/task."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, AsyncIterator

from dan.server.chat_manager import ChatCompleteEvent, ChatQueuedEvent, ChatStreamEvent

from .context_resolver import ResolvedContext
from .identity import format_prefix
from .models import SurfaceMessage

logger = logging.getLogger(__name__)


class ConcurrentDispatcher:
    """Wraps a ``Concierge`` to add per-project concurrency.

    * Different projects → processed concurrently (separate asyncio tasks)
    * Same project → messages queued and drained serially so follow-ups
      see the prior result in the task's turn history.
    """

    def __init__(
        self,
        concierge: Any,
        *,
        max_concurrent_projects: int = 5,
        max_queue_depth: int = 20,
    ) -> None:
        self._concierge = concierge
        self._max_concurrent = max_concurrent_projects
        self._max_queue_depth = max_queue_depth

        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._project_queues: dict[str, asyncio.Queue[tuple[SurfaceMessage, str]]] = {}
        self._response_buses: dict[str, tuple[asyncio.Queue[ChatStreamEvent], float]] = {}
        self._bus_ttl_seconds = 300.0
        self._global_queue: asyncio.Queue[tuple[SurfaceMessage, str]] = asyncio.Queue(
            maxsize=max_queue_depth * max_concurrent_projects,
        )
        self._background_tasks: set[asyncio.Task[None]] = set()

    @property
    def concierge(self) -> Any:
        return self._concierge

    async def dispatch(self, msg: SurfaceMessage) -> AsyncIterator[ChatStreamEvent]:
        """Accept a message and either process it immediately or queue it.

        Yields events for the caller:
        * Immediate processing → yields all ``ChatStreamEvent``s from the concierge.
        * Queued (same project busy) → yields a single ``ChatQueuedEvent`` with a
          ``stream_channel_id`` where the real response will appear later.
        """
        self._reap_stale_buses()
        context = self._concierge.context_resolver.resolve(msg)
        project_id = context.project.project_id

        msg = msg.model_copy(update={
            "metadata": {**msg.metadata, "skip_queue": True},
        })

        if project_id in self._active_tasks and not self._active_tasks[project_id].done():
            async for event in self._enqueue_for_project(msg, project_id, context):
                yield event
            return

        if len(self._active_tasks) >= self._max_concurrent:
            async for event in self._enqueue_overflow(msg, context):
                yield event
            return

        async for event in self._process_immediately(msg, project_id):
            yield event

    async def _process_immediately(
        self, msg: SurfaceMessage, project_id: str,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Spawn a processing task for a new project and yield its events."""
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
                await self._drain_global_overflow()
            except Exception:
                logger.exception("Drain failed for project %s", project_id)
            finally:
                self._active_tasks.pop(project_id, None)

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

        yield ChatQueuedEvent(
            stream_channel_id=channel_id,
            correlation_id=correlation_id,
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
        await self._global_queue.put((msg.model_copy(update={
            "metadata": {**msg.metadata, "correlation_id": correlation_id},
        }), channel_id))

        yield ChatQueuedEvent(
            stream_channel_id=channel_id,
            correlation_id=correlation_id,
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
        except Exception:
            logger.exception("Concierge.process() failed")
            await target.put(ChatCompleteEvent(
                message_id=uuid.uuid4().hex[:12],
                content="An internal error occurred while processing your message.",
                token_usage={},
                context_window=0,
                graph_revision="",
            ))

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
            except Exception:
                logger.exception("Failed processing queued message for project %s", project_id)
                await bus.put(ChatCompleteEvent(
                    message_id=uuid.uuid4().hex[:12],
                    content="An internal error occurred while processing a queued message.",
                    token_usage={},
                    context_window=0,
                    graph_revision="",
                ))
            await bus.put(None)

        self._project_queues.pop(project_id, None)

    async def _drain_global_overflow(self) -> None:
        """Pick up overflow-queued messages once a project slot frees up."""
        while not self._global_queue.empty() and len(self._active_tasks) < self._max_concurrent:
            queued_msg, channel_id = await self._global_queue.get()
            context = self._concierge.context_resolver.resolve(queued_msg)
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
        except Exception:
            logger.exception("Failed processing overflow message for project %s", project_id)
            await bus.put(ChatCompleteEvent(
                message_id=uuid.uuid4().hex[:12],
                content="An internal error occurred.",
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

    # ------------------------------------------------------------------
    # Response bus management
    # ------------------------------------------------------------------

    def _create_response_bus(self, channel_id: str) -> asyncio.Queue:
        bus: asyncio.Queue = asyncio.Queue()
        self._response_buses[channel_id] = (bus, time.monotonic())
        return bus

    def _get_or_create_bus(self, channel_id: str) -> asyncio.Queue:
        entry = self._response_buses.get(channel_id)
        if entry is not None:
            return entry[0]
        return self._create_response_bus(channel_id)

    def get_response_bus(self, channel_id: str) -> asyncio.Queue[ChatStreamEvent | None] | None:
        """Retrieve the response bus for a queued message's stream channel."""
        entry = self._response_buses.get(channel_id)
        return entry[0] if entry is not None else None

    def cleanup_response_bus(self, channel_id: str) -> None:
        """Remove a response bus after the consumer has drained it."""
        self._response_buses.pop(channel_id, None)

    def _reap_stale_buses(self) -> None:
        """Remove response buses older than TTL (guards against leaked consumers)."""
        now = time.monotonic()
        stale = [k for k, (_, ts) in self._response_buses.items() if now - ts > self._bus_ttl_seconds]
        for k in stale:
            self._response_buses.pop(k, None)

    # ------------------------------------------------------------------
    # Background task tracking (Issue 3: prevent GC warnings)
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
