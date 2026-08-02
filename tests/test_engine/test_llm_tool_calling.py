"""Tests for 18-1 Task 0: LLMExecutor generic tool-calling loop."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.engine.events import EventType
from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.state import NodeStatus
from dan.executors.llm import LLMExecutor
from dan.executors.tool import ToolRegistry
from dan.models.nodes import LLMOperator, RetryPolicy
from dan.models.ports import OutputPort
from dan.providers import CompletionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(
    event_callback: AsyncMock | None = None,
    tool_registry: ToolRegistry | None = None,
) -> ExecutionContext:
    ctx = ExecutionContext(
        state=MagicMock(),
        config=EngineConfig(llm_api_key="test-key", checkpoint_enabled=False),
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        event_callback=event_callback or AsyncMock(),
        run_id="test-run",
        tool_registry=tool_registry,
    )
    return ctx


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


WEATHER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}

SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search",
        "description": "Search the web",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


class FakeProvider:
    """Fake provider that returns pre-programmed CompletionResult sequences."""

    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)
        self._call_idx = 0
        self.call_history: list[dict[str, Any]] = []

    async def complete(self, *, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        idx = self._call_idx
        self._call_idx += 1
        self.call_history.append({"messages": messages, "model": model, "kwargs": kwargs})
        if idx < len(self._responses):
            return self._responses[idx]
        return CompletionResult(text="<no more responses>", usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    async def stream(self, *, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        raise NotImplementedError("Streaming not used in tool-calling tests")


class FakeProviderRegistry:
    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider

    def resolve(self, model: str):
        return self._provider


class FakePIISession:
    def __init__(self, key: str) -> None:
        self.key = key

    def tokenize(self, text: str) -> str:
        return text.replace("secret", "[PII]")

    def detokenize(self, text: str) -> str:
        return text.replace("[PII]", "secret")


# ===========================================================================
# 1. Backward compatibility — no tools
# ===========================================================================

class TestNoToolsBackwardCompat:
    @pytest.mark.asyncio
    async def test_plain_text_no_tools(self):
        """Nodes without tools work exactly as before."""
        provider = FakeProvider([
            CompletionResult(
                text="Hello world",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ])
        ctx = _make_context()
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o", prompt_template="Say hello",
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["text"] == "Hello world"
        assert result.metadata["usage"]["total_tokens"] == 15

    @pytest.mark.asyncio
    async def test_empty_tools_list_same_as_no_tools(self):
        """Explicitly empty tools list has identical behavior."""
        provider = FakeProvider([
            CompletionResult(text="Hi", usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}),
        ])
        ctx = _make_context()
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o", prompt_template="Hello",
            tools=[],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["text"] == "Hi"

    @pytest.mark.asyncio
    async def test_plain_text_aliases_single_declared_output_port(self):
        provider = FakeProvider([
            CompletionResult(
                text="# Report\n\nAll checks passed.",
                usage={"prompt_tokens": 11, "completion_tokens": 9, "total_tokens": 20},
            ),
        ])
        ctx = _make_context()
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1",
            name="report",
            model="gpt-4o",
            prompt_template="Write a short markdown report",
            output_ports=[OutputPort(name="markdown")],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["text"] == "# Report\n\nAll checks passed."
        assert result.outputs["markdown"] == "# Report\n\nAll checks passed."

    @pytest.mark.asyncio
    async def test_legacy_extended_thinking_tier_param_is_not_forwarded(self):
        provider = FakeProvider([
            CompletionResult(
                text="Hello world",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ])
        ctx = _make_context()
        ctx.provider_registry = FakeProviderRegistry(provider)
        ctx.model_selector = SimpleNamespace(
            resolve_effective_policy=lambda node, config: SimpleNamespace(strategy="test"),
            select=AsyncMock(
                return_value=SimpleNamespace(
                    model="gpt-4o",
                    tier_result=None,
                    tier_params={"extended_thinking": True, "max_tokens": 8192},
                )
            ),
        )

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o", prompt_template="Say hello",
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert provider.call_history
        assert "extended_thinking" not in provider.call_history[-1]["kwargs"]


class TestPIISessionKeyResolution:
    @pytest.mark.asyncio
    async def test_resolve_provider_uses_public_workflow_id_for_pii_session(self, monkeypatch):
        from dan.llm_core import pii_tokenizer

        provider = FakeProvider([
            CompletionResult(text="ok", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
        ])
        ctx = _make_context()
        ctx.provider_registry = FakeProviderRegistry(provider)
        ctx.run_id = ""
        ctx.workflow_id = "wf-public"

        captured: dict[str, Any] = {}

        monkeypatch.setattr(pii_tokenizer, "is_pii_enabled", lambda: True)

        def fake_get_pii_session(key: str):
            captured["key"] = key
            return FakePIISession(key)

        monkeypatch.setattr(pii_tokenizer, "get_pii_session", fake_get_pii_session)

        executor = LLMExecutor()
        resolved = executor._resolve_provider("gpt-4o", ctx)
        result = await resolved.complete(
            messages=[{"role": "user", "content": "secret"}],
            model="gpt-4o",
        )

        assert captured["key"] == "wf-public"
        assert result.text == "ok"
        assert provider.call_history
        assert provider.call_history[0]["messages"][0]["content"] == "[PII]"

    @pytest.mark.asyncio
    async def test_resolve_provider_fails_closed_when_pii_wrapper_breaks(self, monkeypatch):
        from dan.llm_core import pii_tokenizer

        provider = FakeProvider([
            CompletionResult(text="ok", usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}),
        ])
        ctx = _make_context()
        ctx.provider_registry = FakeProviderRegistry(provider)

        monkeypatch.setenv("DAN_PII_PROTECTION", "1")
        monkeypatch.setattr(pii_tokenizer, "is_pii_enabled", lambda: True)

        def broken_get_session(_key: str):
            raise RuntimeError("session load failed")

        monkeypatch.setattr(pii_tokenizer, "get_pii_session", broken_get_session)

        executor = LLMExecutor()
        resolved = executor._resolve_provider("gpt-4o", ctx)

        with pytest.raises(RuntimeError, match="PII protection is enabled"):
            await resolved.complete(
                messages=[{"role": "user", "content": "secret"}],
                model="gpt-4o",
            )


# ===========================================================================
# 2. Single-round tool calling
# ===========================================================================

class TestSingleRoundToolCalling:
    @pytest.mark.asyncio
    async def test_single_tool_call_and_final_text(self):
        """Model calls one tool, gets result, returns final text."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"})],
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            ),
            CompletionResult(
                text="The weather in NYC is sunny.",
                usage={"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45},
            ),
        ])

        async def get_weather(city: str) -> dict:
            return {"temp": 72, "condition": "sunny"}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)

        ctx = _make_context(tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="What's the weather in NYC?",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["text"] == "The weather in NYC is sunny."
        assert result.metadata["usage"]["total_tokens"] == 75
        assert provider._call_idx == 2

    @pytest.mark.asyncio
    async def test_tools_passed_to_provider(self):
        """Verify that tool schemas are passed to provider.complete() as kwargs."""
        provider = FakeProvider([
            CompletionResult(text="No tools needed", usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}),
        ])
        ctx = _make_context()
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Hello",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        await executor.execute(node, {}, ctx)

        assert provider.call_history[0]["kwargs"]["tools"] == [WEATHER_TOOL_SCHEMA]


