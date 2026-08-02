from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dan.providers import get_model_behavior
from dan.providers.openai_provider import OpenAIProvider


def test_moonshot_host_behavior_disables_explicit_tool_choice() -> None:
    provider, _ = _make_provider(
        SimpleNamespace(choices=[], usage=None),
        base_url="https://api.moonshot.ai/v1",
    )
    behavior = get_model_behavior(provider, "custom-tool-model")

    assert behavior.supports_tool_calls is True
    assert behavior.supports_exact_tool_choice is False
    assert behavior.supports_required_tool_choice is False


def test_standard_openai_model_behavior_keeps_explicit_tool_choice() -> None:
    provider, _ = _make_provider(SimpleNamespace(choices=[], usage=None))
    behavior = get_model_behavior(provider, "gpt-5.4")

    assert behavior.supports_exact_tool_choice is True
    assert behavior.supports_required_tool_choice is True


class _AsyncStream:
    def __init__(self, chunks: list[object]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self) -> _AsyncStream:
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _make_provider(
    response: object,
    *,
    base_url: str | None = None,
) -> tuple[OpenAIProvider, AsyncMock]:
    create = AsyncMock(return_value=response)
    provider = object.__new__(OpenAIProvider)
    provider._base_url = base_url
    provider._client = SimpleNamespace(
        base_url=base_url,
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )
    )
    provider._timeout_seconds = 30
    provider._request_timeout = 30
    return provider, create


@pytest.mark.asyncio
async def test_openai_provider_close_awaits_underlying_client() -> None:
    close = AsyncMock()
    provider = object.__new__(OpenAIProvider)
    provider._client = SimpleNamespace(close=close)

    await provider.close()

    close.assert_awaited_once()
    assert provider._client is None


@pytest.mark.asyncio
async def test_openai_provider_coerces_kimi_temperature_for_complete() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, create = _make_provider(
        response,
        base_url="https://api.moonshot.ai/v1",
    )

    result = await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="kimi-k2.5",
        temperature=0.2,
    )

    assert result.text == "ok"
    assert create.await_args.kwargs["temperature"] == 1.0


@pytest.mark.asyncio
async def test_openai_provider_preserves_temperature_for_non_kimi_complete() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, create = _make_provider(response)

    await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-4o",
        temperature=0.2,
    )

    assert create.await_args.kwargs["temperature"] == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_openai_provider_normalizes_anyof_tool_schema_for_compatible_backends() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, create = _make_provider(response)
    tool_schema = {
        "type": "function",
        "function": {
            "name": "file_edit",
            "description": "Edit a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "edits": {"type": "array"},
                },
                "required": ["path"],
                "anyOf": [
                    {"required": ["start_line"]},
                    {"required": ["edits"]},
                ],
            },
        },
    }
    original_schema = {
        "type": "function",
        "function": {
            "name": "file_edit",
            "description": "Edit a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "edits": {"type": "array"},
                },
                "required": ["path"],
                "anyOf": [
                    {"required": ["start_line"]},
                    {"required": ["edits"]},
                ],
            },
        },
    }

    await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-4o",
        tools=[tool_schema],
    )

    sent_schema = create.await_args.kwargs["tools"][0]["function"]["parameters"]
    assert "type" not in sent_schema
    assert "required" not in sent_schema
    assert "properties" not in sent_schema
    assert sent_schema["anyOf"] == [
        {
            "required": ["path", "start_line"],
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "edits": {"type": "array"},
            },
        },
        {
            "required": ["path", "edits"],
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "edits": {"type": "array"},
            },
        },
    ]
    assert tool_schema == original_schema


@pytest.mark.asyncio
async def test_openai_provider_flattens_kimi_root_tool_parameter_unions() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, create = _make_provider(
        response,
        base_url="https://api.moonshot.ai/v1",
    )
    tool_schema = {
        "type": "function",
        "function": {
            "name": "file_edit",
            "description": "Edit a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_line": {"type": "integer"},
                                "old_string": {"type": "string"},
                            },
                            "anyOf": [
                                {"required": ["start_line"]},
                                {"required": ["old_string"]},
                            ],
                        },
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
                "anyOf": [
                    {"required": ["start_line"]},
                    {"required": ["edits"]},
                ],
            },
        },
    }

    await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="kimi-k2.6",
        tools=[tool_schema],
    )

    sent_schema = create.await_args.kwargs["tools"][0]["function"]["parameters"]
    assert sent_schema["type"] == "object"
    assert "anyOf" not in sent_schema
    assert sent_schema["required"] == ["path"]
    assert sent_schema["additionalProperties"] is False
    assert sent_schema["properties"]["path"] == {"type": "string"}
    edit_item_schema = sent_schema["properties"]["edits"]["items"]
    assert sent_schema["properties"]["edits"]["type"] == "array"
    assert "type" not in edit_item_schema
    assert edit_item_schema["anyOf"][0]["type"] == "object"
    assert edit_item_schema["anyOf"][0]["properties"] == {
        "start_line": {"type": "integer"},
        "old_string": {"type": "string"},
    }
    assert tool_schema["function"]["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_openai_provider_coerces_kimi_temperature_for_stream() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, create = _make_provider(
        response,
        base_url="https://api.moonshot.ai/v1",
    )

    chunks = [
        chunk
        async for chunk in provider.stream(
            messages=[{"role": "user", "content": "hello"}],
            model="kimi-k2.5",
            temperature=0.0,
        )
    ]

    assert create.await_args.kwargs["temperature"] == 1.0
    assert chunks[-1].done is True
    assert chunks[-1].accumulated == "hi"


