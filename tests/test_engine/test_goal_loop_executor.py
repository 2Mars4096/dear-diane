"""Tests for the GoalLoopExecutor and GoalLoopNode."""

from __future__ import annotations

import pytest

from dan.models.control_flow import GoalLoopNode
from dan.models.graph import Graph, Node
from dan.registry import NodeTypeRegistry


# ---------------------------------------------------------------------------
# GoalLoopNode model tests
# ---------------------------------------------------------------------------


class TestGoalLoopNode:
    def test_node_type_literal(self):
        node = GoalLoopNode(
            id="g1", name="g1", goal_text="hit target",
            body_graph="g1_body",
        )
        assert node.node_type == "goal_loop"

    def test_defaults(self):
        node = GoalLoopNode(
            id="g1", name="g1", goal_text="x", body_graph="g1_body",
        )
        assert node.metric_name == "score"
        assert node.target_value == 1.0
        assert node.comparison == ">="
        assert node.max_iterations == 10
        assert node.evaluator == "llm_judge"
        assert node.success_criteria is None

    def test_custom_fields(self):
        node = GoalLoopNode(
            id="g2", name="g2",
            goal_text="reduce cost",
            metric_name="cost",
            target_value=100.0,
            comparison="<=",
            max_iterations=5,
            evaluator="script",
            success_criteria="cost < 100",
            body_graph="g2_body",
        )
        assert node.metric_name == "cost"
        assert node.comparison == "<="
        assert node.max_iterations == 5
        assert node.success_criteria == "cost < 100"

    def test_registered_in_node_type_registry(self):
        assert NodeTypeRegistry.is_registered("goal_loop")
        assert NodeTypeRegistry.get("goal_loop") is GoalLoopNode

    def test_roundtrip_serialization(self):
        node = GoalLoopNode(
            id="g1", name="g1", goal_text="hit target",
            body_graph="g1_body",
        )
        data = node.model_dump()
        assert data["node_type"] == "goal_loop"
        assert data["goal_text"] == "hit target"

    def test_in_graph_node_union(self):
        """GoalLoopNode can be deserialized as part of a Graph."""
        graph = Graph(
            nodes=[
                GoalLoopNode(
                    id="g1", name="g1",
                    goal_text="test goal",
                    body_graph="g1_body",
                ),
            ],
        )
        assert len(graph.nodes) == 1
        assert graph.nodes[0].node_type == "goal_loop"


# ---------------------------------------------------------------------------
# GoalLoopExecutor tests
# ---------------------------------------------------------------------------


class _FakeLocalState:
    def __init__(self):
        self._scopes: dict[str, dict] = {}

    def get_scope(self, node_id: str) -> dict:
        if node_id not in self._scopes:
            self._scopes[node_id] = {}
        return self._scopes[node_id]


class _FakeContext:
    def __init__(self, subgraph_outputs: list[dict]):
        self._outputs = subgraph_outputs
        self._call_count = 0
        self.events: list[dict] = []
        self.local_state = _FakeLocalState()

    async def emit_event(self, *, event_type, node_id, node_type, data):
        self.events.append({"type": event_type, "node_id": node_id, "data": data})

    async def run_subgraph(self, sub_graph_key, inputs, parent_node_id=None):
        idx = min(self._call_count, len(self._outputs) - 1)
        result = dict(self._outputs[idx])
        self._call_count += 1
        return result


