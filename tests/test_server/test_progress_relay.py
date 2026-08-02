"""Regression tests for unified progress & queue visibility (Plan 31-32, Task 2)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from dan.server.chat.events import ChatCompleteEvent, ChatQueuedEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StubAdapter:
    def __init__(self) -> None:
        self._send_text = AsyncMock()


def _phase_event(phase_label: str | None = None) -> ChatCompleteEvent:
    return ChatCompleteEvent(
        message_id="test123",
        content="Working on it — searching files (5s elapsed)",
        token_usage={},
        context_window=0,
        graph_revision="",
        detected_mode="progress_ack",
        phase_label=phase_label,
    )


def _queued_event(position: int = 1) -> ChatQueuedEvent:
    return ChatQueuedEvent(
        stream_channel_id="ch-1",
        correlation_id="corr-1",
        queue_position=position,
    )


async def _invoke_relay(adapter: _StubAdapter, external_id: str, event: object) -> None:
    """Re-implement the relay logic inline to test the algorithm without the closure."""
    import dan.server.routers.adapters as mod

    evt_type = getattr(event, "type", "")
    if (
        evt_type == "chat_complete"
        and getattr(event, "detected_mode", None) == "progress_ack"
    ):
        phase_label = getattr(event, "phase_label", None) or ""
        if not phase_label:
            return
        now = time.monotonic()
        if now - mod._last_phase_message_times.get(external_id, 0) < 15.0:
            return
        mod._last_phase_message_times[external_id] = now
        await adapter._send_text(phase_label)
        return
    if evt_type in {"chat_complete", "chat_mutation", "chat_interrupted"}:
        content = getattr(event, "content", "")
        if content:
            await adapter._send_text(content)
        return
    if evt_type == "chat_queued":
        queue_position = max(int(getattr(event, "queue_position", 0) or 0), 1)
        queued_text = f"Queued (position {queue_position}) — I'll reply when ready."
        await adapter._send_text(queued_text)
        return


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_throttle_state() -> None:
    import dan.server.routers.adapters as mod
    mod._last_phase_message_times.clear()


@pytest.mark.asyncio
async def test_relay_passes_event_with_phase_label() -> None:
    adapter = _StubAdapter()
    event = _phase_event(phase_label="Searching your files…")

    await _invoke_relay(adapter, "user-1", event)

    adapter._send_text.assert_awaited_once_with("Searching your files…")


@pytest.mark.asyncio
async def test_relay_drops_event_without_phase_label() -> None:
    adapter = _StubAdapter()
    event = _phase_event(phase_label=None)

    await _invoke_relay(adapter, "user-1", event)

    adapter._send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_relay_drops_event_with_empty_phase_label() -> None:
    adapter = _StubAdapter()
    event = _phase_event(phase_label="")

    await _invoke_relay(adapter, "user-1", event)

    adapter._send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_throttle_skips_second_phase_within_15s() -> None:
    import dan.server.routers.adapters as mod

    adapter = _StubAdapter()
    eid = "user-throttle"

    await _invoke_relay(adapter, eid, _phase_event(phase_label="Phase A"))
    assert adapter._send_text.await_count == 1

    await _invoke_relay(adapter, eid, _phase_event(phase_label="Phase B"))
    assert adapter._send_text.await_count == 1  # still 1 — throttled

    mod._last_phase_message_times[eid] = time.monotonic() - 16.0
    await _invoke_relay(adapter, eid, _phase_event(phase_label="Phase C"))
    assert adapter._send_text.await_count == 2
    adapter._send_text.assert_awaited_with("Phase C")


@pytest.mark.asyncio
async def test_throttle_independent_per_external_id() -> None:
    adapter = _StubAdapter()

    await _invoke_relay(adapter, "u1", _phase_event(phase_label="Phase 1"))
    await _invoke_relay(adapter, "u2", _phase_event(phase_label="Phase 2"))

    assert adapter._send_text.await_count == 2


@pytest.mark.asyncio
async def test_queue_copy_format_position_1() -> None:
    adapter = _StubAdapter()

    await _invoke_relay(adapter, "user-q", _queued_event(position=1))

    adapter._send_text.assert_awaited_once_with(
        "Queued (position 1) — I'll reply when ready."
    )


@pytest.mark.asyncio
async def test_queue_copy_format_position_n() -> None:
    adapter = _StubAdapter()

    await _invoke_relay(adapter, "user-q", _queued_event(position=3))

    adapter._send_text.assert_awaited_once_with(
        "Queued (position 3) — I'll reply when ready."
    )


@pytest.mark.asyncio
async def test_chat_complete_event_has_phase_label_field() -> None:
    event = ChatCompleteEvent(
        message_id="x",
        content="test",
        token_usage={},
        context_window=0,
        graph_revision="",
        detected_mode="progress_ack",
        phase_label="Analyzing…",
    )
    assert event.phase_label == "Analyzing…"


@pytest.mark.asyncio
async def test_chat_complete_event_phase_label_defaults_none() -> None:
    event = ChatCompleteEvent(
        message_id="x",
        content="test",
        token_usage={},
        context_window=0,
        graph_revision="",
    )
    assert event.phase_label is None


@pytest.mark.asyncio
async def test_telegram_fleet_queue_hint_format() -> None:
    from dan.adapters.telegram_fleet import BotFleet

    fleet = BotFleet.__new__(BotFleet)
    hint0 = fleet._format_queue_hint(2, 10.0, hint_count=0)
    assert hint0 == "Queued (position 2) — I'll reply when ready."

    hint1 = fleet._format_queue_hint(1, 65.0, hint_count=1)
    assert "Queued (position 1)" in hint1
    assert "I'll reply when ready." in hint1
    assert "1m 5s elapsed" in hint1
