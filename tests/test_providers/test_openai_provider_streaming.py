from __future__ import annotations

from types import SimpleNamespace

import pytest

from dan.providers.openai_provider import OpenAIProvider


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content="hello from kimi", tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        usage = SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
            prompt_tokens_details=None,
        )
        return SimpleNamespace(choices=[choice], usage=usage)


class _FakeClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions())
        self.timeout = None


@pytest.mark.asyncio
async def test_kimi_stream_falls_back_to_complete() -> None:
    client = _FakeClient()
    provider = OpenAIProvider.from_client(client)

    chunks = [
        chunk
        async for chunk in provider.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="kimi-k2.5",
            temperature=0.3,
            max_tokens=2200,
        )
    ]

    assert [chunk.delta for chunk in chunks] == ["hello from kimi", ""]
    assert chunks[-1].done is True
    assert client.chat.completions.calls == [
        {
            "model": "kimi-k2.5",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.6,
            "max_tokens": 2200,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]
