"""Integration tests — execute graphs end-to-end with mock executors."""

import asyncio
import pytest

from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.executor import ExecutionContext, ExecutorRegistry
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    GateNode,
    IfElseNode,
    InputNode,
    InputVariable,
    WhileLoopNode,
)
from dan.models.context import CompactionRule, CompactionStrategy, FailurePolicy, MergeStrategy
from dan.models.edges import ControlEdge, DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, LLMOperator, NodeBase
from dan.models.ports import InputPort, OutputPort


def _config():
    return EngineConfig(checkpoint_enabled=False)


def _engine():
    return Engine(config=_config(), checkpoint_store=NullCheckpointStore())


# ---------------------------------------------------------------------------
# Mock LLM executor — returns deterministic outputs
# ---------------------------------------------------------------------------


class MockLLMExecutor:
    """Returns the prompt template as the output text (no actual LLM call)."""

    def __init__(self, responses: dict[str, dict] | None = None):
        self._responses = responses or {}

    async def execute(
        self, node: NodeBase, inputs: dict, context: ExecutionContext
    ) -> NodeResult:
        if node.id in self._responses:
            return NodeResult(outputs=self._responses[node.id])

        return NodeResult(
            outputs={"text": f"mock-output-from-{node.id}"},
            status=NodeStatus.COMPLETED,
        )


# ---------------------------------------------------------------------------
# Test: InputNode run input injection
# ---------------------------------------------------------------------------


class TestInputNodeInjection:
    @pytest.mark.asyncio
    async def test_run_inputs_flow_into_inputnode_variables(self):
        """Engine.run(inputs=...) should populate InputNode variable outputs."""
        workflow_inputs = InputNode(
            id="workflow_inputs",
            name="Workflow Inputs",
            variables=[
                InputVariable(name="start_year", type="number"),
                InputVariable(name="end_year", type="number"),
            ],
            output_ports=[
                OutputPort(name="start_year"),
                OutputPort(name="end_year"),
            ],
        )
        entry = CodeOperator(
            id="entry",
            name="Entry",
            code="result = {'start_year': start_year, 'end_year': end_year}",
            input_ports=[InputPort(name="start_year"), InputPort(name="end_year")],
            output_ports=[OutputPort(name="start_year"), OutputPort(name="end_year")],
        )

        graph = Graph(
            nodes=[workflow_inputs, entry],
            edges=[
                DataEdge(
                    id="e1",
                    source_node_id="workflow_inputs",
                    source_port="start_year",
                    target_node_id="entry",
                    target_port="start_year",
                ),
                DataEdge(
                    id="e2",
                    source_node_id="workflow_inputs",
                    source_port="end_year",
                    target_node_id="entry",
                    target_port="end_year",
                ),
            ],
            entry_points=["workflow_inputs"],
            exit_points=["entry"],
        )

        engine = _engine()
        result = await engine.run(
            graph,
            inputs={"start_year": 2018, "end_year": 2019},
        )

        assert result.success
        assert result.outputs["start_year"] == 2018
        assert result.outputs["end_year"] == 2019

    @pytest.mark.asyncio
    async def test_subgraph_inputnode_receives_composite_inputs(self):
        """Composite sub-graph InputNode variables should receive mapped inputs."""
        workflow_inputs = InputNode(
            id="workflow_inputs",
            name="Workflow Inputs",
            variables=[InputVariable(name="input", type="number")],
            output_ports=[OutputPort(name="input")],
        )
        echo = CodeOperator(
            id="echo",
            name="Echo",
            code="result = {'value': input}",
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="value")],
        )
        body_graph = Graph(
            nodes=[workflow_inputs, echo],
            edges=[
                DataEdge(
                    id="be1",
                    source_node_id="workflow_inputs",
                    source_port="input",
                    target_node_id="echo",
                    target_port="input",
                )
            ],
            entry_points=["workflow_inputs"],
            exit_points=["echo"],
        )
        comp = CompositeNode(
            id="comp",
            name="Composite",
            body_graph="body",
            input_mappings={"input": "input"},
            output_mappings={"value": "value"},
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="value")],
        )
        graph = Graph(
            nodes=[comp],
            sub_graphs={"body": body_graph},
            entry_points=["comp"],
            exit_points=["comp"],
        )

        engine = _engine()
        result = await engine.run(graph, inputs={"input": 42})
        assert result.success
        assert result.outputs["value"] == 42


