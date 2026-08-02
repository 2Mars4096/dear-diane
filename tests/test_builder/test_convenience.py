"""Tests for builder convenience methods (Plan 32-1)."""

import pytest

from dan.builder import workflow, WorkflowBuilder
from dan.builder.compiler import BuildError
from dan.builder.refs import NodeRef


# ── chain() ────────────────────────────────────────────────────────────


class TestChain:
    def test_single_step(self):
        wf = workflow("t")
        ref = wf.chain(("step1", "Do something"))
        assert isinstance(ref, NodeRef)
        assert ref.node_id == "step1"
        graph = wf.build()
        assert len(graph.nodes) == 1
        assert graph.nodes[0].id == "step1"

    def test_three_steps(self):
        wf = workflow("t")
        ref = wf.chain(
            ("a", "First"),
            ("b", "Second"),
            ("c", "Third"),
        )
        assert ref.node_id == "c"
        graph = wf.build()
        assert len(graph.nodes) == 3
        node_ids = {n.id for n in graph.nodes}
        assert node_ids == {"a", "b", "c"}
        data_edges = [e for e in graph.edges if e.edge_type == "data"]
        assert len(data_edges) >= 2

    def test_with_model_override(self):
        wf = workflow("t")
        ref = wf.chain(("s1", "Prompt A", "gpt-4o"))
        graph = wf.build()
        node = graph.node_by_id("s1")
        assert node is not None
        assert node.model == "gpt-4o"

    def test_name_prefix(self):
        wf = workflow("t")
        ref = wf.chain(("x", "Do it"), name_prefix="stage")
        assert ref.node_id == "stage_x"
        graph = wf.build()
        assert graph.node_by_id("stage_x") is not None

    def test_empty_raises(self):
        wf = workflow("t")
        with pytest.raises(BuildError):
            wf.chain()

    def test_chainable_with_rshift(self):
        wf = workflow("t")
        last = wf.chain(("a", "Step A"), ("b", "Step B"))
        final = wf.llm("final", prompt="Wrap up")
        last >> final
        graph = wf.build()
        edges_to_final = graph.edges_to("final")
        assert len(edges_to_final) >= 1


# ── review_loop() ─────────────────────────────────────────────────────


class TestReviewLoop:
    def test_basic(self):
        wf = workflow("t")
        ref = wf.review_loop(
            writer_prompt="Write a draft",
            reviewer_prompt="Review the draft",
        )
        assert isinstance(ref, NodeRef)
        assert ref.node_id == "review_loop"
        graph = wf.build()
        node = graph.node_by_id("review_loop")
        assert node is not None
        assert node.node_type == "while_loop"
        body_key = node.body_graph
        assert body_key in graph.sub_graphs
        body = graph.sub_graphs[body_key]
        body_ids = {n.id for n in body.nodes}
        assert "review_writer" in body_ids
        assert "review_reviewer" in body_ids

    def test_custom_max_rounds(self):
        wf = workflow("t")
        wf.review_loop(
            writer_prompt="Write",
            reviewer_prompt="Review",
            max_rounds=5,
            name="edit",
        )
        graph = wf.build()
        node = graph.node_by_id("edit_loop")
        assert node is not None
        assert node.max_iterations == 5

    def test_custom_models(self):
        wf = workflow("t")
        wf.review_loop(
            writer_prompt="Write",
            reviewer_prompt="Review",
            writer_model="claude-4",
            reviewer_model="gpt-4o",
            name="m",
        )
        graph = wf.build()
        body = graph.sub_graphs[graph.node_by_id("m_loop").body_graph]
        writer = next(n for n in body.nodes if n.id == "m_writer")
        reviewer = next(n for n in body.nodes if n.id == "m_reviewer")
        assert writer.model == "claude-4"
        assert reviewer.model == "gpt-4o"

    def test_ports_are_optional(self):
        wf = workflow("t")
        wf.review_loop("Write", "Review")
        graph = wf.build()
        node = graph.node_by_id("review_loop")
        for port in node.input_ports:
            assert not port.required, f"Port {port.name!r} should be optional"

    def test_has_state_defaults(self):
        wf = workflow("t")
        wf.review_loop("Write", "Review")
        graph = wf.build()
        node = graph.node_by_id("review_loop")
        assert node.state_schema is not None
        assert "draft" in node.state_schema
        assert "quality_score" in node.state_schema
        assert node.state_defaults is not None
        assert node.state_defaults["draft"] == ""
        assert node.state_defaults["quality_score"] == 0

    def test_chain_into_review_loop(self):
        """chain >> review_loop builds successfully and wires to 'draft'."""
        wf = workflow("t")
        result = wf.chain(("a", "Step A"), ("b", "Step B"))
        loop = wf.review_loop("Improve", "Review")
        result >> loop
        graph = wf.build()
        edges = [(e.source_node_id, e.source_port, e.target_node_id, e.target_port)
                 for e in graph.edges if e.edge_type == "data"]
        assert ("b", "text", "review_loop", "draft") in edges

    def test_review_loop_downstream_chain(self):
        """review_loop >> downstream wires from 'draft' port."""
        wf = workflow("t")
        loop = wf.review_loop("Write", "Review")
        final = wf.llm("final", prompt="Format output")
        loop >> final
        graph = wf.build()
        edges = [(e.source_node_id, e.source_port, e.target_node_id, e.target_port)
                 for e in graph.edges if e.edge_type == "data"]
        assert ("review_loop", "draft", "final", "input") in edges

    def test_full_pipeline_chain_review_downstream(self):
        """draft >> review_loop >> format builds end-to-end."""
        wf = workflow("t")
        draft = wf.llm("draft", prompt="Write a report")
        loop = wf.review_loop("Improve", "Review")
        fmt = wf.llm("format", prompt="Format output")
        draft >> loop >> fmt
        graph = wf.build()
        edges = {(e.source_node_id, e.source_port, e.target_node_id, e.target_port)
                 for e in graph.edges if e.edge_type == "data"}
        assert ("draft", "text", "review_loop", "draft") in edges
        assert ("review_loop", "draft", "format", "input") in edges

    def test_noderef_default_input_output(self):
        """review_loop NodeRef carries custom default I/O ports."""
        wf = workflow("t")
        ref = wf.review_loop("Write", "Review")
        assert ref.default_input == "draft"
        assert ref.default_output == "draft"

    def test_custom_review_fields_state_defaults(self):
        wf = workflow("t")
        wf.review_loop(
            "Write", "Review",
            condition="score > 0.9",
            review_fields={"score": {"type": "number"}, "notes": {"type": "string"}},
            feedback_key="notes",
        )
        graph = wf.build()
        node = graph.node_by_id("review_loop")
        assert node.state_defaults["score"] == 0.0
        assert node.state_defaults["notes"] == ""