# ===========================================================================
# 3. Multi-round tool calling
# ===========================================================================

class TestMultiRoundToolCalling:
    @pytest.mark.asyncio
    async def test_two_round_tool_calling(self):
        """Model calls tool -> gets result -> calls another tool -> returns text."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"}, "call_1")],
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            ),
            CompletionResult(
                text="",
                tool_calls=[_tool_call("search", {"query": "NYC restaurants"}, "call_2")],
                usage={"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
            ),
            CompletionResult(
                text="NYC is sunny. Here are restaurants: ...",
                usage={"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
            ),
        ])

        async def get_weather(city: str) -> dict:
            return {"temp": 72, "condition": "sunny"}

        async def search(query: str) -> dict:
            return {"results": ["Restaurant A", "Restaurant B"]}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)
        registry.register("search", search)

        ctx = _make_context(tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Weather and restaurants in NYC?",
            tools=[WEATHER_TOOL_SCHEMA, SEARCH_TOOL_SCHEMA],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "NYC is sunny" in result.outputs["text"]
        assert result.metadata["usage"]["total_tokens"] == 130
        assert provider._call_idx == 3

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_single_round(self):
        """Model calls two tools in one round (parallel tool calls)."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[
                    _tool_call("get_weather", {"city": "NYC"}, "call_1"),
                    _tool_call("get_weather", {"city": "LA"}, "call_2"),
                ],
                usage={"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
            ),
            CompletionResult(
                text="NYC: sunny, LA: cloudy",
                usage={"prompt_tokens": 40, "completion_tokens": 10, "total_tokens": 50},
            ),
        ])

        async def get_weather(city: str) -> dict:
            return {"temp": 72, "condition": "sunny" if city == "NYC" else "cloudy"}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)

        ctx = _make_context(tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Weather in NYC and LA?",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "NYC" in result.outputs["text"]

        second_call_messages = provider.call_history[1]["messages"]
        tool_result_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        assert len(tool_result_msgs) == 2


# ===========================================================================
# 4. max_tool_rounds safety bound
# ===========================================================================

class TestMaxToolRounds:
    @pytest.mark.asyncio
    async def test_max_tool_rounds_exceeded(self):
        """Loop terminates when max_tool_rounds is reached."""
        responses = [
            CompletionResult(
                text="",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"}, f"call_{i}")],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
            for i in range(5)
        ]
        provider = FakeProvider(responses)

        async def get_weather(city: str) -> dict:
            return {"temp": 72}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)

        ctx = _make_context(tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Weather?",
            tools=[WEATHER_TOOL_SCHEMA],
            max_tool_rounds=3,
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.FAILED
        assert "max_tool_rounds" in (result.error or "")


# ===========================================================================
# 5. Tool not found error handling
# ===========================================================================

class TestToolNotFound:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_in_result(self):
        """When tool is not in registry, error JSON is returned as tool result."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("nonexistent_tool", {"arg": "val"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            CompletionResult(
                text="Tool was not found, sorry.",
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            ),
        ])

        ctx = _make_context(tool_registry=ToolRegistry())
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Do something",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        second_call_messages = provider.call_history[1]["messages"]
        tool_msg = [m for m in second_call_messages if m.get("role") == "tool"][0]
        assert "not found" in tool_msg["content"]

    @pytest.mark.asyncio
    async def test_no_tool_registry_returns_error(self):
        """When context has no tool_registry at all, error JSON is returned."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            CompletionResult(
                text="I couldn't look up the weather.",
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            ),
        ])

        ctx = _make_context()
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Weather?",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        second_call_messages = provider.call_history[1]["messages"]
        tool_msg = [m for m in second_call_messages if m.get("role") == "tool"][0]
        assert "not found" in tool_msg["content"]


