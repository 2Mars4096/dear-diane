from __future__ import annotations

import pytest

from dan.server.chat_manager import ChatCompleteEvent


async def _event_stream(*events):
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_collect_terminal_content_preserves_meaningful_fragments() -> None:
    from dan.server.terminal_output import collect_terminal_content

    result = await collect_terminal_content(
        _event_stream(
            ChatCompleteEvent(
                message_id="ack",
                content="Got it. Working on your request...",
                graph_revision="",
                detected_mode="progress_ack",
            ),
            ChatCompleteEvent(
                message_id="phase",
                content="Phase 1 complete.",
                graph_revision="",
            ),
            ChatCompleteEvent(
                message_id="reassure",
                content="Still working on it...",
                graph_revision="",
            ),
            ChatCompleteEvent(
                message_id="final",
                content="Final answer.",
                graph_revision="",
            ),
        ),
        reassurance_messages={"Still working on it..."},
    )

    assert result == "Phase 1 complete.\n\nFinal answer."
