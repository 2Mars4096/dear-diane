"""Integration test — graph with mixed models runs with mocked providers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine import Engine, EngineConfig
from dan.engine.executor import ExecutorRegistry
from dan.models.graph import Graph
from dan.providers import CompletionResult, ProviderConfig, StreamChunk
from dan.providers.registry import ProviderRegistry


class MockProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        self.calls.append(model)
        return CompletionResult(
            text=f"[{self.name}] output for {model}",
            model=model,
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )

    async def stream(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        self.calls.append(model)
        text = f"[{self.name}] streamed"
        yield StreamChunk(delta=text, accumulated=text)
        yield StreamChunk(delta="", accumulated=text, done=True, usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})


def _build_mixed_model_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "mixed-models"},
        "nodes": [
            {
                "id": "node_claude",
                "node_type": "llm_operator",
                "name": "Claude Node",
                "model": "claude-sonnet-4",
                "prompt_template": "Summarize: {input}",
                "input_ports": [{"name": "input", "schema": {}}],
                "output_ports": [{"name": "text", "schema": {}}],
            },
            {
                "id": "node_gpt",
                "node_type": "llm_operator",
                "name": "GPT Node",
                "model": "gpt-4o",
                "prompt_template": "Refine: {text}",
                "input_ports": [{"name": "text", "schema": {}}],
                "output_ports": [{"name": "text", "schema": {}}],
            },
        ],
        "edges": [
            {
                "id": "e1",
                "edge_type": "data",
                "source_node_id": "node_claude",
                "source_port": "text",
                "target_node_id": "node_gpt",
                "target_port": "text",
            },
        ],
        "sub_graphs": {},
        "entry_points": ["node_claude"],
        "exit_points": ["node_gpt"],
        "shared_context": [],
        "artifact_refs": [],
    }


@pytest.mark.asyncio
async def test_mixed_model_graph_routes_correctly():
    """Two LLM nodes with different models route to different providers."""
    anthropic_p = MockProvider("anthropic")
    openai_p = MockProvider("openai")

    config = EngineConfig(
        llm_api_key="test-key",
        llm_default_model="gpt-4o",
        checkpoint_enabled=False,
    )
    engine = Engine(config=config, checkpoint_store=None)

    engine.provider_registry = ProviderRegistry()
    engine.provider_registry.register("openai", openai_p)
    engine.provider_registry.register("anthropic", anthropic_p)
    engine.provider_registry.register("default", openai_p)

    graph_data = _build_mixed_model_graph()
    graph = Graph.model_validate(graph_data)

    result = await engine.run(graph, inputs={"input": "test data"})
    assert result.success
    assert "claude-sonnet-4" in anthropic_p.calls
    assert "gpt-4o" in openai_p.calls


@pytest.mark.asyncio
async def test_single_api_key_backward_compat():
    """Existing single-key config still works — all models go through default provider."""
    config = EngineConfig(
        llm_api_key="test-key",
        llm_base_url="https://api.example.com/v1",
        llm_default_model="claude-sonnet-4-6",
        checkpoint_enabled=False,
    )
    engine = Engine(config=config, checkpoint_store=None)

    assert engine.provider_registry.has_provider("default")

    default_provider = engine.provider_registry.resolve("claude-sonnet-4-6")
    assert default_provider is not None

    unknown_model_provider = engine.provider_registry.resolve("my-custom-model")
    assert unknown_model_provider is default_provider
