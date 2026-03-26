"""Resource-based concurrency tracking and priority queuing for the concierge dispatcher."""
from __future__ import annotations

import asyncio
import heapq
import logging
import os
import time
from enum import IntEnum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resource budget & tracker (Task 7)
# ---------------------------------------------------------------------------

class ResourceBudget(BaseModel):
    """Configurable resource limits for the concierge stack."""

    max_concurrent_llm_calls: int = 10
    max_concurrent_runs: int = 5
    max_memory_mb: int | None = None

    @classmethod
    def from_env(cls) -> ResourceBudget:
        """Build a budget from environment variables, falling back to defaults."""
        kwargs: dict[str, Any] = {}
        llm = os.environ.get("DAN_MAX_CONCURRENT_LLM")
        if llm is not None:
            kwargs["max_concurrent_llm_calls"] = int(llm)
        runs = os.environ.get("DAN_MAX_CONCURRENT_RUNS")
        if runs is not None:
            kwargs["max_concurrent_runs"] = int(runs)
        mem = os.environ.get("DAN_MAX_MEMORY_MB")
        if mem is not None:
            kwargs["max_memory_mb"] = int(mem)
        return cls(**kwargs)


class ResourceTracker:
    """Track active resource usage across the concierge stack.

    All mutations are protected by an asyncio.Lock so concurrent
    ``try_acquire`` / ``release`` calls never race.

    ``"run"`` slots are dispatcher admission slots for the foreground portion
    of a live turn. ``"llm"`` slots reflect actual model-call time and can be
    acquired independently by chat/triage paths around ``provider.complete()``.
    """

    def __init__(self, budget: ResourceBudget | None = None) -> None:
        self._budget = budget or ResourceBudget()
        self._active_llm_calls = 0
        self._active_runs = 0
        self._condition = asyncio.Condition()

    @property
    def budget(self) -> ResourceBudget:
        return self._budget

    async def try_acquire(self, resource_type: str = "run") -> bool:
        """Attempt to acquire a resource slot.

        Returns ``True`` if the resource was acquired, ``False`` if
        the budget is exhausted for *resource_type*.

        Supported types: ``"llm"``, ``"run"`` (default).
        """
        async with self._condition:
            if not self._can_acquire_locked(resource_type):
                return False
            self._increment_locked(resource_type)
            return True

    async def wait_acquire(
        self,
        resource_type: str = "run",
        *,
        timeout: float | None = None,
    ) -> bool:
        """Wait until a resource slot is available, then acquire it."""
        deadline = None if timeout is None else (time.monotonic() + timeout)
        async with self._condition:
            while not self._can_acquire_locked(resource_type):
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return False
                else:
                    await self._condition.wait()
            self._increment_locked(resource_type)
            return True

    async def release(self, resource_type: str = "run") -> None:
        """Release a previously acquired resource slot."""
        async with self._condition:
            if resource_type == "llm":
                self._active_llm_calls = max(0, self._active_llm_calls - 1)
            else:
                self._active_runs = max(0, self._active_runs - 1)
            self._condition.notify_all()

    def _can_acquire_locked(self, resource_type: str) -> bool:
        if resource_type == "llm":
            return self._active_llm_calls < self._budget.max_concurrent_llm_calls
        return self._active_runs < self._budget.max_concurrent_runs

    def _increment_locked(self, resource_type: str) -> None:
        if resource_type == "llm":
            self._active_llm_calls += 1
        else:
            self._active_runs += 1

    @property
    def available(self) -> bool:
        """``True`` if there is capacity for another chat/run slot."""
        return (
            self._active_runs < self._budget.max_concurrent_runs
            and self._active_llm_calls < self._budget.max_concurrent_llm_calls
        )

    @property
    def run_available(self) -> bool:
        """``True`` if there is capacity for another foreground run slot."""
        return self._active_runs < self._budget.max_concurrent_runs

    def snapshot(self) -> dict[str, Any]:
        """Current usage snapshot for status reporting."""
        return {
            "active_llm_calls": self._active_llm_calls,
            "max_llm_calls": self._budget.max_concurrent_llm_calls,
            "active_runs": self._active_runs,
            "max_runs": self._budget.max_concurrent_runs,
            "max_memory_mb": self._budget.max_memory_mb,
            "available": self.available,
        }


