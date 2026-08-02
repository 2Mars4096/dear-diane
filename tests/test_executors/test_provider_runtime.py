from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.providers import CompletionResult
from dan.executors.control_flow import RouterExecutor
from dan.executors.reflection import ReflectionExecutor
from dan.executors.provider_runtime import (
    _GatewayBackedProviderAdapter,
    resolve_completion_provider,
    resolve_embedding_provider,
    resolve_llm_provider,
)
from dan.models.nodes import ReflectionNode


class _FakeProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        messages: list[dict[str, object]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: object,
    ) -> CompletionResult:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )
        return CompletionResult(text=self.text, model=model)


class _Registry:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider
        self.calls: list[str] = []

    def resolve(self, model: str) -> _FakeProvider:
        self.calls.append(model)
        return self.provider


class _RegistryWithClose(_Registry):
    def get(self, name: str) -> object | None:
        return self.provider if name == "default" else None

    def provider_names(self) -> list[str]:
        return ["default"]


class _Context:
    def __init__(
        self,
        provider_registry: object | None = None,
        model_gateway: object | None = None,
    ) -> None:
        self.provider_registry = provider_registry
        self.model_gateway = model_gateway
        self.config = SimpleNamespace(
            llm_api_key="sk-test",
            llm_base_url="https://example.invalid/v1",
            llm_default_model="gpt-4o",
            default_embedding_model="text-embedding-3-small",
        )
        self.cost_tracker = None

    @asynccontextmanager
    async def llm_slot(self):
        yield


class _FakePIISession:
    def __init__(self, key: str) -> None:
        self.key = key

    def tokenize(self, text: str) -> str:
        return text.replace("secret", "[PII]")

    def detokenize(self, text: str) -> str:
        return text.replace("[PII]", "secret")


def test_resolve_completion_provider_prefers_registry() -> None:
    provider = _FakeProvider("registry-title")
    registry = _Registry(provider)
    context = _Context(provider_registry=registry)

    resolved = resolve_completion_provider(context, "gpt-4o")

    assert resolved is context.model_gateway
    assert resolved is not None
    assert resolved.registry is registry
    assert registry.calls == []


def test_resolve_completion_provider_prefers_model_gateway() -> None:
    provider = _FakeProvider("gateway-title")
    gateway = SimpleNamespace(
        complete=AsyncMock(return_value=CompletionResult(text="gateway-title", model="gpt-4o")),
        resolve=MagicMock(return_value=provider),
    )
    context = _Context(model_gateway=gateway)

    resolved = resolve_completion_provider(context, "gpt-4o")

    assert resolved is gateway
    gateway.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_llm_provider_uses_gateway_adapter_when_gateway_present() -> None:
    provider = _FakeProvider("gateway-title")

    async def _stream(**kwargs: object):
        if False:
            yield kwargs

    gateway = SimpleNamespace(
        complete=AsyncMock(return_value=CompletionResult(text="gateway-title", model="gpt-4o")),
        stream=_stream,
        resolve=MagicMock(return_value=provider),
    )
    context = _Context(model_gateway=gateway)

    resolved = resolve_llm_provider(context, "gpt-4o")
    result = await resolved.complete(
        messages=[{"role": "user", "content": "Choose a title"}],
        model="gpt-4o",
        temperature=0.0,
        tools=[{"type": "function", "function": {"name": "lookup"}}],
    )

    assert result.text == "gateway-title"
    gateway.resolve.assert_called_once_with("gpt-4o")
    gateway.complete.assert_awaited_once()
    call = gateway.complete.await_args.kwargs
    assert call["model"] == "gpt-4o"
    assert call["temperature"] == 0.0
    assert call["tools"] == [{"type": "function", "function": {"name": "lookup"}}]