# ── map_reduce() ──────────────────────────────────────────────────────


class TestMapReduce:
    def test_basic(self):
        wf = workflow("t")
        ref = wf.map_reduce(
            items_expr="items",
            map_prompt="Process item",
            reduce_prompt="Aggregate results",
        )
        assert isinstance(ref, NodeRef)
        assert ref.node_id == "map_reduce_reduce"
        graph = wf.build()
        node_ids = {n.id for n in graph.nodes}
        assert "map_reduce_fan_out" in node_ids
        assert "map_reduce_reduce" in node_ids
        fe_node = graph.node_by_id("map_reduce_fan_out")
        assert fe_node is not None
        assert fe_node.node_type == "for_each"

    def test_custom_name(self):
        wf = workflow("t")
        ref = wf.map_reduce(
            items_expr="data",
            map_prompt="Map it",
            reduce_prompt="Reduce it",
            name="my_mr",
        )
        assert ref.node_id == "my_mr_reduce"
        graph = wf.build()
        assert graph.node_by_id("my_mr_fan_out") is not None
        assert graph.node_by_id("my_mr_reduce") is not None


# ── tool_chain() ──────────────────────────────────────────────────────


class TestToolChain:
    def test_basic_mixed(self):
        wf = workflow("t", canonical_workers=False)
        ref = wf.tool_chain(
            ("gen", None, "Generate text"),
            ("search", "web_search", {"query": "test"}),
            ("summarize", None, "Summarize the results"),
        )
        assert ref.node_id == "summarize"
        graph = wf.build()
        assert len(graph.nodes) == 3
        assert graph.node_by_id("gen").node_type == "llm_operator"
        assert graph.node_by_id("search").node_type == "tool_operator"
        assert graph.node_by_id("summarize").node_type == "llm_operator"
        data_edges = [e for e in graph.edges if e.edge_type == "data"]
        assert len(data_edges) >= 2

    def test_all_tools(self):
        wf = workflow("t", canonical_workers=False)
        ref = wf.tool_chain(
            ("t1", "tool_a", {}),
            ("t2", "tool_b", {"x": 1}),
        )
        assert ref.node_id == "t2"
        graph = wf.build()
        assert all(n.node_type == "tool_operator" for n in graph.nodes)

    def test_empty_raises(self):
        wf = workflow("t")
        with pytest.raises(BuildError):
            wf.tool_chain()


# ── Pipeline operator (|) ─────────────────────────────────────────────


class TestPipelineOperator:
    def test_three_node_pipeline(self):
        wf = workflow("t")
        a = wf.llm("a", prompt="A")
        b = wf.llm("b", prompt="B")
        c = wf.llm("c", prompt="C")
        result = a | b | c
        assert result is c
        graph = wf.build()
        data_edges = [e for e in graph.edges if e.edge_type == "data"]
        assert len(data_edges) == 2
        sources = {e.source_node_id for e in data_edges}
        targets = {e.target_node_id for e in data_edges}
        assert "a" in sources
        assert "b" in sources and "b" in targets
        assert "c" in targets

    def test_same_graph_as_rshift(self):
        """``a | b | c`` produces the same edges as ``a >> b >> c``."""
        wf1 = workflow("pipe")
        a1 = wf1.llm("a", prompt="A")
        b1 = wf1.llm("b", prompt="B")
        c1 = wf1.llm("c", prompt="C")
        a1 | b1 | c1
        g1 = wf1.build()

        wf2 = workflow("rshift")
        a2 = wf2.llm("a", prompt="A")
        b2 = wf2.llm("b", prompt="B")
        c2 = wf2.llm("c", prompt="C")
        a2 >> b2 >> c2
        g2 = wf2.build()

        def _edge_tuples(g):
            return sorted(
                (e.source_node_id, e.source_port, e.target_node_id, e.target_port)
                for e in g.edges
                if e.edge_type == "data"
            )

        assert _edge_tuples(g1) == _edge_tuples(g2)

    def test_mixed_tool_llm(self):
        wf = workflow("t")
        a = wf.tool("fetch", tool_id="web_fetch", tool_config={})
        b = wf.llm("summarize", prompt="Summarize")
        result = a | b
        assert result is b
        graph = wf.build()
        data_edges = [e for e in graph.edges if e.edge_type == "data"]
        assert len(data_edges) == 1
        assert data_edges[0].source_node_id == "fetch"
        assert data_edges[0].target_node_id == "summarize"

    def test_returns_not_implemented_for_non_noderef(self):
        ref = NodeRef("x", "llm_operator")
        assert ref.__or__(42) is NotImplemented