class TestGoalLoopExecutor:
    @pytest.fixture
    def executor(self):
        from dan.executors.control_flow import GoalLoopExecutor
        return GoalLoopExecutor()

    @pytest.fixture
    def goal_node(self):
        return GoalLoopNode(
            id="gl", name="gl",
            goal_text="reach 90%",
            metric_name="accuracy",
            target_value=90.0,
            comparison=">=",
            max_iterations=5,
            body_graph="gl_body",
        )

    @pytest.mark.asyncio
    async def test_goal_met_on_first_iteration(self, executor, goal_node):
        ctx = _FakeContext([{"accuracy": 95.0}])
        result = await executor.execute(goal_node, {}, ctx)
        assert result.status.value == "completed"
        assert result.outputs["goal_met"] is True
        assert result.outputs["iterations"] == 1
        assert result.outputs["best_score"] == 95.0

    @pytest.mark.asyncio
    async def test_goal_met_after_multiple_iterations(self, executor, goal_node):
        ctx = _FakeContext([
            {"accuracy": 50.0},
            {"accuracy": 75.0},
            {"accuracy": 92.0},
        ])
        result = await executor.execute(goal_node, {}, ctx)
        assert result.outputs["goal_met"] is True
        assert result.outputs["iterations"] == 3

    @pytest.mark.asyncio
    async def test_goal_not_met_exhausts_iterations(self, executor):
        node = GoalLoopNode(
            id="gl", name="gl",
            goal_text="never met",
            metric_name="val",
            target_value=100.0,
            comparison=">=",
            max_iterations=3,
            body_graph="gl_body",
        )
        ctx = _FakeContext([{"val": 10.0}, {"val": 20.0}, {"val": 30.0}])
        result = await executor.execute(node, {}, ctx)
        assert result.outputs["goal_met"] is False
        assert result.outputs["iterations"] == 3
        assert result.outputs["best_score"] == 30.0

    @pytest.mark.asyncio
    async def test_tracks_best_score(self, executor):
        node = GoalLoopNode(
            id="gl", name="gl",
            goal_text="maximize",
            metric_name="val",
            target_value=100.0,
            comparison=">=",
            max_iterations=4,
            body_graph="gl_body",
        )
        ctx = _FakeContext([
            {"val": 30.0},
            {"val": 80.0},
            {"val": 50.0},
            {"val": 60.0},
        ])
        result = await executor.execute(node, {}, ctx)
        assert result.outputs["best_score"] == 80.0

    @pytest.mark.asyncio
    async def test_less_than_comparison(self, executor):
        node = GoalLoopNode(
            id="gl", name="gl",
            goal_text="minimize cost",
            metric_name="cost",
            target_value=50.0,
            comparison="<",
            max_iterations=5,
            body_graph="gl_body",
        )
        ctx = _FakeContext([
            {"cost": 100.0},
            {"cost": 80.0},
            {"cost": 40.0},
        ])
        result = await executor.execute(node, {}, ctx)
        assert result.outputs["goal_met"] is True
        assert result.outputs["iterations"] == 3

    @pytest.mark.asyncio
    async def test_success_criteria_expression(self, executor):
        node = GoalLoopNode(
            id="gl", name="gl",
            goal_text="custom check",
            metric_name="val",
            target_value=0.0,
            comparison=">=",
            max_iterations=5,
            success_criteria="score > 50",
            body_graph="gl_body",
        )
        ctx = _FakeContext([
            {"val": 20.0},
            {"val": 60.0},
        ])
        result = await executor.execute(node, {}, ctx)
        assert result.outputs["goal_met"] is True
        assert result.outputs["iterations"] == 2

    @pytest.mark.asyncio
    async def test_emits_iteration_events(self, executor, goal_node):
        ctx = _FakeContext([{"accuracy": 95.0}])
        await executor.execute(goal_node, {}, ctx)
        started = [e for e in ctx.events if e["type"] == "iteration_started"]
        completed = [e for e in ctx.events if e["type"] == "iteration_completed"]
        assert len(started) == 1
        assert len(completed) == 1
        assert completed[0]["data"]["met"] is True

    @pytest.mark.asyncio
    async def test_goal_text_passed_to_body(self, executor, goal_node):
        received_inputs = {}

        class _TrackingCtx(_FakeContext):
            async def run_subgraph(self, sub_graph_key, inputs, parent_node_id=None):
                received_inputs.update(inputs)
                return {"accuracy": 95.0}

        ctx = _TrackingCtx([])
        await executor.execute(goal_node, {"data": "test"}, ctx)
        assert received_inputs["goal_text"] == "reach 90%"
        assert received_inputs["data"] == "test"

    @pytest.mark.asyncio
    async def test_missing_metric_falls_back_to_result(self, executor):
        node = GoalLoopNode(
            id="gl", name="gl",
            goal_text="test",
            metric_name="nonexistent",
            target_value=50.0,
            comparison=">=",
            max_iterations=2,
            body_graph="gl_body",
        )
        ctx = _FakeContext([{"result": 60.0}])
        result = await executor.execute(node, {}, ctx)
        assert result.outputs["goal_met"] is True
