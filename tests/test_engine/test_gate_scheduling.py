"""Tests for cycle-aware scheduling and gate-based branching/looping."""

from __future__ import annotations

import asyncio

import pytest

from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import ExecutionContext, ExecutorRegistry
from dan.engine.scheduler import (
    _is_gate_node,
    _topological_levels,
    _topological_levels_with_backedges,
)
from dan.models.control_flow import GateNode
from dan.models.edges import ControlEdge, DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, NodeBase
from dan.models.ports import InputPort, OutputPort
from dan.validation.graph import validate_graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config():
    return EngineConfig(checkpoint_enabled=False)


def _engine(registry: ExecutorRegistry | None = None):
    return Engine(
        config=_config(),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=registry,
    )


class _CountingGateExecutor:
    """Mock gate executor that counts iterations via an external counter.

    When the counter is below *stop_at*, outputs on the ``continue`` port.
    Otherwise outputs on the ``done`` port.  This lets tests control how
    many iterations the while-gate cycle runs.
    """

    def __init__(self, stop_at: int = 3):
        self.stop_at = stop_at
        self.call_count = 0

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        self.call_count += 1
        counter = inputs.get("counter", 0)
        if counter < self.stop_at:
            return NodeResult(outputs={"continue": inputs})
        return NodeResult(outputs={"done": inputs})


class _IfElseGateExecutor:
    """Mock gate executor for if_else mode.

    Routes to ``true`` if the ``value`` input > threshold, else ``false``.
    """

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        val = inputs.get("value", 0)
        if val > self.threshold:
            return NodeResult(outputs={"true": inputs})
        return NodeResult(outputs={"false": inputs})


class _IncrementExecutor:
    """Mock code executor that increments counter by 1."""

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        counter = inputs.get("counter", 0)
        return NodeResult(outputs={"counter": counter + 1})


class _PassthroughExecutor:
    """Returns inputs as outputs under port name ``result``."""

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        return NodeResult(outputs={"result": inputs.get("data", "ok")})


class _ConstExecutor:
    """Returns a fixed output dict."""

    def __init__(self, outputs: dict):
        self._outputs = outputs

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        return NodeResult(outputs=dict(self._outputs))


# ---------------------------------------------------------------------------
# Simple gate if_else branching
# ---------------------------------------------------------------------------


class TestGateIfElseBranching:
    @pytest.mark.asyncio
    async def test_routes_to_true_branch(self):
        """Gate in if_else mode routes to the 'true' branch; false branch skipped."""
        gate = GateNode(
            id="gate", name="Gate", condition="value > 0.5", gate_mode="if_else",
            input_ports=[InputPort(name="value")],
            output_ports=[OutputPort(name="true"), OutputPort(name="false")],
        )
        src = CodeOperator(
            id="src", name="Source", code="result = {'value': 0.9}",
            output_ports=[OutputPort(name="value")],
        )
        true_node = CodeOperator(
            id="true_sink", name="True Sink", code="result = {'out': 'yes'}",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )
        false_node = CodeOperator(
            id="false_sink", name="False Sink", code="result = {'out': 'no'}",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )

        graph = Graph(
            nodes=[src, gate, true_node, false_node],
            edges=[
                DataEdge(id="e1", source_node_id="src", source_port="value",
                         target_node_id="gate", target_port="value"),
                DataEdge(id="e2", source_node_id="gate", source_port="true",
                         target_node_id="true_sink", target_port="data"),
                DataEdge(id="e3", source_node_id="gate", source_port="false",
                         target_node_id="false_sink", target_port="data"),
            ],
            entry_points=["src"],
            exit_points=["true_sink", "false_sink"],
        )

        reg = ExecutorRegistry()
        reg.register("code_operator", _ConstExecutor({"value": 0.9}))
        reg.register("gate", _IfElseGateExecutor(threshold=0.5))
        engine = _engine(registry=reg)

        result = await engine.run(graph)
        assert result.success
        assert result.node_statuses["true_sink"] == "completed"
        assert result.node_statuses["false_sink"] == "skipped"

    @pytest.mark.asyncio
    async def test_routes_to_false_branch(self):
        """Gate in if_else mode routes to the 'false' branch when condition fails."""
        gate = GateNode(
            id="gate", name="Gate", condition="value > 0.5", gate_mode="if_else",
            input_ports=[InputPort(name="value")],
            output_ports=[OutputPort(name="true"), OutputPort(name="false")],
        )
        src = CodeOperator(
            id="src", name="Source", code="result = {'value': 0.1}",
            output_ports=[OutputPort(name="value")],
        )
        true_node = CodeOperator(
            id="true_sink", name="True Sink", code="pass",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )
        false_node = CodeOperator(
            id="false_sink", name="False Sink", code="pass",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )

        graph = Graph(
            nodes=[src, gate, true_node, false_node],
            edges=[
                DataEdge(id="e1", source_node_id="src", source_port="value",
                         target_node_id="gate", target_port="value"),
                DataEdge(id="e2", source_node_id="gate", source_port="true",
                         target_node_id="true_sink", target_port="data"),
                DataEdge(id="e3", source_node_id="gate", source_port="false",
                         target_node_id="false_sink", target_port="data"),
            ],
            entry_points=["src"],
            exit_points=["true_sink", "false_sink"],
        )

        reg = ExecutorRegistry()
        reg.register("code_operator", _ConstExecutor({"value": 0.1}))
        reg.register("gate", _IfElseGateExecutor(threshold=0.5))
        engine = _engine(registry=reg)

        result = await engine.run(graph)
        assert result.success
        assert result.node_statuses["true_sink"] == "skipped"
        assert result.node_statuses["false_sink"] == "completed"


