"""Tests for Plan 32-7 Task 5: Post-hoc Graph materialization."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from dan.builder import workflow
from dan.builder.decompiler import decompile
from dan.meta.graph_materializer import (
    _detect_linear_chains,
    _detect_review_loops,
    graph_to_builder_code,
    materialize_graph,
)
from dan.models.graph import Graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_two_node_chain() -> Graph:
    """Build a simple 2-node LLM chain graph."""
    wf = workflow("two_node_chain")
    a = wf.llm("step_one", prompt="First step")
    b = wf.llm("step_two", prompt="Second step")
    a >> b
    return wf.build()


def _build_three_node_chain() -> Graph:
    """Build a 3-node LLM chain graph."""
    wf = workflow("three_node_chain")
    a = wf.llm("gather", prompt="Gather data")
    b = wf.llm("analyze", prompt="Analyze data")
    c = wf.llm("summarize", prompt="Summarize findings")
    a >> b >> c
    return wf.build()


def _build_review_loop_graph() -> Graph:
    """Build a graph containing a review loop."""
    wf = workflow("review_test")
    wf.review_loop(
        writer_prompt="Write a draft",
        reviewer_prompt="Review the draft",
        name="essay",
        max_rounds=3,
    )
    return wf.build()


def _build_mixed_graph() -> Graph:
    """Build a graph with LLM nodes and a tool node (no pure chain)."""
    wf = workflow("mixed")
    a = wf.llm("step_a", prompt="Step A")
    b = wf.tool("step_b", tool_id="web_search")
    c = wf.llm("step_c", prompt="Step C")
    a >> b >> c
    return wf.build()


def _build_branching_graph() -> Graph:
    """Build a graph with branching (non-linear topology)."""
    wf = workflow("branching")
    a = wf.llm("start", prompt="Start")
    b = wf.llm("branch_a", prompt="Branch A")
    c = wf.llm("branch_b", prompt="Branch B")
    wf.edge(a["text"], b["text"])
    wf.edge(a["text"], c["text"])
    return wf.build()


def _exec_code_to_graph(code: str) -> Graph:
    """Execute builder DSL code and return the resulting Graph."""
    ns: dict[str, Any] = {}
    exec(code, ns)
    return ns["graph"]


def _graphs_structurally_equivalent(g1: Graph, g2: Graph) -> bool:
    """Check that two graphs have the same node types, IDs, and edge topology."""
    ids1 = sorted(n.id for n in g1.nodes)
    ids2 = sorted(n.id for n in g2.nodes)
    if ids1 != ids2:
        return False

    types1 = sorted((n.id, n.node_type) for n in g1.nodes)
    types2 = sorted((n.id, n.node_type) for n in g2.nodes)
    if types1 != types2:
        return False

    edges1 = sorted(
        (e.source_node_id, e.target_node_id, e.edge_type)
        for e in g1.edges
    )
    edges2 = sorted(
        (e.source_node_id, e.target_node_id, e.edge_type)
        for e in g2.edges
    )
    return edges1 == edges2


# ---------------------------------------------------------------------------
# Tests: graph_to_builder_code
# ---------------------------------------------------------------------------

class TestGraphToBuilderCode:
    def test_produces_executable_code_two_node_chain(self):
        graph = _build_two_node_chain()
        code = graph_to_builder_code(graph)

        assert "wf" in code or "workflow" in code
        assert "graph" in code

        graph2 = _exec_code_to_graph(code)
        assert len(graph2.nodes) == len(graph.nodes)
        assert _graphs_structurally_equivalent(graph, graph2)

    def test_produces_executable_code_three_node_chain(self):
        graph = _build_three_node_chain()
        code = graph_to_builder_code(graph)

        graph2 = _exec_code_to_graph(code)
        assert len(graph2.nodes) == 3
        assert _graphs_structurally_equivalent(graph, graph2)

    def test_use_convenience_false_matches_decompile(self):
        graph = _build_two_node_chain()
        base = decompile(graph)
        no_convenience = graph_to_builder_code(graph, use_convenience=False)
        assert no_convenience == base

    def test_review_loop_annotated(self):
        graph = _build_review_loop_graph()
        code = graph_to_builder_code(graph)
        assert "Review loop pattern detected" in code

    def test_chain_annotation_present(self):
        """A 3-node LLM chain should have a chain pattern comment."""
        graph = _build_three_node_chain()
        code = graph_to_builder_code(graph)
        assert "# Chain pattern detected" in code
        assert "wf.chain(" in code  # in the "Equivalent to:" comment
        graph2 = _exec_code_to_graph(code)
        assert _graphs_structurally_equivalent(graph, graph2)

    def test_mixed_graph_no_chain_annotation(self):
        """A mixed LLM+tool graph should not have a chain annotation since the
        chain is broken by a tool node."""
        graph = _build_mixed_graph()
        code = graph_to_builder_code(graph)
        assert "Chain pattern" not in code
        graph2 = _exec_code_to_graph(code)
        assert _graphs_structurally_equivalent(graph, graph2)

    def test_branching_graph_no_chain(self):
        """A branching graph should not detect a linear chain."""
        graph = _build_branching_graph()
        code = graph_to_builder_code(graph)
        assert "Chain pattern" not in code
        graph2 = _exec_code_to_graph(code)
        assert _graphs_structurally_equivalent(graph, graph2)


# ---------------------------------------------------------------------------
# Tests: materialize_graph
# ---------------------------------------------------------------------------

class TestMaterializeGraph:
    def test_returns_expected_structure(self):
        graph = _build_two_node_chain()
        result = materialize_graph(graph)
        assert result["materialized"] is True
        assert result["node_count"] == 2
        assert "builder_code" in result
        assert isinstance(result["builder_code"], str)
        assert "persisted" not in result

    def test_persists_when_store_and_id_given(self):
        graph = _build_two_node_chain()
        store = MagicMock()
        result = materialize_graph(
            graph, graph_store=store, workflow_id="wf-123",
        )
        assert result["persisted"] is True
        store.save_graph.assert_called_once()
        call_args = store.save_graph.call_args
        assert call_args[0][0] == "wf-123"

    def test_persist_failure_handled(self):
        graph = _build_two_node_chain()
        store = MagicMock()
        store.save_graph.side_effect = RuntimeError("boom")
        result = materialize_graph(
            graph, graph_store=store, workflow_id="wf-456",
        )
        assert result["persisted"] is False
        assert result["materialized"] is True

    def test_no_persist_without_workflow_id(self):
        graph = _build_two_node_chain()
        store = MagicMock()
        result = materialize_graph(graph, graph_store=store)
        assert "persisted" not in result
        store.save_graph.assert_not_called()

    def test_no_persist_without_store(self):
        graph = _build_two_node_chain()
        result = materialize_graph(graph, workflow_id="wf-789")
        assert "persisted" not in result


# ---------------------------------------------------------------------------
# Tests: round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_two_node_chain_round_trip(self):
        g1 = _build_two_node_chain()
        code = graph_to_builder_code(g1)
        g2 = _exec_code_to_graph(code)
        assert _graphs_structurally_equivalent(g1, g2)

    def test_three_node_chain_round_trip(self):
        g1 = _build_three_node_chain()
        code = graph_to_builder_code(g1)
        g2 = _exec_code_to_graph(code)
        assert _graphs_structurally_equivalent(g1, g2)

    def test_review_loop_round_trip(self):
        g1 = _build_review_loop_graph()
        code = graph_to_builder_code(g1)
        g2 = _exec_code_to_graph(code)
        assert _graphs_structurally_equivalent(g1, g2)
        sub_keys_1 = sorted(g1.sub_graphs.keys())
        sub_keys_2 = sorted(g2.sub_graphs.keys())
        assert sub_keys_1 == sub_keys_2

    def test_mixed_graph_round_trip(self):
        g1 = _build_mixed_graph()
        code = graph_to_builder_code(g1)
        g2 = _exec_code_to_graph(code)
        assert _graphs_structurally_equivalent(g1, g2)


# ---------------------------------------------------------------------------
# Tests: pattern detection internals
# ---------------------------------------------------------------------------

class TestDetectLinearChains:
    def test_detects_two_node_chain(self):
        graph = _build_two_node_chain()
        chains = _detect_linear_chains(graph)
        assert chains is not None
        assert len(chains) == 1
        assert len(chains[0]) == 2

    def test_detects_three_node_chain(self):
        graph = _build_three_node_chain()
        chains = _detect_linear_chains(graph)
        assert chains is not None
        assert len(chains) == 1
        assert len(chains[0]) == 3

    def test_no_chain_for_single_node(self):
        wf = workflow("single")
        wf.llm("only", prompt="Solo")
        graph = wf.build()
        chains = _detect_linear_chains(graph)
        assert chains is None

    def test_no_chain_for_mixed_types(self):
        graph = _build_mixed_graph()
        chains = _detect_linear_chains(graph)
        assert chains is None

    def test_no_chain_for_branching(self):
        graph = _build_branching_graph()
        chains = _detect_linear_chains(graph)
        assert chains is None


class TestDetectReviewLoops:
    def test_detects_review_loop(self):
        graph = _build_review_loop_graph()
        hits = _detect_review_loops(graph)
        assert len(hits) == 1
        assert "essay_loop" in hits[0]

    def test_no_review_loop_in_chain(self):
        graph = _build_two_node_chain()
        hits = _detect_review_loops(graph)
        assert len(hits) == 0
