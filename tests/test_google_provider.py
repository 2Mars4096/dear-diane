from __future__ import annotations

from types import SimpleNamespace

import pytest

genai = pytest.importorskip("google.generativeai")
json_format = pytest.importorskip("google.protobuf.json_format")

from dan.providers.google_provider import GoogleProvider


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


class _FakeGenerativeModel:
    init_calls: list[dict] = []
    generate_calls: list[dict] = []
    response: object | None = None

    def __init__(self, model_name: str, **kwargs):
        self.model_name = model_name
        self.kwargs = kwargs
        type(self).init_calls.append({
            "model_name": model_name,
            "kwargs": kwargs,
        })

    async def generate_content_async(self, **kwargs):
        type(self).generate_calls.append(kwargs)
        return type(self).response


def _make_provider(response: object) -> GoogleProvider:
    _FakeGenerativeModel.init_calls = []
    _FakeGenerativeModel.generate_calls = []
    _FakeGenerativeModel.response = response

    provider = object.__new__(GoogleProvider)
    provider._genai = SimpleNamespace(GenerativeModel=_FakeGenerativeModel)
    provider._protos = genai.protos
    provider._message_to_dict = json_format.MessageToDict
    provider._timeout_seconds = 30
    return provider


def _to_dict(obj):
    base = obj._pb if hasattr(obj, "_pb") else obj
    return json_format.MessageToDict(base, preserving_proto_field_name=True)


@pytest.mark.asyncio
async def test_google_provider_translates_tool_history_and_parses_function_call_response():
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=genai.protos.Content(
                    {
                        "role": "model",
                        "parts": [
                            {"text": "Need tool"},
                            {"function_call": {"name": "list_directory", "args": {"path": "."}}},
                        ],
                    },
                    ignore_unknown_fields=True,
                ),
                finish_reason=SimpleNamespace(name="STOP"),
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
        ),
    )
    provider = _make_provider(response)

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
        model="gemini-2.0-flash",
        tools=_tool_schema(),
        tool_choice={"type": "function", "function": {"name": "list_directory"}},
    )

    assert result.text == "Need tool"
    assert result.tool_calls == [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "list_directory",
                "arguments": '{"path": "."}',
            },
        }
    ]

    assert _FakeGenerativeModel.init_calls[0]["kwargs"]["system_instruction"] == "sys"
    generate_kwargs = _FakeGenerativeModel.generate_calls[0]

    tool_config = _to_dict(generate_kwargs["tool_config"])
    assert tool_config["function_calling_config"]["mode"] == "ANY"
    assert tool_config["function_calling_config"]["allowed_function_names"] == ["list_directory"]

    tools = [_to_dict(tool) for tool in generate_kwargs["tools"]]
    assert tools[0]["function_declarations"][0]["name"] == "list_directory"

    contents = [_to_dict(content) for content in generate_kwargs["contents"]]
    assert contents[0] == {"role": "user", "parts": [{"text": "hello"}]}
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0] == {"text": "Checking"}
    assert contents[1]["parts"][1]["function_call"]["name"] == "list_directory"
    assert contents[1]["parts"][1]["function_call"]["args"] == {"path": "."}
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["function_response"]["name"] == "list_directory"
    assert contents[2]["parts"][0]["function_response"]["response"] == {"result": "file_a\nfile_b"}
    assert result.raw_assistant_message is not None
    assert result.raw_assistant_message["role"] == "assistant"
    assert result.raw_assistant_message["content"] == "Need tool"
    assert result.raw_assistant_message["tool_calls"][0]["function"]["name"] == "list_directory"
    assert result.raw_assistant_message["gemini_parts"][1]["function_call"]["name"] == "list_directory"
    assert result.provider_metadata == {"family": "google"}


@pytest.mark.asyncio
async def test_google_provider_returns_raw_assistant_message_for_text_only_response():
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                content=genai.protos.Content(
                    {
                        "role": "model",
                        "parts": [
                            {"text": "Hello Gemini"},
                        ],
                    },
                    ignore_unknown_fields=True,
                ),
                finish_reason=SimpleNamespace(name="STOP"),
            )
        ],
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
        ),
    )
    provider = _make_provider(response)

    result = await provider.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="gemini-2.0-flash",
    )

    assert result.raw_assistant_message is not None
    assert result.raw_assistant_message["role"] == "assistant"
    assert result.raw_assistant_message["content"] == "Hello Gemini"
    assert result.raw_assistant_message["gemini_parts"] == [{"text": "Hello Gemini"}]
