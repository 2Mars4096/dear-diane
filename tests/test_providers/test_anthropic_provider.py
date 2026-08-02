"""Tests for AnthropicProvider — message format and structured replay."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.providers import CompletionResult, ProviderConfig
from dan.providers.anthropic_provider import AnthropicProvider


class _FakeSearchBlock:
    type = "search_result"
    title = "Example result"
    url = "https://example.com/report"
    source = "web_search"
    cited_text = "Revenue grew 24% in 2025."
    content = [{"type": "text", "text": "Revenue grew 24% in 2025."}]
    citations = {"enabled": True}


def test_serialize_assistant_message_preserves_search_result_citation_fields():
    message = AnthropicProvider._serialize_assistant_message(
        text="",
        tool_calls=[],
        content_blocks=[_FakeSearchBlock()],
    )

    assert message["anthropic_content"] == [{
        "type": "search_result",
        "title": "Example result",
        "url": "https://example.com/report",
        "source": "web_search",
        "cited_text": "Revenue grew 24% in 2025.",
        "content": [{"type": "text", "text": "Revenue grew 24% in 2025."}],
        "citations": {"enabled": True},
    }]


def test_convert_messages_preserves_structured_tool_result_blocks():
    system_text, converted = AnthropicProvider._convert_messages([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "toolu_1",
                "function": {
                    "name": "web_search",
                    "arguments": "{}",
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "toolu_1",
            "content": "fallback text",
            "anthropic_tool_result_content": [{
                "type": "search_result",
                "title": "Example result",
                "url": "https://example.com/report",
                "source": "web_search",
                "citations": {"enabled": True},
            }],
        },
    ])

    assert system_text is None
    assert converted == [
        {
            "role": "assistant",
            "content": [{
                "type": "tool_use",
                "id": "toolu_1",
                "name": "web_search",
                "input": {},
            }],
        },
        {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": [{
                    "type": "search_result",
                    "title": "Example result",
                    "url": "https://example.com/report",
                    "source": "web_search",
                    "citations": {"enabled": True},
                }],
            }],
        },
    ]


def test_convert_messages_translates_openai_image_block() -> None:
    _system, messages = AnthropicProvider._convert_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "review this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
    )

    content = messages[0]["content"]
    assert content[0] == {"type": "text", "text": "review this"}
    assert content[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
    }


class TestAnthropicSystemExtraction:
    def test_split_system_extracts_system_messages(self):
        from dan.providers.anthropic_provider import AnthropicProvider

        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "Be concise"},
        ]
        system_text, non_system = AnthropicProvider._split_system(messages)
        assert system_text == "You are helpful\n\nBe concise"
        assert len(non_system) == 1
        assert non_system[0]["role"] == "user"

    def test_split_system_no_system_messages(self):
        from dan.providers.anthropic_provider import AnthropicProvider

        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        system_text, non_system = AnthropicProvider._split_system(messages)
        assert system_text is None
        assert len(non_system) == 2


@pytest.mark.asyncio
async def test_complete_returns_completion_result():
    from dan.providers.anthropic_provider import AnthropicProvider

    mock_client = AsyncMock()

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Hello from Claude"

    usage_mock = MagicMock()
    usage_mock.input_tokens = 15
    usage_mock.output_tokens = 25

    resp = MagicMock()
    resp.content = [text_block]
    resp.usage = usage_mock

    mock_client.messages.create = AsyncMock(return_value=resp)

    provider = object.__new__(AnthropicProvider)
    provider._client = mock_client
    provider._timeout_seconds = 600

    result = await provider.complete(
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        model="claude-sonnet-4",
    )

    assert isinstance(result, CompletionResult)
    assert result.text == "Hello from Claude"
    assert result.usage["prompt_tokens"] == 15
    assert result.usage["completion_tokens"] == 25

    call_args = mock_client.messages.create.call_args
    assert call_args.kwargs.get("system") == "sys"
    assert all(m["role"] != "system" for m in call_args.kwargs["messages"])


def test_import_error_message():
    """Verify clear error when anthropic package is not installed."""
    import importlib

    saved = sys.modules.get("anthropic")
    sys.modules["anthropic"] = None  # type: ignore

    try:
        from dan.providers.anthropic_provider import AnthropicProvider
        with pytest.raises(ImportError, match="anthropic"):
            AnthropicProvider(ProviderConfig(api_key="test"))
    finally:
        if saved is not None:
            sys.modules["anthropic"] = saved
        else:
            sys.modules.pop("anthropic", None)
