from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dan.server.chat_manager import ChatManager, GraphSummary


@pytest.mark.asyncio
async def test_build_messages_includes_debug_context_in_unified_prompt() -> None:
    manager = ChatManager(
        provider_registry=MagicMock(),
        graph_store=MagicMock(),
    )
    summary = GraphSummary(
        workflow_id="wf-1",
        name="Test",
        description="",
        node_count=0,
        edge_count=0,
        revision="rev-1",
    )

    messages = await manager._build_messages(
        summary=summary,
        user_message="please debug this",
        history=[],
        mode="debug",
        debug_context="Last failed run: run-123\nError: missing file",
        workflow_id="wf-1",
    )

    assert messages[0]["role"] == "system"
    assert "## Recent failures" in messages[0]["content"]
    assert "Last failed run: run-123" in messages[0]["content"]
    assert "Error: missing file" in messages[0]["content"]
