from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dan.providers.anthropic_provider import AnthropicProvider


def _tool_schema() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List directory entries",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        }
    ]


def _make_provider(response: object) -> tuple[AnthropicProvider, AsyncMock]:
    create = AsyncMock(return_value=response)
    provider = object.__new__(AnthropicProvider)
    provider._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    provider._timeout_seconds = 30
    return provider, create


@pytest.mark.asyncio
async def test_anthropic_provider_translates_tool_history_and_exact_choice():
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Done.")],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="end_turn",
    )
    provider, create = _make_provider(response)

    result = await provider.complete(
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "Checking",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "list_directory",
                            "arguments": '{"path":"."}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "file_a\nfile_b"},
        ],
        model="claude-sonnet-4-6",
        tools=_tool_schema(),
        tool_choice={"type": "function", "function": {"name": "list_directory"}},
    )

    assert result.text == "Done."
    kwargs = create.await_args.kwargs
    assert kwargs["system"] == "sys"
    assert kwargs["tool_choice"] == {"type": "tool", "name": "list_directory"}
    assert kwargs["tools"][0]["name"] == "list_directory"
    assert kwargs["messages"][0] == {"role": "user", "content": "hello"}

    assistant_msg = kwargs["messages"][1]
    assert assistant_msg["role"] == "assistant"
    assert assistant_msg["content"][0] == {"type": "text", "text": "Checking"}
    assert assistant_msg["content"][1]["type"] == "tool_use"
    assert assistant_msg["content"][1]["id"] == "call_1"
    assert assistant_msg["content"][1]["name"] == "list_directory"
    assert assistant_msg["content"][1]["input"] == {"path": "."}

    tool_result_msg = kwargs["messages"][2]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["type"] == "tool_result"
    assert tool_result_msg["content"][0]["tool_use_id"] == "call_1"
    assert tool_result_msg["content"][0]["content"] == "file_a\nfile_b"


@pytest.mark.asyncio
async def test_anthropic_provider_parses_tool_use_response():
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Need tool"),
            SimpleNamespace(type="tool_use", id="toolu_1", name="list_directory", input={"path": "."}),
        ],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="tool_use",
    )
    provider, create = _make_provider(response)

    result = await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="claude-sonnet-4-6",
        tools=_tool_schema(),
        tool_choice="required",
    )

    assert result.text == "Need tool"
    assert result.tool_calls == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {
                "name": "list_directory",
                "arguments": '{"path": "."}',
            },
        }
    ]
    assert create.await_args.kwargs["tool_choice"] == {"type": "any"}
    assert result.raw_assistant_message is not None
    assert result.raw_assistant_message["role"] == "assistant"
    assert result.raw_assistant_message["content"] == "Need tool"
    assert result.raw_assistant_message["tool_calls"][0]["function"]["name"] == "list_directory"
    assert result.raw_assistant_message["anthropic_content"][1]["type"] == "tool_use"
    assert result.provider_metadata == {"family": "anthropic"}


@pytest.mark.asyncio
async def test_anthropic_provider_returns_raw_assistant_message_for_text_only_response():
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Hello Claude")],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        stop_reason="end_turn",
    )
    provider, _ = _make_provider(response)

    result = await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="claude-sonnet-4-6",
    )

    assert result.raw_assistant_message is not None
    assert result.raw_assistant_message["role"] == "assistant"
    assert result.raw_assistant_message["content"] == "Hello Claude"
    assert result.raw_assistant_message["anthropic_content"] == [
        {"type": "text", "text": "Hello Claude"}
    ]