# ===========================================================================
# 6. Event emission
# ===========================================================================

class TestToolCallEvents:
    @pytest.mark.asyncio
    async def test_tool_call_events_emitted(self):
        """TOOL_CALL_STARTED and TOOL_CALL_RESULT events are emitted for each tool call."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            CompletionResult(
                text="Sunny!",
                usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            ),
        ])

        async def get_weather(city: str) -> dict:
            return {"temp": 72}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)

        cb = AsyncMock()
        ctx = _make_context(event_callback=cb, tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Weather?",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        await executor.execute(node, {}, ctx)

        started_events = [
            c for c in cb.call_args_list
            if c[0][0].event_type == EventType.TOOL_CALL_STARTED
        ]
        result_events = [
            c for c in cb.call_args_list
            if c[0][0].event_type == EventType.TOOL_CALL_RESULT
        ]
        assert len(started_events) == 1
        assert len(result_events) == 1
        assert started_events[0][0][0].data["tool_name"] == "get_weather"
        assert result_events[0][0][0].data["tool_name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_multi_round_events(self):
        """Events are emitted for each tool call across multiple rounds."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"}, "call_1")],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            CompletionResult(
                text="",
                tool_calls=[_tool_call("search", {"query": "NYC"}, "call_2")],
                usage={"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
            ),
            CompletionResult(text="Done!", usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}),
        ])

        async def get_weather(city: str) -> dict:
            return {"temp": 72}

        async def search(query: str) -> dict:
            return {"results": []}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)
        registry.register("search", search)

        cb = AsyncMock()
        ctx = _make_context(event_callback=cb, tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Info?",
            tools=[WEATHER_TOOL_SCHEMA, SEARCH_TOOL_SCHEMA],
        )
        await executor.execute(node, {}, ctx)

        started_events = [
            c for c in cb.call_args_list
            if c[0][0].event_type == EventType.TOOL_CALL_STARTED
        ]
        result_events = [
            c for c in cb.call_args_list
            if c[0][0].event_type == EventType.TOOL_CALL_RESULT
        ]
        assert len(started_events) == 2
        assert len(result_events) == 2


# ===========================================================================
# 7. Cumulative usage tracking
# ===========================================================================

