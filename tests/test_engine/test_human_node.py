"""Tests for Plan 16-3: HumanNode generalization.

Covers model serialization, backward compatibility, rendering surface
protocol, renderer implementations, and executor integration.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dan.engine.events import EventType
from dan.engine.executor import (
    AutoRenderer,
    HumanRenderRequest,
    HumanRenderResponse,
    HumanRenderer,
    LegacyCallbackRenderer,
    ProgrammaticRenderer,
)
from dan.engine.state import NodeStatus
from dan.models.control_flow import HumanInTheLoopNode, HumanNode


# ===================================================================
# 1. Model serialization
# ===================================================================


class TestHumanNodeModel:
    def test_default_fields(self):
        node = HumanNode(id="h1", name="Ask User")
        assert node.node_type == "human"
        assert node.prompt == ""
        assert node.render_mode == "text"
        assert node.instructions == ""
        assert node.options is None
        assert node.input_schema is None
        assert node.output_schema is None
        assert node.timeout_seconds is None
        assert node.default_action is None
        assert node.render_target == "dialog"

    def test_all_new_fields(self):
        node = HumanNode(
            id="h2",
            name="Review",
            prompt="Please review",
            render_mode="approval",
            instructions="Check grammar",
            input_schema={"type": "object", "properties": {"draft": {"type": "string"}}},
            output_schema={
                "type": "object",
                "properties": {"approved": {"type": "boolean"}},
                "required": ["approved"],
            },
            options=None,
            timeout_seconds=60.0,
            default_action='{"approved": true}',
            render_target="chat",
        )
        assert node.render_mode == "approval"
        assert node.render_target == "chat"
        assert node.output_schema["required"] == ["approved"]

    def test_roundtrip_serialization(self):
        node = HumanNode(
            id="h3",
            name="Pick",
            render_mode="selection",
            options=["A", "B", "C"],
        )
        data = node.model_dump()
        assert data["node_type"] == "human"
        assert data["options"] == ["A", "B", "C"]
        restored = HumanNode.model_validate(data)
        assert restored.render_mode == "selection"
        assert restored.options == ["A", "B", "C"]

    def test_form_mode_with_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "number"},
            },
            "required": ["name"],
        }
        node = HumanNode(id="h4", name="Form", render_mode="form", output_schema=schema)
        assert node.render_mode == "form"
        assert node.output_schema == schema


# ===================================================================
# 2. Backward compatibility
# ===================================================================


class TestBackwardCompat:
    def test_human_in_the_loop_node_is_subclass(self):
        assert issubclass(HumanInTheLoopNode, HumanNode)

    def test_human_in_the_loop_node_type(self):
        node = HumanInTheLoopNode(id="old1", name="Old")
        assert node.node_type == "human_in_the_loop"

    def test_isinstance_check(self):
        """HumanInTheLoopNode instances are also HumanNode instances."""
        node = HumanInTheLoopNode(id="old2", name="Legacy")
        assert isinstance(node, HumanNode)
        assert isinstance(node, HumanInTheLoopNode)

    def test_old_node_has_new_fields(self):
        """Legacy node inherits all new fields with defaults."""
        node = HumanInTheLoopNode(id="old3", name="Legacy", prompt="hello")
        assert node.render_mode == "text"
        assert node.instructions == ""
        assert node.options is None

    def test_discriminated_union_human(self):
        """'human' type resolves to HumanNode in the Node union."""
        from dan.models.graph import Node
        from pydantic import TypeAdapter

        adapter = TypeAdapter(Node)
        data = {"id": "n1", "name": "Test", "node_type": "human"}
        node = adapter.validate_python(data)
        assert isinstance(node, HumanNode)
        assert node.node_type == "human"

    def test_discriminated_union_human_in_the_loop(self):
        """'human_in_the_loop' type resolves to HumanInTheLoopNode."""
        from dan.models.graph import Node
        from pydantic import TypeAdapter

        adapter = TypeAdapter(Node)
        data = {"id": "n2", "name": "Test", "node_type": "human_in_the_loop", "prompt": "hi"}
        node = adapter.validate_python(data)
        assert isinstance(node, HumanInTheLoopNode)
        assert node.prompt == "hi"

    def test_executor_alias(self):
        from dan.executors.control_flow import HumanInTheLoopExecutor, HumanNodeExecutor
        assert HumanInTheLoopExecutor is HumanNodeExecutor


# ===================================================================
# 3. Rendering surface dataclasses
# ===================================================================


class TestRenderModels:
    def test_render_request_defaults(self):
        req = HumanRenderRequest(node_id="n1", prompt="hello")
        assert req.render_mode == "text"
        assert req.request_id  # auto-generated UUID
        assert req.options is None

    def test_render_request_full(self):
        req = HumanRenderRequest(
            node_id="n2",
            node_name="Review",
            render_mode="approval",
            prompt="Approve?",
            instructions="Check carefully",
            input_data={"draft": "some text"},
            output_schema={"type": "object"},
            options=None,
            timeout_seconds=30.0,
            default_action='{"approved": true}',
        )
        assert req.render_mode == "approval"
        assert req.input_data == {"draft": "some text"}

    def test_render_response(self):
        resp = HumanRenderResponse(
            request_id="abc",
            data={"approved": True},
            source="human",
        )
        assert resp.source == "human"
        assert resp.data["approved"] is True

    def test_renderer_protocol(self):
        """LegacyCallbackRenderer satisfies the HumanRenderer protocol."""
        cb = AsyncMock(return_value={"response": "ok"})
        renderer = LegacyCallbackRenderer(cb)
        assert isinstance(renderer, HumanRenderer)


# ===================================================================
# 4. LegacyCallbackRenderer
# ===================================================================


class TestLegacyCallbackRenderer:
    @pytest.mark.asyncio
    async def test_wraps_callback(self):
        cb = AsyncMock(return_value={"answer": "42"})
        renderer = LegacyCallbackRenderer(cb)
        req = HumanRenderRequest(node_id="n1", prompt="What?")
        resp = await renderer.render(req)
        assert resp.data == {"answer": "42"}
        assert resp.source == "human"
        cb.assert_awaited_once()
        call_arg = cb.call_args[0][0]
        assert call_arg["node_id"] == "n1"
        assert call_arg["prompt"] == "What?"

    @pytest.mark.asyncio
    async def test_non_dict_response(self):
        cb = AsyncMock(return_value="plain text")
        renderer = LegacyCallbackRenderer(cb)
        req = HumanRenderRequest(node_id="n2", prompt="Say something")
        resp = await renderer.render(req)
        assert resp.data == {"response": "plain text"}


# ===================================================================
# 5. AutoRenderer
# ===================================================================


class TestAutoRenderer:
    @pytest.mark.asyncio
    async def test_returns_default_action(self):
        renderer = AutoRenderer()
        req = HumanRenderRequest(node_id="n1", default_action="yes")
        resp = await renderer.render(req)
        assert resp.data == {"response": "yes"}
        assert resp.source == "default"

    @pytest.mark.asyncio
    async def test_fails_without_default(self):
        renderer = AutoRenderer()
        req = HumanRenderRequest(node_id="n1")
        with pytest.raises(RuntimeError, match="no default_action"):
            await renderer.render(req)


# ===================================================================
# 6. ProgrammaticRenderer
# ===================================================================


class TestProgrammaticRenderer:
    @pytest.mark.asyncio
    async def test_returns_scripted_response(self):
        renderer = ProgrammaticRenderer(
            responses={"node_a": {"approved": True, "comment": "LGTM"}}
        )
        req = HumanRenderRequest(node_id="node_a", prompt="Review")
        resp = await renderer.render(req)
        assert resp.data == {"approved": True, "comment": "LGTM"}
        assert resp.source == "human"

    @pytest.mark.asyncio
    async def test_falls_back_to_default(self):
        renderer = ProgrammaticRenderer(responses={})
        req = HumanRenderRequest(node_id="unknown", default_action="fallback")
        resp = await renderer.render(req)
        assert resp.data == {"response": "fallback"}
        assert resp.source == "default"

    @pytest.mark.asyncio
    async def test_raises_for_unknown_no_default(self):
        renderer = ProgrammaticRenderer(responses={})
        req = HumanRenderRequest(node_id="unknown")
        with pytest.raises(KeyError, match="no scripted response"):
            await renderer.render(req)


# ===================================================================
# 7. HumanNodeExecutor integration tests
# ===================================================================

def _make_context(
    human_renderer=None,
    human_input_callback=None,
    events: list | None = None,
):
    """Build a minimal ExecutionContext for executor tests."""
    from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
    from dan.engine.executor import EngineConfig, ExecutionContext
    from dan.engine.state import ExecutionState
    from dan.models.graph import Graph

    graph = Graph(nodes=[], edges=[])
    state = ExecutionState(graph, run_id="test-run")
    shared = SharedContextStore([])
    artifacts = ArtifactStore()
    local = LocalStateManager()
    config = EngineConfig()
    captured = events if events is not None else []

    async def capture_event(evt: Any) -> None:
        captured.append(evt)

    return ExecutionContext(
        state=state,
        config=config,
        shared_context=shared,
        artifacts=artifacts,
        local_state=local,
        human_input_callback=human_input_callback,
        event_callback=capture_event,
        run_id="test-run",
        human_renderer=human_renderer,
    )


class TestHumanNodeExecutor:
    @pytest.mark.asyncio
    async def test_text_mode_with_renderer(self):
        """Basic text mode using ProgrammaticRenderer."""
        from dan.executors.control_flow import HumanNodeExecutor

        renderer = ProgrammaticRenderer({"h1": {"response": "hello world"}})
        ctx = _make_context(human_renderer=renderer)
        node = HumanNode(id="h1", name="Ask", prompt="Say something")
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {"context": "data"}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["response"] == "hello world"
        assert result.outputs["context"] == "data"
        assert result.metadata["source"] == "human"

    @pytest.mark.asyncio
    async def test_approval_mode(self):
        from dan.executors.control_flow import HumanNodeExecutor

        renderer = ProgrammaticRenderer({"h1": {"approved": True, "comment": "ok"}})
        ctx = _make_context(human_renderer=renderer)
        node = HumanNode(
            id="h1", name="Review", render_mode="approval",
            prompt="Approve?",
            output_schema={
                "type": "object",
                "properties": {"approved": {"type": "boolean"}},
                "required": ["approved"],
            },
        )
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["approved"] is True

    @pytest.mark.asyncio
    async def test_approval_reject(self):
        from dan.executors.control_flow import HumanNodeExecutor

        renderer = ProgrammaticRenderer({"h1": {"approved": False, "comment": "needs work"}})
        ctx = _make_context(human_renderer=renderer)
        node = HumanNode(id="h1", name="Review", render_mode="approval", prompt="Approve?")
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["approved"] is False

    @pytest.mark.asyncio
    async def test_selection_mode(self):
        from dan.executors.control_flow import HumanNodeExecutor

        renderer = ProgrammaticRenderer({"h1": {"selection": "Option B"}})
        ctx = _make_context(human_renderer=renderer)
        node = HumanNode(
            id="h1", name="Choose", render_mode="selection",
            options=["Option A", "Option B", "Option C"],
            prompt="Pick one",
        )
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["selection"] == "Option B"

    @pytest.mark.asyncio
    async def test_form_mode_valid_schema(self):
        from dan.executors.control_flow import HumanNodeExecutor

        renderer = ProgrammaticRenderer({"h1": {"name": "Alice", "age": 30}})
        ctx = _make_context(human_renderer=renderer)
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "number"},
            },
            "required": ["name"],
        }
        node = HumanNode(id="h1", name="Form", render_mode="form", output_schema=schema)
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_form_mode_validation_failure_with_default(self):
        """Schema validation fails, falls back to default_action after retries."""
        from dan.executors.control_flow import HumanNodeExecutor

        renderer = ProgrammaticRenderer({"h1": {"bad_field": "oops"}})
        ctx = _make_context(human_renderer=renderer)
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        node = HumanNode(
            id="h1", name="Form", render_mode="form",
            output_schema=schema,
            default_action="fallback",
        )
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["response"] == "fallback"
        assert result.metadata["source"] == "default_action"

    @pytest.mark.asyncio
    async def test_form_mode_validation_failure_no_default(self):
        """Schema validation fails with no default_action → FAILED."""
        from dan.executors.control_flow import HumanNodeExecutor

        renderer = ProgrammaticRenderer({"h1": {"bad_field": "oops"}})
        ctx = _make_context(human_renderer=renderer)
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        node = HumanNode(id="h1", name="Form", render_mode="form", output_schema=schema)
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.FAILED
        assert "validation failed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_timeout_with_default(self):
        """Timeout triggers default_action."""
        from dan.executors.control_flow import HumanNodeExecutor

        async def slow_render(req: HumanRenderRequest) -> HumanRenderResponse:
            await asyncio.sleep(10)
            return HumanRenderResponse(request_id=req.request_id, data={})

        class SlowRenderer:
            async def render(self, req: HumanRenderRequest) -> HumanRenderResponse:
                return await slow_render(req)

        ctx = _make_context(human_renderer=SlowRenderer())
        node = HumanNode(
            id="h1", name="Slow", timeout_seconds=0.05,
            default_action="auto-approved",
        )
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["response"] == "auto-approved"
        assert result.metadata["source"] == "timeout_default"

    @pytest.mark.asyncio
    async def test_timeout_no_default_fails(self):
        from dan.executors.control_flow import HumanNodeExecutor

        class SlowRenderer:
            async def render(self, req: HumanRenderRequest) -> HumanRenderResponse:
                await asyncio.sleep(10)
                return HumanRenderResponse(request_id=req.request_id, data={})

        ctx = _make_context(human_renderer=SlowRenderer())
        node = HumanNode(id="h1", name="Slow", timeout_seconds=0.05)
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.FAILED
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_no_renderer_no_callback_with_default(self):
        from dan.executors.control_flow import HumanNodeExecutor

        ctx = _make_context()
        node = HumanNode(id="h1", name="Auto", default_action="auto")
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["response"] == "auto"

    @pytest.mark.asyncio
    async def test_no_renderer_no_callback_no_default_fails(self):
        from dan.executors.control_flow import HumanNodeExecutor

        ctx = _make_context()
        node = HumanNode(id="h1", name="Nothing")
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)
        assert result.status == NodeStatus.FAILED

    @pytest.mark.asyncio
    async def test_legacy_callback_path(self):
        """When only human_input_callback is set (no explicit renderer), it auto-wraps."""
        from dan.executors.control_flow import HumanNodeExecutor

        cb = AsyncMock(return_value={"response": "via callback"})
        ctx = _make_context(human_input_callback=cb)
        assert ctx.human_renderer is not None
        node = HumanNode(id="h1", name="Legacy", prompt="Question?")
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {"input": "data"}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["response"] == "via callback"

    @pytest.mark.asyncio
    async def test_events_emitted(self):
        """Both human_input_needed and human_input_received events fire."""
        from dan.executors.control_flow import HumanNodeExecutor

        events: list = []
        renderer = ProgrammaticRenderer({"h1": {"response": "ok"}})
        ctx = _make_context(human_renderer=renderer, events=events)
        node = HumanNode(id="h1", name="Evented", prompt="Say")
        executor = HumanNodeExecutor()
        await executor.execute(node, {}, ctx)

        event_types = [e.event_type.value for e in events]
        assert "human_input_needed" in event_types
        assert "human_input_received" in event_types

    @pytest.mark.asyncio
    async def test_dynamic_prompt_from_inputs(self):
        """Dynamic prompt from inputs overrides node.prompt."""
        from dan.executors.control_flow import HumanNodeExecutor

        events: list = []
        renderer = ProgrammaticRenderer({"h1": {"response": "done"}})
        ctx = _make_context(human_renderer=renderer, events=events)
        node = HumanNode(id="h1", name="Dynamic", prompt="static")
        executor = HumanNodeExecutor()
        await executor.execute(node, {"user_prompt": "dynamic prompt"}, ctx)

        needed_event = next(e for e in events if e.event_type.value == "human_input_needed")
        assert needed_event.data["prompt"] == "dynamic prompt"


# ===================================================================
# 8. Both node types registered in scheduler
# ===================================================================


class TestSchedulerRegistration:
    def test_both_types_registered(self):
        from dan.engine.executor import EngineConfig, ExecutorRegistry
        from dan.engine.scheduler import Engine

        engine = Engine(
            config=EngineConfig(
                llm_api_key="test-key",
                checkpoint_enabled=False,
                memory_enabled=False,
            ),
        )
        assert engine.executor_registry.has("human")
        assert engine.executor_registry.has("human_in_the_loop")
        assert engine.executor_registry.get("human") is engine.executor_registry.get("human_in_the_loop")


# ===================================================================
# 9. Context auto-wrapping
# ===================================================================


class TestContextAutoWrap:
    def test_explicit_renderer_takes_priority(self):
        renderer = AutoRenderer()
        cb = AsyncMock()
        ctx = _make_context(human_renderer=renderer, human_input_callback=cb)
        assert isinstance(ctx.human_renderer, AutoRenderer)

    def test_callback_auto_wraps(self):
        cb = AsyncMock()
        ctx = _make_context(human_input_callback=cb)
        assert isinstance(ctx.human_renderer, LegacyCallbackRenderer)

    def test_no_callback_no_renderer(self):
        ctx = _make_context()
        assert ctx.human_renderer is None


# ===================================================================
# 10. Additional render modes (file_upload, rich)
# ===================================================================


class TestAdditionalRenderModes:
    @pytest.mark.asyncio
    async def test_file_upload_mode(self):
        """file_upload render_mode is propagated to the render request."""
        from dan.executors.control_flow import HumanNodeExecutor

        captured_requests: list[HumanRenderRequest] = []

        class CapturingRenderer:
            async def render(self, req: HumanRenderRequest) -> HumanRenderResponse:
                captured_requests.append(req)
                return HumanRenderResponse(
                    request_id=req.request_id,
                    data={"file_path": "/tmp/data.csv"},
                    source="human",
                )

        ctx = _make_context(human_renderer=CapturingRenderer())
        node = HumanNode(
            id="h1", name="Upload", render_mode="file_upload",
            prompt="Upload a file",
            instructions="CSV or JSON files only",
        )
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["file_path"] == "/tmp/data.csv"
        assert len(captured_requests) == 1
        assert captured_requests[0].render_mode == "file_upload"
        assert captured_requests[0].instructions == "CSV or JSON files only"

    @pytest.mark.asyncio
    async def test_rich_mode(self):
        """rich render_mode is propagated to the render request."""
        from dan.executors.control_flow import HumanNodeExecutor

        captured_requests: list[HumanRenderRequest] = []

        class CapturingRenderer:
            async def render(self, req: HumanRenderRequest) -> HumanRenderResponse:
                captured_requests.append(req)
                return HumanRenderResponse(
                    request_id=req.request_id,
                    data={"response": "multi-turn result", "turn_count": 3},
                    source="human",
                )

        ctx = _make_context(human_renderer=CapturingRenderer())
        node = HumanNode(
            id="h1", name="Chat", render_mode="rich",
            prompt="Start a conversation",
            render_target="chat",
        )
        executor = HumanNodeExecutor()
        result = await executor.execute(node, {}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["response"] == "multi-turn result"
        assert len(captured_requests) == 1
        assert captured_requests[0].render_mode == "rich"
        assert result.metadata["render_mode"] == "rich"