# ---------------------------------------------------------------------------
# Test: Linear chain
# ---------------------------------------------------------------------------


class TestLinearChain:
    @pytest.mark.asyncio
    async def test_three_node_chain(self):
        """A -> B -> C, each a CodeOperator that transforms data."""
        a = CodeOperator(
            id="a", name="A", code="result = {'value': 1}",
            output_ports=[OutputPort(name="value")],
        )
        b = CodeOperator(
            id="b", name="B", code="result = {'value': value * 10}",
            input_ports=[InputPort(name="value")],
            output_ports=[OutputPort(name="value")],
        )
        c = CodeOperator(
            id="c", name="C", code="result = {'value': value + 5}",
            input_ports=[InputPort(name="value")],
            output_ports=[OutputPort(name="value")],
        )

        graph = Graph(
            nodes=[a, b, c],
            edges=[
                DataEdge(id="e1", source_node_id="a", source_port="value",
                         target_node_id="b", target_port="value"),
                DataEdge(id="e2", source_node_id="b", source_port="value",
                         target_node_id="c", target_port="value"),
            ],
            entry_points=["a"],
            exit_points=["c"],
        )

        engine = _engine()
        result = await engine.run(graph)
        assert result.success
        assert result.outputs["value"] == 15  # (1 * 10) + 5


# ---------------------------------------------------------------------------
# Test: IfElse branching
# ---------------------------------------------------------------------------


class TestIfElseBranching:
    @pytest.mark.asyncio
    async def test_true_branch(self):
        """IfElse routes to the 'true' branch when condition is met."""
        source = CodeOperator(
            id="src", name="Source",
            code="result = {'score': 0.9}",
            output_ports=[OutputPort(name="score")],
        )
        branch = IfElseNode(
            id="branch", name="Branch",
            condition="score > 0.5",
            input_ports=[InputPort(name="score")],
            output_ports=[OutputPort(name="branch"), OutputPort(name="score")],
        )
        true_node = CodeOperator(
            id="true_out", name="True",
            code="result = {'decision': 'accept'}",
            input_ports=[InputPort(name="score"), InputPort(name="branch", required=False)],
            output_ports=[OutputPort(name="decision")],
        )
        false_node = CodeOperator(
            id="false_out", name="False",
            code="result = {'decision': 'reject'}",
            input_ports=[InputPort(name="score"), InputPort(name="branch", required=False)],
            output_ports=[OutputPort(name="decision")],
        )

        graph = Graph(
            nodes=[source, branch, true_node, false_node],
            edges=[
                DataEdge(id="e1", source_node_id="src", source_port="score",
                         target_node_id="branch", target_port="score"),
                DataEdge(id="e2", source_node_id="branch", source_port="score",
                         target_node_id="true_out", target_port="score"),
                ControlEdge(id="ce1", source_node_id="branch", source_port="branch",
                            target_node_id="true_out", target_port="branch",
                            condition="true"),
                DataEdge(id="e3", source_node_id="branch", source_port="score",
                         target_node_id="false_out", target_port="score"),
                ControlEdge(id="ce2", source_node_id="branch", source_port="branch",
                            target_node_id="false_out", target_port="branch",
                            condition="false"),
            ],
            entry_points=["src"],
            exit_points=["true_out", "false_out"],
        )

        engine = _engine()
        result = await engine.run(graph)
        assert result.success
        assert result.outputs.get("decision") == "accept"
        assert result.node_statuses["true_out"] == "completed"
        assert result.node_statuses["false_out"] == "skipped"


# ---------------------------------------------------------------------------
# Test: WhileLoop
# ---------------------------------------------------------------------------


class TestWhileLoop:
    @pytest.mark.asyncio
    async def test_loop_with_condition_exit(self):
        """WhileLoop iterates body sub-graph until condition is false."""
        body_node = CodeOperator(
            id="inc", name="Increment",
            code="result = {'counter': counter + 1}",
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="counter")],
        )
        body_graph = Graph(
            nodes=[body_node],
            entry_points=["inc"],
            exit_points=["inc"],
        )

        loop = WhileLoopNode(
            id="loop", name="Counter Loop",
            condition="counter < 5",
            body_graph="loop_body",
            max_iterations=20,
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="counter")],
            failure_policy=FailurePolicy(max_iterations=20),
        )

        main_graph = Graph(
            nodes=[loop],
            sub_graphs={"loop_body": body_graph},
            entry_points=["loop"],
            exit_points=["loop"],
        )

        engine = _engine()
        result = await engine.run(main_graph, inputs={"counter": 0})
        assert result.success
        assert result.outputs["counter"] == 5


