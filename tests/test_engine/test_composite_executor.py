"""Unit tests for CompositeExecutor — port mapping and sub-graph delegation."""

import pytest

from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import CompositeExecutor
from dan.models.control_flow import CompositeNode
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort


def _make_context(subgraph_returns: dict[str, dict] | None = None) -> ExecutionContext:
    """Build a minimal ExecutionContext with a mocked run_subgraph."""
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)
    returns = subgraph_returns or {}

    async def mock_run_subgraph(key: str, inputs: dict, parent_node_id: str | None = None, targeted_inputs: dict | None = None) -> dict:
        if key in returns:
            return returns[key]
        return inputs

    return ExecutionContext(
        state=state,
        config=EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        run_subgraph=mock_run_subgraph,
    )


class TestCompositeExecutor:
    @pytest.mark.asyncio
    async def test_basic_input_output_mapping(self):
        node = CompositeNode(
            id="comp1",
            name="Composite",
            body_graph="sub1",
            input_mappings={"outer_in": "inner_in"},
            output_mappings={"inner_out": "outer_out"},
            input_ports=[InputPort(name="outer_in")],
            output_ports=[OutputPort(name="outer_out")],
        )

        ctx = _make_context({"sub1": {"inner_out": "hello", "extra": 42}})
        executor = CompositeExecutor()
        result = await executor.execute(node, {"outer_in": "world"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["outer_out"] == "hello"
        assert result.outputs["extra"] == 42

    @pytest.mark.asyncio
    async def test_empty_mappings_passthrough(self):
        node = CompositeNode(
            id="comp2",
            name="Passthrough",
            body_graph="sub2",
            input_mappings={},
            output_mappings={},
            input_ports=[InputPort(name="data")],
            output_ports=[OutputPort(name="data")],
        )

        ctx = _make_context({"sub2": {"data": "unchanged"}})
        executor = CompositeExecutor()
        result = await executor.execute(node, {"data": "original"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs == {"data": "unchanged"}

    @pytest.mark.asyncio
    async def test_unmapped_inputs_pass_through(self):
        node = CompositeNode(
            id="comp3",
            name="Partial Map",
            body_graph="sub3",
            input_mappings={"a": "x"},
            output_mappings={},
            input_ports=[InputPort(name="a"), InputPort(name="b")],
            output_ports=[OutputPort(name="result")],
        )

        captured = {}

        async def capture_subgraph(key: str, inputs: dict, parent_node_id: str | None = None, targeted_inputs: dict | None = None) -> dict:
            captured.update(inputs)
            return {"result": "done"}

        ctx = _make_context()
        ctx._run_subgraph = capture_subgraph

        executor = CompositeExecutor()
        result = await executor.execute(node, {"a": 1, "b": 2}, ctx)

        assert captured["x"] == 1
        assert captured["b"] == 2
        assert "a" not in captured
        assert result.outputs["result"] == "done"

    @pytest.mark.asyncio
    async def test_multiple_output_mappings(self):
        node = CompositeNode(
            id="comp4",
            name="Multi-out",
            body_graph="sub4",
            input_mappings={},
            output_mappings={"res_a": "out_a", "res_b": "out_b"},
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="out_a"), OutputPort(name="out_b")],
        )

        ctx = _make_context({"sub4": {"res_a": 10, "res_b": 20, "res_c": 30}})
        executor = CompositeExecutor()
        result = await executor.execute(node, {"input": "x"}, ctx)

        assert result.outputs["out_a"] == 10
        assert result.outputs["out_b"] == 20
        assert result.outputs["res_c"] == 30