# ---------------------------------------------------------------------------
# Gate while-loop
# ---------------------------------------------------------------------------


def _while_loop_graph(max_iterations: int = 10) -> Graph:
    """Build: src -> inc -> gate(while) --continue--> inc (back-edge).

    gate --done--> sink

    Topology:
      src produces {counter: 0}
      inc increments counter
      gate checks counter < stop_at and outputs continue/done
    """
    src = CodeOperator(
        id="src", name="Source", code="...",
        output_ports=[OutputPort(name="counter")],
    )
    inc = CodeOperator(
        id="inc", name="Increment", code="...",
        input_ports=[InputPort(name="counter")],
        output_ports=[OutputPort(name="counter")],
    )
    gate = GateNode(
        id="gate", name="Loop Gate", condition="counter < 3",
        gate_mode="while", max_iterations=max_iterations,
        input_ports=[InputPort(name="counter")],
        output_ports=[
            OutputPort(name="continue"),
            OutputPort(name="done"),
        ],
    )
    sink = CodeOperator(
        id="sink", name="Sink", code="...",
        input_ports=[InputPort(name="data", required=False)],
        output_ports=[OutputPort(name="result")],
    )

    return Graph(
        nodes=[src, inc, gate, sink],
        edges=[
            DataEdge(id="e1", source_node_id="src", source_port="counter",
                     target_node_id="inc", target_port="counter"),
            DataEdge(id="e2", source_node_id="inc", source_port="counter",
                     target_node_id="gate", target_port="counter"),
            # Back-edge: gate.continue -> inc.counter
            DataEdge(id="e_back", source_node_id="gate", source_port="continue",
                     target_node_id="inc", target_port="counter"),
            DataEdge(id="e_done", source_node_id="gate", source_port="done",
                     target_node_id="sink", target_port="data"),
        ],
        entry_points=["src"],
        exit_points=["sink"],
    )


