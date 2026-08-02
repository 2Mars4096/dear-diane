from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from dan.providers import ProviderConfig, resolve_provider_timeout
from dan.providers.anthropic_provider import AnthropicProvider
from dan.providers.google_provider import GoogleProvider
from dan.providers.openai_provider import OpenAIProvider


def test_resolve_provider_timeout_prefers_config_extra(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DAN_PROVIDER_TIMEOUT", "90")
    cfg = ProviderConfig(extra={"timeout_seconds": 42})
    assert resolve_provider_timeout(cfg) == 42.0


def test_openai_provider_bounds_connect_timeout() -> None:
    timeout = OpenAIProvider._build_request_timeout(120.0)

    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 10.0
    assert timeout.read == 120.0


@pytest.mark.asyncio
async def test_openai_provider_passes_timeout_to_requests():
    mock_create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    mock_client.timeout = 33.0

    provider = OpenAIProvider.from_client(mock_client)
    await provider.complete(messages=[{"role": "user", "content": "hi"}], model="gpt-test")

    assert mock_create.await_args.kwargs["timeout"] == 33.0


@pytest.mark.asyncio
async def test_anthropic_provider_passes_timeout_to_requests():
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider._timeout_seconds = 27.0
    provider._client = MagicMock()
    provider._client.messages.create = AsyncMock(
        return_value=SimpleNamespace(content=[], usage=None, stop_reason="end_turn")
    )

    await provider.complete(messages=[{"role": "user", "content": "hi"}], model="claude-test")

    assert provider._client.messages.create.await_args.kwargs["timeout"] == 27.0


@pytest.mark.asyncio
async def test_google_provider_complete_times_out():
    class _SlowChat:
        async def send_message_async(self, *args, **kwargs):
            await asyncio.sleep(0.05)
            return SimpleNamespace(text="too late", usage_metadata=None, candidates=[])

    class _FakeModel:
        def start_chat(self, history=None):
            return _SlowChat()

    provider = GoogleProvider.__new__(GoogleProvider)
    provider._timeout_seconds = 0.01
    provider._genai = SimpleNamespace(
        GenerativeModel=lambda *args, **kwargs: _FakeModel()
    )

    with pytest.raises(asyncio.TimeoutError):
        await provider.complete(messages=[{"role": "user", "content": "hi"}], model="gemini-test")
