from __future__ import annotations

import pytest

from dan.server.routers.chat import ChatMessageRequest


def test_chat_message_request_strips_empty_history_turns() -> None:
    req = ChatMessageRequest(
        workflow_id="wf1",
        message="hello",
        history=[
            {"role": "user", "content": "keep this"},
            {"role": "assistant", "content": ""},
            {"role": "assistant", "content": "   "},
            {"role": "assistant", "content": "keep this too"},
            {"role": "system", "content": "strip this"},
            {"role": "tool", "content": "strip this"},
        ],
    )

    assert req.history == [
        {"role": "user", "content": "keep this"},
        {"role": "assistant", "content": "keep this too"},
    ]


def test_chat_message_request_normalizes_control_plane_mode_alias() -> None:
    req = ChatMessageRequest(
        workflow_id="wf1",
        message="hello",
        control_plane_mode="new",
    )

    assert req.control_plane_mode == "v2"


def test_chat_message_request_accepts_surface_context_control_plane_override() -> None:
    req = ChatMessageRequest(
        workflow_id="wf1",
        message="hello",
        surface_context={"control_plane_mode": "legacy"},
    )

    assert req.control_plane_mode == "v1"


def test_chat_message_request_rejects_conflicting_control_plane_overrides() -> None:
    with pytest.raises(ValueError, match="control_plane_mode conflicts"):
        ChatMessageRequest(
            workflow_id="wf1",
            message="hello",
            control_plane_mode="v2",
            surface_context={"control_plane_mode": "v1"},
        )