class TestGateWhileLoop:
    @pytest.mark.asyncio
    async def test_loop_executes_multiple_iterations(self):
        """Gate while-loop re-executes cycle region until condition is false."""
        graph = _while_loop_graph(max_iterations=10)

        gate_exec = _CountingGateExecutor(stop_at=5)
        reg = ExecutorRegistry()
        reg.register("code_operator", _ConstExecutor({"counter": 0}))
        reg.register("gate", gate_exec)

        inc_exec = _IncrementExecutor()
        reg.register("code_operator", inc_exec)

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"counter": 0})
        assert result.success
        assert gate_exec.call_count >= 3

    @pytest.mark.asyncio
    async def test_loop_respects_max_iterations(self):
        """Gate while-loop stops at max_iterations even if condition stays true."""
        graph = _while_loop_graph(max_iterations=2)

        gate_exec = _CountingGateExecutor(stop_at=999)
        reg = ExecutorRegistry()
        reg.register("code_operator", _IncrementExecutor())
        reg.register("gate", gate_exec)

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"counter": 0})
        assert result.success
        assert gate_exec.call_count <= 3  # initial + max_iterations(2)

    @pytest.mark.asyncio
    async def test_loop_emits_iteration_events(self):
        """Gate while-loop emits ITERATION_STARTED and ITERATION_COMPLETED events."""
        graph = _while_loop_graph(max_iterations=10)
        events: list[EngineEvent] = []

        async def capture(event: EngineEvent):
            events.append(event)

        gate_exec = _CountingGateExecutor(stop_at=3)
        reg = ExecutorRegistry()
        reg.register("code_operator", _IncrementExecutor())
        reg.register("gate", gate_exec)

        engine = Engine(
            config=_config(),
            checkpoint_store=NullCheckpointStore(),
            executor_registry=reg,
            event_callback=capture,
        )
        await engine.run(graph, inputs={"counter": 0})

        iter_started = [e for e in events if e.event_type == EventType.ITERATION_STARTED]
        iter_completed = [e for e in events if e.event_type == EventType.ITERATION_COMPLETED]
        assert len(iter_started) > 0
        assert len(iter_completed) > 0
        assert len(iter_started) == len(iter_completed)

    @pytest.mark.asyncio
    async def test_loop_body_waits_for_initial_continue_signal(self):
        """Body node should not run before gate emits initial continue."""
        src = CodeOperator(
            id="src",
            name="Source",
            code="result = {'try_more': False}",
            output_ports=[OutputPort(name="try_more")],
        )
        body = CodeOperator(
            id="body",
            name="Body",
            code="result = {'try_more': True}",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="try_more")],
        )
        gate = GateNode(
            id="gate",
            name="Loop Gate",
            condition="try_more",
            gate_mode="while",
            max_iterations=5,
            input_ports=[InputPort(name="try_more")],
            output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
        )
        sink = CodeOperator(
            id="sink",
            name="Sink",
            code="result = {'ok': True}",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )

        graph = Graph(
            nodes=[src, body, gate, sink],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src",
                    source_port="try_more",
                    target_node_id="gate",
                    target_port="try_more",
                ),
                DataEdge(
                    id="e2",
                    source_node_id="gate",
                    source_port="continue",
                    target_node_id="body",
                    target_port="data",
                ),
                DataEdge(
                    id="e3",
                    source_node_id="body",
                    source_port="try_more",
                    target_node_id="gate",
                    target_port="try_more",
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

        engine = _engine()
        result = await engine.run(graph)
        assert result.success
        assert result.node_statuses["body"] == "skipped"
        assert result.node_statuses["sink"] == "completed"


# ---------------------------------------------------------------------------
# DAG fast-path preserved
# ---------------------------------------------------------------------------


class TestDAGFastPath:
    @pytest.mark.asyncio
    async def test_no_backedges_uses_fast_path(self):
        """When there are no gate back-edges, execution is identical to DAG path."""
        a = CodeOperator(
            id="a", name="A", code="...",
            output_ports=[OutputPort(name="out")],
        )
        b = CodeOperator(
            id="b", name="B", code="...",
            input_ports=[InputPort(name="inp")],
            output_ports=[OutputPort(name="out")],
        )
        graph = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="out",
                         target_node_id="b", target_port="inp"),
            ],
            entry_points=["a"],
            exit_points=["b"],
        )

        levels, back_edges, cycle_regions = _topological_levels_with_backedges(graph)
        assert back_edges == {}
        assert cycle_regions == {}
        assert len(levels) == 2

    def test_topo_levels_match_original_for_dag(self):
        """_topological_levels_with_backedges produces same levels as _topological_levels for DAGs."""
        a = CodeOperator(
            id="a", name="A", code="...",
            output_ports=[OutputPort(name="out")],
        )
        b = CodeOperator(
            id="b", name="B", code="...",
            input_ports=[InputPort(name="inp")],
            output_ports=[OutputPort(name="out")],
        )
        c = CodeOperator(
            id="c", name="C", code="...",
            input_ports=[InputPort(name="inp")],
            output_ports=[OutputPort(name="out")],
        )
        graph = Graph(
            nodes=[a, b, c],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="out",
                         target_node_id="b", target_port="inp"),
                DataEdge(id="e2", source_node_id="a", source_port="out",
                         target_node_id="c", target_port="inp"),
            ],
            entry_points=["a"],
            exit_points=["b", "c"],
        )

        orig_levels = _topological_levels(graph)
        new_levels, back_edges, _ = _topological_levels_with_backedges(graph)
        assert back_edges == {}
        assert orig_levels == new_levels


