"""Global event bus for cross-surface event streaming."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_GLOBAL_SUBSCRIBERS = 50
MAX_QUEUE_SIZE = 1000


class GlobalEventBus:
    """Streams events from all runs to all global subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._filters: dict[str, dict[str, str | None]] = {}

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(
        self,
        subscriber_id: str,
        surface_filter: str | None = None,
        workflow_filter: str | None = None,
    ) -> asyncio.Queue[dict[str, Any]]:
        if len(self._subscribers) >= MAX_GLOBAL_SUBSCRIBERS:
            raise RuntimeError(
                f"Max global subscribers ({MAX_GLOBAL_SUBSCRIBERS}) reached"
            )
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._subscribers[subscriber_id] = queue
        self._filters[subscriber_id] = {
            "surface": surface_filter,
            "workflow": workflow_filter,
        }
        return queue

    def unsubscribe(self, subscriber_id: str) -> None:
        self._subscribers.pop(subscriber_id, None)
        self._filters.pop(subscriber_id, None)

    def broadcast(self, event: dict[str, Any]) -> None:
        for sub_id, queue in list(self._subscribers.items()):
            filters = self._filters.get(sub_id, {})
            if filters.get("surface") and event.get("surface_id") != filters["surface"]:
                continue
            if filters.get("workflow") and event.get("workflow_name") != filters["workflow"]:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
                logger.warning(
                    "Global bus: dropped event for slow subscriber %s", sub_id
                )