class TestCumulativeUsage:
    @pytest.mark.asyncio
    async def test_usage_includes_all_rounds(self):
        """Cumulative usage includes tokens from initial call + tool loop rounds."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"})],
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            ),
            CompletionResult(
                text="Result",
                usage={"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            ),
        ])

        async def get_weather(city: str) -> dict:
            return {"temp": 72}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)

        ctx = _make_context(tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Weather?",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.metadata["usage"]["prompt_tokens"] == 300
        assert result.metadata["usage"]["completion_tokens"] == 150
        assert result.metadata["usage"]["total_tokens"] == 450

    @pytest.mark.asyncio
    async def test_cost_tracker_called_for_each_round(self):
        """CostTracker.record() is called for each LLM call (initial + tool loop)."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            CompletionResult(
                text="Done",
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            ),
        ])

        async def get_weather(city: str) -> dict:
            return {"temp": 72}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)

        cost_tracker = MagicMock()
        ctx = _make_context(tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)
        ctx.cost_tracker = cost_tracker

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Weather?",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        await executor.execute(node, {}, ctx)

        assert cost_tracker.record.call_count == 2


# ===========================================================================
# 8. Model fields
# ===========================================================================

class TestLLMOperatorToolFields:
    def test_default_tools_empty(self):
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o", prompt_template="hello",
        )
        assert node.tools == []
        assert node.max_tool_rounds == 10

    def test_tools_serialization_roundtrip(self):
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o", prompt_template="hello",
            tools=[WEATHER_TOOL_SCHEMA],
            max_tool_rounds=5,
        )
        data = node.model_dump()
        restored = LLMOperator.model_validate(data)
        assert restored.tools == [WEATHER_TOOL_SCHEMA]
        assert restored.max_tool_rounds == 5


class TestExecutionContextToolRegistry:
    def test_context_exposes_tool_registry(self):
        registry = ToolRegistry()
        ctx = _make_context(tool_registry=registry)
        assert ctx.tool_registry is registry


# ===========================================================================
# 9. Tool execution error handling
# ===========================================================================

class TestToolExecutionErrors:
    @pytest.mark.asyncio
    async def test_tool_exception_returns_error_json(self):
        """When a tool raises an exception, an error JSON is returned to the model."""
        provider = FakeProvider([
            CompletionResult(
                text="",
                tool_calls=[_tool_call("failing_tool", {"arg": "val"})],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            CompletionResult(
                text="The tool failed but I can handle it.",
                usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            ),
        ])

        async def failing_tool(arg: str) -> dict:
            raise RuntimeError("Tool crashed!")

        registry = ToolRegistry()
        registry.register("failing_tool", failing_tool)

        ctx = _make_context(tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Do it",
            tools=[{"type": "function", "function": {"name": "failing_tool", "parameters": {}}}],
        )
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        second_call_msgs = provider.call_history[1]["messages"]
        tool_msg = [m for m in second_call_msgs if m.get("role") == "tool"][0]
        error_data = json.loads(tool_msg["content"])
        assert "Tool crashed!" in error_data["error"]


# ===========================================================================
# 10. Message structure verification
# ===========================================================================

class TestMessageStructure:
    @pytest.mark.asyncio
    async def test_assistant_tool_calls_and_tool_results_in_messages(self):
        """Verify the conversation structure: assistant with tool_calls + tool results."""
        provider = FakeProvider([
            CompletionResult(
                text="thinking...",
                tool_calls=[_tool_call("get_weather", {"city": "NYC"}, "tc_1")],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
            CompletionResult(
                text="Final answer",
                usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            ),
        ])

        async def get_weather(city: str) -> dict:
            return {"temp": 72}

        registry = ToolRegistry()
        registry.register("get_weather", get_weather)

        ctx = _make_context(tool_registry=registry)
        ctx.provider_registry = FakeProviderRegistry(provider)

        executor = LLMExecutor()
        node = LLMOperator(
            id="n1", name="test", model="gpt-4o",
            prompt_template="Weather?",
            tools=[WEATHER_TOOL_SCHEMA],
        )
        await executor.execute(node, {}, ctx)

        second_call_messages = provider.call_history[1]["messages"]

        assistant_msgs = [m for m in second_call_messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) >= 1
        last_assistant = assistant_msgs[-1]
        assert "tool_calls" in last_assistant
        assert last_assistant["tool_calls"][0]["id"] == "tc_1"

        tool_msgs = [m for m in second_call_messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "tc_1"
        result_data = json.loads(tool_msgs[0]["content"])
        assert result_data["temp"] == 72
