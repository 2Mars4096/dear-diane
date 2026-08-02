from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from dan.providers import CompletionResult, LLMAuthenticationError, ProviderConfig
from dan.providers.factory import build_provider_registry
from dan.providers.openai_provider import OpenAIProvider
from dan.providers.retrying_provider import (
    ProviderRetryPolicy,
    RetryingLLMProvider,
    resolve_provider_retry_policy,
    wrap_provider_with_retries,
)


class _FakeProvider:
    def __init__(self, *, complete=None, stream=None) -> None:
        self.complete = complete or AsyncMock()
        self.stream = stream


def _fake_openai_response(text: str) -> MagicMock:
    response = MagicMock()
    choice = MagicMock()
    message = MagicMock()
    message.content = text
    message.tool_calls = None
    choice.message = message
    choice.finish_reason = "stop"
    response.choices = [choice]
    response.usage = None
    return response


@pytest.mark.asyncio
async def test_retrying_provider_retries_transient_complete_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)
    provider = _FakeProvider(
        complete=AsyncMock(
            side_effect=[
                httpx.ConnectError("temporary network failure"),
                CompletionResult(text="ok"),
            ]
        )
    )
    wrapped = RetryingLLMProvider(
        provider,
        policy=ProviderRetryPolicy(max_attempts=2, initial_delay_seconds=0.5, max_delay_seconds=1.0),
    )

    result = await wrapped.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="kimi-k2.5",
    )

    assert result.text == "ok"
    assert provider.complete.await_count == 2
    sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_retrying_provider_runs_recovery_hook_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)
    recovery = AsyncMock()
    provider = _FakeProvider(
        complete=AsyncMock(
            side_effect=[
                httpx.ConnectError("temporary network failure"),
                CompletionResult(text="ok"),
            ]
        )
    )
    provider.recover_from_error = recovery
    wrapped = RetryingLLMProvider(
        provider,
        policy=ProviderRetryPolicy(max_attempts=2, initial_delay_seconds=0.5, max_delay_seconds=1.0),
    )

    result = await wrapped.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="z-ai/glm-5.1",
    )

    assert result.text == "ok"
    recovery.assert_awaited_once()
    sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_retrying_openai_provider_rebuilds_client_after_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)

    first_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(side_effect=httpx.ConnectError("temporary network failure"))
            )
        ),
        close=AsyncMock(),
        base_url="https://openrouter.ai/api/v1",
        timeout=30.0,
    )
    second_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=_fake_openai_response("ok"))
            )
        ),
        close=AsyncMock(),
        base_url="https://openrouter.ai/api/v1",
        timeout=30.0,
    )
    client_ctor = MagicMock(side_effect=[first_client, second_client])
    monkeypatch.setattr("dan.providers.openai_provider.AsyncOpenAI", client_ctor)

    provider = OpenAIProvider(
        ProviderConfig(
            api_key="test",
            base_url="https://openrouter.ai/api/v1",
        )
    )
    wrapped = RetryingLLMProvider(
        provider,
        policy=ProviderRetryPolicy(max_attempts=2, initial_delay_seconds=0.5, max_delay_seconds=1.0),
    )

    result = await wrapped.complete(
        messages=[{"role": "user", "content": "hi"}],
        model="z-ai/glm-5.1",
    )

    assert result.text == "ok"
    assert client_ctor.call_count == 2
    first_client.close.assert_awaited_once()
    second_client.chat.completions.create.assert_awaited_once()
    sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_retrying_provider_does_not_retry_authentication_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)
    provider = _FakeProvider(
        complete=AsyncMock(side_effect=LLMAuthenticationError("bad key"))
    )
    wrapped = RetryingLLMProvider(
        provider,
        policy=ProviderRetryPolicy(max_attempts=3, initial_delay_seconds=0.5, max_delay_seconds=1.0),
    )

    with pytest.raises(LLMAuthenticationError):
        await wrapped.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="kimi-k2.5",
        )

    assert provider.complete.await_count == 1
    sleep.assert_not_awaited()


def test_resolve_provider_retry_policy_prefers_config_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_PROVIDER_RETRY_MAX_ATTEMPTS", "9")
    policy = resolve_provider_retry_policy(
        ProviderConfig(
            api_key="test",
            extra={
                "retry_max_attempts": 4,
                "retry_initial_delay_seconds": 0.25,
                "retry_max_delay_seconds": 1.5,
            },
        )
    )

    assert policy.max_attempts == 4
    assert policy.initial_delay_seconds == pytest.approx(0.25)
    assert policy.max_delay_seconds == pytest.approx(1.5)


def test_wrap_provider_with_retries_can_return_raw_provider() -> None:
    raw_provider = OpenAIProvider.from_client(
        SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock())
            ),
            timeout=30.0,
        )
    )

    wrapped = wrap_provider_with_retries(
        raw_provider,
        ProviderConfig(api_key="test", extra={"retry_max_attempts": 1}),
    )

    assert wrapped is raw_provider


def test_build_provider_registry_wraps_default_provider() -> None:
    class _Config:
        llm_api_key = "test-key"
        llm_base_url = "https://api.example.com/v1"
        providers = {}
        model_provider_map = {}

    registry = build_provider_registry(_Config())
    provider = registry.get("default")

    assert isinstance(provider, RetryingLLMProvider)
    assert isinstance(provider._provider, OpenAIProvider)
