"""Tests for goal_loop agent type in the markdown loader/compiler."""

from __future__ import annotations

import pytest

from dan.loader.models import AgentSpec


class TestGoalLoopAgentSpec:
    def test_agent_type_accepts_goal_loop(self):
        spec = AgentSpec(agent_type="goal_loop", name="test")
        assert spec.agent_type == "goal_loop"

    def test_agent_type_default_is_llm(self):
        spec = AgentSpec(name="test")
        assert spec.agent_type == "llm"


class TestGoalLoopCompilation:
    def test_compile_goal_loop_agent(self):
        from dan.loader.compiler import _compile_agent
        from dan.loader.diagnostics import Diagnostic

        spec = AgentSpec(
            agent_type="goal_loop",
            name="optimizer",
            prompt_body="maximize the revenue",
        )
        spec.raw_frontmatter = {
            "type": "goal_loop",
            "goal_text": "maximize revenue",
            "metric_name": "revenue",
            "target_value": 1000.0,
            "comparison": ">=",
            "max_iterations": 15,
            "evaluator": "llm_judge",
        }

        diagnostics: list[Diagnostic] = []
        node, sub_graphs = _compile_agent(
            "optimizer", spec, diagnostics,
        )

        assert node is not None
        assert node.node_type == "goal_loop"
        assert node.goal_text == "maximize revenue"
        assert node.metric_name == "revenue"
        assert node.target_value == 1000.0
        assert node.max_iterations == 15
        assert node.body_graph == "optimizer_body"
        assert len(diagnostics) == 0

    def test_compile_goal_loop_defaults(self):
        from dan.loader.compiler import _compile_agent
        from dan.loader.diagnostics import Diagnostic

        spec = AgentSpec(
            agent_type="goal_loop",
            name="gl",
            prompt_body="hit goal",
        )
        spec.raw_frontmatter = {"type": "goal_loop"}

        diagnostics: list[Diagnostic] = []
        node, _ = _compile_agent("gl", spec, diagnostics)

        assert node is not None
        assert node.goal_text == "hit goal"
        assert node.metric_name == "score"
        assert node.target_value == 1.0
        assert node.comparison == ">="
        assert node.max_iterations == 10
