from __future__ import annotations

import asyncio
import time

import pytest

import dan.server.routers.chat as chat_router_module
from dan.server.routers.chat import ReconnectableChatStream


@pytest.mark.asyncio
async def test_reap_stale_chat_streams_preserves_active_producer():
    channel_id = "chat-test-active"
    queue = ReconnectableChatStream()

    async def _producer() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(_producer())
    chat_router_module._register_chat_stream(channel_id, queue, task=task)
    chat_router_module._chat_streams[channel_id] = (
        queue,
        time.monotonic() - chat_router_module._CHAT_STREAM_TTL_SECONDS - 5,
    )

    chat_router_module._reap_stale_chat_streams()

    assert channel_id in chat_router_module._chat_streams
    assert channel_id in chat_router_module._chat_produce_tasks

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    chat_router_module._chat_streams.pop(channel_id, None)
    chat_router_module._chat_produce_tasks.pop(channel_id, None)
