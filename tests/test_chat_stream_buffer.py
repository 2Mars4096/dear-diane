from __future__ import annotations

import pytest

from dan.server.chat_stream_buffer import (
    ReconnectableChatStream,
    should_preserve_chat_stream,
)


@pytest.mark.asyncio
async def test_terminal_snapshot_replays_after_queue_drains() -> None:
    stream = ReconnectableChatStream(max_buffered_events=4)
    terminal = {
        "type": "chat_complete",
        "message_id": "msg-1",
        "content": "done",
    }

    await stream.put(terminal)
    await stream.put(None)

    assert stream.has_reconnect_state() is True
    assert await stream.get() == terminal
    assert await stream.get() is None
    assert stream.empty() is True

    stream.prime_reconnect_snapshot()

    assert await stream.get() == terminal
    assert await stream.get() is None


@pytest.mark.asyncio
async def test_detached_buffer_is_bounded_and_coalesces_tokens() -> None:
    stream = ReconnectableChatStream(max_buffered_events=3)

    await stream.put({"type": "chat_token", "delta": "a", "accumulated": "a"})
    await stream.put({"type": "chat_token", "delta": "b", "accumulated": "ab"})
    await stream.put({"type": "chat_tool_call_start", "tool_call_id": "tc-1"})
    await stream.put({"type": "chat_token", "delta": "c", "accumulated": "abc"})
    await stream.put({"type": "chat_tool_call_result", "tool_call_id": "tc-1"})

    assert stream.qsize() <= 3

    drained = [await stream.get() for _ in range(stream.qsize())]
    token_events = [evt for evt in drained if isinstance(evt, dict) and evt.get("type") == "chat_token"]

    assert len(token_events) == 1
    assert token_events[0]["accumulated"] == "abc"
    assert drained[-1]["type"] == "chat_tool_call_result"


def test_should_preserve_stream_after_terminal_delivery() -> None:
    stream = ReconnectableChatStream(max_buffered_events=4)
    stream.put_nowait({"type": "chat_complete", "message_id": "msg-1", "content": "done"})
    stream.put_nowait(None)

    assert should_preserve_chat_stream(stream, producer_running=False) is True


def test_should_preserve_stream_while_producer_is_active() -> None:
    stream = ReconnectableChatStream(max_buffered_events=4)

    assert should_preserve_chat_stream(stream, producer_running=True) is True
    assert should_preserve_chat_stream(stream, producer_running=False) is False