# ---------------------------------------------------------------------------
# Priority queuing (Task 8)
# ---------------------------------------------------------------------------

class MessagePriority(IntEnum):
    """Priority levels for queued messages.  Lower value = higher priority."""

    CRITICAL = 0  # status/cancel — always processed immediately
    HIGH = 1      # active goal continuation
    NORMAL = 2    # new requests
    LOW = 3       # background tasks, consolidation


def classify_priority(message: str, active_goals: list[Any] | None = None) -> MessagePriority:
    """Infer priority from message content and goal state.

    Rules (in order):
    1. ``/cancel``, ``/status``, ``/priority critical`` → CRITICAL
    2. ``/priority high`` or ``urgent`` keyword → HIGH
    3. Any active goals → HIGH (continuing an active goal)
    4. ``/priority low`` → LOW
    5. Otherwise → NORMAL
    """
    lower = message.strip().lower()

    if lower.startswith(("/cancel", "/status", "/priority critical")):
        return MessagePriority.CRITICAL
    if lower.startswith("/priority high") or "urgent" in lower:
        return MessagePriority.HIGH
    if active_goals:
        return MessagePriority.HIGH
    if lower.startswith("/priority low"):
        return MessagePriority.LOW
    return MessagePriority.NORMAL


# ---------------------------------------------------------------------------
# Heapq-backed priority queue (FIFO within same priority)
# ---------------------------------------------------------------------------

class PriorityQueue:
    """A bounded priority queue backed by :mod:`heapq`.

    Items with lower ``priority`` value are dequeued first.  Within the
    same priority level, FIFO ordering is preserved via a monotonic counter.
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._heap: list[tuple[int, int, Any]] = []
        self._counter = 0
        self._maxsize = maxsize
        self._not_empty = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def qsize(self) -> int:
        return len(self._heap)

    def empty(self) -> bool:
        return len(self._heap) == 0

    def full(self) -> bool:
        if self._maxsize <= 0:
            return False
        return len(self._heap) >= self._maxsize

    async def put(self, item: Any, priority: int = MessagePriority.NORMAL) -> None:
        """Push *item* with the given *priority* (lower = higher priority)."""
        async with self._lock:
            if self._maxsize > 0 and len(self._heap) >= self._maxsize:
                raise asyncio.QueueFull()
            entry = (priority, self._counter, item)
            self._counter += 1
            heapq.heappush(self._heap, entry)
            self._not_empty.set()

    def put_nowait(self, item: Any, priority: int = MessagePriority.NORMAL) -> None:
        """Non-async push. Raises :class:`asyncio.QueueFull` if at capacity."""
        if self._maxsize > 0 and len(self._heap) >= self._maxsize:
            raise asyncio.QueueFull()
        entry = (priority, self._counter, item)
        self._counter += 1
        heapq.heappush(self._heap, entry)
        self._not_empty.set()

    async def get(self) -> Any:
        """Pop and return the highest-priority (lowest value) item.

        Blocks until an item is available.
        """
        while True:
            async with self._lock:
                if self._heap:
                    _priority, _seq, item = heapq.heappop(self._heap)
                    if not self._heap:
                        self._not_empty.clear()
                    return item
            self._not_empty.clear()
            await self._not_empty.wait()

    def get_nowait(self) -> Any:
        """Pop without blocking. Raises :class:`asyncio.QueueEmpty` if empty."""
        if not self._heap:
            raise asyncio.QueueEmpty()
        _priority, _seq, item = heapq.heappop(self._heap)
        if not self._heap:
            self._not_empty.clear()
        return item
