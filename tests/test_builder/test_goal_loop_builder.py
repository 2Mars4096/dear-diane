"""Tests for the goal_loop builder DSL method."""

from __future__ import annotations

import pytest

from dan.builder.builder import workflow
from dan.models.control_flow import GoalLoopNode


class TestGoalLoopBuilder:
    def test_basic_goal_loop_creates_node_and_subgraph(self):
        wf = workflow("test")
        with wf.goal_loop(
            "optimize",
            goal_text="maximize accuracy",
            metric_name="accuracy",
            target_value=95.0,
        ) as sub:
            sub.llm("step", model="test", prompt="optimize")

        graph = wf.build()

        goal_nodes = [n for n in graph.nodes if n.node_type == "goal_loop"]
        assert len(goal_nodes) == 1
        node = goal_nodes[0]
        assert isinstance(node, GoalLoopNode)
        assert node.goal_text == "maximize accuracy"
        assert node.metric_name == "accuracy"
        assert node.target_value == 95.0
        assert "optimize_body" in graph.sub_graphs

    def test_goal_loop_with_all_options(self):
        wf = workflow("test")
        with wf.goal_loop(
            "gl",
            goal_text="reduce cost",
            metric_name="cost",
            target_value=50.0,
            comparison="<=",
            max_iterations=20,
            evaluator="script",
            success_criteria="cost < 50",
            name="Cost Reducer",
            description="Iteratively reduce cost",
        ) as sub:
            sub.llm("body", model="test", prompt="reduce")

        graph = wf.build()
        goal_nodes = [n for n in graph.nodes if n.node_type == "goal_loop"]
        assert len(goal_nodes) == 1
        node = goal_nodes[0]
        assert isinstance(node, GoalLoopNode)
        assert node.comparison == "<="
        assert node.max_iterations == 20
        assert node.evaluator == "script"
        assert node.success_criteria == "cost < 50"
        assert node.name == "Cost Reducer"

    def test_goal_loop_default_output_port(self):
        from dan.builder.compiler import default_output_port
        assert default_output_port("goal_loop") == "result"

    def test_goal_loop_body_has_nodes(self):
        wf = workflow("test")
        with wf.goal_loop(
            "gl",
            goal_text="test",
        ) as sub:
            sub.llm("a", model="m", prompt="p1")
            sub.llm("b", model="m", prompt="p2")

        graph = wf.build()
        body = graph.sub_graphs["gl_body"]
        assert len(body.nodes) == 2

    def test_goal_loop_in_chain(self):
        """goal_loop can be part of a larger workflow chain."""
        wf = workflow("test")
        prep = wf.llm("prep", model="m", prompt="prepare data")
        with wf.goal_loop(
            "optimize",
            goal_text="hit target",
        ) as sub:
            sub.llm("step", model="m", prompt="iterate")

        graph = wf.build()
        assert len(graph.nodes) >= 2
        goal_nodes = [n for n in graph.nodes if n.node_type == "goal_loop"]
        assert len(goal_nodes) == 1
