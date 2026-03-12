"""Reconnect-aware in-memory buffering for chat and run event streams."""

from __future__ import annotations

import asyncio
import copy
from collections import deque
from typing import Any

_TERMINAL_CHAT_EVENT_TYPES = frozenset(
    {"chat_complete", "chat_mutation", "chat_error", "chat_interrupted"}
)
_TERMINAL_RUN_EVENT_TYPES = frozenset(
    {"run_completed", "run_failed", "run_cancelled"}
)


class ReconnectableChatStream:
    """Bounded event buffer that can replay a terminal snapshot on reconnect."""

    def __init__(self, *, max_buffered_events: int = 128) -> None:
        self._items: deque[Any] = deque()
        self._not_empty = asyncio.Event()
        self._consumer_count = 0
        self._max_buffered_events = max(2, int(max_buffered_events))
        self._terminal_snapshot: Any | None = None

    @property
    def has_consumer(self) -> bool:
        return self._consumer_count > 0

    def attach_consumer(self) -> None:
        self._consumer_count += 1

    def detach_consumer(self) -> None:
        if self._consumer_count > 0:
            self._consumer_count -= 1

    def empty(self) -> bool:
        return not self._items

    def qsize(self) -> int:
        return len(self._items)

    def has_reconnect_state(self) -> bool:
        return bool(self._items) or self._terminal_snapshot is not None

    async def put(self, event: Any) -> None:
        self.put_nowait(event)

    def put_nowait(self, event: Any) -> None:
        self._capture_terminal_snapshot(event)
        if self.has_consumer:
            self._items.append(self._clone_event(event))
        else:
            self._buffer_while_detached(event)
        self._not_empty.set()

    async def get(self) -> Any:
        while not self._items:
            await self._not_empty.wait()
            if not self._items:
                self._not_empty.clear()
        item = self._items.popleft()
        if not self._items:
            self._not_empty.clear()
        return item

    def requeue_front(self, event: Any) -> None:
        self._capture_terminal_snapshot(event)
        self._items.appendleft(self._clone_event(event))
        self._not_empty.set()

    def prime_reconnect_snapshot(self) -> None:
        """Replay the terminal snapshot when reconnecting after the queue drained."""
        if self._items or self._terminal_snapshot is None:
            return
        self._items.append(self._clone_event(self._terminal_snapshot))
        self._items.append(None)
        self._not_empty.set()

    @staticmethod
    def _clone_event(event: Any) -> Any:
        if isinstance(event, (dict, list)):
            return copy.deepcopy(event)
        return event

    def _buffer_while_detached(self, event: Any) -> None:
        if event is None:
            if not self._items or self._items[-1] is not None:
                self._items.append(None)
            self._trim_detached_buffer()
            return

        if self._is_token_event(event):
            for idx in range(len(self._items) - 1, -1, -1):
                existing = self._items[idx]
                if existing is None:
                    continue
                if self._is_token_event(existing):
                    self._items[idx] = self._clone_event(event)
                    self._trim_detached_buffer()
                    return
                break

        self._items.append(self._clone_event(event))
        self._trim_detached_buffer()

    def _trim_detached_buffer(self) -> None:
        while len(self._items) > self._max_buffered_events:
            token_idx = self._find_oldest_token_index()
            if token_idx is not None:
                del self._items[token_idx]
                continue
            self._items.popleft()

    def _find_oldest_token_index(self) -> int | None:
        for idx, item in enumerate(self._items):
            if self._is_token_event(item):
                return idx
        return None

    @staticmethod
    def _is_token_event(event: Any) -> bool:
        return isinstance(event, dict) and event.get("type") == "chat_token"

    def _capture_terminal_snapshot(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        event_type = event.get("type")
        if event_type in _TERMINAL_CHAT_EVENT_TYPES:
            self._terminal_snapshot = self._clone_event(event)
            return
        if event_type == "chat_run_event":
            run_event = event.get("run_event")
            if (
                isinstance(run_event, dict)
                and run_event.get("event_type") in _TERMINAL_RUN_EVENT_TYPES
            ):
                self._terminal_snapshot = self._clone_event(event)
