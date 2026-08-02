"""Tests for cycle-aware scheduling, back-edge detection, and gate validation.

Covers:
  - DAG fast-path preservation
  - While-gate loop execution with back-edges
  - max_iterations enforcement
  - if_else gate branch skipping
  - Validation: gateless cycles rejected, gated cycles accepted, multi-gate rejected
  - PortDataStore.clear_node
"""

from __future__ import annotations

import asyncio

import pytest

from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import ExecutionContext, ExecutorRegistry
from dan.engine.scheduler import (
    _topological_levels,
    _topological_levels_with_backedges,
)
from dan.engine.state import ExecutionState, PortDataStore
from dan.models.control_flow import GateNode
from dan.models.edges import DataEdge
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
    """Outputs on ``continue`` while call_count < stop_at, then ``done``."""

    def __init__(self, stop_at: int = 3):
        self.stop_at = stop_at
        self.call_count = 0

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        self.call_count += 1
        if self.call_count < self.stop_at:
            return NodeResult(outputs={"continue": inputs})
        return NodeResult(outputs={"done": inputs})


class _IncrementExecutor:
    """Increments ``counter`` by 1."""

    def __init__(self):
        self.call_count = 0

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        self.call_count += 1
        counter = inputs.get("counter", 0)
        return NodeResult(outputs={"counter": counter + 1})


class _PassthroughExecutor:
    """Returns inputs as ``result`` output."""

    def __init__(self):
        self.call_count = 0

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        self.call_count += 1
        return NodeResult(outputs={"result": inputs.get("data", "ok")})


class _ConstExecutor:
    """Returns a fixed output dict."""

    def __init__(self, outputs: dict):
        self._outputs = outputs
        self.call_count = 0

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        self.call_count += 1
        return NodeResult(outputs=dict(self._outputs))


class _IfElseGateExecutor:
    """Routes to ``true`` if ``value`` > threshold, else ``false``."""

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        val = inputs.get("value", 0)
        if val > self.threshold:
            return NodeResult(outputs={"true": inputs})
        return NodeResult(outputs={"false": inputs})


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def _simple_dag() -> Graph:
    """A -> B -> C, pure DAG."""
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
    return Graph(
        nodes=[a, b, c],
        edges=[
            DataEdge(id="e1", source_node_id="a", source_port="out",
                     target_node_id="b", target_port="inp"),
            DataEdge(id="e2", source_node_id="b", source_port="out",
                     target_node_id="c", target_port="inp"),
        ],
        entry_points=["a"],
        exit_points=["c"],
    )


def _while_loop_graph(max_iterations: int = 10) -> Graph:
    """src -> inc -> gate(while) --continue--> inc, --done--> sink."""
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
        output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
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
                     target_node_id="gate", target_port="counter"),
            DataEdge(id="e2", source_node_id="gate", source_port="continue",
                     target_node_id="inc", target_port="counter"),
            DataEdge(id="e_back", source_node_id="inc", source_port="counter",
                     target_node_id="gate", target_port="counter"),
            DataEdge(id="e_done", source_node_id="gate", source_port="done",
                     target_node_id="sink", target_port="data"),
        ],
        entry_points=["src"],
        exit_points=["sink"],
    )


