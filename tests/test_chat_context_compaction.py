from __future__ import annotations

import dan.server.chat_manager as chat_manager_module
from dan.server.chat_manager import _compact_context


def _tool_call() -> list[dict[str, object]]:
    return [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "file_read", "arguments": "{}"},
        }
    ]


def test_compact_context_drops_orphan_tool_messages() -> None:
    messages = [
        {"role": "system", "content": "You are DAN."},
        {"role": "user", "content": "Summarize the draft."},
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "orphaned tool result",
        },
        {"role": "assistant", "content": "Done."},
    ]

    compacted = _compact_context(messages, "test-model")

    assert [msg["role"] for msg in compacted] == ["system", "user", "assistant"]
    assert all(msg.get("role") != "tool" for msg in compacted)


def test_compact_context_keeps_valid_assistant_tool_bundle_intact(
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_manager_module, "_get_context_window", lambda _model: 5000)

    large = "x" * 2600
    messages = [
        {"role": "system", "content": "You are DAN."},
        {"role": "user", "content": f"old-user-1 {large}"},
        {"role": "assistant", "content": f"old-assistant-1 {large}"},
        {"role": "user", "content": f"old-user-2 {large}"},
        {"role": "assistant", "content": f"old-assistant-2 {large}"},
        {"role": "user", "content": f"investigate {large}"},
        {"role": "assistant", "content": "", "tool_calls": _tool_call()},
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": f"tool output {large}",
        },
        {"role": "user", "content": f"recent-user {large}"},
        {"role": "assistant", "content": f"recent-assistant {large}"},
    ]

    compacted = _compact_context(messages, "test-model")

    bundle_start = next(
        index
        for index, msg in enumerate(compacted)
        if msg.get("role") == "assistant" and msg.get("tool_calls")
    )
    assert compacted[bundle_start + 1]["role"] == "tool"
    assert compacted[bundle_start + 1]["tool_call_id"] == "call-1"
    assert all(
        msg["role"] != "tool"
        or (
            index > 0
            and compacted[index - 1]["role"] == "assistant"
            and compacted[index - 1].get("tool_calls")
        )
        for index, msg in enumerate(compacted)
    )
