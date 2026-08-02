"""Tests for DEAD_EDGE_WARNING event emission."""

from __future__ import annotations

import pytest

from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.events import EngineEvent, EventType
from dan.engine.executor import ExecutionContext, ExecutorRegistry
from dan.models.control_flow import GateNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, NodeBase
from dan.models.ports import InputPort, OutputPort


def _config():
    return EngineConfig(checkpoint_enabled=False)


def _engine(registry: ExecutorRegistry, callback=None):
    return Engine(
        config=_config(),
        checkpoint_store=NullCheckpointStore(),
        executor_registry=registry,
        event_callback=callback,
    )


class _FixedOutputExecutor:
    """Returns a fixed output dict, ignoring inputs."""

    def __init__(self, outputs: dict):
        self._outputs = outputs

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        return NodeResult(outputs=dict(self._outputs))


class _IfElseGateExecutor:
    """Routes to 'true' if value > threshold, else 'false'."""

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        val = inputs.get("value", 0)
        if val > self.threshold:
            return NodeResult(outputs={"true": inputs})
        return NodeResult(outputs={"false": inputs})


class TestDeadEdgeWarnings:
    @pytest.mark.asyncio
    async def test_dead_edge_warning_emitted(self):
        """Warning emitted when a data edge's source port has no value."""
        a = CodeOperator(
            id="a", name="A", code="...",
            output_ports=[OutputPort(name="out"), OutputPort(name="other")],
        )
        b = CodeOperator(
            id="b", name="B", code="...",
            input_ports=[InputPort(name="data")],
            output_ports=[OutputPort(name="result")],
        )
        graph = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(
                    id="e_dead", source_node_id="a", source_port="other",
                    target_node_id="b", target_port="data",
                ),
            ],
            entry_points=["a"],
            exit_points=["b"],
        )

        events: list[EngineEvent] = []

        async def capture(event: EngineEvent):
            events.append(event)

        reg = ExecutorRegistry()
        reg.register("code_operator", _FixedOutputExecutor({"out": "hello"}))

        engine = _engine(reg, callback=capture)
        result = await engine.run(graph)
        assert result.success

        warnings = [e for e in events if e.event_type == EventType.DEAD_EDGE_WARNING]
        assert len(warnings) == 1
        w = warnings[0]
        assert w.node_id == "b"
        assert w.data["edge_id"] == "e_dead"
        assert w.data["source_node_id"] == "a"
        assert w.data["source_port"] == "other"
        assert w.data["target_port"] == "data"

    @pytest.mark.asyncio
    async def test_no_dead_edge_warning_for_gate_branch(self):
        """No warning for the inactive branch of an if/else gate."""
        gate = GateNode(
            id="gate", name="Gate", condition="value > 0.5", gate_mode="if_else",
            input_ports=[InputPort(name="value")],
            output_ports=[OutputPort(name="true"), OutputPort(name="false")],
        )
        src = CodeOperator(
            id="src", name="Source", code="...",
            output_ports=[OutputPort(name="value")],
        )
        true_sink = CodeOperator(
            id="true_sink", name="True Sink", code="...",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )
        false_sink = CodeOperator(
            id="false_sink", name="False Sink", code="...",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )

        graph = Graph(
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

        events: list[EngineEvent] = []

        async def capture(event: EngineEvent):
            events.append(event)

        reg = ExecutorRegistry()
        reg.register("code_operator", _FixedOutputExecutor({"value": 0.9}))
        reg.register("gate", _IfElseGateExecutor(threshold=0.5))

        engine = _engine(reg, callback=capture)
        result = await engine.run(graph)
        assert result.success

        warnings = [e for e in events if e.event_type == EventType.DEAD_EDGE_WARNING]
        assert len(warnings) == 0, (
            f"Expected no dead-edge warnings for gate inactive branch, got {warnings}"
        )

    @pytest.mark.asyncio
    async def test_no_dead_edge_warning_when_data_present(self):
        """No warning when the source port has data."""
        a = CodeOperator(
            id="a", name="A", code="...",
            output_ports=[OutputPort(name="out")],
        )
        b = CodeOperator(
            id="b", name="B", code="...",
            input_ports=[InputPort(name="data")],
            output_ports=[OutputPort(name="result")],
        )
        graph = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(
                    id="e1", source_node_id="a", source_port="out",
                    target_node_id="b", target_port="data",
                ),
            ],
            entry_points=["a"],
            exit_points=["b"],
        )

        events: list[EngineEvent] = []

        async def capture(event: EngineEvent):
            events.append(event)

        reg = ExecutorRegistry()
        reg.register("code_operator", _FixedOutputExecutor({"out": "hello"}))

        engine = _engine(reg, callback=capture)
        result = await engine.run(graph)
        assert result.success

        warnings = [e for e in events if e.event_type == EventType.DEAD_EDGE_WARNING]
        assert len(warnings) == 0, (
            f"Expected no dead-edge warnings when data present, got {warnings}"
        )

    @pytest.mark.asyncio
    async def test_no_dead_edge_warning_for_optional_target_port(self):
        """No warning when the target port is optional (required=False)."""
        a = CodeOperator(
            id="a", name="A", code="...",
            output_ports=[OutputPort(name="out"), OutputPort(name="other")],
        )
        b = CodeOperator(
            id="b", name="B", code="...",
            input_ports=[InputPort(name="data", required=False)],
            output_ports=[OutputPort(name="result")],
        )
        graph = Graph(
            nodes=[a, b],
            edges=[
                DataEdge(
                    id="e_opt", source_node_id="a", source_port="other",
                    target_node_id="b", target_port="data",
                ),
            ],
            entry_points=["a"],
            exit_points=["b"],
        )

        events: list[EngineEvent] = []

        async def capture(event: EngineEvent):
            events.append(event)

        reg = ExecutorRegistry()
        reg.register("code_operator", _FixedOutputExecutor({"out": "hello"}))

        engine = _engine(reg, callback=capture)
        result = await engine.run(graph)
        assert result.success

        warnings = [e for e in events if e.event_type == EventType.DEAD_EDGE_WARNING]
        assert len(warnings) == 0, (
            f"Expected no dead-edge warnings for optional target port, got {warnings}"
        )
