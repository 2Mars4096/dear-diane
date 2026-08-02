"""Tests for ChatCapabilityRegistry, CapabilityContext, and base handlers (25-1)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dan.agent_runtime.capability_calls import (
    PendingCapabilityCall,
    annotate_capability_call_plan,
    capability_cache_key,
    execute_capability_call,
)
from dan.server.capability_registry import (
    ALL_MODES,
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
    build_tool_schema,
)
from dan.server.capability_handlers import (
    handle_get_activity,
    handle_list_graphs,
    register_base_capabilities,
)


# ── Registry unit tests ────────────────────────────────────────────

def _dummy_schema(name: str) -> dict:
    return build_tool_schema(name, f"Test tool {name}", {"type": "object", "properties": {}})


async def _dummy_handler(args, ctx):
    return CapabilityResult(success=True, message="ok")


@dataclass
class _FakeCapabilityResult:
    success: bool = True
    message: str = "ok"
    output_preview: str = ""
    data: object = None
    retryable: bool = False
    error_type: str = ""


@pytest.mark.asyncio
async def test_file_read_capability_cache_requires_matching_fingerprint() -> None:
    pending = annotate_capability_call_plan(
        [PendingCapabilityCall("file_read", {"path": "notes.md"}, '{"path": "notes.md"}', "event_1")],
        is_cacheable=lambda name: True,
        cache_key_for=capability_cache_key,
    )[0]
    cached = _FakeCapabilityResult(message="cached file")

    async def _dispatch(_tool_name: str, _args: object) -> _FakeCapabilityResult:
        raise AssertionError("stale dispatch should not run for matching fingerprint")

    outcome = await execute_capability_call(
        pending,
        dispatch=_dispatch,
        make_error_result=lambda exc: _FakeCapabilityResult(success=False, message=str(exc)),
        tool_result_cache={pending.cache_key: _FakeCapabilityResult(message="untrusted exact cache")},
        file_read_cache={"notes.md": [(1, float("inf"), ("fingerprint", 1), cached)]},
        file_fingerprint_for=lambda _path: ("fingerprint", 1),
        max_retryable_retries=1,
    )

    assert outcome.cache_hit is True
    assert outcome.cap_result.message == "cached file"


@pytest.mark.asyncio
async def test_file_read_capability_cache_rereads_on_fingerprint_change() -> None:
    pending = annotate_capability_call_plan(
        [PendingCapabilityCall("file_read", {"path": "notes.md"}, '{"path": "notes.md"}', "event_1")],
        is_cacheable=lambda name: True,
        cache_key_for=capability_cache_key,
    )[0]
    calls = 0

    async def _dispatch(_tool_name: str, _args: object) -> _FakeCapabilityResult:
        nonlocal calls
        calls += 1
        return _FakeCapabilityResult(
            message="fresh file",
            data={"returned_start_line": 1, "returned_end_line": 4, "truncated": False},
        )

    file_read_cache: dict[str, list[tuple[int, float, object, _FakeCapabilityResult]]] = {
        "notes.md": [(1, float("inf"), ("old", 1), _FakeCapabilityResult(message="stale"))]
    }
    outcome = await execute_capability_call(
        pending,
        dispatch=_dispatch,
        make_error_result=lambda exc: _FakeCapabilityResult(success=False, message=str(exc)),
        tool_result_cache={pending.cache_key: _FakeCapabilityResult(message="untrusted exact cache")},
        file_read_cache=file_read_cache,
        file_fingerprint_for=lambda _path: ("new", 2),
        max_retryable_retries=1,
    )

    assert calls == 1
    assert outcome.cache_hit is False
    assert outcome.cap_result.message == "fresh file"
    assert file_read_cache["notes.md"][-1][2] == ("new", 2)


class TestChatCapabilityRegistry:

    def test_register_and_get_tools(self):
        reg = ChatCapabilityRegistry()
        reg.register("foo", _dummy_schema("foo"), _dummy_handler, modes=["agent", "build"])
        reg.register("bar", _dummy_schema("bar"), _dummy_handler, modes=["ask"])

        agent_tools = reg.get_tools("agent")
        assert len(agent_tools) == 1
        names = {t["function"]["name"] for t in agent_tools}
        assert names == {"foo"}

        ask_tools = reg.get_tools("ask")
        assert len(ask_tools) == 1
        assert ask_tools[0]["function"]["name"] == "bar"

    def test_get_handler(self):
        reg = ChatCapabilityRegistry()
        reg.register("foo", _dummy_schema("foo"), _dummy_handler)
        assert reg.get_handler("foo") is _dummy_handler
        assert reg.get_handler("nonexistent") is None

    def test_is_available(self):
        reg = ChatCapabilityRegistry()
        reg.register("foo", _dummy_schema("foo"), _dummy_handler, modes=["agent"])
        assert reg.is_available("foo", "agent") is True
        assert reg.is_available("foo", "ask") is False
        assert reg.is_available("nope", "agent") is False

    def test_list_tool_names(self):
        reg = ChatCapabilityRegistry()
        reg.register("a", _dummy_schema("a"), _dummy_handler, modes=["ask", "agent"])
        reg.register("b", _dummy_schema("b"), _dummy_handler, modes=["agent"])
        assert sorted(reg.list_tool_names()) == ["a", "b"]
        assert reg.list_tool_names("ask") == ["a"]
        assert sorted(reg.list_tool_names("agent")) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_execute_success(self):
        reg = ChatCapabilityRegistry()
        reg.register("foo", _dummy_schema("foo"), _dummy_handler, modes=["agent"])
        ctx = CapabilityContext(workflow_id="test")
        result = await reg.execute("foo", {}, ctx, mode="agent")
        assert result.success is True
        assert result.message == "ok"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        reg = ChatCapabilityRegistry()
        ctx = CapabilityContext(workflow_id="test")
        result = await reg.execute("missing", {}, ctx)
        assert result.success is False
        assert "Unknown" in result.message

    @pytest.mark.asyncio
    async def test_execute_rejects_wrong_mode(self):
        reg = ChatCapabilityRegistry()
        reg.register("foo", _dummy_schema("foo"), _dummy_handler, modes=["agent"])
        ctx = CapabilityContext(workflow_id="test")
        result = await reg.execute("foo", {}, ctx, mode="ask")
        assert result.success is False
        assert "not available" in result.message

    @pytest.mark.asyncio
    async def test_execute_handler_exception(self):
        async def failing_handler(args, ctx):
            raise ValueError("boom")

        reg = ChatCapabilityRegistry()
        reg.register("fail", _dummy_schema("fail"), failing_handler)
        ctx = CapabilityContext(workflow_id="test")
        result = await reg.execute("fail", {}, ctx)
        assert result.success is False
        assert "boom" in result.message

    def test_default_modes_all(self):
        reg = ChatCapabilityRegistry()
        reg.register("open", _dummy_schema("open"), _dummy_handler)
        for mode in ALL_MODES:
            assert reg.is_available("open", mode)


# ── Mode filtering: ask/plan must NOT get mutation tool ────────────

class TestModeFiltering:

    def test_ask_mode_excludes_mutations(self):
        reg = ChatCapabilityRegistry()
        register_base_capabilities(reg)
        ask_names = reg.list_tool_names("ask")
        assert "list_graphs" in ask_names
        assert "get_activity" in ask_names

    def test_ask_mode_includes_read_only_web_search(self):
        reg = ChatCapabilityRegistry()
        register_base_capabilities(reg)
        ask_names = reg.list_tool_names("ask")
        assert "web_search" in ask_names

    def test_base_capabilities_registered(self):
        reg = ChatCapabilityRegistry()
        register_base_capabilities(reg)
        assert reg.get_handler("list_graphs") is not None
        assert reg.get_handler("get_activity") is not None


# ── Handler unit tests ─────────────────────────────────────────────

class TestListGraphsHandler:

    @pytest.mark.asyncio
    async def test_no_graphs(self):
        class MockStore:
            def list_graphs(self):
                return []

        ctx = CapabilityContext(workflow_id="test", graph_store=MockStore())
        result = await handle_list_graphs({}, ctx)
        assert result.success is True
        assert "No workflows" in result.message

    @pytest.mark.asyncio
    async def test_with_graphs(self):
        class MockStore:
            def list_graphs(self):
                return [
                    {"graph_id": "wf1", "nodes": [1, 2], "edges": [1]},
                    {"graph_id": "wf2", "nodes": [], "edges": []},
                ]

        ctx = CapabilityContext(workflow_id="test", graph_store=MockStore())
        result = await handle_list_graphs({}, ctx)
        assert result.success is True
        assert "2 workflow" in result.message
        assert "wf1" in result.message

    @pytest.mark.asyncio
    async def test_no_store(self):
        ctx = CapabilityContext(workflow_id="test")
        result = await handle_list_graphs({}, ctx)
        assert result.success is False


class TestGetActivityHandler:

    @pytest.mark.asyncio
    async def test_with_activity(self):
        class MockTracker:
            def get_activity(self):
                return {
                    "active": [{"run_id": "r1", "graph_id": "wf1", "status": "running"}],
                    "recent": [],
                    "connected_surfaces": [],
                }

        ctx = CapabilityContext(workflow_id="test", activity_tracker=MockTracker())
        result = await handle_get_activity({}, ctx)
        assert result.success is True
        assert "r1" in result.message

    @pytest.mark.asyncio
    async def test_no_activity(self):
        class MockTracker:
            def get_activity(self):
                return {"active": [], "recent": [], "connected_surfaces": []}

        ctx = CapabilityContext(workflow_id="test", activity_tracker=MockTracker())
        result = await handle_get_activity({}, ctx)
        assert result.success is True
        assert "No active runs" in result.message

    @pytest.mark.asyncio
    async def test_no_tracker(self):
        ctx = CapabilityContext(workflow_id="test")
        result = await handle_get_activity({}, ctx)
        assert result.success is False


# ── _extract_capability_tool_call integration ──────────────────────

class TestExtractCapabilityToolCall:
    """Verify the ChatManager method that routes LLM tool calls to capabilities."""

    def _make_manager(self):
        from unittest.mock import MagicMock
        from dan.server.chat_manager import ChatManager

        reg = ChatCapabilityRegistry()
        reg.register("list_graphs", _dummy_schema("list_graphs"), _dummy_handler, modes=["agent", "ask"])
        ctx = CapabilityContext(workflow_id="test")
        mgr = ChatManager.__new__(ChatManager)
        mgr._capability_registry = reg
        mgr._capability_context = ctx
        return mgr

    def _make_result(self, tool_name: str, arguments: str = "{}"):
        """Build a fake CompletionResult with a tool call."""
        from types import SimpleNamespace
        return SimpleNamespace(
            text="",
            tool_calls=[{
                "function": {"name": tool_name, "arguments": arguments},
            }],
            usage=None,
        )

    def test_extracts_capability_tool(self):
        mgr = self._make_manager()
        result = self._make_result("list_graphs", '{"foo": 1}')
        extracted = mgr._extract_capability_tool_call(result, "agent")
        assert extracted is not None
        name, args = extracted
        assert name == "list_graphs"
        assert args == {"foo": 1}

    def test_skips_mutation_tool(self):
        mgr = self._make_manager()
        result = self._make_result("plan_graph_mutations")
        extracted = mgr._extract_capability_tool_call(result, "agent")
        assert extracted is None

    def test_respects_mode_filtering(self):
        mgr = self._make_manager()
        reg = mgr._capability_registry
        reg.register("write_only", _dummy_schema("write_only"), _dummy_handler, modes=["agent"])
        result = self._make_result("write_only")
        assert mgr._extract_capability_tool_call(result, "agent") is not None
        assert mgr._extract_capability_tool_call(result, "ask") is None

    def test_returns_none_without_registry(self):
        from types import SimpleNamespace
        from dan.server.chat_manager import ChatManager
        mgr = ChatManager.__new__(ChatManager)
        mgr._capability_registry = None
        result = SimpleNamespace(text="", tool_calls=[{"function": {"name": "list_graphs", "arguments": "{}"}}], usage=None)
        assert mgr._extract_capability_tool_call(result, "agent") is None

    def test_handles_bad_json_arguments(self):
        mgr = self._make_manager()
        result = self._make_result("list_graphs", "not-json")
        extracted = mgr._extract_capability_tool_call(result, "agent")
        assert extracted is not None
        _, args = extracted
        assert args == {}


# ── _extract_all_capability_tool_calls (multi-tool dispatch) ────────

class TestExtractAllCapabilityToolCalls:
    """Verify multi-tool extraction for 25-1 task 2-3."""

    def _make_manager(self):
        from dan.server.chat_manager import ChatManager

        reg = ChatCapabilityRegistry()
        reg.register("list_graphs", _dummy_schema("list_graphs"), _dummy_handler, modes=["agent", "ask"])
        reg.register("get_activity", _dummy_schema("get_activity"), _dummy_handler, modes=["agent", "ask"])
        ctx = CapabilityContext(workflow_id="test")
        mgr = ChatManager.__new__(ChatManager)
        mgr._capability_registry = reg
        mgr._capability_context = ctx
        return mgr

    def _make_result_multi(self, tool_calls: list[tuple[str, str]]) -> object:
        """Build a fake CompletionResult with multiple tool calls."""
        from types import SimpleNamespace
        return SimpleNamespace(
            text="",
            tool_calls=[
                {"function": {"name": name, "arguments": args}}
                for name, args in tool_calls
            ],
            usage=None,
        )

    def test_extracts_multiple_capability_tools(self):
        mgr = self._make_manager()
        result = self._make_result_multi([
            ("list_graphs", '{}'),
            ("get_activity", '{}'),
        ])
        extracted = mgr._extract_all_capability_tool_calls(result, "agent")
        assert len(extracted) == 2
        assert extracted[0] == ("list_graphs", {})
        assert extracted[1] == ("get_activity", {})

    def test_skips_mutation_tool_when_mixed(self):
        mgr = self._make_manager()
        result = self._make_result_multi([
            ("list_graphs", '{}'),
            ("plan_graph_mutations", '{"operations": []}'),
            ("get_activity", '{}'),
        ])
        extracted = mgr._extract_all_capability_tool_calls(result, "agent")
        assert len(extracted) == 2
        assert extracted[0] == ("list_graphs", {})
        assert extracted[1] == ("get_activity", {})

    def test_returns_empty_when_no_capability_tools(self):
        mgr = self._make_manager()
        result = self._make_result_multi([("plan_graph_mutations", '{"operations": []}')])
        extracted = mgr._extract_all_capability_tool_calls(result, "agent")
        assert extracted == []

    def test_returns_empty_without_registry(self):
        from dan.server.chat_manager import ChatManager
        from types import SimpleNamespace

        mgr = ChatManager.__new__(ChatManager)
        mgr._capability_registry = None
        result = SimpleNamespace(
            text="",
            tool_calls=[{"function": {"name": "list_graphs", "arguments": "{}"}}],
            usage=None,
        )
        extracted = mgr._extract_all_capability_tool_calls(result, "agent")
        assert extracted == []

    def test_mode_filtering_extracts_only_allowed_tools(self):
        mgr = self._make_manager()
        reg = mgr._capability_registry
        reg.register("agent_only", _dummy_schema("agent_only"), _dummy_handler, modes=["agent"])
        result = self._make_result_multi([
            ("list_graphs", '{}'),
            ("agent_only", '{}'),
        ])
        agent_extracted = mgr._extract_all_capability_tool_calls(result, "agent")
        assert len(agent_extracted) == 2
        ask_extracted = mgr._extract_all_capability_tool_calls(result, "ask")
        assert len(ask_extracted) == 1
        assert ask_extracted[0] == ("list_graphs", {})


# ── build_tool_schema ──────────────────────────────────────────────

class TestBuildToolSchema:

    def test_schema_shape(self):
        schema = build_tool_schema("test", "A test", {"type": "object", "properties": {}})
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test"
        assert schema["function"]["description"] == "A test"
        assert "properties" in schema["function"]["parameters"]