def _if_else_gate_graph() -> Graph:
    """src -> gate(if_else) --true--> A, --false--> B."""
    src = CodeOperator(
        id="src", name="Source", code="...",
        output_ports=[OutputPort(name="value")],
    )
    gate = GateNode(
        id="gate", name="Gate", condition="value > 0.5",
        gate_mode="if_else",
        input_ports=[InputPort(name="value")],
        output_ports=[OutputPort(name="true"), OutputPort(name="false")],
    )
    a = CodeOperator(
        id="a", name="TrueBranch", code="...",
        input_ports=[InputPort(name="data", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    b = CodeOperator(
        id="b", name="FalseBranch", code="...",
        input_ports=[InputPort(name="data", required=False)],
        output_ports=[OutputPort(name="result")],
    )
    return Graph(
        nodes=[src, gate, a, b],
        edges=[
            DataEdge(id="e1", source_node_id="src", source_port="value",
                     target_node_id="gate", target_port="value"),
            DataEdge(id="e_true", source_node_id="gate", source_port="true",
                     target_node_id="a", target_port="data"),
            DataEdge(id="e_false", source_node_id="gate", source_port="false",
                     target_node_id="b", target_port="data"),
        ],
        entry_points=["src"],
        exit_points=["a", "b"],
    )


# ---------------------------------------------------------------------------
# PortDataStore.clear_node
# ---------------------------------------------------------------------------


class TestPortDataStoreClearNode:
    def test_clear_node_removes_all_ports(self):
        store = PortDataStore()
        store.set("node1", "out_a", 10)
        store.set("node1", "out_b", 20)
        store.set("node2", "out_a", 30)

        store.clear_node("node1")

        assert not store.has("node1", "out_a")
        assert not store.has("node1", "out_b")
        assert store.has("node2", "out_a")

    def test_clear_node_noop_for_missing(self):
        store = PortDataStore()
        store.set("node1", "out", 1)
        store.clear_node("nonexistent")
        assert store.has("node1", "out")

    def test_clear_node_empties_get_node_outputs(self):
        store = PortDataStore()
        store.set("n", "p1", "a")
        store.set("n", "p2", "b")
        assert len(store.get_node_outputs("n")) == 2
        store.clear_node("n")
        assert store.get_node_outputs("n") == {}


# ---------------------------------------------------------------------------
# DAG fast-path
# ---------------------------------------------------------------------------


class TestDAGFastPath:
    def test_dag_fast_path(self):
        """Graph with no cycles uses standard topo sort — identical levels."""
        graph = _simple_dag()
        orig_levels = _topological_levels(graph)
        new_levels, back_edges, cycle_regions = _topological_levels_with_backedges(graph)

        assert back_edges == {}
        assert cycle_regions == {}
        assert orig_levels == new_levels

    @pytest.mark.asyncio
    async def test_dag_execution_unchanged(self):
        """Engine runs a pure DAG through the fast-path with correct results."""
        graph = _simple_dag()
        reg = ExecutorRegistry()
        reg.register("code_operator", _ConstExecutor({"out": "hello"}))
        engine = _engine(registry=reg)

        result = await engine.run(graph)
        assert result.success
        assert all(s == "completed" for s in result.node_statuses.values())


# ---------------------------------------------------------------------------
# While-gate loop
# ---------------------------------------------------------------------------


class TestSimpleWhileGateLoop:
    @pytest.mark.asyncio
    async def test_simple_while_gate_loop(self):
        """A -> B -> Gate(while) --continue--> B loops, --done--> C runs once."""
        graph = _while_loop_graph(max_iterations=10)

        gate_exec = _CountingGateExecutor(stop_at=3)
        inc_exec = _IncrementExecutor()
        sink_exec = _PassthroughExecutor()

        reg = ExecutorRegistry()
        reg.register("code_operator", inc_exec)
        reg.register("gate", gate_exec)

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"counter": 0})

        assert result.success
        assert gate_exec.call_count >= 3
        assert inc_exec.call_count >= 3
        assert result.node_statuses.get("sink") == "completed"

    @pytest.mark.asyncio
    async def test_back_edge_detected(self):
        """Topo sort identifies the gate->inc edge as a back-edge."""
        graph = _while_loop_graph()
        _, back_edges, cycle_regions = _topological_levels_with_backedges(graph)
        assert "gate" in back_edges
        assert back_edges["gate"] == "inc"
        assert "inc" in cycle_regions["gate"]
        assert "gate" in cycle_regions["gate"]
        assert "src" not in cycle_regions["gate"]


# ---------------------------------------------------------------------------
# max_iterations enforcement
# ---------------------------------------------------------------------------


class TestGateMaxIterations:
    @pytest.mark.asyncio
    async def test_gate_max_iterations(self):
        """Loop stops at max_iterations even if gate keeps outputting continue."""
        graph = _while_loop_graph(max_iterations=2)

        gate_exec = _CountingGateExecutor(stop_at=999)
        inc_exec = _IncrementExecutor()

        reg = ExecutorRegistry()
        reg.register("code_operator", inc_exec)
        reg.register("gate", gate_exec)

        engine = _engine(registry=reg)
        result = await engine.run(graph, inputs={"counter": 0})

        assert result.success
        # initial execution + max_iterations(2) re-executions = 3 max
        assert gate_exec.call_count <= 3


# ---------------------------------------------------------------------------
# if_else gate branch skip
# ---------------------------------------------------------------------------


class TestIfElseGateBranchSkip:
    @pytest.mark.asyncio
    async def test_if_else_gate_branch_skip_true(self):
        """When gate selects 'true', node A runs and B is skipped."""
        graph = _if_else_gate_graph()

        reg = ExecutorRegistry()
        reg.register("code_operator", _ConstExecutor({"value": 0.9}))
        reg.register("gate", _IfElseGateExecutor(threshold=0.5))
        engine = _engine(registry=reg)

        result = await engine.run(graph)
        assert result.success
        assert result.node_statuses["a"] == "completed"
        assert result.node_statuses["b"] == "skipped"

    @pytest.mark.asyncio
    async def test_if_else_gate_branch_skip_false(self):
        """When gate selects 'false', node B runs and A is skipped."""
        graph = _if_else_gate_graph()

        reg = ExecutorRegistry()
        reg.register("code_operator", _ConstExecutor({"value": 0.1}))
        reg.register("gate", _IfElseGateExecutor(threshold=0.5))
        engine = _engine(registry=reg)

        result = await engine.run(graph)
        assert result.success
        assert result.node_statuses["a"] == "skipped"
        assert result.node_statuses["b"] == "completed"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidationRejectsGatelessCycle:
    def test_validation_rejects_gateless_cycle(self):
        """A cycle between plain code nodes is rejected."""
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
        cycle_errors = [e for e in errors if "cycle" in e.lower()]
        assert len(cycle_errors) >= 1, f"Expected cycle error, got: {errors}"


class TestValidationAllowsGatedCycle:
    def test_validation_allows_gated_cycle(self):
        """A cycle with exactly one while-gate passes validation."""
        graph = _while_loop_graph(max_iterations=5)
        errors = validate_graph(graph)
        blocking = [
            e for e in errors
            if "cycle" in e.lower()
            and "schema safety bypassed" not in e.lower()
            and "untyped" not in e.lower()
        ]
        assert not blocking, f"Unexpected cycle errors: {blocking}"


class TestValidationRejectsIfElseModeCycle:
    def test_rejects_if_else_mode_cycle(self):
        """A cycle through a gate in if_else mode is rejected."""
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
        gate_errors = [e for e in errors if "gate" in e.lower() and "if_else" in e]
        assert len(gate_errors) >= 1


class TestValidationRejectsMultiGateCycle:
    def test_rejects_two_while_gates_in_cycle(self):
        """Two while-gates sharing a cycle region are rejected."""
        a = CodeOperator(
            id="a", name="A", code="...",
            input_ports=[InputPort(name="inp", required=False)],
            output_ports=[OutputPort(name="out")],
        )
        gate1 = GateNode(
            id="gate1", name="Gate1", condition="True",
            gate_mode="while", max_iterations=5,
            input_ports=[InputPort(name="inp")],
            output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
        )
        gate2 = GateNode(
            id="gate2", name="Gate2", condition="True",
            gate_mode="while", max_iterations=5,
            input_ports=[InputPort(name="inp")],
            output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
        )
        graph = Graph(
            nodes=[a, gate1, gate2],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="out",
                         target_node_id="gate1", target_port="inp"),
                DataEdge(id="e2", source_node_id="gate1", source_port="continue",
                         target_node_id="gate2", target_port="inp"),
                DataEdge(id="e3", source_node_id="gate2", source_port="continue",
                         target_node_id="a", target_port="inp"),
            ],
            entry_points=["a"],
            exit_points=["gate1", "gate2"],
        )
        errors = validate_graph(graph)
        multi_gate = [e for e in errors if "multi-gate" in e.lower()]
        assert len(multi_gate) >= 1, f"Expected multi-gate error, got: {errors}"
