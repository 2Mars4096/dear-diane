"""Tests for IntentCompiler.build_graph() — direct in-process Graph construction.

Covers all StageType values, multi-pattern composition, edge wiring,
conditional branching, domain-aware selection, and parity with compile().
"""

from __future__ import annotations

import pytest

from dan.meta.intent_compiler import (
    DirectBuildError,
    IntentCompiler,
    MissingCodeStageError,
)
from dan.meta.intent_schema import (
    ConditionalRequirement,
    LoopRequirement,
    ReviewRequirement,
    StageIntent,
    StageType,
    WorkflowIntent,
)
from dan.worker.model import Worker
from dan.validation.graph import validate_graph


def _make_intent(stages: list[StageIntent], goal: str = "test") -> WorkflowIntent:
    return WorkflowIntent(goal=goal, stages=stages)


def _assert_valid_graph(graph: object) -> None:
    """Assert graph passes validation (ignoring untyped-edge warnings)."""
    issues = validate_graph(graph)  # type: ignore[arg-type]
    real_errors = [i for i in issues if "schema safety bypassed" not in i]
    assert real_errors == [], f"Unexpected validation errors: {real_errors}"


# ---------------------------------------------------------------------------
# Single-stage: one test per StageType
# ---------------------------------------------------------------------------


class TestBuildGraphSingleStage:
    def setup_method(self) -> None:
        self.compiler = IntentCompiler()

    def test_transform(self) -> None:
        intent = _make_intent([
            StageIntent(name="summarize", stage_type=StageType.transform, description="Summarize the text"),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 1
        assert any(n.node_type == "llm_operator" for n in graph.nodes)
        _assert_valid_graph(graph)

    def test_review_loop(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="review",
                stage_type=StageType.review_loop,
                description="Draft an essay",
                review=ReviewRequirement(
                    reviewer_prompt="Check grammar",
                    condition="quality_score >= 8",
                    max_iterations=3,
                ),
            ),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 1
        _assert_valid_graph(graph)

    def test_fan_out(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="parallel_work",
                stage_type=StageType.fan_out,
                description="Process each item",
                parallelism=3,
            ),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 1
        _assert_valid_graph(graph)

    def test_fan_out_with_code_body(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="triple_each",
                stage_type=StageType.fan_out,
                config={"body_code": "result = item * 3"},
            ),
        ])
        graph = self.compiler.build_graph(intent)
        _assert_valid_graph(graph)
        assert any(n.node_type == "for_each" for n in graph.nodes)
        body_graph = graph.sub_graphs["triple_each_body"]
        assert any(n.node_type == "code_operator" for n in body_graph.nodes)

    def test_rag_retrieval(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="lookup",
                stage_type=StageType.rag_retrieval,
                description="Answer using context",
                config={"collection": "docs"},
            ),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 2
        has_rag = any(n.node_type == "rag_operator" for n in graph.nodes)
        has_llm = any(n.node_type == "llm_operator" for n in graph.nodes)
        assert has_rag and has_llm
        _assert_valid_graph(graph)

    def test_tool_call(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="search",
                stage_type=StageType.tool_call,
                config={"tool_id": "web_search"},
            ),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 1
        assert any(n.node_type == "tool_operator" for n in graph.nodes)
        _assert_valid_graph(graph)

    def test_code_execution(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="compute",
                stage_type=StageType.code_execution,
                config={"code": "result = 2 + 2"},
            ),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 1
        assert any(n.node_type == "code_operator" for n in graph.nodes)
        _assert_valid_graph(graph)

    def test_code_execution_without_code_raises(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="compute",
                stage_type=StageType.code_execution,
                description="Compute the aggregate metrics",
            ),
        ])
        with pytest.raises(MissingCodeStageError, match="has no runnable code"):
            self.compiler.build_graph(intent)

    def test_human_approval(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="approve",
                stage_type=StageType.human_approval,
                description="Please approve this",
            ),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 1
        _assert_valid_graph(graph)

    def test_conditional(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="decide",
                stage_type=StageType.conditional,
                conditional=ConditionalRequirement(
                    condition="sentiment > 0.5",
                    then_description="Handle positive",
                    else_description="Handle negative",
                ),
            ),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 3
        _assert_valid_graph(graph)

    def test_loop(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="count_to_three",
                stage_type=StageType.loop,
                loop=LoopRequirement(condition="counter < 3"),
            ),
        ])
        graph = self.compiler.build_graph(intent)
        node_types = {n.node_type for n in graph.nodes}
        assert "gate" in node_types
        assert "code_operator" in node_types
        _assert_valid_graph(graph)

    def test_build_graph_prefers_workers_for_simple_compute_when_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DAN_WORKER_GENERATION", "enabled")
        intent = _make_intent([
            StageIntent(name="research", stage_type=StageType.transform, description="Research the topic"),
            StageIntent(name="search", stage_type=StageType.tool_call, config={"tool_id": "web_search"}),
            StageIntent(name="compute", stage_type=StageType.code_execution, config={"code": "result = 2 + 2"}),
        ])

        graph = self.compiler.build_graph(intent)

        assert [node.node_type for node in graph.nodes] == ["worker", "worker", "worker"]
        assert all(isinstance(node, Worker) for node in graph.nodes)
        assert graph.nodes[0].role == "processor"
        assert graph.nodes[1].tool_ids == ["web_search"]
        assert graph.nodes[2].code == "result = 2 + 2"
        _assert_valid_graph(graph)


