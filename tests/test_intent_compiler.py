"""Tests for IntentCompiler (33-6 task 3): verify compile() handles all stage types."""

from __future__ import annotations

import pytest

from dan.meta.intent_compiler import IntentCompiler
from dan.meta.intent_schema import (
    LoopRequirement,
    ReviewRequirement,
    StageIntent,
    StageType,
    WorkflowIntent,
)


def _chain_intent() -> WorkflowIntent:
    """Build a simple 3-step chain: research, analyze, summarize."""
    return WorkflowIntent(
        goal="Build a simple 3-step chain",
        stages=[
            StageIntent(name="research", description="Research a topic", stage_type=StageType.transform),
            StageIntent(name="analyze", description="Analyze findings", stage_type=StageType.transform),
            StageIntent(name="summarize", description="Write a summary", stage_type=StageType.transform),
        ],
    )


def _review_loop_intent() -> WorkflowIntent:
    """Single review_loop stage."""
    return WorkflowIntent(
        goal="Draft then review until approved",
        stages=[
            StageIntent(
                name="draft_review",
                description="Draft content and get feedback",
                stage_type=StageType.review_loop,
                review=ReviewRequirement(
                    reviewer_prompt="Review for quality",
                    condition="quality_score >= 8",
                    max_iterations=3,
                ),
            ),
        ],
    )


def _fan_out_intent() -> WorkflowIntent:
    """Single fan_out stage."""
    return WorkflowIntent(
        goal="Process items in parallel",
        stages=[
            StageIntent(
                name="process_each",
                description="Process each item",
                stage_type=StageType.fan_out,
                parallelism=4,
            ),
        ],
    )


def _rag_intent() -> WorkflowIntent:
    """Single rag_retrieval stage."""
    return WorkflowIntent(
        goal="RAG pipeline: retrieve and answer",
        stages=[
            StageIntent(
                name="rag_qa",
                description="Retrieve context and answer",
                stage_type=StageType.rag_retrieval,
            ),
        ],
    )


def _loop_intent() -> WorkflowIntent:
    """Single loop stage."""
    return WorkflowIntent(
        goal="Count up to 3",
        stages=[
            StageIntent(
                name="count_to_three",
                stage_type=StageType.loop,
                loop=LoopRequirement(condition="counter < 3"),
            ),
        ],
    )


class TestIntentCompilerCompile:
    """33-6 task 3: Verify IntentCompiler.compile() handles extracted intents."""

    def test_chain_produces_valid_code(self) -> None:
        """3 transform stages → valid builder code with transform workers and wf.build()."""
        intent = _chain_intent()
        compiler = IntentCompiler()
        code = compiler.compile(intent)
        assert "wf.llm" in code or "wf.worker" in code
        assert "graph = wf.build()" in code
        assert "research" in code
        assert "analyze" in code
        assert "summarize" in code

    def test_review_loop_produces_valid_code(self) -> None:
        """1 review_loop stage → valid builder code with review_loop."""
        intent = _review_loop_intent()
        compiler = IntentCompiler()
        code = compiler.compile(intent)
        assert "review_loop" in code
        assert "graph = wf.build()" in code

    def test_fan_out_produces_valid_code(self) -> None:
        """1 fan_out stage → valid builder code with for_each or fan-out."""
        intent = _fan_out_intent()
        compiler = IntentCompiler()
        code = compiler.compile(intent)
        assert "for_each" in code or "fan" in code.lower()
        assert "graph = wf.build()" in code

    def test_fan_out_with_code_body_produces_code_node(self) -> None:
        intent = WorkflowIntent(
            goal="triple items",
            stages=[
                StageIntent(
                    name="triple_each",
                    stage_type=StageType.fan_out,
                    config={"body_code": "result = item * 3"},
                ),
            ],
        )
        compiler = IntentCompiler()
        code = compiler.compile(intent)
        assert (
            'body.code("triple_each_proc"' in code
            or 'body.worker("triple_each_proc"' in code
        )
        assert "result = item * 3" in code

    def test_rag_retrieval_produces_valid_code(self) -> None:
        """1 rag_retrieval stage → valid builder code."""
        intent = _rag_intent()
        compiler = IntentCompiler()
        code = compiler.compile(intent)
        assert "graph = wf.build()" in code

    def test_loop_produces_valid_code(self) -> None:
        """1 loop stage → valid builder code with a while gate."""
        intent = _loop_intent()
        compiler = IntentCompiler()
        code = compiler.compile(intent)
        assert 'wf.gate("count_to_three_gate"' in code
        assert 'gate_mode="while"' in code
        assert "graph = wf.build()" in code

    def test_chain_code_executes_and_produces_graph(self) -> None:
        """Compiled chain code executes and produces a graph dict."""
        intent = _chain_intent()
        compiler = IntentCompiler()
        code = compiler.compile(intent)
        ns: dict = {}
        exec(code, ns)
        graph = ns.get("graph")
        assert graph is not None
        assert hasattr(graph, "model_dump") or isinstance(graph, dict)
        g_dict = graph.model_dump() if hasattr(graph, "model_dump") else graph
        nodes = g_dict.get("nodes", [])
        assert len(nodes) >= 3
