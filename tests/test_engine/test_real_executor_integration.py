"""Integration tests using REAL executors (CodeExecutor + GateExecutor) through the engine.

Unlike test_gate_scheduling.py and test_cycle_scheduling.py which use mock executors,
these tests exercise the full executor→engine seam with real code execution and
condition evaluation. Catches bugs that mocks hide: output format mismatches,
input flattening regressions, dict `.result` port wiring.
"""

from __future__ import annotations

import pytest

from dan.engine import Engine, EngineConfig
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import ExecutorRegistry
from dan.executors.code import CodeExecutor
from dan.executors.control_flow import GateExecutor
from dan.models.control_flow import GateNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator
from dan.models.ports import InputPort, OutputPort


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _real_registry() -> ExecutorRegistry:
    reg = ExecutorRegistry()
    reg.register("code_operator", CodeExecutor())
    reg.register("gate", GateExecutor())
    return reg


def _engine(event_callback=None) -> Engine:
    return Engine(
        config=EngineConfig(checkpoint_enabled=False),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=_real_registry(),
        event_callback=event_callback,
    )


# ---------------------------------------------------------------------------
# 6-1. If/else with real executors
# ---------------------------------------------------------------------------


class TestIfElseRealExecutors:
    """CodeExecutor → GateExecutor(if_else) → two CodeExecutor branches."""

    @staticmethod
    def _build_graph(value: float) -> Graph:
        src = CodeOperator(
            id="src", name="Source",
            code=f'result = {{"value": {value}}}',
            output_ports=[OutputPort(name="value"), OutputPort(name="result")],
        )
        gate = GateNode(
            id="gate", name="Gate",
            condition="value > 0.5", gate_mode="if_else",
            input_ports=[InputPort(name="value")],
            output_ports=[OutputPort(name="true"), OutputPort(name="false")],
        )
        true_sink = CodeOperator(
            id="true_sink", name="True Sink",
            code='result = {"branch": "true"}',
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="branch"), OutputPort(name="result")],
        )
        false_sink = CodeOperator(
            id="false_sink", name="False Sink",
            code='result = {"branch": "false"}',
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="branch"), OutputPort(name="result")],
        )
        return Graph(
            nodes=[src, gate, true_sink, false_sink],
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

    @pytest.mark.asyncio
    async def test_routes_true_branch_when_above_threshold(self):
        """value=0.9 > 0.5 → true_sink completes, false_sink skipped."""
        graph = self._build_graph(0.9)
        result = await _engine().run(graph)

        assert result.success
        assert result.node_statuses["true_sink"] == "completed"
        assert result.node_statuses["false_sink"] == "skipped"

    @pytest.mark.asyncio
    async def test_routes_false_branch_when_below_threshold(self):
        """value=0.1 < 0.5 → false_sink completes, true_sink skipped."""
        graph = self._build_graph(0.1)
        result = await _engine().run(graph)

        assert result.success
        assert result.node_statuses["true_sink"] == "skipped"
        assert result.node_statuses["false_sink"] == "completed"

    @pytest.mark.asyncio
    async def test_gate_emits_correct_metadata(self):
        """Gate executor sets active_branch in metadata."""
        graph = self._build_graph(0.9)
        result = await _engine().run(graph)

        assert result.success
        gate_meta = result.metadata.get("gate")
        assert gate_meta is not None
        assert gate_meta["active_branch"] == "true"


# ---------------------------------------------------------------------------
# 6-2. While loop with real executors
# ---------------------------------------------------------------------------


class TestWhileLoopRealExecutors:
    """CodeExecutor(src) → CodeExecutor(inc) → GateExecutor(while) loop."""

    @staticmethod
    def _build_graph(max_iterations: int = 10) -> Graph:
        src = CodeOperator(
            id="src", name="Source",
            code='result = {"counter": 0}',
            output_ports=[OutputPort(name="counter"), OutputPort(name="result")],
        )
        inc = CodeOperator(
            id="inc", name="Increment",
            code='result = {"counter": counter + 1}',
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="counter"), OutputPort(name="result")],
        )
        gate = GateNode(
            id="gate", name="Loop Gate",
            condition="counter < 3", gate_mode="while",
            max_iterations=max_iterations,
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="continue"), OutputPort(name="done")],
        )
        sink = CodeOperator(
            id="sink", name="Sink",
            code='result = {"final": "done"}',
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="final"), OutputPort(name="result")],
        )
        return Graph(
            nodes=[src, inc, gate, sink],
            edges=[
                DataEdge(id="e1", source_node_id="src", source_port="counter",
                         target_node_id="gate", target_port="counter"),
                DataEdge(id="e2", source_node_id="gate", source_port="continue",
                         target_node_id="inc", target_port="counter"),
                DataEdge(id="e_back", source_node_id="inc",
                         source_port="counter",
                         target_node_id="gate", target_port="counter"),
                DataEdge(id="e_done", source_node_id="gate", source_port="done",
                         target_node_id="sink", target_port="data"),
            ],
            entry_points=["src"],
            exit_points=["sink"],
        )

    @pytest.mark.asyncio
    async def test_loop_runs_and_exits(self):
        """Loop increments counter 0→1→2→3, gate exits at counter=3, sink completes."""
        graph = self._build_graph()
        result = await _engine().run(graph)

        assert result.success
        assert result.node_statuses["sink"] == "completed"

    @pytest.mark.asyncio
    async def test_loop_emits_gate_evaluated_events(self):
        """Gate executor emits gate_evaluated events each iteration."""
        events: list[EngineEvent] = []

        async def capture(event: EngineEvent):
            events.append(event)

        graph = self._build_graph()
        result = await _engine(event_callback=capture).run(graph)

        assert result.success
        gate_events = [
            e for e in events
            if e.event_type == EventType.GATE_EVALUATED
        ]
        assert len(gate_events) >= 1, "Expected at least one gate_evaluated event"
        for ge in gate_events:
            assert ge.data.get("gate_mode") == "while"
            assert "condition_vars" in ge.data
            assert "counter" in ge.data["condition_vars"]

    @pytest.mark.asyncio
    async def test_loop_emits_iteration_events(self):
        """Engine emits ITERATION_STARTED / ITERATION_COMPLETED for each cycle."""
        events: list[EngineEvent] = []

        async def capture(event: EngineEvent):
            events.append(event)

        graph = self._build_graph()
        result = await _engine(event_callback=capture).run(graph)

        assert result.success
        iter_started = [e for e in events if e.event_type == EventType.ITERATION_STARTED]
        iter_completed = [e for e in events if e.event_type == EventType.ITERATION_COMPLETED]
        assert len(iter_started) > 0
        assert len(iter_completed) > 0
        assert len(iter_started) == len(iter_completed)


