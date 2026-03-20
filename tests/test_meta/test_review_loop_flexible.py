"""Tests for 32-6 Task 2: Flexible review_loop() criteria."""

from __future__ import annotations

import pytest

from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.meta.intent_compiler import IntentCompiler
from dan.meta.intent_schema import (
    ReviewRequirement,
    StageIntent,
    StageType,
    WorkflowIntent,
)


class TestReviewLoopDefaults:
    """Verify existing default behavior is completely unchanged."""

    def test_default_builds_successfully(self):
        wf = workflow("test")
        ref = wf.review_loop("Write a draft", "Review for quality")
        assert isinstance(ref, NodeRef)
        graph = wf.build()
        assert any(n.id == "review_loop" for n in graph.nodes)

    def test_default_condition_is_quality_score(self):
        wf = workflow("test")
        wf.review_loop("Write", "Review")
        graph = wf.build()
        loop_node = next(n for n in graph.nodes if n.id == "review_loop")
        assert loop_node.condition == "quality_score < 8"

    def test_default_ports_include_standard_fields(self):
        wf = workflow("test")
        wf.review_loop("Write", "Review")
        graph = wf.build()
        loop_node = next(n for n in graph.nodes if n.id == "review_loop")
        port_names = [p.name for p in loop_node.input_ports]
        assert "draft" in port_names
        assert "quality_score" in port_names
        assert "feedback" in port_names

    def test_default_max_rounds_content(self):
        wf = workflow("test")
        wf.review_loop("Write a report on the topic", "Review")
        graph = wf.build()
        loop_node = next(n for n in graph.nodes if n.id == "review_loop")
        assert loop_node.max_iterations == 2

    def test_default_max_rounds_non_content(self):
        wf = workflow("test")
        wf.review_loop("Analyze the data", "Review")
        graph = wf.build()
        loop_node = next(n for n in graph.nodes if n.id == "review_loop")
        assert loop_node.max_iterations == 3


class TestReviewLoopCustomCriteria:
    def test_custom_condition(self):
        wf = workflow("test")
        wf.review_loop("Write", "Review", condition="citations_valid == true")
        graph = wf.build()
        loop_node = next(n for n in graph.nodes if n.id == "review_loop")
        assert loop_node.condition == "citations_valid == true"

    def test_custom_review_fields(self):
        wf = workflow("test")
        wf.review_loop(
            "Write",
            "Review",
            review_fields={
                "citations_valid": {"type": "boolean"},
                "issues": {"type": "string"},
            },
        )
        graph = wf.build()
        loop_node = next(n for n in graph.nodes if n.id == "review_loop")
        port_names = [p.name for p in loop_node.input_ports]
        assert "draft" in port_names
        assert "citations_valid" in port_names
        assert "issues" in port_names
        assert "quality_score" not in port_names

    def test_custom_feedback_key(self):
        wf = workflow("test")
        wf.review_loop(
            "Write",
            "Review",
            review_fields={
                "accuracy": {"type": "number"},
                "notes": {"type": "string"},
            },
            feedback_key="notes",
            condition="accuracy < 0.9",
        )
        graph = wf.build()
        loop_node = next(n for n in graph.nodes if n.id == "review_loop")
        assert loop_node.condition == "accuracy < 0.9"

    def test_custom_fields_in_reviewer_schema(self):
        wf = workflow("test")
        wf.review_loop(
            "Write",
            "Verify citations",
            review_fields={
                "citations_valid": {"type": "boolean"},
                "missing_refs": {"type": "string"},
            },
            condition="citations_valid == false",
            feedback_key="missing_refs",
        )
        graph = wf.build()
        loop_node = next(n for n in graph.nodes if n.id == "review_loop")
        body = graph.sub_graphs[loop_node.body_graph]
        reviewer = next(n for n in body.nodes if n.id == "review_reviewer")
        schema = reviewer.output_json_schema or {}
        props = schema.get("properties", {})
        assert "citations_valid" in props
        assert "missing_refs" in props
        required = schema.get("required", [])
        assert "citations_valid" in required
        assert "missing_refs" in required


class TestReviewLoopCompilerPassthrough:
    def test_compose_review_loop_passes_condition(self):
        compiler = IntentCompiler()
        intent = WorkflowIntent(
            goal="test",
            stages=[
                StageIntent(
                    name="quality",
                    stage_type=StageType.review_loop,
                    description="Check quality",
                    review=ReviewRequirement(
                        reviewer_prompt="Review accuracy",
                        condition="accuracy >= 0.95",
                        max_iterations=2,
                    ),
                ),
            ],
        )
        code = compiler.compile_composed(intent, ["review_loop"])
        assert "condition=" in code

    def test_compose_research_review_passes_condition(self):
        compiler = IntentCompiler()
        intent = WorkflowIntent(
            goal="test",
            stages=[
                StageIntent(
                    name="research",
                    stage_type=StageType.transform,
                    description="Research topic",
                ),
                StageIntent(
                    name="review",
                    stage_type=StageType.review_loop,
                    description="Review output",
                    review=ReviewRequirement(
                        reviewer_prompt="Verify citations",
                        condition="citations_ok == true",
                        max_iterations=3,
                    ),
                ),
            ],
        )
        code = compiler.compile_composed(intent, ["research_review"])
        assert "condition=" in code