@pytest.mark.asyncio
async def test_gateway_backed_provider_adapter_closes_resolved_provider_once() -> None:
    provider = _FakeProvider("gateway-title")
    provider.close = AsyncMock()  # type: ignore[attr-defined]
    registry = _RegistryWithClose(provider)
    gateway = SimpleNamespace(
        complete=AsyncMock(return_value=CompletionResult(text="gateway-title", model="gpt-4o")),
        stream=AsyncMock(),
        resolve=MagicMock(return_value=provider),
        registry=registry,
    )
    adapter = _GatewayBackedProviderAdapter(gateway, context=SimpleNamespace(), model="gpt-4o")

    await adapter.close()

    provider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_llm_provider_prefers_gateway_adapter_from_registry() -> None:
    provider = _FakeProvider("registry-title")
    registry = _Registry(provider)
    context = _Context(provider_registry=registry)
    gateway_complete_calls: list[dict[str, object]] = []
    original_gateway = context.model_gateway

    resolved = resolve_llm_provider(context, "gpt-4o")

    assert resolved is not None
    assert context.model_gateway is not None
    assert context.model_gateway is not original_gateway
    assert registry.calls == ["gpt-4o"]

    original_complete = context.model_gateway.complete

    async def _recording_complete(**kwargs: object) -> CompletionResult:
        gateway_complete_calls.append(dict(kwargs))
        return await original_complete(**kwargs)

    context.model_gateway.complete = _recording_complete

    result = await resolved.complete(
        messages=[{"role": "user", "content": "hello"}],
        model="gpt-4o",
        temperature=0.1,
        tools=[{"type": "function", "function": {"name": "lookup"}}],
    )

    assert result.text == "registry-title"
    assert provider.calls
    assert provider.calls[0]["model"] == "gpt-4o"
    assert provider.calls[0]["kwargs"]["tools"] == [
        {"type": "function", "function": {"name": "lookup"}}
    ]
    assert gateway_complete_calls
    assert gateway_complete_calls[0]["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_resolve_llm_provider_passes_pii_session_through_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dan.llm_core import pii_tokenizer

    provider = _FakeProvider("secret registry-title")
    registry = _Registry(provider)
    context = _Context(provider_registry=registry)
    context.pii_session_key = lambda model: f"session:{model}"
    captured: dict[str, object] = {}

    monkeypatch.setattr(pii_tokenizer, "is_pii_enabled", lambda: True)

    def _fake_get_pii_session(key: str) -> _FakePIISession:
        captured["key"] = key
        return _FakePIISession(key)

    monkeypatch.setattr(pii_tokenizer, "get_pii_session", _fake_get_pii_session)

    resolved = resolve_llm_provider(context, "gpt-4o")
    result = await resolved.complete(
        messages=[{"role": "user", "content": "secret note"}],
        model="gpt-4o",
    )

    assert captured["key"] == "session:gpt-4o"
    assert result.text == "secret registry-title"
    assert provider.calls[0]["messages"][0]["content"] == "[PII] note"


@pytest.mark.asyncio
async def test_resolve_completion_provider_builds_gateway_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="fallback-title", tool_calls=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_resp)
    mock_cls = MagicMock(return_value=mock_client)
    monkeypatch.setattr("dan.providers.openai_provider.AsyncOpenAI", mock_cls)

    context = _Context()
    provider = resolve_completion_provider(
        context,
        "gpt-4o",
        allow_legacy_openai_fallback=True,
    )
    assert provider is context.model_gateway
    assert provider is not None

    result = await provider.complete(
        messages=[{"role": "user", "content": "Choose a title"}],
        model="gpt-4o",
        temperature=0.0,
    )

    assert result.text == "fallback-title"
    assert result.usage is not None
    assert result.usage["prompt_tokens"] == 12
    assert result.usage["completion_tokens"] == 3
    mock_cls.assert_called_once()
    _, kwargs = mock_cls.call_args
    assert kwargs["api_key"] == "sk-test"
    assert kwargs["base_url"] == "https://example.invalid/v1"
    timeout = kwargs["timeout"]
    if hasattr(timeout, "read"):
        assert timeout.read == 120.0
    else:
        assert timeout == 120.0


@pytest.mark.asyncio
async def test_router_executor_uses_gateway_fallback_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="route-b", tool_calls=None),
            )
        ],
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_resp)
    monkeypatch.setattr(
        "dan.providers.openai_provider.AsyncOpenAI",
        MagicMock(return_value=mock_client),
    )

    context = _Context()
    result = await RouterExecutor._call_router_llm(context, "gpt-4o", "choose route")

    assert result == "route-b"


@pytest.mark.asyncio
async def test_reflection_executor_uses_registry_provider() -> None:
    provider = _FakeProvider(
        '[{"condition":"bad input","action":"sanitize","reason":"cause",'
        '"confidence":0.9,"tags":["llm_operator"],"repair_level":"prompt_fix"}]'
    )
    registry = _Registry(provider)
    context = _Context(provider_registry=registry)
    node = ReflectionNode(id="r1", name="Reflect", reflection_model="gpt-4o")
    inputs = {
        "run_id": "run-1",
        "run_errors": [
            {
                "node_id": "n1",
                "node_type": "llm_operator",
                "message": "boom",
            }
        ],
    }

    result = await ReflectionExecutor().execute(node, inputs, context)

    assert result.status.name == "COMPLETED"
    assert result.outputs["principle_count"] == 1
    assert provider.calls and provider.calls[0]["model"] == "gpt-4o"


def test_resolve_embedding_provider_prefers_registry_then_vector_store_config() -> None:
    registry_provider = object()
    registry = SimpleNamespace(resolve=lambda model: registry_provider)
    context = SimpleNamespace(
        embedding_registry=registry,
        config=SimpleNamespace(default_embedding_model="text-embedding-3-small"),
    )
    node = SimpleNamespace(
        embedding_model="text-embedding-3-large",
        vector_store_config={},
    )

    resolved, model = resolve_embedding_provider(node, context)
    assert resolved is registry_provider
    assert model == "text-embedding-3-large"

    fallback_provider = object()
    node = SimpleNamespace(
        embedding_model="",
        vector_store_config={"embedding_provider": fallback_provider},
    )
    context = SimpleNamespace(
        embedding_registry=None,
        config=SimpleNamespace(default_embedding_model="text-embedding-3-small"),
    )
    resolved, model = resolve_embedding_provider(node, context)
    assert resolved is fallback_provider
    assert model == "text-embedding-3-small"