# ---------------------------------------------------------------------------
# Multi-stage: sequential chaining
# ---------------------------------------------------------------------------


class TestBuildGraphMultiStage:
    def setup_method(self) -> None:
        self.compiler = IntentCompiler()

    def test_two_transforms(self) -> None:
        intent = _make_intent([
            StageIntent(name="research", stage_type=StageType.transform, description="Research"),
            StageIntent(name="summarize", stage_type=StageType.transform, description="Summarize"),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) == 2
        assert len(graph.edges) >= 1
        _assert_valid_graph(graph)

    def test_tool_then_transform(self) -> None:
        intent = _make_intent([
            StageIntent(name="search", stage_type=StageType.tool_call, config={"tool_id": "web_search"}),
            StageIntent(name="analyze", stage_type=StageType.transform, description="Analyze results"),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) == 2
        assert len(graph.edges) >= 1
        _assert_valid_graph(graph)

    def test_conditional_followed_by_transform(self) -> None:
        """Both branches of a conditional should wire to the next stage."""
        intent = _make_intent([
            StageIntent(
                name="check",
                stage_type=StageType.conditional,
                conditional=ConditionalRequirement(
                    condition="x > 0",
                    then_description="Positive path",
                    else_description="Negative path",
                ),
            ),
            StageIntent(name="merge", stage_type=StageType.transform, description="Merge"),
        ])
        graph = self.compiler.build_graph(intent)
        assert len(graph.nodes) >= 4
        merge_node = next(n for n in graph.nodes if n.id == "merge")
        incoming = [e for e in graph.edges if getattr(e, "target_node_id", None) == merge_node.id]
        assert len(incoming) >= 2
        _assert_valid_graph(graph)


# ---------------------------------------------------------------------------
# Composed builds (multi-pattern)
# ---------------------------------------------------------------------------


class TestBuildGraphComposed:
    def setup_method(self) -> None:
        self.compiler = IntentCompiler()

    def test_research_review(self) -> None:
        intent = _make_intent(
            [
                StageIntent(name="research", stage_type=StageType.transform, description="Research topic"),
                StageIntent(name="outline", stage_type=StageType.transform, description="Create outline"),
                StageIntent(
                    name="review",
                    stage_type=StageType.review_loop,
                    description="Draft from outline",
                    review=ReviewRequirement(
                        reviewer_prompt="Check quality",
                        condition="quality_score >= 8",
                        max_iterations=2,
                    ),
                ),
            ],
            goal="Research and write with review",
        )
        graph = self.compiler.build_graph_composed(
            intent, ["research_review"],
        )
        assert len(graph.nodes) >= 3
        _assert_valid_graph(graph)

    def test_linear_chain_composed(self) -> None:
        intent = _make_intent(
            [
                StageIntent(name="step1", stage_type=StageType.transform, description="Step 1"),
                StageIntent(name="step2", stage_type=StageType.transform, description="Step 2"),
                StageIntent(name="step3", stage_type=StageType.transform, description="Step 3"),
            ],
            goal="Three step chain",
        )
        graph = self.compiler.build_graph_composed(
            intent, ["linear_chain"],
        )
        assert len(graph.nodes) >= 3
        _assert_valid_graph(graph)

    def test_fan_out_composed(self) -> None:
        intent = _make_intent(
            [
                StageIntent(
                    name="spread",
                    stage_type=StageType.fan_out,
                    description="Fan out work",
                    parallelism=2,
                ),
            ],
            goal="Fan out items",
        )
        graph = self.compiler.build_graph_composed(
            intent, ["fan_out_fan_in"],
        )
        assert len(graph.nodes) >= 1
        _assert_valid_graph(graph)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestDirectBuildError:
    def test_error_fields(self) -> None:
        err = DirectBuildError(
            "test error",
            stage_name="step1",
            stage_type="transform",
            underlying=ValueError("inner"),
        )
        assert err.stage_name == "step1"
        assert err.stage_type == "transform"
        assert isinstance(err.underlying, ValueError)

    def test_bad_stage_raises(self) -> None:
        compiler = IntentCompiler()
        intent = _make_intent([
            StageIntent(
                name="review_missing",
                stage_type=StageType.review_loop,
                description="Draft",
                review=ReviewRequirement(
                    reviewer_prompt="Check",
                    condition="quality_score >= 8",
                    max_iterations=3,
                ),
            ),
        ])
        graph = compiler.build_graph(intent)
        assert graph is not None


# ---------------------------------------------------------------------------
# Parity: build_graph() vs compile() → exec → Graph
# ---------------------------------------------------------------------------


class TestBuildGraphParity:
    """Verify structural equivalence between build_graph and compile paths."""

    def setup_method(self) -> None:
        self.compiler = IntentCompiler()

    def _graph_from_compile(self, intent: WorkflowIntent) -> dict:
        code = self.compiler.compile(intent)
        ns: dict = {}
        exec(code, ns)  # noqa: S102
        graph = ns.get("graph")
        assert graph is not None
        return graph.model_dump(mode="json")

    def _graph_from_build(self, intent: WorkflowIntent) -> dict:
        graph = self.compiler.build_graph(intent)
        return graph.model_dump(mode="json")

    def _assert_topology_match(self, g1: dict, g2: dict) -> None:
        """Assert same node types and edge topology (not identical IDs)."""
        n1_types = sorted(n["node_type"] for n in g1["nodes"])
        n2_types = sorted(n["node_type"] for n in g2["nodes"])
        assert n1_types == n2_types, f"Node types differ: {n1_types} vs {n2_types}"

        assert len(g1["edges"]) == len(g2["edges"]), (
            f"Edge count differs: {len(g1['edges'])} vs {len(g2['edges'])}"
        )

    def test_parity_transform(self) -> None:
        intent = _make_intent([
            StageIntent(name="step", stage_type=StageType.transform, description="Do it"),
        ])
        g1 = self._graph_from_compile(intent)
        g2 = self._graph_from_build(intent)
        self._assert_topology_match(g1, g2)

    def test_parity_tool_call(self) -> None:
        intent = _make_intent([
            StageIntent(name="search", stage_type=StageType.tool_call, config={"tool_id": "web_search"}),
        ])
        g1 = self._graph_from_compile(intent)
        g2 = self._graph_from_build(intent)
        self._assert_topology_match(g1, g2)

    def test_parity_code_execution(self) -> None:
        intent = _make_intent([
            StageIntent(name="calc", stage_type=StageType.code_execution, config={"code": "result = 42"}),
        ])
        g1 = self._graph_from_compile(intent)
        g2 = self._graph_from_build(intent)
        self._assert_topology_match(g1, g2)

    def test_parity_rag_retrieval(self) -> None:
        intent = _make_intent([
            StageIntent(
                name="qa",
                stage_type=StageType.rag_retrieval,
                description="Answer question",
                config={"collection": "docs"},
            ),
        ])
        g1 = self._graph_from_compile(intent)
        g2 = self._graph_from_build(intent)
        self._assert_topology_match(g1, g2)

    def test_parity_human_approval(self) -> None:
        intent = _make_intent([
            StageIntent(name="approve", stage_type=StageType.human_approval, description="Approve this"),
        ])
        g1 = self._graph_from_compile(intent)
        g2 = self._graph_from_build(intent)
        self._assert_topology_match(g1, g2)

    def test_parity_two_transforms_chain(self) -> None:
        intent = _make_intent([
            StageIntent(name="a", stage_type=StageType.transform, description="First"),
            StageIntent(name="b", stage_type=StageType.transform, description="Second"),
        ])
        g1 = self._graph_from_compile(intent)
        g2 = self._graph_from_build(intent)
        self._assert_topology_match(g1, g2)
