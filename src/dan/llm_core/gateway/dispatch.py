"""Shared in-process dispatch broker for gateway-backed LLM calls."""

from __future__ import annotations

import asyncio
import time
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from dan.llm_core.config import GatewayConfig


def _positive_int(value: int | None) -> int | None:
    if value is None:
        return None
    return value if value > 0 else None


def _positive_float(value: float | None) -> float | None:
    if value is None:
        return None
    return value if value > 0 else None


@dataclass(slots=True)
class DispatchLease:
    """Lease returned after the broker admits an outbound model call."""

    dispatch_group: str
    queue_depth_at_submit: int
    wait_ms: float


class GatewayDispatcher:
    """Fair, loop-local dispatcher shared by gateway instances."""

    def __init__(
        self,
        *,
        dispatch_group: str,
        max_in_flight: int | None,
        max_queue_size: int | None,
        max_requests_per_second: float | None,
        queue_timeout_seconds: float | None,
    ) -> None:
        self._dispatch_group = dispatch_group
        self._max_in_flight = _positive_int(max_in_flight)
        self._max_queue_size = _positive_int(max_queue_size)
        self._max_requests_per_second = _positive_float(max_requests_per_second)
        self._queue_timeout_seconds = _positive_float(queue_timeout_seconds)
        self._min_interval_seconds = (
            1.0 / self._max_requests_per_second
            if self._max_requests_per_second
            else 0.0
        )
        self._condition = asyncio.Condition()
        self._in_flight = 0
        self._queued_count = 0
        self._next_ticket = 0
        self._current_ticket = 0
        self._cancelled_tickets: set[int] = set()
        self._next_dispatch_at = 0.0

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[DispatchLease]:
        lease = await self.acquire()
        try:
            yield lease
        finally:
            await self.release()

    async def acquire(self) -> DispatchLease:
        started_at = time.monotonic()
        async with self._condition:
            queue_depth_at_submit = self._queued_count
            immediate_ready = (
                self._queued_count == 0
                and self._has_capacity_locked()
                and self._rate_ready_locked(started_at)
            )
            if (
                not immediate_ready
                and self._max_queue_size is not None
                and self._queued_count >= self._max_queue_size
            ):
                raise RuntimeError(
                    "Gateway dispatch queue is full "
                    f"for group {self._dispatch_group!r} "
                    f"({self._queued_count}/{self._max_queue_size} queued)"
                )

            ticket = self._next_ticket
            self._next_ticket += 1
            self._queued_count += 1
            deadline = (
                started_at + self._queue_timeout_seconds
                if self._queue_timeout_seconds
                else None
            )

            while True:
                self._advance_cancelled_locked()
                now = time.monotonic()
                if (
                    ticket == self._current_ticket
                    and self._has_capacity_locked()
                    and self._rate_ready_locked(now)
                ):
                    self._queued_count -= 1
                    self._current_ticket += 1
                    self._advance_cancelled_locked()
                    self._in_flight += 1
                    if self._min_interval_seconds > 0:
                        self._next_dispatch_at = now + self._min_interval_seconds
                    self._condition.notify_all()
                    return DispatchLease(
                        dispatch_group=self._dispatch_group,
                        queue_depth_at_submit=queue_depth_at_submit,
                        wait_ms=(now - started_at) * 1000,
                    )

                wait_timeout: float | None = None
                if deadline is not None:
                    remaining = deadline - now
                    if remaining <= 0:
                        self._cancel_ticket_locked(ticket)
                        raise asyncio.TimeoutError(
                            "Gateway dispatch queue wait timed out "
                            f"after {self._queue_timeout_seconds}s "
                            f"for group {self._dispatch_group!r}"
                        )
                    wait_timeout = remaining

                if (
                    ticket == self._current_ticket
                    and self._has_capacity_locked()
                    and not self._rate_ready_locked(now)
                ):
                    rate_wait = max(self._next_dispatch_at - now, 0.0)
                    if wait_timeout is None or rate_wait < wait_timeout:
                        wait_timeout = rate_wait

                try:
                    if wait_timeout is None:
                        await self._condition.wait()
                    elif wait_timeout > 0:
                        await asyncio.wait_for(
                            self._condition.wait(),
                            timeout=wait_timeout,
                        )
                    else:
                        await asyncio.sleep(0)
                except asyncio.TimeoutError:
                    if deadline is None or time.monotonic() < deadline:
                        continue
                    self._cancel_ticket_locked(ticket)
                    raise asyncio.TimeoutError(
                        "Gateway dispatch queue wait timed out "
                        f"after {self._queue_timeout_seconds}s "
                        f"for group {self._dispatch_group!r}"
                    )
                except asyncio.CancelledError:
                    self._cancel_ticket_locked(ticket)
                    raise

    async def release(self) -> None:
        async with self._condition:
            if self._in_flight > 0:
                self._in_flight -= 1
            self._condition.notify_all()

    def _advance_cancelled_locked(self) -> None:
        while self._current_ticket in self._cancelled_tickets:
            self._cancelled_tickets.remove(self._current_ticket)
            self._current_ticket += 1

    def _cancel_ticket_locked(self, ticket: int) -> None:
        if self._queued_count > 0:
            self._queued_count -= 1
        if ticket >= self._current_ticket:
            self._cancelled_tickets.add(ticket)
        self._advance_cancelled_locked()
        self._condition.notify_all()

    def _has_capacity_locked(self) -> bool:
        if self._max_in_flight is None:
            return True
        return self._in_flight < self._max_in_flight

    def _rate_ready_locked(self, now: float) -> bool:
        if self._min_interval_seconds <= 0:
            return True
        return now >= self._next_dispatch_at


_SHARED_DISPATCHERS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    dict[tuple[str, int | None, int | None, float | None, float | None], GatewayDispatcher],
] = weakref.WeakKeyDictionary()


def get_shared_dispatcher(config: GatewayConfig) -> GatewayDispatcher | None:
    """Return a loop-local shared dispatcher for the given gateway config."""
    if not config.dispatch_enabled:
        return None

    max_in_flight = _positive_int(config.dispatch_max_in_flight)
    max_queue_size = _positive_int(config.dispatch_max_queue_size)
    max_requests_per_second = _positive_float(
        config.dispatch_max_requests_per_second
    )
    queue_timeout_seconds = _positive_float(
        config.dispatch_queue_timeout_seconds
    )
    if max_in_flight is None and max_requests_per_second is None:
        return None

    loop = asyncio.get_running_loop()
    group = (config.dispatch_group or "default").strip() or "default"
    key = (
        group,
        max_in_flight,
        max_queue_size,
        max_requests_per_second,
        queue_timeout_seconds,
    )
    dispatchers = _SHARED_DISPATCHERS.setdefault(loop, {})
    dispatcher = dispatchers.get(key)
    if dispatcher is None:
        dispatcher = GatewayDispatcher(
            dispatch_group=group,
            max_in_flight=max_in_flight,
            max_queue_size=max_queue_size,
            max_requests_per_second=max_requests_per_second,
            queue_timeout_seconds=queue_timeout_seconds,
        )
        dispatchers[key] = dispatcher
    return dispatcher
