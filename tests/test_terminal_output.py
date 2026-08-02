"""Tests for terminal output event collapsing."""

from __future__ import annotations

import pytest

from dan.server.chat.events import ChatCompleteEvent, ChatNoticeEvent
from dan.server.terminal_output import collect_terminal_content


async def _stream(events):
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_collect_terminal_content_ignores_non_terminal_notices() -> None:
    result = await collect_terminal_content(
        _stream(
            [
                ChatNoticeEvent(
                    content="Some cited claims could not be verified.",
                    level="warning",
                ),
                ChatCompleteEvent(
                    message_id="m1",
                    content="Final answer",
                    token_usage={},
                    context_window=0,
                    graph_revision="",
                ),
            ]
        )
    )

    assert result == "Final answer"