# ---------------------------------------------------------------------------
# 6-3. End-to-end .result port wiring
# ---------------------------------------------------------------------------


class TestResultPortWiring:
    """CodeExecutor dict output wired via the `.result` port to a downstream consumer."""

    @pytest.mark.asyncio
    async def test_result_port_passes_full_dict(self):
        """src returns {x:10, y:20}; sink receives full dict on `.result` port and sums."""
        src = CodeOperator(
            id="src", name="Source",
            code='result = {"x": 10, "y": 20}',
            output_ports=[
                OutputPort(name="x"),
                OutputPort(name="y"),
                OutputPort(name="result"),
            ],
        )
        sink = CodeOperator(
            id="sink", name="Sink",
            code='result = {"sum": data["x"] + data["y"]}',
            input_ports=[InputPort(name="data")],
            output_ports=[
                OutputPort(name="sum"),
                OutputPort(name="result"),
            ],
        )
        graph = Graph(
            nodes=[src, sink],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="src", source_port="result",
                    target_node_id="sink", target_port="data",
                ),
            ],
            entry_points=["src"],
            exit_points=["sink"],
        )

        result = await _engine().run(graph)

        assert result.success
        assert result.node_statuses["sink"] == "completed"
        assert result.outputs.get("sum") == 30
        expected_result = {"sum": 30}
        assert result.outputs.get("result") == expected_result

    @pytest.mark.asyncio
    async def test_flattened_key_wiring_still_works(self):
        """Wiring individual flattened keys (src.x → sink.x) also works."""
        src = CodeOperator(
            id="src", name="Source",
            code='result = {"x": 10, "y": 20}',
            output_ports=[
                OutputPort(name="x"),
                OutputPort(name="y"),
                OutputPort(name="result"),
            ],
        )
        sink = CodeOperator(
            id="sink", name="Sink",
            code='result = {"product": x * y}',
            input_ports=[InputPort(name="x"), InputPort(name="y")],
            output_ports=[
                OutputPort(name="product"),
                OutputPort(name="result"),
            ],
        )
        graph = Graph(
            nodes=[src, sink],
            edges=[
                DataEdge(id="e1", source_node_id="src", source_port="x",
                         target_node_id="sink", target_port="x"),
                DataEdge(id="e2", source_node_id="src", source_port="y",
                         target_node_id="sink", target_port="y"),
            ],
            entry_points=["src"],
            exit_points=["sink"],
        )

        result = await _engine().run(graph)

        assert result.success
        assert result.outputs.get("product") == 200

    @pytest.mark.asyncio
    async def test_result_port_with_gate_condition(self):
        """Full pipeline: code → gate (condition from flattened result) → branches.

        Validates the seam: CodeExecutor dict output → GateExecutor input flattening
        → condition evaluation → branch routing.
        """
        src = CodeOperator(
            id="src", name="Source",
            code='result = {"score": 0.8, "label": "good"}',
            output_ports=[
                OutputPort(name="score"),
                OutputPort(name="label"),
                OutputPort(name="result"),
            ],
        )
        gate = GateNode(
            id="gate", name="Gate",
            condition="score > 0.5", gate_mode="if_else",
            input_ports=[InputPort(name="score")],
            output_ports=[OutputPort(name="true"), OutputPort(name="false")],
        )
        accept = CodeOperator(
            id="accept", name="Accept",
            code='result = {"decision": "accepted"}',
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="decision"), OutputPort(name="result")],
        )
        reject = CodeOperator(
            id="reject", name="Reject",
            code='result = {"decision": "rejected"}',
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="decision"), OutputPort(name="result")],
        )
        graph = Graph(
            nodes=[src, gate, accept, reject],
            edges=[
                DataEdge(id="e1", source_node_id="src", source_port="score",
                         target_node_id="gate", target_port="score"),
                DataEdge(id="e2", source_node_id="gate", source_port="true",
                         target_node_id="accept", target_port="data"),
                DataEdge(id="e3", source_node_id="gate", source_port="false",
                         target_node_id="reject", target_port="data"),
            ],
            entry_points=["src"],
            exit_points=["accept", "reject"],
        )

        result = await _engine().run(graph)

        assert result.success
        assert result.node_statuses["accept"] == "completed"
        assert result.node_statuses["reject"] == "skipped"
