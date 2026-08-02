"""Tests for BaseAgentRuntime — gateway delegation, streaming, and boundaries."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

from dan.agent_runtime.runtime import AgentRuntime, BaseAgentRuntime
from dan.agent_runtime.types import AgentEvent, AgentRequest, AgentResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_RUNTIME_DIR = PROJECT_ROOT / "src" / "dan" / "agent_runtime"


@dataclass
class _FakeCompletionResult:
    text: str = "Hello, world!"
    usage: dict[str, int] | None = None
    model: str = "gpt-4o"
    tool_calls: list[dict[str, Any]] | None = None
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    finish_reason: str = "stop"
    raw_assistant_message: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class _FakeStreamChunk:
    delta: str
    accumulated: str
    done: bool = False
    usage: dict[str, int] | None = None


def _make_gateway(
    complete_result: _FakeCompletionResult | None = None,
    stream_chunks: list[_FakeStreamChunk] | None = None,
) -> AsyncMock:
    gw = AsyncMock()
    gw.complete = AsyncMock(
        return_value=complete_result or _FakeCompletionResult()
    )

    async def _fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[_FakeStreamChunk]:
        chunks = stream_chunks or [
            _FakeStreamChunk(delta="Hi", accumulated="Hi"),
            _FakeStreamChunk(delta=" there", accumulated="Hi there", done=True),
        ]
        for c in chunks:
            yield c

    gw.stream = _fake_stream
    return gw


class TestRunTurn:
    @pytest.mark.asyncio
    async def test_delegates_to_gateway(self) -> None:
        gw = _make_gateway(
            _FakeCompletionResult(
                text="Response",
                model="gpt-4o",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )
        )
        rt = BaseAgentRuntime(gateway=gw)
        req = AgentRequest(
            messages=[{"role": "user", "content": "test"}],
            model="gpt-4o",
            temperature=0.5,
            max_tokens=100,
        )

        result = await rt.run_turn(req)

        gw.complete.assert_awaited_once()
        call_args = gw.complete.call_args
        assert call_args[0][0] == req.messages
        assert call_args[0][1] == "gpt-4o"
        assert call_args[1]["temperature"] == 0.5
        assert call_args[1]["max_tokens"] == 100
        assert result.text == "Response"
        assert result.model_used == "gpt-4o"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 5}

    @pytest.mark.asyncio
    async def test_without_gateway_returns_error(self) -> None:
        rt = BaseAgentRuntime()
        req = AgentRequest(messages=[{"role": "user", "content": "hi"}])

        result = await rt.run_turn(req)

        assert result.error == "No model gateway configured"
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_with_tools_forwards_to_gateway(self) -> None:
        gw = _make_gateway()
        rt = BaseAgentRuntime(gateway=gw)
        tools = [{"type": "function", "function": {"name": "search"}}]
        req = AgentRequest(
            messages=[{"role": "user", "content": "find me X"}],
            tools=tools,
        )

        await rt.run_turn(req)

        call_kwargs = gw.complete.call_args[1]
        assert call_kwargs["tools"] == tools

    @pytest.mark.asyncio
    async def test_without_tools_no_tools_kwarg(self) -> None:
        gw = _make_gateway()
        rt = BaseAgentRuntime(gateway=gw)
        req = AgentRequest(messages=[{"role": "user", "content": "hi"}])

        await rt.run_turn(req)

        call_kwargs = gw.complete.call_args[1]
        assert "tools" not in call_kwargs

    @pytest.mark.asyncio
    async def test_model_fallback_from_result(self) -> None:
        """If gateway returns a different model name, AgentResult reflects it."""
        gw = _make_gateway(
            _FakeCompletionResult(text="ok", model="gpt-4o-2024-08-06")
        )
        rt = BaseAgentRuntime(gateway=gw)
        req = AgentRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
        )

        result = await rt.run_turn(req)
        assert result.model_used == "gpt-4o-2024-08-06"

    @pytest.mark.asyncio
    async def test_model_used_falls_back_to_request_model(self) -> None:
        """If CompletionResult.model is empty, use the request model."""
        gw = _make_gateway(
            _FakeCompletionResult(text="ok", model="")
        )
        rt = BaseAgentRuntime(gateway=gw)
        req = AgentRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )

        result = await rt.run_turn(req)
        assert result.model_used == "gpt-4o-mini"


class TestStreamTurn:
    @pytest.mark.asyncio
    async def test_yields_events(self) -> None:
        gw = _make_gateway(
            stream_chunks=[
                _FakeStreamChunk(delta="Hello", accumulated="Hello"),
                _FakeStreamChunk(
                    delta=" world", accumulated="Hello world", done=True
                ),
            ]
        )
        rt = BaseAgentRuntime(gateway=gw)
        req = AgentRequest(messages=[{"role": "user", "content": "hi"}])

        events: list[AgentEvent] = []
        async for ev in rt.stream_turn(req):
            events.append(ev)

        assert len(events) == 2
        assert events[0].kind == "text_delta"
        assert events[0].data == "Hello"
        assert events[1].kind == "complete"
        assert events[1].data == "Hello world"

    @pytest.mark.asyncio
    async def test_stream_without_gateway_yields_error(self) -> None:
        rt = BaseAgentRuntime()
        req = AgentRequest(messages=[{"role": "user", "content": "hi"}])

        events: list[AgentEvent] = []
        async for ev in rt.stream_turn(req):
            events.append(ev)

        assert len(events) == 1
        assert events[0].kind == "error"
        assert "No model gateway" in str(events[0].data)

    @pytest.mark.asyncio
    async def test_stream_with_tools(self) -> None:
        gw = _make_gateway()
        rt = BaseAgentRuntime(gateway=gw)
        tools = [{"type": "function", "function": {"name": "calc"}}]
        req = AgentRequest(
            messages=[{"role": "user", "content": "compute"}],
            tools=tools,
        )

        events: list[AgentEvent] = []
        async for ev in rt.stream_turn(req):
            events.append(ev)

        assert len(events) >= 1


class TestProtocolConformance:
    def test_base_runtime_satisfies_protocol(self) -> None:
        assert isinstance(BaseAgentRuntime(), AgentRuntime)


class TestImportBoundary:
    """AST-walk agent_runtime/ and verify no forbidden imports."""

    FORBIDDEN_PREFIXES = [
        "dan.server.",
        "dan.server",
        "dan.cli.",
        "dan.cli",
        "dan.engine.",
        "dan.engine",
        "dan.executors.",
        "dan.executors",
    ]

    def test_agent_runtime_no_server_imports(self) -> None:
        violations: list[str] = []
        for py_file in sorted(AGENT_RUNTIME_DIR.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            try:
                tree = ast.parse(
                    py_file.read_text(encoding="utf-8"), filename=str(py_file)
                )
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                targets: list[str] = []
                if isinstance(node, ast.Import):
                    targets = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    targets = [node.module]

                for target in targets:
                    for prefix in self.FORBIDDEN_PREFIXES:
                        if target == prefix or target.startswith(prefix + ".") or target.startswith(prefix):
                            rel = py_file.relative_to(AGENT_RUNTIME_DIR)
                            violations.append(
                                f"  {rel}:{node.lineno} -> {target}"
                            )

        assert not violations, (
            f"agent_runtime/ must not import from server/cli/engine/executors:\n"
            + "\n".join(violations)
        )