# ---------------------------------------------------------------------------
# Test: ForEach fan-out/fan-in
# ---------------------------------------------------------------------------


class TestForEach:
    @pytest.mark.asyncio
    async def test_parallel_fanout(self):
        """ForEach processes items in parallel and merges results."""
        body_node = CodeOperator(
            id="double", name="Double",
            code="result = {'value': item * 2}",
            input_ports=[InputPort(name="item"), InputPort(name="index")],
            output_ports=[OutputPort(name="value")],
        )
        body_graph = Graph(
            nodes=[body_node],
            entry_points=["double"],
            exit_points=["double"],
        )

        foreach = ForEachNode(
            id="fan", name="Fan-Out",
            body_graph="body",
            parallelism=3,
            merge_strategy=MergeStrategy.APPEND,
            input_ports=[InputPort(name="items")],
            output_ports=[OutputPort(name="results")],
        )

        main_graph = Graph(
            nodes=[foreach],
            sub_graphs={"body": body_graph},
            entry_points=["fan"],
            exit_points=["fan"],
        )

        engine = _engine()
        result = await engine.run(main_graph, inputs={"items": [1, 2, 3]})
        assert result.success
        results = result.outputs["results"]
        assert len(results) == 3
        values = sorted(r["value"] for r in results)
        assert values == [2, 4, 6]


# ---------------------------------------------------------------------------
# Test: Composite with cyclic sub-graph (gate loop in body)
# ---------------------------------------------------------------------------


class TestCompositeCyclicSubgraph:
    """Composite whose body has a gate(while) back-edge — ReAct-style template."""

    @pytest.mark.asyncio
    async def test_composite_with_gate_loop_in_body(self):
        """Composite with cyclic body (Gate.continue back-edge) runs correctly."""
        inc = CodeOperator(
            id="inc",
            name="Increment",
            code="result = {'counter': counter + 1}",
            input_ports=[InputPort(name="counter")],
            output_ports=[OutputPort(name="counter")],
        )
        gate = GateNode(
            id="gate",
            name="Loop Gate",
            gate_mode="while",
            condition="counter < 2",
            max_iterations=5,
            input_ports=[InputPort(name="counter")],
            output_ports=[
                OutputPort(name="continue"),
                OutputPort(name="done"),
            ],
        )
        body_graph = Graph(
            nodes=[inc, gate],
            edges=[
                DataEdge(id="e1", source_node_id="inc", source_port="counter",
                        target_node_id="gate", target_port="counter"),
                DataEdge(id="e2", source_node_id="gate", source_port="continue",
                        target_node_id="inc", target_port="counter"),
            ],
            entry_points=["inc"],
            exit_points=["gate"],
        )
        composite = CompositeNode(
            id="comp",
            name="Loop Composite",
            body_graph="body",
            input_mappings={"input": "counter"},
            output_mappings={"done": "output"},
            input_ports=[InputPort(name="input")],
            output_ports=[OutputPort(name="output")],
        )
        main_graph = Graph(
            nodes=[composite],
            sub_graphs={"body": body_graph},
            entry_points=["comp"],
            exit_points=["comp"],
        )

        engine = _engine()
        result = await engine.run(main_graph, inputs={"input": 0})
        assert result.success, result.errors
        # Iteration 0: inc gets 0, outputs 1. Gate: 1 < 2, continue.
        # Iteration 1: inc gets 1, outputs 2. Gate: 2 < 2 false, done.
        assert result.outputs.get("output", {}).get("counter") == 2


# ---------------------------------------------------------------------------
# Test: Checkpoint and resume
# ---------------------------------------------------------------------------


class TestCheckpointResume:
    @pytest.mark.asyncio
    async def test_resume_no_checkpoint(self):
        engine = Engine(config=EngineConfig(checkpoint_enabled=False))
        graph = Graph(nodes=[], entry_points=[], exit_points=[])
        result = await engine.resume(graph, run_id="nonexistent")
        assert not result.success
        assert "checkpoint" in str(result.errors).lower()