@pytest.mark.asyncio
async def test_openai_provider_stream_preserves_tool_calls_in_final_chunk() -> None:
    stream_response = _AsyncStream(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="",
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call_1",
                                    type="function",
                                    function=SimpleNamespace(
                                        name="list_directory",
                                        arguments='{"path": "."}',
                                    ),
                                )
                            ],
                        ),
                        finish_reason="tool_calls",
                    )
                ],
                usage=None,
            )
        ]
    )
    provider, create = _make_provider(stream_response)

    chunks = [
        chunk
        async for chunk in provider.stream(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "description": "List a directory.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    ]

    assert create.await_args.kwargs["stream"] is True
    assert chunks[-1].done is True
    assert chunks[-1].finish_reason == "tool_calls"
    assert chunks[-1].tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "list_directory",
                "arguments": '{"path": "."}',
            },
        }
    ]
    assert chunks[-1].raw_assistant_message == {
        "role": "assistant",
        "content": None,
        "tool_calls": chunks[-1].tool_calls,
    }


class _FakeFunction:
    name = "list_directory"
    arguments = '{"path": "."}'
    model_extra = {"thought_signature": "sig-123"}

    def model_dump(self, **kwargs):
        return {
            "name": self.name,
            "arguments": self.arguments,
        }


class _FakeToolCall:
    id = "call_1"
    type = "function"
    function = _FakeFunction()
    model_extra = {"provider_metadata": {"family": "gemini"}}

    def model_dump(self, **kwargs):
        return {
            "id": self.id,
            "type": self.type,
            "function": self.function.model_dump(),
        }


@pytest.mark.asyncio
async def test_openai_provider_preserves_extra_tool_call_metadata():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="", tool_calls=[_FakeToolCall()]),
                finish_reason="tool_calls",
            )
        ],
        usage=None,
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=response))
        ),
        timeout=30,
    )
    provider = OpenAIProvider.from_client(client)

    result = await provider.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="gemini-3.1-pro-preview",
    )

    assert result.tool_calls is not None
    assert result.tool_calls[0]["function"]["thought_signature"] == "sig-123"
    assert result.tool_calls[0]["provider_metadata"] == {"family": "gemini"}


def test_openai_provider_kimi_behavior_falls_back_without_base_url() -> None:
    provider, _ = _make_provider(
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
    )

    kimi_behavior = get_model_behavior(provider, "kimi-k2.5")
    gpt_behavior = get_model_behavior(provider, "gpt-4o")

    assert kimi_behavior.supports_required_tool_choice is False
    assert kimi_behavior.assistant_replay_mode == "raw"
    assert gpt_behavior.supports_required_tool_choice is True


@pytest.mark.asyncio
async def test_openai_provider_returns_raw_assistant_message() -> None:
    message = SimpleNamespace(
        content="",
        tool_calls=[_FakeToolCall()],
        model_extra={"reasoning_content": "step trace"},
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=message,
                finish_reason="tool_calls",
            )
        ],
        usage=None,
    )
    provider, _ = _make_provider(response)

    result = await provider.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="kimi-k2.5",
    )

    assert result.raw_assistant_message is not None
    assert result.raw_assistant_message["role"] == "assistant"
    assert result.raw_assistant_message["content"] is None
    assert result.raw_assistant_message["reasoning_content"] == "step trace"
    assert result.raw_assistant_message["tool_calls"][0]["function"]["name"] == "list_directory"


@pytest.mark.asyncio
async def test_openai_provider_preserves_reasoning_details_on_raw_assistant_message() -> None:
    message = SimpleNamespace(
        content="There are 3 r's in strawberry.",
        tool_calls=None,
        reasoning_details=[
            {
                "type": "reasoning.summary",
                "text": "Counted the letters carefully.",
            }
        ],
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=message,
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, _ = _make_provider(response)

    result = await provider.complete(
        messages=[{"role": "user", "content": "How many r's are in strawberry?"}],
        model="z-ai/glm-5.1",
    )

    assert result.raw_assistant_message is not None
    assert result.raw_assistant_message["role"] == "assistant"
    assert result.raw_assistant_message["content"] == "There are 3 r's in strawberry."
    assert result.raw_assistant_message["reasoning_details"] == [
        {
            "type": "reasoning.summary",
            "text": "Counted the letters carefully.",
        }
    ]


@pytest.mark.asyncio
async def test_openai_provider_moves_reasoning_into_extra_body() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, create = _make_provider(response)

    await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="z-ai/glm-5.1",
        reasoning={"enabled": True},
    )

    assert "reasoning" not in create.await_args.kwargs
    assert create.await_args.kwargs["extra_body"]["reasoning"] == {"enabled": True}


@pytest.mark.asyncio
async def test_openai_provider_applies_env_reasoning_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_OPENAI_COMPAT_REASONING", "enabled")
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, create = _make_provider(response)

    await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="z-ai/glm-5.1",
    )

    assert create.await_args.kwargs["extra_body"]["reasoning"] == {"enabled": True}


@pytest.mark.asyncio
async def test_openai_provider_records_request_details_on_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_OPENAI_COMPAT_REASONING", "enabled")
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider, _create = _make_provider(response)

    result = await provider.complete(
        messages=[
            {"role": "system", "content": "Be precise."},
            {"role": "user", "content": "hello"},
        ],
        model="z-ai/glm-5.1",
        max_tokens=2048,
    )

    details = result.provider_metadata["request_details"]
    assert details["request_mode"] == "complete"
    assert details["provider_name"] == "OpenAIProvider"
    assert details["message_count"] == 2
    assert details["system_message_count"] == 1
    assert details["user_message_count"] == 1
    assert details["effective_max_tokens"] == 2048
    assert details["reasoning_enabled"] is True
