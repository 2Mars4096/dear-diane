"""Tests for LLMExecutor provider dispatch integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine.executor import EngineConfig, ExecutionContext
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.llm import LLMExecutor
from dan.models.nodes import LLMOperator, RetryPolicy
from dan.providers import CompletionResult, StreamChunk
from dan.providers.anthropic_provider import AnthropicProvider
from dan.providers.google_provider import GoogleProvider
from dan.providers.multimodal import content_with_image_attachments, normalize_openai_messages_for_multimodal
from dan.providers.registry import ProviderRegistry


class FakeProvider:
    def __init__(self, name: str = "fake", fail_stream: bool = False) -> None:
        self.name = name
        self.calls: list[dict] = []
        self._fail_stream = fail_stream

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        self.calls.append({"method": "complete", "model": model, "messages": messages})
        return CompletionResult(text=f"[{self.name}] response", model=model, usage={"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15})

    async def stream(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        if self._fail_stream:
            raise Exception("streaming not supported")
        self.calls.append({"method": "stream", "model": model, "messages": messages})
        yield StreamChunk(delta="hello", accumulated="hello")
        yield StreamChunk(delta="", accumulated="hello", done=True, usage={"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15})


def _make_context(registry: ProviderRegistry | None = None) -> ExecutionContext:
    from dan.models.graph import Graph
    graph = Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "test"},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "shared_context": [],
        "artifact_refs": [],
    })
    state = ExecutionState(graph)
    config = EngineConfig(llm_api_key="test-key", llm_default_model="gpt-4o")
    return ExecutionContext(
        state=state,
        config=config,
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        provider_registry=registry,
    )


def _make_llm_node(model: str = "gpt-4o", **kwargs) -> LLMOperator:
    prompt_template = kwargs.pop("prompt_template", "Say {input}")
    return LLMOperator(
        id="llm1", name="test-llm",
        model=model, prompt_template=prompt_template,
        **kwargs,
    )


def test_multimodal_helper_builds_openai_image_blocks(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    content = content_with_image_attachments(
        "What is wrong with this UI?",
        [{"kind": "image", "local_path": str(image)}],
    )

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "Attached images:" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_multimodal_helper_accepts_inline_clipboard_image_data_url():
    data_url = "data:image/png;base64,iVBORw0KGgpmYWtl"

    content = content_with_image_attachments(
        "What is in this screenshot?",
        [{"kind": "figure", "name": "Screenshot.png", "data_url": data_url}],
    )

    assert isinstance(content, list)
    assert "inline image" in content[0]["text"]
    assert content[1] == {"type": "image_url", "image_url": {"url": data_url}}


def test_openai_message_normalizer_accepts_short_image_url_alias():
    messages = normalize_openai_messages_for_multimodal(
        [{"role": "user", "content": [{"type": "image_url", "url": "data:image/png;base64,abc"}]}]
    )

    assert messages[0]["content"][0] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }


def test_anthropic_adapter_translates_openai_image_block():
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

    assert messages[0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
    }


def test_google_adapter_translates_openai_image_block():
    _system, history = GoogleProvider._to_gemini_messages(
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

    assert history[0]["parts"] == [
        {"text": "review this"},
        {"inline_data": {"mime_type": "image/png", "data": "abc"}},
    ]


@pytest.mark.asyncio
async def test_dispatches_to_correct_provider():
    openai_p = FakeProvider("openai")
    anthropic_p = FakeProvider("anthropic")

    reg = ProviderRegistry()
    reg.register("openai", openai_p)
    reg.register("anthropic", anthropic_p)

    ctx = _make_context(reg)

    executor = LLMExecutor()
    node = _make_llm_node("claude-sonnet-4")
    result = await executor.execute(node, {"input": "hi"}, ctx)
    assert result.status == NodeStatus.COMPLETED
    assert len(anthropic_p.calls) > 0
    assert len(openai_p.calls) == 0


@pytest.mark.asyncio
async def test_dispatches_openai_model():
    openai_p = FakeProvider("openai")
    reg = ProviderRegistry()
    reg.register("openai", openai_p)

    ctx = _make_context(reg)
    executor = LLMExecutor()
    node = _make_llm_node("gpt-4o")
    result = await executor.execute(node, {"input": "hello"}, ctx)
    assert result.status == NodeStatus.COMPLETED
    assert result.outputs.get("text") is not None
    assert len(openai_p.calls) > 0


@pytest.mark.asyncio
async def test_falls_back_to_complete_when_stream_fails():
    provider = FakeProvider("openai", fail_stream=True)
    reg = ProviderRegistry()
    reg.register("default", provider)

    ctx = _make_context(reg)
    executor = LLMExecutor()
    node = _make_llm_node("custom-model")
    result = await executor.execute(node, {"input": "hi"}, ctx)
    assert result.status == NodeStatus.COMPLETED
    complete_calls = [c for c in provider.calls if c["method"] == "complete"]
    assert len(complete_calls) >= 1


@pytest.mark.asyncio
async def test_cross_provider_fallback():
    from openai import RateLimitError

    class FailingProvider:
        async def complete(self, messages, model, **kw):
            raise RateLimitError("rate limited", response=MagicMock(status_code=429), body=None)
        async def stream(self, messages, model, **kw):
            raise RateLimitError("rate limited", response=MagicMock(status_code=429), body=None)
            yield  # make it an async generator

    fallback_p = FakeProvider("openai-fallback")

    reg = ProviderRegistry()
    reg.register("anthropic", FailingProvider())
    reg.register("openai", fallback_p)

    ctx = _make_context(reg)
    executor = LLMExecutor()
    node = _make_llm_node(
        "claude-sonnet-4",
        retry_policy=RetryPolicy(max_retries=1, fallback_model="gpt-4o"),
    )
    result = await executor.execute(node, {"input": "hi"}, ctx)
    assert result.status == NodeStatus.COMPLETED
    assert len(fallback_p.calls) > 0


@pytest.mark.asyncio
async def test_runtime_gateway_builds_from_config_without_registry():
    """When no provider_registry is injected, executor should synthesize a runtime gateway."""
    from unittest.mock import patch

    ctx = _make_context(registry=None)
    executor = LLMExecutor()
    node = _make_llm_node("gpt-4o")

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message.content = "fallback response"
    fake_resp.usage = None

    with patch("dan.providers.openai_provider.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        result = await executor.execute(node, {"input": "test"}, ctx)
        assert result.status == NodeStatus.COMPLETED


@pytest.mark.asyncio
async def test_explicit_client_overrides_config_gateway_fallback():
    """An injected client should remain usable when no registry/gateway is wired."""
    from unittest.mock import patch

    ctx = _make_context(registry=None)
    node = _make_llm_node("gpt-4o")

    def _chunk(delta: str, *, done: bool = False) -> MagicMock:
        chunk = MagicMock()
        if done:
            chunk.choices = []
            usage = MagicMock()
            usage.prompt_tokens = 5
            usage.completion_tokens = 3
            usage.total_tokens = 8
            usage.prompt_tokens_details = None
            chunk.usage = usage
        else:
            delta_obj = MagicMock()
            delta_obj.content = delta
            choice = MagicMock()
            choice.delta = delta_obj
            chunk.choices = [choice]
            chunk.usage = None
        return chunk

    async def fake_create(**kwargs):
        if kwargs.get("stream"):
            async def _stream():
                yield _chunk("legacy ")
                yield _chunk("client response")
                yield _chunk("", done=True)

            return _stream()
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "legacy client response"
        resp.usage = None
        return resp

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=fake_create)

    executor = LLMExecutor(client=mock_client)

    with patch(
        "dan.providers.openai_provider.AsyncOpenAI",
        side_effect=AssertionError("runtime config fallback should not construct a new client"),
    ) as mock_ctor:
        result = await executor.execute(node, {"input": "test"}, ctx)

    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["text"] == "legacy client response"
    mock_client.chat.completions.create.assert_awaited()
    mock_ctor.assert_not_called()


@pytest.mark.asyncio
async def test_renders_system_prompt_and_legacy_placeholders_without_breaking_json_braces():
    provider = FakeProvider("openai")
    reg = ProviderRegistry()
    reg.register("default", provider)

    ctx = _make_context(reg)
    executor = LLMExecutor()
    node = _make_llm_node(
        "custom-model",
        prompt_template="Summarize {{input}} for {date}.",
        system_prompt='Return JSON like {"ticker":"SMCI"} for {date}.',
    )

    result = await executor.execute(
        node,
        {"input": "earnings", "date": "2026-03-21"},
        ctx,
    )

    assert result.status == NodeStatus.COMPLETED
    assert provider.calls
    messages = provider.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == 'Return JSON like {"ticker":"SMCI"} for 2026-03-21.'
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Summarize earnings for 2026-03-21."


@pytest.mark.asyncio
async def test_appends_lint_feedback_to_system_prompt() -> None:
    provider = FakeProvider("openai")
    reg = ProviderRegistry()
    reg.register("default", provider)

    ctx = _make_context(reg)
    executor = LLMExecutor()
    node = _make_llm_node(
        "custom-model",
        prompt_template="Answer {input}",
        system_prompt="Base instruction.",
    )

    result = await executor.execute(
        node,
        {
            "input": "hello",
            "__lint_feedback__": "Missing required key 'summary'.",
            "__lint_retry_attempt__": 1,
        },
        ctx,
    )

    assert result.status == NodeStatus.COMPLETED
    assert provider.calls
    messages = provider.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "Base instruction." in messages[0]["content"]
    assert "Previous output failed downstream handoff lint." in messages[0]["content"]
    assert "Missing required key 'summary'." in messages[0]["content"]
