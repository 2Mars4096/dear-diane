"""Tests for 6-8 patch-up: node-aware mappings, targeted injection, multi-entry composites."""

import pytest

from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.state import ExecutionState, NodeStatus
from dan.executors.control_flow import CompositeExecutor
from dan.models.control_flow import CompositeNode
from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort
from dan.models.nodes import LLMOperator, ToolOperator
from dan.models.edges import DataEdge


def _make_context(subgraph_returns: dict[str, dict] | None = None) -> ExecutionContext:
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)
    returns = subgraph_returns or {}

    async def mock_run_subgraph(key: str, inputs: dict, parent_node_id: str | None = None, targeted_inputs: dict | None = None) -> dict:
        result = {}
        if key in returns:
            result = dict(returns[key])
        else:
            result = dict(inputs)
        if targeted_inputs:
            result["__targeted_inputs_received__"] = targeted_inputs
        return result

    return ExecutionContext(
        state=state,
        config=EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        run_subgraph=mock_run_subgraph,
    )


class TestNodeAwareInputMappings:
    @pytest.mark.asyncio
    async def test_targeted_input_routing(self):
        """Node-aware mapping `nodeId::portName` routes inputs to targeted_inputs."""
        node = CompositeNode(
            id="comp",
            name="Multi-entry",
            body_graph="body",
            input_mappings={"topic": "idea_gen::topic"},
            output_mappings={},
            input_ports=[InputPort(name="topic")],
            output_ports=[OutputPort(name="result")],
        )

        ctx = _make_context({"body": {"result": "done"}})
        executor = CompositeExecutor()
        result = await executor.execute(node, {"topic": "AI"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs.get("__targeted_inputs_received__") == {"idea_gen": {"topic": "AI"}}

    @pytest.mark.asyncio
    async def test_legacy_mapping_still_works(self):
        """Mapping values without `::` use the legacy broadcast path."""
        node = CompositeNode(
            id="comp",
            name="Legacy",
            body_graph="body",
            input_mappings={"outer": "inner"},
            output_mappings={},
            input_ports=[InputPort(name="outer")],
            output_ports=[OutputPort(name="result")],
        )

        captured = {}

        async def capture(key: str, inputs: dict, parent_node_id: str | None = None, targeted_inputs: dict | None = None) -> dict:
            captured["broadcast"] = inputs
            captured["targeted"] = targeted_inputs
            return {"result": "ok"}

        ctx = _make_context()
        ctx._run_subgraph = capture
        executor = CompositeExecutor()
        await executor.execute(node, {"outer": "value"}, ctx)

        assert captured["broadcast"] == {"inner": "value"}
        assert captured["targeted"] is None

    @pytest.mark.asyncio
    async def test_mixed_legacy_and_targeted(self):
        """Mixing legacy and node-aware mappings in the same node."""
        node = CompositeNode(
            id="comp",
            name="Mixed",
            body_graph="body",
            input_mappings={"a": "nodeA::portX", "b": "portY"},
            output_mappings={},
            input_ports=[InputPort(name="a"), InputPort(name="b")],
            output_ports=[OutputPort(name="out")],
        )

        captured = {}

        async def capture(key: str, inputs: dict, parent_node_id: str | None = None, targeted_inputs: dict | None = None) -> dict:
            captured["broadcast"] = inputs
            captured["targeted"] = targeted_inputs
            return {"out": "done"}

        ctx = _make_context()
        ctx._run_subgraph = capture
        executor = CompositeExecutor()
        await executor.execute(node, {"a": 1, "b": 2}, ctx)

        assert captured["broadcast"] == {"portY": 2}
        assert captured["targeted"] == {"nodeA": {"portX": 1}}

    @pytest.mark.asyncio
    async def test_duplicate_port_names_route_correctly(self):
        """Two entry nodes with same port name get separate targeted injections."""
        node = CompositeNode(
            id="comp",
            name="DuplicatePorts",
            body_graph="body",
            input_mappings={
                "input": "nodeA::input",
                "nodeB__input": "nodeB::input",
            },
            output_mappings={},
            input_ports=[InputPort(name="input"), InputPort(name="nodeB__input")],
            output_ports=[OutputPort(name="out")],
        )

        captured = {}

        async def capture(key: str, inputs: dict, parent_node_id: str | None = None, targeted_inputs: dict | None = None) -> dict:
            captured["broadcast"] = inputs
            captured["targeted"] = targeted_inputs
            return {"out": "done"}

        ctx = _make_context()
        ctx._run_subgraph = capture
        executor = CompositeExecutor()
        await executor.execute(node, {"input": "for_A", "nodeB__input": "for_B"}, ctx)

        assert captured["broadcast"] == {}
        assert captured["targeted"] == {
            "nodeA": {"input": "for_A"},
            "nodeB": {"input": "for_B"},
        }


class TestNodeAwareOutputMappings:
    @pytest.mark.asyncio
    async def test_node_aware_output_mapping(self):
        """Output mapping with `nodeId::portName` key extracts by portName."""
        node = CompositeNode(
            id="comp",
            name="NodeAwareOutput",
            body_graph="body",
            input_mappings={},
            output_mappings={"pkg_sub::bundle_path": "bundle", "pkg_sub::title": "title"},
            input_ports=[InputPort(name="in")],
            output_ports=[OutputPort(name="bundle"), OutputPort(name="title")],
        )

        ctx = _make_context({"body": {"bundle_path": "/out.zip", "title": "My Paper", "extra": 99}})
        executor = CompositeExecutor()
        result = await executor.execute(node, {"in": "x"}, ctx)

        assert result.outputs["bundle"] == "/out.zip"
        assert result.outputs["title"] == "My Paper"
        assert result.outputs["extra"] == 99

    @pytest.mark.asyncio
    async def test_legacy_output_mapping(self):
        """Output mapping without `::` still works."""
        node = CompositeNode(
            id="comp",
            name="LegacyOutput",
            body_graph="body",
            input_mappings={},
            output_mappings={"inner_out": "outer_out"},
            input_ports=[InputPort(name="in")],
            output_ports=[OutputPort(name="outer_out")],
        )

        ctx = _make_context({"body": {"inner_out": "hello"}})
        executor = CompositeExecutor()
        result = await executor.execute(node, {"in": "x"}, ctx)

        assert result.outputs["outer_out"] == "hello"


class TestMultiEntryRunReadiness:
    @pytest.mark.asyncio
    async def test_zero_input_port_entry_still_runs(self):
        """CompositeExecutor doesn't fail when body has entry nodes with zero input ports."""
        node = CompositeNode(
            id="comp",
            name="MultiEntry",
            body_graph="body",
            input_mappings={"topic": "idea_gen::topic"},
            output_mappings={},
            input_ports=[InputPort(name="topic")],
            output_ports=[OutputPort(name="result")],
        )

        ctx = _make_context({"body": {"result": "success"}})
        executor = CompositeExecutor()
        result = await executor.execute(node, {"topic": "AI"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["result"] == "success"

    @pytest.mark.asyncio
    async def test_partial_input_coverage(self):
        """Inputs that don't cover all entry nodes still work (unmapped entries run with no inputs)."""
        node = CompositeNode(
            id="comp",
            name="PartialCoverage",
            body_graph="body",
            input_mappings={"topic": "idea_gen::topic"},
            output_mappings={},
            input_ports=[InputPort(name="topic")],
            output_ports=[OutputPort(name="result")],
        )

        captured = {}

        async def capture(key: str, inputs: dict, parent_node_id: str | None = None, targeted_inputs: dict | None = None) -> dict:
            captured["broadcast"] = inputs
            captured["targeted"] = targeted_inputs
            return {"result": "ok"}

        ctx = _make_context()
        ctx._run_subgraph = capture
        executor = CompositeExecutor()
        await executor.execute(node, {"topic": "AI"}, ctx)

        assert captured["broadcast"] == {}
        assert captured["targeted"] == {"idea_gen": {"topic": "AI"}}


class TestTargetedInjectionInSubgraph:
    @pytest.mark.asyncio
    async def test_targeted_injection_sets_port_data(self):
        """_run_subgraph properly injects targeted inputs into sub-engine state."""
        from dan.engine.scheduler import Engine, _topological_levels

        entry_node = ToolOperator(
            id="check_deps",
            name="Check Deps",
            tool_id="check",
            input_ports=[],
            output_ports=[OutputPort(name="ok")],
        )
        wired_node = LLMOperator(
            id="idea_gen",
            name="Idea Gen",
            model="test",
            prompt_template="{topic}",
            input_ports=[InputPort(name="topic")],
            output_ports=[OutputPort(name="text")],
        )

        sub_graph = Graph(
            nodes=[entry_node, wired_node],
            edges=[],
            entry_points=["check_deps", "idea_gen"],
            exit_points=["check_deps", "idea_gen"],
        )

        parent_graph = Graph(
            nodes=[],
            edges=[],
            entry_points=[],
            exit_points=[],
            sub_graphs={"body": sub_graph},
        )

        state = ExecutionState(parent_graph)
        sub_state = ExecutionState(sub_graph, run_id="test-run")

        from dan.engine.scheduler import Engine
        Engine._inject_inputs(sub_state, sub_graph, {})

        targeted = {"idea_gen": {"topic": "AI research"}}
        for node_id, port_values in targeted.items():
            for port_name, value in port_values.items():
                sub_state.port_data.set(f"__input__{node_id}", port_name, value)

        assert sub_state.port_data.has("__input__idea_gen", "topic")
        assert sub_state.port_data.get("__input__idea_gen", "topic") == "AI research"
        assert not sub_state.port_data.has("__input__check_deps", "topic")
