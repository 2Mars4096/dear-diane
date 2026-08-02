"""Tests for the multi-surface gateway API (Phase 13, Plan 23-1)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dan.builder import workflow

# The tests need to work with the gateway endpoints.
# Use httpx.ASGITransport to test against the FastAPI app directly.


@pytest.fixture
def minimal_graph_dict() -> dict[str, Any]:
    """A minimal valid graph for testing dispatch."""
    return {
        "metadata": {"name": "test-workflow", "version": "1.0"},
        "nodes": [
            {
                "id": "input",
                "name": "Input",
                "node_type": "input",
                "variables": [],
            }
        ],
        "edges": [],
    }


@pytest.fixture
def tmp_workflow(tmp_path: Path, minimal_graph_dict: dict) -> Path:
    """Create a temporary workflow JSON file."""
    f = tmp_path / "test_workflow.json"
    f.write_text(json.dumps(minimal_graph_dict))
    return f


# ── Model tests ─────────────────────────────────────────────────────


class TestDispatchRequest:
    def test_valid_with_workflow_path(self):
        from dan.server.gateway.models import DispatchRequest

        req = DispatchRequest(workflow_path="/some/path.json")
        assert req.workflow_path == "/some/path.json"

    def test_valid_with_workflow_id(self):
        from dan.server.gateway.models import DispatchRequest

        req = DispatchRequest(workflow_id="my-graph")
        assert req.workflow_id == "my-graph"

    def test_valid_with_text(self):
        from dan.server.gateway.models import DispatchRequest

        req = DispatchRequest(text="run paper review")
        assert req.text == "run paper review"

    def test_invalid_empty(self):
        from dan.server.gateway.models import DispatchRequest

        with pytest.raises(ValueError, match="At least one"):
            DispatchRequest()

    def test_invalid_blank_sources(self):
        from dan.server.gateway.models import DispatchRequest

        with pytest.raises(ValueError, match="At least one"):
            DispatchRequest(workflow_path="")
        with pytest.raises(ValueError, match="At least one"):
            DispatchRequest(workflow_id="   ")
        with pytest.raises(ValueError, match="At least one"):
            DispatchRequest(text=" ")

    def test_optional_fields(self):
        from dan.server.gateway.models import DispatchRequest

        req = DispatchRequest(
            workflow_path="/p.json",
            surface_id="cli",
            use_meta=True,
            config_overrides={"model": "gpt-4o"},
            human_timeout=60,
        )
        assert req.use_meta is True
        assert req.config_overrides == {"model": "gpt-4o"}

    def test_auto_approve_default_false(self):
        from dan.server.gateway.models import DispatchRequest

        req = DispatchRequest(text="goal")
        assert req.auto_approve is False

    def test_auto_approve_explicit(self):
        from dan.server.gateway.models import DispatchRequest

        req = DispatchRequest(text="goal", auto_approve=True)
        assert req.auto_approve is True


# ── Activity tracker tests ──────────────────────────────────────────


class TestActivityTracker:
    def test_register_and_list_surfaces(self):
        from dan.server.gateway.activity import ActivityTracker

        rm = MagicMock()
        rm.list_runs.return_value = []
        tracker = ActivityTracker(rm)
        tracker.register_surface("cli-1", "cli")
        tracker.register_surface("tg-1", "telegram")
        surfaces = tracker.get_active_surfaces()
        assert len(surfaces) == 2
        ids = {s["surface_id"] for s in surfaces}
        assert ids == {"cli-1", "tg-1"}

    def test_stale_surface_excluded(self):
        from dan.server.gateway.activity import ActivityTracker

        rm = MagicMock()
        rm.list_runs.return_value = []
        tracker = ActivityTracker(rm)
        tracker.register_surface("old", "cli")
        tracker._surfaces["old"]["last_active"] = time.time() - 600
        surfaces = tracker.get_active_surfaces(stale_threshold=300)
        assert len(surfaces) == 0

    def test_activity_snapshot(self):
        from dan.server.gateway.activity import ActivityTracker

        rm = MagicMock()
        rm.list_runs.return_value = [
            {"run_id": "r1", "status": "running", "start_time": time.time()},
            {"run_id": "r2", "status": "completed", "start_time": time.time() - 100},
        ]
        tracker = ActivityTracker(rm)
        snap = tracker.get_activity()
        assert len(snap.active) == 1
        assert len(snap.recent) == 1


class TestTelemetryAnalyticsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_hourly_summary_for_gateway_store(self, monkeypatch):
        from dan.server.gateway import router as gateway_router
        from dan.server.telemetry import InMemoryTelemetryStore, TelemetryEvent

        store = InMemoryTelemetryStore()
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            surface="editor",
            session_id="editor-1",
            model="gpt-4.1",
            model_used="gpt-4.1",
            chat_mode="plan",
            total_tokens=120,
            estimated_cost=0.0012,
        ))
        await store.record(TelemetryEvent(
            event_type="gateway_call",
            surface="editor",
            session_id="editor-1",
            model="gpt-4.1",
            model_used="gpt-4.1",
            chat_mode="plan",
            total_tokens=120,
            estimated_cost=0.0012,
        ))

        rm = MagicMock()
        rm._telemetry_store = store
        monkeypatch.setattr(gateway_router, "_run_manager", rm)

        summary = await gateway_router.get_telemetry_analytics(
            hours=24,
            surface="editor",
            session_id="editor-1",
        )

        assert summary["totals"]["events"] == 2
        assert summary["totals"]["chat_turns"] == 1
        assert summary["totals"]["gateway_calls"] == 1
        assert summary["totals"]["total_tokens"] == 120
        assert summary["window_hours"] == 24
        assert summary["models"][0]["group_key"]["model_used"] == "gpt-4.1"


# ── Global event bus tests ──────────────────────────────────────────


class TestGlobalEventBus:
    def test_subscribe_and_broadcast(self):
        from dan.server.gateway.events import GlobalEventBus

        bus = GlobalEventBus()
        q = bus.subscribe("sub-1")
        bus.broadcast({"event_type": "test", "run_id": "r1"})
        assert not q.empty()
        event = q.get_nowait()
        assert event["event_type"] == "test"

    def test_max_subscribers(self):
        from dan.server.gateway.events import GlobalEventBus, MAX_GLOBAL_SUBSCRIBERS

        bus = GlobalEventBus()
        for i in range(MAX_GLOBAL_SUBSCRIBERS):
            bus.subscribe(f"sub-{i}")
        with pytest.raises(RuntimeError, match="Max global subscribers"):
            bus.subscribe("one-too-many")

    def test_unsubscribe(self):
        from dan.server.gateway.events import GlobalEventBus

        bus = GlobalEventBus()
        bus.subscribe("sub-1")
        assert bus.subscriber_count == 1
        bus.unsubscribe("sub-1")
        assert bus.subscriber_count == 0

    def test_surface_filter(self):
        from dan.server.gateway.events import GlobalEventBus

        bus = GlobalEventBus()
        q = bus.subscribe("sub-1", surface_filter="telegram")
        bus.broadcast({"event_type": "test", "surface_id": "cli"})
        bus.broadcast({"event_type": "test", "surface_id": "telegram"})
        assert q.qsize() == 1

    def test_backpressure_drop_oldest(self):
        from dan.server.gateway.events import GlobalEventBus, MAX_QUEUE_SIZE

        bus = GlobalEventBus()
        q = bus.subscribe("sub-1")
        for i in range(MAX_QUEUE_SIZE + 5):
            bus.broadcast({"event_type": "test", "seq": i})
        assert q.qsize() == MAX_QUEUE_SIZE


# ── RunManager additions tests ──────────────────────────────────────


class TestRunManagerGatewayMethods:
    @pytest.mark.asyncio
    async def test_cancel_run_nonexistent(self):
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunManager

        rm = RunManager(engine_config=EngineConfig())
        assert rm.cancel_run("nonexistent") is False

    def test_get_all_pending_empty(self):
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunManager

        rm = RunManager(engine_config=EngineConfig())
        assert rm.get_all_pending_human_inputs() == []

    def test_submit_human_input_atomic(self):
        """Second submit for same request_id returns False."""
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunManager

        rm = RunManager(engine_config=EngineConfig())
        evt = asyncio.Event()
        rm._pending_human_inputs["req-1"] = evt
        rm._runs["run-1"] = MagicMock()
        assert rm.submit_human_input("run-1", "req-1", {"answer": "yes"}) is True
        assert rm.submit_human_input("run-1", "req-1", {"answer": "no"}) is False

    def test_pop_meta_approval_response(self):
        """pop_meta_approval_response returns stored response and clears state."""
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunManager

        rm = RunManager(engine_config=EngineConfig())
        rm.register_meta_approval("req-meta", run_id="run-1")
        # Simulate client approving via submit-input
        assert rm.submit_human_input("run-1", "req-meta", {"approved": True}) is True
        resp = rm.pop_meta_approval_response("req-meta")
        assert resp == {"approved": True}
        assert "req-meta" not in rm._human_input_responses
        assert "run-1" not in rm._meta_approval_request_by_run
        assert "req-meta" not in rm._meta_approval_run_by_request

    def test_pop_meta_approval_response_missing_returns_empty(self):
        """pop_meta_approval_response returns {} when request_id not found."""
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunManager

        rm = RunManager(engine_config=EngineConfig())
        resp = rm.pop_meta_approval_response("nonexistent")
        assert resp == {}

    def test_cancel_pending_meta_approval_run(self):
        """cancel_run cancels pre-run approval waits (no engine task yet)."""
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunManager, RunRecord, RunStatus

        rm = RunManager(engine_config=EngineConfig())
        rm._runs["run-1"] = RunRecord(run_id="run-1", graph_id="wf-1", status=RunStatus.PENDING)
        evt = rm.register_meta_approval("req-meta", run_id="run-1")
        assert rm.cancel_run("run-1") is True
        assert evt.is_set()
        rec = rm.get_run("run-1")
        assert rec is not None
        assert rec.status == RunStatus.CANCELLED
        assert rec.events[-1]["event_type"] == "run_cancelled"

    def test_cancel_pending_run_without_task(self):
        """cancel_run should cancel pending runs even if no task was created yet."""
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunManager, RunRecord, RunStatus

        rm = RunManager(engine_config=EngineConfig())
        rm._runs["run-2"] = RunRecord(run_id="run-2", graph_id="wf-2", status=RunStatus.PENDING)
        assert rm.cancel_run("run-2") is True
        rec = rm.get_run("run-2")
        assert rec is not None
        assert rec.status == RunStatus.CANCELLED
        assert rec.events[-1]["event_type"] == "run_cancelled"


# ── Workflow loader tests ───────────────────────────────────────────


class TestWorkflowLoader:
    def test_detect_source_type(self):
        from dan.utils.workflow_loader import detect_source_type

        assert detect_source_type(Path("foo.json")) == "json"
        assert detect_source_type(Path("foo.md")) == "markdown"
        assert detect_source_type(Path("foo.py")) == "python"

    def test_detect_unsupported(self):
        from dan.utils.workflow_loader import WorkflowLoadError, detect_source_type

        with pytest.raises(WorkflowLoadError):
            detect_source_type(Path("foo.txt"))

    def test_load_json(self, tmp_workflow: Path):
        from dan.utils.workflow_loader import load_graph

        graph = load_graph(tmp_workflow)
        assert graph.metadata.name == "test-workflow"

    def test_validate_path_traversal(self, tmp_path: Path):
        from dan.utils.workflow_loader import WorkflowLoadError, validate_workflow_path

        evil = tmp_path / ".." / ".." / "etc" / "passwd"
        with pytest.raises(WorkflowLoadError):
            validate_workflow_path(evil, tmp_path)

    def test_validate_path_sibling_dir_traversal(self, tmp_path: Path):
        from dan.utils.workflow_loader import WorkflowLoadError, validate_workflow_path

        workspace = tmp_path / "safe"
        workspace.mkdir()
        evil = tmp_path / "safe_evil"
        evil.mkdir()
        evil_file = evil / "malicious.json"
        evil_file.write_text("{}")
        with pytest.raises(WorkflowLoadError):
            validate_workflow_path(evil_file, workspace)

    def test_validate_path_ok(self, tmp_workflow: Path):
        from dan.utils.workflow_loader import validate_workflow_path

        result = validate_workflow_path(tmp_workflow, tmp_workflow.parent)
        assert result.exists()


# ── EngineEvent.from_dict tests ─────────────────────────────────────


class TestEngineEventFromDict:
    def test_round_trip(self):
        from dan.engine.events import EngineEvent, EventType

        original = EngineEvent(
            event_type=EventType.RUN_STARTED,
            run_id="test-run",
            timestamp=1234567890.0,
            node_id="n1",
            node_type="LLMOperator",
            data={"key": "value"},
        )
        restored = EngineEvent.from_dict(original.to_dict())
        assert restored.event_type == original.event_type
        assert restored.run_id == original.run_id
        assert restored.timestamp == original.timestamp
        assert restored.node_id == original.node_id
        assert restored.data == original.data

    def test_unknown_event_type(self):
        from dan.engine.events import EngineEvent

        d = {"event_type": "totally_unknown", "run_id": "r", "timestamp": 0}
        event = EngineEvent.from_dict(d)
        assert event.run_id == "r"


class TestGatewayTextDispatch:
    @staticmethod
    def _conditional_branch_graph(*, source_value: int) -> dict[str, Any]:
        from dan.models.control_flow import GateNode
        from dan.models.edges import DataEdge
        from dan.models.graph import Graph
        from dan.models.nodes import CodeOperator
        from dan.models.ports import InputPort, OutputPort

        src = CodeOperator(
            id="src",
            name="Source",
            code=f"result = {{'value': {source_value}}}",
            output_ports=[OutputPort(name="value"), OutputPort(name="result")],
        )
        gate = GateNode(
            id="gate",
            name="Gate",
            condition="value > 5",
            gate_mode="if_else",
            input_ports=[InputPort(name="value")],
            output_ports=[OutputPort(name="true"), OutputPort(name="false")],
        )
        true_sink = CodeOperator(
            id="true_sink",
            name="True Sink",
            code="result = {'text': 'BIG'}",
            input_ports=[InputPort(name='data', required=False)],
            output_ports=[OutputPort(name="text"), OutputPort(name="result")],
        )
        false_sink = CodeOperator(
            id="false_sink",
            name="False Sink",
            code="result = {'text': 'SMALL'}",
            input_ports=[InputPort(name='data', required=False)],
            output_ports=[OutputPort(name="text"), OutputPort(name="result")],
        )
        graph = Graph(
            nodes=[src, gate, true_sink, false_sink],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src",
                    source_port="value",
                    target_node_id="gate",
                    target_port="value",
                ),
                DataEdge(
                    id="e2",
                    source_node_id="gate",
                    source_port="true",
                    target_node_id="true_sink",
                    target_port="data",
                ),
                DataEdge(
                    id="e3",
                    source_node_id="gate",
                    source_port="false",
                    target_node_id="false_sink",
                    target_port="data",
                ),
            ],
            entry_points=["src"],
            exit_points=["true_sink", "false_sink"],
        )
        return graph.model_dump(mode="json")

    @staticmethod
    def _while_loop_counter_graph() -> dict[str, Any]:
        from dan.models.control_flow import GateNode
        from dan.models.edges import DataEdge
        from dan.models.graph import Graph
        from dan.models.nodes import CodeOperator
        from dan.models.ports import InputPort, OutputPort

        src = CodeOperator(
            id="src",
            name="Source",
            code="result = {'counter': 0}",
            output_ports=[OutputPort(name="counter"), OutputPort(name="result")],
        )
        gate = GateNode(
            id="gate",
            name="Loop Gate",
            condition="counter < 3",
            gate_mode="while",
            max_iterations=10,
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
        )
        inc = CodeOperator(
            id="inc",
            name="Increment",
            code="result = {'counter': counter + 1}",
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="counter"), OutputPort(name="result")],
        )
        sink = CodeOperator(
            id="sink",
            name="Sink",
            code="result = {'value': data['counter']}",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="value"), OutputPort(name="result")],
        )
        graph = Graph(
            nodes=[src, gate, inc, sink],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src",
                    source_port="counter",
                    target_node_id="gate",
                    target_port="counter",
                ),
                DataEdge(
                    id="e2",
                    source_node_id="gate",
                    source_port="continue",
                    target_node_id="inc",
                    target_port="counter",
                ),
                DataEdge(
                    id="e3",
                    source_node_id="inc",
                    source_port="counter",
                    target_node_id="gate",
                    target_port="counter",
                ),
                DataEdge(
                    id="e4",
                    source_node_id="gate",
                    source_port="done",
                    target_node_id="sink",
                    target_port="data",
                ),
            ],
            entry_points=["src"],
            exit_points=["sink"],
        )
        return graph.model_dump(mode="json")

    @staticmethod
    def _for_each_sum_graph() -> dict[str, Any]:
        from dan.builder.refs import NodeRef

        wf = workflow("foreach_sum")
        seed = wf.code(
            "seed_items",
            code="result = {'items': [1, 2, 3]}",
            output_ports=[{"name": "items", "json_schema": {"type": "array"}}],
        )
        with wf.for_each("triple_each", items=seed["items"], parallelism=2) as body:
            body.code(
                "triple_item",
                code="result = {'value': item * 3}",
                input_ports=[{"name": "item", "json_schema": {"type": "integer"}}],
                output_ports=[{"name": "value", "json_schema": {"type": "integer"}}],
            )
        foreach_ref = NodeRef("triple_each", "for_each", wf)
        collector = wf.code(
            "collect_sum",
            code="result = {'value': sum(entry['value'] for entry in results)}",
            input_ports=[{"name": "results", "json_schema": {"type": "array"}}],
            output_ports=[{"name": "value", "json_schema": {"type": "integer"}}],
        )
        wf.edge(foreach_ref["results"], collector["results"])
        return wf.build().model_dump(mode="json")

    @staticmethod
    def _double_then_add_graph() -> dict[str, Any]:
        wf = workflow("double_then_add")
        seed = wf.code(
            "seed",
            code="result = {'value': 4}",
            output_ports=[{"name": "value", "json_schema": {"type": "integer"}}],
        )
        double = wf.code(
            "double",
            code="result = {'value': value * 2}",
            input_ports=[{"name": "value", "json_schema": {"type": "integer"}}],
            output_ports=[{"name": "value", "json_schema": {"type": "integer"}}],
        )
        add_three = wf.code(
            "add_three",
            code="result = {'value': value + 3}",
            input_ports=[{"name": "value", "json_schema": {"type": "integer"}}],
            output_ports=[{"name": "value", "json_schema": {"type": "integer"}}],
        )
        wf.edge(seed["value"], double["value"])
        wf.edge(double["value"], add_three["value"])
        return wf.build().model_dump(mode="json")

    @staticmethod
    def _average_format_graph() -> dict[str, Any]:
        wf = workflow("average_and_format")
        numbers = wf.code(
            "numbers",
            code="result = {'numbers': [2, 4, 6]}",
            output_ports=[{"name": "numbers", "json_schema": {"type": "array"}}],
        )
        average = wf.code(
            "average",
            code="result = {'average': sum(numbers) / len(numbers)}",
            input_ports=[{"name": "numbers", "json_schema": {"type": "array"}}],
            output_ports=[{"name": "average", "json_schema": {"type": "number"}}],
        )
        formatter = wf.code(
            "format_average",
            code="result = {'text': f'AVG={average:.1f}'}",
            input_ports=[{"name": "average", "json_schema": {"type": "number"}}],
            output_ports=[{"name": "text", "json_schema": {"type": "string"}}],
        )
        wf.edge(numbers["numbers"], average["numbers"])
        wf.edge(average["average"], formatter["average"])
        return wf.build().model_dump(mode="json")

    @staticmethod
    def _combine_label_count_graph() -> dict[str, Any]:
        wf = workflow("combine_label_and_count")
        seed = wf.code(
            "seed",
            code="result = {'label': 'dan', 'count': 3}",
            output_ports=[
                {"name": "label", "json_schema": {"type": "string"}},
                {"name": "count", "json_schema": {"type": "integer"}},
            ],
        )
        combine = wf.code(
            "combine",
            code="result = {'text': f'{label}:{count}'}",
            input_ports=[
                {"name": "label", "json_schema": {"type": "string"}},
                {"name": "count", "json_schema": {"type": "integer"}},
            ],
            output_ports=[{"name": "text", "json_schema": {"type": "string"}}],
        )
        wf.edge(seed["label"], combine["label"])
        wf.edge(seed["count"], combine["count"])
        return wf.build().model_dump(mode="json")

    @staticmethod
    def _slugify_graph() -> dict[str, Any]:
        wf = workflow("slugify_phrase")
        seed = wf.code(
            "seed",
            code="result = {'text': 'Deep Agent Network'}",
            output_ports=[{"name": "text", "json_schema": {"type": "string"}}],
        )
        slugify = wf.code(
            "slugify",
            code="result = {'text': text.lower().replace(' ', '-')}",
            input_ports=[{"name": "text", "json_schema": {"type": "string"}}],
            output_ports=[{"name": "text", "json_schema": {"type": "string"}}],
        )
        wf.edge(seed["text"], slugify["text"])
        return wf.build().model_dump(mode="json")

    @staticmethod
    def _runnable_graph_for_goal(goal: str) -> dict[str, Any]:
        goal_lower = goal.lower()
        if "bigger than 5" in goal_lower or "otherwise small" in goal_lower:
            source_value = 7 if "7" in goal_lower else 2
            return TestGatewayTextDispatch._conditional_branch_graph(source_value=source_value)
        if "triple each" in goal_lower or "batch thing" in goal_lower or "sum to 18" in goal_lower:
            return TestGatewayTextDispatch._for_each_sum_graph()
        if "count up till 3" in goal_lower or "counts up to 3" in goal_lower or "loop" in goal_lower:
            return TestGatewayTextDispatch._while_loop_counter_graph()
        if "double it" in goal_lower or "add 3" in goal_lower or "11" in goal_lower:
            return TestGatewayTextDispatch._double_then_add_graph()
        if "average" in goal_lower or "avg" in goal_lower:
            return TestGatewayTextDispatch._average_format_graph()
        if "dan:3" in goal_lower or ("combine" in goal_lower and "count" in goal_lower):
            return TestGatewayTextDispatch._combine_label_count_graph()
        if "slug" in goal_lower or "deep-agent-network" in goal_lower:
            return TestGatewayTextDispatch._slugify_graph()
        if "multiply" in goal_lower or "15" in goal_lower:
            wf = workflow("multiply_and_add")
            wf.code(
                "calc",
                code="result = {'value': 15}",
                output_ports=[{"name": "value"}],
            )
            return wf.build().model_dump(mode="json")
        if "string" in goal_lower or "hello" in goal_lower or "uppercase" in goal_lower:
            wf = workflow("string_result")
            wf.code(
                "emit",
                code="result = {'text': 'HELLO WORLD'}",
                output_ports=[{"name": "text"}],
            )
            return wf.build().model_dump(mode="json")

        wf = workflow("sum_numbers")
        wf.code(
            "calc",
            code="result = {'value': 6}",
            output_ports=[{"name": "value"}],
        )
        return wf.build().model_dump(mode="json")

    @staticmethod
    async def _wait_for_terminal_run(rm: Any, run_id: str, *, timeout_s: float = 3.0) -> Any:
        from dan.server.run_manager import RunStatus

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            record = rm.get_run(run_id)
            if record is not None and record.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return record
            await asyncio.sleep(0.05)
        raise AssertionError(f"Run {run_id} did not reach a terminal state within {timeout_s}s")

    @pytest.mark.asyncio
    async def test_dispatch_text_invalid_graph_returns_422(self, monkeypatch):
        """Planner output with invalid graph shape should return HTTP 422, not 500."""
        from fastapi import HTTPException
        from dan.server.gateway.router import _dispatch_text

        class _Review:
            valid = True
            errors: list[str] = []

        class _PlannerOutput:
            review = _Review()
            plan = {"kind": "generate"}

        class _Planner:
            async def plan(self, goal: str):  # noqa: ARG002
                return _PlannerOutput()

            async def execute_plan(self, plan: dict, **kwargs):  # noqa: ARG002
                return {
                    "workflow_id": "wf-invalid",
                    "graph": {"nodes": "invalid-type"},
                }

        def _fake_build_meta_controller():
            return (None, _Planner(), None)

        monkeypatch.setattr(
            "dan.server.app._build_meta_controller",
            _fake_build_meta_controller,
            raising=False,
        )

        rm = MagicMock()
        tracker = MagicMock()
        bus = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _dispatch_text(
                rm=rm,
                tracker=tracker,
                bus=bus,
                goal="test goal",
                inputs=None,
                auto_approve=True,
                surface_id="cli",
                human_timeout=30,
            )

        assert exc.value.status_code == 422
        assert "invalid graph" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_dispatch_text_non_dict_graph_returns_422(self, monkeypatch):
        """Planner output with non-dict graph payload should return HTTP 422."""
        from fastapi import HTTPException
        from dan.server.gateway.router import _dispatch_text

        class _Review:
            valid = True
            errors: list[str] = []

        class _PlannerOutput:
            review = _Review()
            plan = {"kind": "generate"}

        class _Planner:
            async def plan(self, goal: str):  # noqa: ARG002
                return _PlannerOutput()

            async def execute_plan(self, plan: dict, **kwargs):  # noqa: ARG002
                return {
                    "workflow_id": "wf-invalid",
                    "graph": ["not", "a", "dict"],
                }

        def _fake_build_meta_controller():
            return (None, _Planner(), None)

        monkeypatch.setattr(
            "dan.server.app._build_meta_controller",
            _fake_build_meta_controller,
            raising=False,
        )

        rm = MagicMock()
        tracker = MagicMock()
        bus = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _dispatch_text(
                rm=rm,
                tracker=tracker,
                bus=bus,
                goal="test goal",
                inputs=None,
                auto_approve=True,
                surface_id="cli",
                human_timeout=30,
            )

        assert exc.value.status_code == 422
        assert "invalid graph" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_dispatch_text_blocks_non_run_ready_workflow(self, monkeypatch):
        """38-16/8-6: gateway must not claim success or auto-run a workflow that is validated but not run-ready."""
        from fastapi import HTTPException
        from dan.server.gateway.router import _dispatch_text

        class _Review:
            valid = True
            errors: list[str] = []

        class _PlannerOutput:
            review = _Review()
            plan = {"kind": "generate"}

        class _Planner:
            async def plan(self, goal: str):  # noqa: ARG002
                return _PlannerOutput()

            async def execute_plan(self, plan: dict, **kwargs):  # noqa: ARG002
                return {
                    "workflow_id": "Draft Workflow",
                    "graph": {
                        "version": "dan_graph_v1",
                        "metadata": {"name": ""},
                        "nodes": [],
                        "edges": [],
                    },
                }

        def _fake_build_meta_controller():
            return (None, _Planner(), None)

        monkeypatch.setattr(
            "dan.server.app._build_meta_controller",
            _fake_build_meta_controller,
            raising=False,
        )

        rm = MagicMock()
        rm.start_run = AsyncMock()
        tracker = MagicMock()
        bus = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _dispatch_text(
                rm=rm,
                tracker=tracker,
                bus=bus,
                goal="test goal",
                inputs=None,
                auto_approve=True,
                surface_id="cli",
                human_timeout=30,
            )

        assert exc.value.status_code == 422
        assert "non-runnable workflow" in str(exc.value.detail).lower()
        bus.broadcast.assert_not_called()
        rm.start_run.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("goal", "expected_key", "expected_value"),
        [
            (
                "Build something tiny that multiplies and ends at 15, then run it.",
                "value",
                15,
            ),
            (
                "I do not care what you call it. Make a minimal string workflow that emits HELLO WORLD and execute it.",
                "text",
                "HELLO WORLD",
            ),
            (
                "Create any very small runnable workflow that sums to 6 and launch it immediately.",
                "value",
                6,
            ),
        ],
    )
    async def test_dispatch_text_executes_adversarial_requirement_variants(
        self,
        monkeypatch,
        goal: str,
        expected_key: str,
        expected_value: Any,
    ) -> None:
        """Text dispatch should build and complete simple runnable workflows for varied requirements."""
        from dan.engine.executor import EngineConfig
        from dan.server.gateway.router import _dispatch_text
        from dan.server.run_manager import RunManager, RunStatus

        class _Review:
            valid = True
            errors: list[str] = []

        class _PlannerOutput:
            review = _Review()
            plan = {"kind": "generate"}

        outer = self

        class _Planner:
            async def plan(self, goal_text: str):  # noqa: ARG002
                return _PlannerOutput()

            async def execute_plan(self, plan: dict, **kwargs):  # noqa: ARG002
                return {
                    "workflow_id": "Adversarial Workflow",
                    "graph": outer._runnable_graph_for_goal(kwargs.get("user_text", "")),
                }

        def _fake_build_meta_controller():
            return (None, _Planner(), None)

        monkeypatch.setattr(
            "dan.server.app._build_meta_controller",
            _fake_build_meta_controller,
            raising=False,
        )

        rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
        tracker = MagicMock()
        bus = MagicMock()

        result = await _dispatch_text(
            rm=rm,
            tracker=tracker,
            bus=bus,
            goal=goal,
            inputs=None,
            auto_approve=True,
            surface_id="cli",
            human_timeout=30,
        )

        record = await self._wait_for_terminal_run(rm, result.run_id)

        assert record.status == RunStatus.COMPLETED
        assert record.result is not None
        assert record.result.success is True
        assert record.result.outputs[expected_key] == expected_value
        assert result.status in {"pending", "running"}
        tracker.touch_surface.assert_called_with("cli")
        assert any(
            call.args
            and isinstance(call.args[0], dict)
            and call.args[0].get("event_type") == "plan_created"
            for call in bus.broadcast.call_args_list
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("goal", "expected_key", "expected_value"),
        [
            (
                "can you just make something that starts from 4, double it, add 3, and run the thing",
                "value",
                11,
            ),
            (
                "i just need a tiny workflow that does the average of 2, 4, 6 and spits out AVG=4.0, then launch it please",
                "text",
                "AVG=4.0",
            ),
            (
                "maybe make one that combines dan with a count of 3 so it ends up dan:3, then execute it",
                "text",
                "dan:3",
            ),
            (
                "pls set up whatever small workflow turns Deep Agent Network into deep-agent-network and run it",
                "text",
                "deep-agent-network",
            ),
        ],
    )
    async def test_dispatch_text_executes_multi_step_adversarial_requirement_variants(
        self,
        monkeypatch,
        goal: str,
        expected_key: str,
        expected_value: Any,
    ) -> None:
        """Gateway text dispatch should also survive slightly more structured multi-step requirements."""
        from dan.engine.executor import EngineConfig
        from dan.server.gateway.router import _dispatch_text
        from dan.server.run_manager import RunManager, RunStatus

        class _Review:
            valid = True
            errors: list[str] = []

        class _PlannerOutput:
            review = _Review()
            plan = {"kind": "generate"}

        outer = self

        class _Planner:
            async def plan(self, goal_text: str):  # noqa: ARG002
                return _PlannerOutput()

            async def execute_plan(self, plan: dict, **kwargs):  # noqa: ARG002
                return {
                    "workflow_id": "Composed Workflow",
                    "graph": outer._runnable_graph_for_goal(kwargs.get("user_text", "")),
                }

        def _fake_build_meta_controller():
            return (None, _Planner(), None)

        monkeypatch.setattr(
            "dan.server.app._build_meta_controller",
            _fake_build_meta_controller,
            raising=False,
        )

        rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
        tracker = MagicMock()
        bus = MagicMock()

        result = await _dispatch_text(
            rm=rm,
            tracker=tracker,
            bus=bus,
            goal=goal,
            inputs=None,
            auto_approve=True,
            surface_id="cli",
            human_timeout=30,
        )

        record = await self._wait_for_terminal_run(rm, result.run_id)

        assert record.status == RunStatus.COMPLETED
        assert record.result is not None
        assert record.result.success is True
        assert record.result.outputs[expected_key] == expected_value
        assert result.status in {"pending", "running"}
        tracker.touch_surface.assert_called_with("cli")
        assert any(
            call.args
            and isinstance(call.args[0], dict)
            and call.args[0].get("event_type") == "plan_created"
            for call in bus.broadcast.call_args_list
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("goal", "expected_key", "expected_value"),
        [
            (
                "uh can you do a tiny branch thing, if 7 is bigger than 5 say BIG otherwise SMALL, then run it",
                "text",
                "BIG",
            ),
            (
                "make a little branch workflow where if 2 is bigger than 5 say BIG otherwise SMALL and just execute it",
                "text",
                "SMALL",
            ),
            (
                "i want some tiny loop thing that counts up till 3 and then runs and gives me the final value",
                "value",
                3,
            ),
            (
                "do a little batch thing over 1 2 3, triple each one, sum it to 18, and run it",
                "value",
                18,
            ),
        ],
    )
    async def test_dispatch_text_executes_control_flow_adversarial_requirement_variants(
        self,
        monkeypatch,
        goal: str,
        expected_key: str,
        expected_value: Any,
    ) -> None:
        """Gateway text dispatch should also survive basic branch/loop workflow requests."""
        from dan.engine.executor import EngineConfig
        from dan.server.gateway.router import _dispatch_text
        from dan.server.run_manager import RunManager, RunStatus

        class _Review:
            valid = True
            errors: list[str] = []

        class _PlannerOutput:
            review = _Review()
            plan = {"kind": "generate"}

        outer = self

        class _Planner:
            async def plan(self, goal_text: str):  # noqa: ARG002
                return _PlannerOutput()

            async def execute_plan(self, plan: dict, **kwargs):  # noqa: ARG002
                return {
                    "workflow_id": "Control Flow Workflow",
                    "graph": outer._runnable_graph_for_goal(kwargs.get("user_text", "")),
                }

        def _fake_build_meta_controller():
            return (None, _Planner(), None)

        monkeypatch.setattr(
            "dan.server.app._build_meta_controller",
            _fake_build_meta_controller,
            raising=False,
        )

        rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
        tracker = MagicMock()
        bus = MagicMock()

        result = await _dispatch_text(
            rm=rm,
            tracker=tracker,
            bus=bus,
            goal=goal,
            inputs=None,
            auto_approve=True,
            surface_id="cli",
            human_timeout=30,
        )

        record = await self._wait_for_terminal_run(rm, result.run_id)

        assert record.status == RunStatus.COMPLETED
        assert record.result is not None
        assert record.result.success is True
        assert record.result.outputs[expected_key] == expected_value
        assert result.status in {"pending", "running"}
        tracker.touch_surface.assert_called_with("cli")