# ---------------------------------------------------------------------------
# Topo sort with back-edges
# ---------------------------------------------------------------------------


class TestTopologicalLevelsWithBackedges:
    def test_identifies_back_edge(self):
        """A gate(while) continue edge pointing backward is identified as a back-edge."""
        graph = _while_loop_graph()
        levels, back_edges, cycle_regions = _topological_levels_with_backedges(graph)
        assert "gate" in back_edges
        assert back_edges["gate"] == "inc"

    def test_cycle_region_computed(self):
        """Cycle region contains inc and gate (the loop body)."""
        graph = _while_loop_graph()
        _, _, cycle_regions = _topological_levels_with_backedges(graph)
        assert "gate" in cycle_regions
        region = cycle_regions["gate"]
        assert "inc" in region
        assert "gate" in region
        assert "src" not in region


# ---------------------------------------------------------------------------
# Validation: gate-controlled cycles
# ---------------------------------------------------------------------------


class TestValidateGateCycles:
    def test_valid_gate_while_cycle(self):
        """A well-formed gate(while) cycle passes validation."""
        graph = _while_loop_graph()
        errors = validate_graph(graph)
        cycle_errors = [e for e in errors if "cycle" in e.lower() and "gate" not in e.lower()]
        gate_errors = [e for e in errors if "gate" in e.lower() and "cycle" in e.lower()]
        assert not cycle_errors, f"Unexpected cycle errors: {cycle_errors}"
        assert not gate_errors, f"Unexpected gate errors: {gate_errors}"

    def test_rejects_cycle_without_gate(self):
        """A cycle between plain nodes is rejected."""
        a = CodeOperator(
            id="a", name="A", code="...",
            input_ports=[InputPort(name="inp", required=False)],
            output_ports=[OutputPort(name="out")],
        )
        b = CodeOperator(
            id="b", name="B", code="...",
            input_ports=[InputPort(name="inp")],
            output_ports=[OutputPort(name="out")],
        )
        graph = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="out",
                         target_node_id="b", target_port="inp"),
                DataEdge(id="e2", source_node_id="b", source_port="out",
                         target_node_id="a", target_port="inp"),
            ],
            entry_points=["a"],
            exit_points=["b"],
        )
        errors = validate_graph(graph)
        assert any("cycle" in e.lower() for e in errors)

    def test_rejects_gate_if_else_mode_cycle(self):
        """A cycle through a gate in if_else mode (not while) is rejected."""
        inc = CodeOperator(
            id="inc", name="Inc", code="...",
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="counter")],
        )
        gate = GateNode(
            id="gate", name="Gate", condition="counter < 3",
            gate_mode="if_else",
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="true"), OutputPort(name="false")],
        )
        graph = Graph(
            nodes=[inc, gate],
            edges=[
                DataEdge(id="e1", source_node_id="inc", source_port="counter",
                         target_node_id="gate", target_port="counter"),
                DataEdge(id="e2", source_node_id="gate", source_port="true",
                         target_node_id="inc", target_port="counter"),
            ],
            entry_points=["inc"],
            exit_points=["gate"],
        )
        errors = validate_graph(graph)
        gate_cycle_errors = [e for e in errors if "gate" in e.lower() and "if_else" in e]
        assert len(gate_cycle_errors) >= 1

    def test_accepts_dag_no_cycles(self):
        """A plain DAG produces no cycle-related errors."""
        a = CodeOperator(
            id="a", name="A", code="...",
            output_ports=[OutputPort(name="out")],
        )
        b = CodeOperator(
            id="b", name="B", code="...",
            input_ports=[InputPort(name="inp")],
            output_ports=[OutputPort(name="out")],
        )
        graph = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="out",
                         target_node_id="b", target_port="inp"),
            ],
            entry_points=["a"],
            exit_points=["b"],
        )
        errors = validate_graph(graph)
        assert not any("cycle" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# _is_gate_node helper
# ---------------------------------------------------------------------------


class TestIsGateNode:
    def test_gate_node_detected(self):
        gate = GateNode(
            id="g", name="G", condition="True", gate_mode="while",
        )
        assert _is_gate_node(gate) is True

    def test_non_gate_node_not_detected(self):
        code = CodeOperator(id="c", name="C", code="pass")
        assert _is_gate_node(code) is False
