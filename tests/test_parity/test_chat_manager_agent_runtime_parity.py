from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dan.agent_runtime import AgentEvent, AgentRequest, AgentResult, BaseAgentRuntime
from dan.llm_core import CompletionResult, GatewayConfig, ModelGateway, StreamChunk
from dan.providers.registry import ProviderRegistry
from dan.server.chat_manager import ChatManager
from dan.server.llm_gateway import resolve_model_gateway


class RecordingProvider:
    def __init__(self, *, text: str = "ok") -> None:
        self._text = text
        self.complete_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        self.complete_calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )
        return CompletionResult(
            text=self._text,
            usage={"prompt_tokens": 7, "completion_tokens": 11},
            model=model,
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ):
        self.stream_calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )

        async def _gen():
            yield StreamChunk(delta="one", accumulated="one", done=False)
            yield StreamChunk(delta=" two", accumulated="one two", done=True)

        return _gen()


class StubRuntime:
    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    async def run_turn(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        return AgentResult(text="stubbed", model_used="stub-model")

    async def stream_turn(self, request: AgentRequest):
        self.requests.append(request)
        yield AgentEvent(kind="text_delta", data="stub")
        yield AgentEvent(kind="complete", data="stubbed")


def _make_gateway(provider: RecordingProvider) -> ModelGateway:
    registry = ProviderRegistry()
    registry.register("default", provider)
    return ModelGateway(
        registry,
        config=GatewayConfig(
            pii_enabled=False,
            retry_enabled=False,
            telemetry_enabled=False,
            budget_enabled=False,
        ),
    )


def _make_manager(*, agent_runtime: Any | None = None) -> ChatManager:
    return ChatManager(
        ProviderRegistry(),
        graph_store=SimpleNamespace(get_graph=lambda workflow_id: {}),
        agent_runtime=agent_runtime,
    )


@pytest.mark.asyncio
async def test_chat_manager_run_agent_turn_matches_base_runtime() -> None:
    manager_provider = RecordingProvider(text="manager-result")
    direct_provider = RecordingProvider(text="manager-result")
    manager_gateway = _make_gateway(manager_provider)
    direct_gateway = _make_gateway(direct_provider)
    request = AgentRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="parity-model",
        temperature=0.15,
        max_tokens=48,
        tools=[{"type": "function", "function": {"name": "lookup"}}],
    )

    manager = _make_manager()
    manager.model_gateway = manager_gateway

    via_manager = await manager.run_agent_turn(request)
    direct = await BaseAgentRuntime(direct_gateway).run_turn(request)

    assert resolve_model_gateway(manager) is manager_gateway
    assert via_manager == direct
    assert manager_provider.complete_calls == direct_provider.complete_calls


@pytest.mark.asyncio
async def test_chat_manager_stream_agent_turn_matches_base_runtime() -> None:
    manager_provider = RecordingProvider()
    direct_provider = RecordingProvider()
    manager_gateway = _make_gateway(manager_provider)
    direct_gateway = _make_gateway(direct_provider)
    request = AgentRequest(
        messages=[{"role": "user", "content": "stream"}],
        model="parity-stream-model",
        temperature=0.05,
        max_tokens=24,
    )

    manager = _make_manager()
    manager.model_gateway = manager_gateway

    via_manager = [event async for event in manager.stream_agent_turn(request)]
    direct = [event async for event in BaseAgentRuntime(direct_gateway).stream_turn(request)]

    assert via_manager == direct
    assert manager_provider.stream_calls == direct_provider.stream_calls


@pytest.mark.asyncio
async def test_chat_manager_prefers_injected_agent_runtime() -> None:
    runtime = StubRuntime()
    manager = _make_manager(agent_runtime=runtime)
    request = AgentRequest(messages=[{"role": "user", "content": "delegate"}])

    result = await manager.run_agent_turn(request)
    events = [event async for event in manager.stream_agent_turn(request)]

    assert result.text == "stubbed"
    assert events == [
        AgentEvent(kind="text_delta", data="stub"),
        AgentEvent(kind="complete", data="stubbed"),
    ]
    assert runtime.requests == [request, request]
