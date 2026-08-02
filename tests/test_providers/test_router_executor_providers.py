"""Tests for RouterExecutor provider dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine.executor import EngineConfig, ExecutionContext
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import RouterExecutor
from dan.models.control_flow import RouterNode
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry


class FakeProvider:
    def __init__(self, response: str = "route_a") -> None:
        self.response = response
        self.calls: list[dict] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        self.calls.append({"model": model, "messages": messages})
        return CompletionResult(text=self.response, model=model)

    async def stream(self, messages, model, **kw):
        yield  # pragma: no cover


def _make_context(registry: ProviderRegistry | None = None) -> ExecutionContext:
    from dan.models.graph import Graph
    graph = Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "test"},
        "nodes": [], "edges": [], "sub_graphs": {},
        "entry_points": [], "exit_points": [],
        "shared_context": [], "artifact_refs": [],
    })
    return ExecutionContext(
        state=ExecutionState(graph),
        config=EngineConfig(llm_api_key="test-key"),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        provider_registry=registry,
    )


def _make_router_node(model: str = "gpt-4o") -> RouterNode:
    return RouterNode(
        id="router1", name="test-router",
        model=model,
        route_descriptions={"route_a": "First route", "route_b": "Second route"},
    )


@pytest.mark.asyncio
async def test_router_uses_provider_registry():
    provider = FakeProvider("route_a")
    reg = ProviderRegistry()
    reg.register("default", provider)

    ctx = _make_context(reg)
    executor = RouterExecutor()
    node = _make_router_node()
    result = await executor.execute(node, {"data": "test"}, ctx)
    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["route"] == "route_a"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_router_dispatches_anthropic_model():
    anthropic_p = FakeProvider("route_b")
    openai_p = FakeProvider("route_a")

    reg = ProviderRegistry()
    reg.register("openai", openai_p)
    reg.register("anthropic", anthropic_p)

    ctx = _make_context(reg)
    executor = RouterExecutor()
    node = _make_router_node("claude-sonnet-4")
    result = await executor.execute(node, {"data": "test"}, ctx)
    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["route"] == "route_b"
    assert len(anthropic_p.calls) == 1
    assert len(openai_p.calls) == 0


@pytest.mark.asyncio
async def test_router_backward_compat():
    """Router should work without provider_registry via direct AsyncOpenAI."""
    from unittest.mock import patch

    ctx = _make_context(registry=None)
    executor = RouterExecutor()
    node = _make_router_node()

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message.content = "route_a"

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        result = await executor.execute(node, {"data": "test"}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["route"] == "route_a"


@pytest.mark.asyncio
async def test_router_backward_compat_after_openai_provider_preimport():
    """Patching openai.AsyncOpenAI should still work after provider module import."""
    from unittest.mock import patch

    import dan.providers.openai_provider  # noqa: F401

    ctx = _make_context(registry=None)
    executor = RouterExecutor()
    node = _make_router_node()

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message.content = "route_a"

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        result = await executor.execute(node, {"data": "test"}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["route"] == "route_a"
