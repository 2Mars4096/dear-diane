"""Tests for RateLimiter and publish session event subscriptions."""

from __future__ import annotations

import asyncio
import time

import pytest

from dan.publish.http_server import RateLimiter
from dan.publish.session import PublishSessionStore, SessionStatus


class TestRateLimiter:
    def test_allows_under_limit(self):
        rl = RateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert rl.check("wf1") is True

    def test_blocks_over_limit(self):
        rl = RateLimiter(max_requests=3, window_seconds=60)
        assert rl.check("wf1") is True
        assert rl.check("wf1") is True
        assert rl.check("wf1") is True
        assert rl.check("wf1") is False

    def test_separate_workflows(self):
        rl = RateLimiter(max_requests=2, window_seconds=60)
        assert rl.check("wf1") is True
        assert rl.check("wf1") is True
        assert rl.check("wf1") is False
        assert rl.check("wf2") is True

    def test_window_expiry(self):
        rl = RateLimiter(max_requests=1, window_seconds=0)
        assert rl.check("wf1") is True
        assert rl.check("wf1") is True


class TestSessionEvents:
    @pytest.mark.asyncio
    async def test_emit_event_no_subscribers(self):
        store = PublishSessionStore()
        store.emit_event("nonexistent", {"type": "test"})

    @pytest.mark.asyncio
    async def test_set_result_emits_event(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        sid = session.session_id

        events: list[dict] = []
        queue: asyncio.Queue = asyncio.Queue()
        store._event_queues.setdefault(sid, []).append(queue)

        await store.set_result(sid, {"output": "done"})
        assert not queue.empty()
        ev = queue.get_nowait()
        assert ev["type"] == "session_completed"

    @pytest.mark.asyncio
    async def test_set_error_emits_event(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        sid = session.session_id

        queue: asyncio.Queue = asyncio.Queue()
        store._event_queues.setdefault(sid, []).append(queue)

        await store.set_error(sid, "boom")
        ev = queue.get_nowait()
        assert ev["type"] == "session_failed"
        assert ev["error"] == "boom"

    @pytest.mark.asyncio
    async def test_update_status_emits_event(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        sid = session.session_id

        queue: asyncio.Queue = asyncio.Queue()
        store._event_queues.setdefault(sid, []).append(queue)

        await store.update_status(sid, SessionStatus.AWAITING_INPUT)
        ev = queue.get_nowait()
        assert ev["type"] == "status_changed"
        assert ev["status"] == "awaiting_input"

    @pytest.mark.asyncio
    async def test_subscribe_events_terminates_on_complete(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        sid = session.session_id

        collected: list[dict] = []

        async def subscriber():
            async for event in store.subscribe_events(sid):
                collected.append(event)

        task = asyncio.create_task(subscriber())
        await asyncio.sleep(0.05)
        await store.set_result(sid, {"ok": True})
        await asyncio.sleep(0.2)

        if not task.done():
            await asyncio.wait_for(task, timeout=3.0)

        assert any(e["type"] == "session_end" for e in collected)
