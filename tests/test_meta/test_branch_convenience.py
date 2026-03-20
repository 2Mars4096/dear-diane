"""Tests for 32-6 Task 1: wf.branch() convenience + conditional intent path."""

from __future__ import annotations

import pytest

from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.meta.intent_compiler import (
    COVERAGE_CATALOG,
    CoverageChecker,
    IntentCompiler,
)
from dan.meta.intent_schema import (
    ConditionalRequirement,
    StageIntent,
    StageType,
    WorkflowIntent,
)


class TestBranchConvenience:
    def test_basic_branch(self):
        wf = workflow("test")
        gate_ref, then_ref, else_ref = wf.branch(
            condition="score > 0.5",
            then_prompt="Handle positive",
            else_prompt="Handle negative",
        )
        assert isinstance(gate_ref, NodeRef)
        assert isinstance(then_ref, NodeRef)
        assert isinstance(else_ref, NodeRef)
        assert gate_ref.node_id == "branch_gate"
        graph = wf.build()
        node_ids = [n.id for n in graph.nodes]
        assert "branch_gate" in node_ids
        assert "branch_then" in node_ids
        assert "branch_else" in node_ids

    def test_branch_after_chain(self):
        wf = workflow("test")
        last = wf.chain(("s1", "Step 1"), ("s2", "Step 2"))
        gate_ref, then_ref, else_ref = wf.branch(
            condition="sentiment > 0",
            then_prompt="Positive path",
            else_prompt="Negative path",
            name="check",
        )
        last >> gate_ref
        graph = wf.build()
        assert len(graph.nodes) >= 5
        gate_incoming = [e for e in graph.edges if e.target_node_id == "check_gate"]
        assert len(gate_incoming) == 1

    def test_branch_with_model_overrides(self):
        wf = workflow("test")
        gate_ref, then_ref, else_ref = wf.branch(
            condition="x > 0",
            then_prompt="A",
            else_prompt="B",
            then_model="fast-model",
            else_model="quality-model",
        )
        graph = wf.build()
        then_node = next(n for n in graph.nodes if n.id == "branch_then")
        else_node = next(n for n in graph.nodes if n.id == "branch_else")
        assert then_node.model == "fast-model"
        assert else_node.model == "quality-model"

    def test_branch_custom_name(self):
        wf = workflow("test")
        gate_ref, then_ref, else_ref = wf.branch(
            condition="flag",
            then_prompt="Yes",
            else_prompt="No",
            name="my_check",
        )
        graph = wf.build()
        node_ids = [n.id for n in graph.nodes]
        assert "my_check_gate" in node_ids
        assert "my_check_then" in node_ids
        assert "my_check_else" in node_ids

    def test_branch_merge_downstream(self):
        """Both branches converge into one downstream node."""
        wf = workflow("test")
        gate_ref, then_ref, else_ref = wf.branch("x > 0", "A", "B")
        merge = wf.llm("merge", prompt="Combine")
        then_ref >> merge
        else_ref >> merge
        graph = wf.build()
        merge_incoming = [e for e in graph.edges if e.target_node_id == "merge"]
        assert len(merge_incoming) == 2

    def test_existing_if_else_unchanged(self):
        wf = workflow("test")
        ref = wf.if_else("ie1", condition="x > 0")
        assert isinstance(ref, NodeRef)
        graph = wf.build()
        assert any(n.id == "ie1" for n in graph.nodes)

    def test_existing_gate_unchanged(self):
        wf = workflow("test")
        ref = wf.gate("g1", condition="y < 10", gate_mode="if_else")
        assert isinstance(ref, NodeRef)
        graph = wf.build()
        assert any(n.id == "g1" for n in graph.nodes)


class TestConditionalIntentSchema:
    def test_conditional_stage_type_exists(self):
        assert StageType.conditional == "conditional"

    def test_conditional_requirement_model(self):
        cr = ConditionalRequirement(
            condition="score > 5",
            then_description="handle high",
            else_description="handle low",
        )
        assert cr.condition == "score > 5"

    def test_conditional_stage_requires_conditional_field(self):
        with pytest.raises(ValueError, match="ConditionalRequirement is required"):
            StageIntent(
                name="test",
                stage_type=StageType.conditional,
            )

    def test_conditional_stage_valid(self):
        stage = StageIntent(
            name="test",
            stage_type=StageType.conditional,
            conditional=ConditionalRequirement(
                condition="x > 0",
                then_description="positive",
                else_description="negative",
            ),
        )
        assert stage.conditional is not None


class TestConditionalCompiler:
    def test_coverage_catalog_has_conditional(self):
        assert "conditional_branch" in COVERAGE_CATALOG
        entry = COVERAGE_CATALOG["conditional_branch"]
        assert StageType.conditional in entry["stage_types"]

    def test_coverage_checker_supports_conditional(self):
        checker = CoverageChecker()
        intent = WorkflowIntent(
            goal="test",
            stages=[
                StageIntent(
                    name="check",
                    stage_type=StageType.conditional,
                    conditional=ConditionalRequirement(
                        condition="x > 0",
                        then_description="positive",
                        else_description="negative",
                    ),
                )
            ],
        )
        result = checker.check(intent, try_compose=False)
        assert result.fully_covered
        assert result.recommendation == "compile"

    def test_compile_conditional_stage(self):
        compiler = IntentCompiler()
        intent = WorkflowIntent(
            goal="test conditional",
            stages=[
                StageIntent(
                    name="sentiment_check",
                    stage_type=StageType.conditional,
                    conditional=ConditionalRequirement(
                        condition="sentiment > 0.5",
                        then_description="Summarize positive",
                        else_description="Flag negative",
                    ),
                ),
            ],
        )
        code = compiler.compile(intent)
        assert "wf.branch(" in code
        assert "wf.build()" in code
        assert "sentiment > 0.5" in code
        assert "sentiment_check_gate, sentiment_check_then, sentiment_check_else = wf.branch(" in code

    def test_compile_conditional_in_chain(self):
        compiler = IntentCompiler()
        intent = WorkflowIntent(
            goal="test chain with conditional",
            stages=[
                StageIntent(name="analyze", stage_type=StageType.transform, description="Analyze input"),
                StageIntent(
                    name="route",
                    stage_type=StageType.conditional,
                    conditional=ConditionalRequirement(
                        condition="is_valid",
                        then_description="Process valid",
                        else_description="Reject invalid",
                    ),
                ),
            ],
        )
        code = compiler.compile(intent)
        assert "wf.branch(" in code
        assert "wf.llm(" in code
        assert ">> route_gate" in code

    def test_compile_conditional_converges_both_branches(self):
        """When a conditional is between two stages, both branches chain downstream."""
        compiler = IntentCompiler()
        intent = WorkflowIntent(
            goal="test convergence",
            stages=[
                StageIntent(name="analyze", stage_type=StageType.transform, description="Analyze"),
                StageIntent(
                    name="route",
                    stage_type=StageType.conditional,
                    conditional=ConditionalRequirement(
                        condition="score > 0.5",
                        then_description="Good path",
                        else_description="Bad path",
                    ),
                ),
                StageIntent(name="finalize", stage_type=StageType.transform, description="Finalize"),
            ],
        )
        code = compiler.compile(intent)
        assert "route_then >> finalize" in code
        assert "route_else >> finalize" in code

    def test_existing_catalog_count_updated(self):
        assert len(COVERAGE_CATALOG) == 12
