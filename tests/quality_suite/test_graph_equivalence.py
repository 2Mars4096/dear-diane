"""Tests for GraphEquivalenceChecker and round_trip_check."""

from __future__ import annotations

import textwrap

import pytest

from dan.models.edges import ControlEdge, DataEdge
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator, LLMOperator
from dan.models.ports import InputPort, OutputPort

from tests.quality_suite.graph_equivalence import (
    EquivalenceResult,
    GraphEquivalenceChecker,
    round_trip_check,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _llm(id: str, **kw) -> LLMOperator:
    return LLMOperator(
        id=id,
        name=kw.get("name", id),
        model=kw.get("model", "gpt-4o"),
        prompt_template=kw.get("prompt", "prompt"),
        input_ports=kw.get("input_ports", [InputPort(name="input")]),
        output_ports=kw.get("output_ports", [OutputPort(name="text")]),
    )


def _code(id: str, **kw) -> CodeOperator:
    return CodeOperator(
        id=id,
        name=kw.get("name", id),
        code=kw.get("code", "result = {}"),
        input_ports=kw.get("input_ports", [InputPort(name="input")]),
        output_ports=kw.get("output_ports", [OutputPort(name="output")]),
    )


def _data_edge(src: str, tgt: str, sp: str = "text", tp: str = "input") -> DataEdge:
    return DataEdge(
        id=f"{src}_{tgt}",
        source_node_id=src,
        target_node_id=tgt,
        source_port=sp,
        target_port=tp,
    )


def _two_node_graph() -> Graph:
    """A -> B linear chain."""
    return Graph(
        nodes=[_llm("a"), _llm("b")],
        edges=[_data_edge("a", "b")],
        entry_points=["a"],
        exit_points=["b"],
    )


# ---------------------------------------------------------------------------
# Structural comparison tests
# ---------------------------------------------------------------------------


class TestIdenticalGraphs:
    def test_identical_graphs_are_equivalent(self):
        g = _two_node_graph()
        result = GraphEquivalenceChecker().check(g, g)
        assert result.equivalent is True
        assert result.mismatches == []

    def test_empty_graphs_are_equivalent(self):
        result = GraphEquivalenceChecker().check(Graph(), Graph())
        assert result.equivalent is True


class TestNodeMismatches:
    def test_missing_node(self):
        a = Graph(
            nodes=[_llm("x"), _llm("y")],
            edges=[_data_edge("x", "y")],
            entry_points=["x"],
            exit_points=["y"],
        )
        b = Graph(
            nodes=[_llm("x")],
            edges=[],
            entry_points=["x"],
            exit_points=["x"],
        )
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("missing node" in m and "'y'" in m for m in result.mismatches)

    def test_extra_node(self):
        a = Graph(nodes=[_llm("x")], entry_points=["x"])
        b = Graph(nodes=[_llm("x"), _llm("z")], entry_points=["x"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("extra node" in m and "'z'" in m for m in result.mismatches)

    def test_different_node_type(self):
        a = Graph(nodes=[_llm("n")], entry_points=["n"])
        b = Graph(nodes=[_code("n")], entry_points=["n"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("type mismatch" in m for m in result.mismatches)
        assert any("llm_operator" in m and "code_operator" in m for m in result.mismatches)

    def test_input_port_mismatch(self):
        na = _llm("n", input_ports=[InputPort(name="alpha")])
        nb = _llm("n", input_ports=[InputPort(name="beta")])
        a = Graph(nodes=[na], entry_points=["n"])
        b = Graph(nodes=[nb], entry_points=["n"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("input port mismatch" in m for m in result.mismatches)

    def test_output_port_mismatch(self):
        na = _llm("n", output_ports=[OutputPort(name="out_a"), OutputPort(name="out_b")])
        nb = _llm("n", output_ports=[OutputPort(name="out_a")])
        a = Graph(nodes=[na], entry_points=["n"])
        b = Graph(nodes=[nb], entry_points=["n"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("output port mismatch" in m for m in result.mismatches)


class TestEdgeMismatches:
    def test_missing_edge(self):
        nodes = [_llm("a"), _llm("b")]
        a = Graph(nodes=nodes, edges=[_data_edge("a", "b")], entry_points=["a"], exit_points=["b"])
        b = Graph(nodes=nodes, edges=[], entry_points=["a"], exit_points=["b"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("missing edge" in m for m in result.mismatches)

    def test_extra_edge(self):
        nodes = [_llm("a"), _llm("b")]
        a = Graph(nodes=nodes, edges=[], entry_points=["a"])
        b = Graph(nodes=nodes, edges=[_data_edge("a", "b")], entry_points=["a"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("extra edge" in m for m in result.mismatches)

    def test_edge_type_difference(self):
        """DataEdge vs ControlEdge with same endpoints are distinct."""
        nodes = [_llm("a"), _llm("b")]
        de = _data_edge("a", "b", sp="text", tp="input")
        ce = ControlEdge(
            id="ctrl",
            source_node_id="a",
            target_node_id="b",
            source_port="text",
            target_port="input",
        )
        a = Graph(nodes=nodes, edges=[de], entry_points=["a"])
        b = Graph(nodes=nodes, edges=[ce], entry_points=["a"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert len(result.mismatches) >= 1


class TestEntryExitMismatches:
    def test_different_entry_points(self):
        nodes = [_llm("a"), _llm("b")]
        a = Graph(nodes=nodes, entry_points=["a"])
        b = Graph(nodes=nodes, entry_points=["b"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("entry_points mismatch" in m for m in result.mismatches)

    def test_different_exit_points(self):
        nodes = [_llm("a"), _llm("b")]
        a = Graph(nodes=nodes, exit_points=["a"])
        b = Graph(nodes=nodes, exit_points=["b"])
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("exit_points mismatch" in m for m in result.mismatches)


class TestSubGraphMismatches:
    def test_missing_sub_graph(self):
        inner = Graph(nodes=[_code("s1")], entry_points=["s1"])
        a = Graph(sub_graphs={"loop_body": inner})
        b = Graph(sub_graphs={})
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("missing sub_graph" in m and "'loop_body'" in m for m in result.mismatches)

    def test_sub_graph_node_mismatch(self):
        """A structural difference inside a sub-graph is detected."""
        inner_a = Graph(nodes=[_code("s1"), _code("s2")], entry_points=["s1"])
        inner_b = Graph(nodes=[_code("s1")], entry_points=["s1"])
        a = Graph(sub_graphs={"body": inner_a})
        b = Graph(sub_graphs={"body": inner_b})
        result = GraphEquivalenceChecker().check(a, b)
        assert result.equivalent is False
        assert any("sub_graph 'body'" in m and "missing node" in m for m in result.mismatches)


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

SIMPLE_CHAIN_CODE = textwrap.dedent("""\
    from dan.builder import workflow

    wf = workflow("simple_rt")
    a = wf.llm("gen", model="gpt-4o", prompt="Generate: {topic}")
    b = wf.llm("refine", prompt="Refine text")
    a >> b
    graph = wf.build()
""")

REVIEW_REVISE_CODE = textwrap.dedent("""\
    from dan.builder import workflow
    from dan.builder.refs import NodeRef

    wf = workflow("review_revise_rt")
    draft = wf.llm("draft", prompt="Write about: {topic}", input_ports=[{"name": "topic"}])

    with wf.while_loop("loop", condition="score < 8", max_iterations=3) as body:
        review = body.llm("review", prompt="Review: {text}")
        revise = body.llm("revise", prompt="Revise based on feedback: {text}")
        review >> revise

    loop_ref = NodeRef("loop", "while_loop", wf)
    wf.edge(draft["text"], loop_ref["input"])

    graph = wf.build()
""")

FOR_EACH_CODE = textwrap.dedent("""\
    from dan.builder import workflow

    wf = workflow("fan_out_rt")
    gen = wf.llm("gen", prompt="Generate items")

    with wf.for_each("fan", parallelism=4) as body:
        body.code("process", code="result = {'done': True}")

    graph = wf.build()
""")


class TestRoundTrip:
    def test_simple_chain_round_trip(self):
        result = round_trip_check(SIMPLE_CHAIN_CODE)
        assert result.equivalent is True, f"Mismatches: {result.mismatches}"

    def test_review_revise_round_trip(self):
        result = round_trip_check(REVIEW_REVISE_CODE)
        assert result.equivalent is True, f"Mismatches: {result.mismatches}"

    def test_for_each_round_trip(self):
        result = round_trip_check(FOR_EACH_CODE)
        assert result.equivalent is True, f"Mismatches: {result.mismatches}"


class TestEquivalenceResultDataclass:
    def test_defaults(self):
        r = EquivalenceResult(equivalent=True)
        assert r.mismatches == []

    def test_with_mismatches(self):
        r = EquivalenceResult(equivalent=False, mismatches=["a", "b"])
        assert len(r.mismatches) == 2
