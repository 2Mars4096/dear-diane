"""Tests for 18-1 Tasks 7 and 9 (JIT schema loading + context tools)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.events import EventType
from dan.engine.executor import EngineConfig, ExecutionContext
from dan.engine.state import NodeStatus
from dan.executors.llm import LLMExecutor
from dan.models.nodes import LLMOperator
from dan.providers import CompletionResult


def _tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"{name} description",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        },
    }


def _tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class FakeProvider:
    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.call_history: list[dict[str, Any]] = []

    async def complete(self, *, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        self.call_history.append({"messages": messages, "kwargs": kwargs, "model": model})
        i = self._idx
        self._idx += 1
        return self._responses[i]

    async def stream(self, **kwargs):
        raise NotImplementedError


class FakeProviderRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider

    def resolve(self, model: str):
        return self._provider


def _context(cb: AsyncMock | None = None) -> ExecutionContext:
    return ExecutionContext(
        state=MagicMock(),
        config=EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        event_callback=cb or AsyncMock(),
        run_id="run-test",
    )


@pytest.mark.asyncio
async def test_jit_tool_loading_uses_catalog_and_resolver_tool():
    provider = FakeProvider([
        CompletionResult(
            text="",
            tool_calls=[_tool_call("get_tool_schema", {"tool_name": "tool_a"})],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
        CompletionResult(
            text="done",
            usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        ),
    ])
    cb = AsyncMock()
    ctx = _context(cb)
    ctx.provider_registry = FakeProviderRegistry(provider)

    node = LLMOperator(
        id="n1",
        name="llm",
        model="gpt-4o",
        prompt_template="Test",
        jit_tool_loading=True,
        tools=[_tool_schema("tool_a"), _tool_schema("tool_b"), _tool_schema("tool_c"), _tool_schema("tool_d")],
    )

    result = await LLMExecutor().execute(node, {}, ctx)
    assert result.status == NodeStatus.COMPLETED
    assert result.outputs["text"] == "done"

    first_tools = provider.call_history[0]["kwargs"]["tools"]
    names = [t["function"]["name"] for t in first_tools]
    assert "get_tool_schema" in names
    # Catalog entries should omit full parameter schemas.
    catalog_tool = next(t for t in first_tools if t["function"]["name"] == "tool_a")
    assert "parameters" not in catalog_tool["function"]

    second_messages = provider.call_history[1]["messages"]
    tool_msg = [m for m in second_messages if m.get("role") == "tool"][0]
    schema_payload = json.loads(tool_msg["content"])
    assert schema_payload["function"]["name"] == "tool_a"
    assert "parameters" in schema_payload["function"]

    emitted = [c[0][0].event_type for c in cb.call_args_list]
    assert EventType.JIT_SCHEMA_LOADED in emitted


@pytest.mark.asyncio
async def test_context_tools_injected_and_callable():
    provider = FakeProvider([
        CompletionResult(
            text="",
            tool_calls=[_tool_call("list_available_context", {})],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
        CompletionResult(
            text="ok",
            usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
        ),
    ])
    cb = AsyncMock()
    ctx = _context(cb)
    ctx.provider_registry = FakeProviderRegistry(provider)

    inputs = {
        "a": "very long context " * 200,
        "b": "small",
    }
    node = LLMOperator(
        id="n1",
        name="llm",
        model="gpt-4o",
        prompt_template="Use {b}",
        agent_context_tools=True,
        target_input_tokens=80,
    )

    result = await LLMExecutor().execute(node, inputs, ctx)
    assert result.status == NodeStatus.COMPLETED

    first_tools = provider.call_history[0]["kwargs"]["tools"]
    names = [t["function"]["name"] for t in first_tools]
    assert "list_available_context" in names
    assert "search_context" in names
    assert "read_context" in names
    assert "read_state" in names

    emitted = [c[0][0].event_type for c in cb.call_args_list]
    assert EventType.CONTEXT_TOOL_CALLED in emitted
    assert EventType.CONTEXT_DEFERRED in emitted


@pytest.mark.asyncio
async def test_backward_compat_no_jit_no_context_tools():
    provider = FakeProvider([
        CompletionResult(
            text="plain",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
    ])
    ctx = _context()
    ctx.provider_registry = FakeProviderRegistry(provider)

    node = LLMOperator(
        id="n1",
        name="llm",
        model="gpt-4o",
        prompt_template="Hello",
        tools=[_tool_schema("tool_a")],
        jit_tool_loading=False,
        agent_context_tools=False,
    )
    result = await LLMExecutor().execute(node, {}, ctx)
    assert result.status == NodeStatus.COMPLETED
    tools = provider.call_history[0]["kwargs"]["tools"]
    assert tools[0]["function"]["name"] == "tool_a"
    assert "parameters" in tools[0]["function"]
