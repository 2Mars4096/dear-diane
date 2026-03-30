import copy
import pytest
from dan.meta.structural_mutations import (
    MutationMacroResult,
    DispatchResult,
    dispatch_structural_mutation,
    fan_out_node,
    insert_tool,
    insert_validator,
    parallelize,
    resolve_node,
    summarize_graph,
    unwrap_loop,
    wrap_in_review_loop,
)


def _make_graph(nodes, edges=None):
    return {
        "metadata": {"name": "test_workflow"},
        "nodes": nodes,
        "edges": {"data": edges or [], "control": [], "context": []},
        "sub_graphs": {},
    }


def _make_chain():
    """3-node linear chain: a -> b -> c."""
    return _make_graph(
        [
            {"id": "a", "node_type": "llm_operator", "config": {"name": "step_a", "prompt_template": "Research topic"}},
            {"id": "b", "node_type": "llm_operator", "config": {"name": "step_b", "prompt_template": "Analyze results"}},
            {"id": "c", "node_type": "llm_operator", "config": {"name": "step_c", "prompt_template": "Write summary"}},
        ],
        [
            {"source_node_id": "a", "source_port": "text", "target_node_id": "b", "target_port": "text"},
            {"source_node_id": "b", "source_port": "text", "target_node_id": "c", "target_port": "text"},
        ],
    )


class TestWrapInReviewLoop:
    def test_basic(self):
        g = _make_chain()
        result = wrap_in_review_loop(g, "b")
        assert result.success
        assert len(result.nodes_added) == 2
        assert len(g["nodes"]) == 5

    def test_missing_node(self):
        g = _make_chain()
        result = wrap_in_review_loop(g, "nonexistent")
        assert not result.success
        assert "not found" in result.error


class TestFanOutNode:
    def test_basic(self):
        g = _make_chain()
        result = fan_out_node(g, "b")
        assert result.success
        assert len(result.nodes_added) == 2

    def test_missing_node(self):
        g = _make_chain()
        result = fan_out_node(g, "nonexistent")
        assert not result.success


class TestInsertValidator:
    def test_basic(self):
        g = _make_chain()
        result = insert_validator(g, "a", "b")
        assert result.success
        assert len(result.nodes_added) == 1
        assert len(g["nodes"]) == 4
        validator = next(node for node in g["nodes"] if node["node_type"] == "validator")
        assert validator["input_ports"] == [{"name": "data", "json_schema": {}}]
        assert validator["output_ports"] == [
            {"name": "valid", "json_schema": {}},
            {"name": "invalid", "json_schema": {}},
        ]
        assert validator["config"]["validation_rules"] == [
            {"rule_type": "required_keys", "config": {"keys": []}}
        ]

    def test_no_edge(self):
        g = _make_chain()
        result = insert_validator(g, "a", "c")
        assert not result.success
        assert "No edge" in result.error


class TestInsertTool:
    def test_after(self):
        g = _make_chain()
        result = insert_tool(g, "a", "web_search", position="after")
        assert result.success
        assert len(g["nodes"]) == 4

    def test_before(self):
        g = _make_chain()
        result = insert_tool(g, "b", "file_read", position="before")
        assert result.success
        assert len(g["nodes"]) == 4


class TestParallelize:
    def test_basic(self):
        g = _make_graph(
            [
                {"id": "a", "node_type": "llm_operator", "config": {}},
                {"id": "b", "node_type": "llm_operator", "config": {}},
                {"id": "c", "node_type": "llm_operator", "config": {}},
            ],
            [
                {"source_node_id": "a", "source_port": "text", "target_node_id": "c", "target_port": "text"},
                {"source_node_id": "b", "source_port": "text", "target_node_id": "c", "target_port": "text"},
            ],
        )
        result = parallelize(g, ["a", "b"])
        assert result.success

    def test_single_node_fails(self):
        g = _make_chain()
        result = parallelize(g, ["a"])
        assert not result.success


class TestUnwrapLoop:
    def test_basic(self):
        g = _make_graph(
            [
                {"id": "a", "node_type": "llm_operator", "config": {}},
                {"id": "loop", "node_type": "while_loop", "config": {}},
                {"id": "c", "node_type": "llm_operator", "config": {}},
            ],
            [
                {"source_node_id": "a", "source_port": "text", "target_node_id": "loop", "target_port": "data"},
                {"source_node_id": "loop", "source_port": "result", "target_node_id": "c", "target_port": "text"},
            ],
        )
        result = unwrap_loop(g, "loop")
        assert result.success
        assert "loop" in result.nodes_removed
        assert len(g["nodes"]) == 2

    def test_non_loop_fails(self):
        g = _make_chain()
        result = unwrap_loop(g, "a")
        assert not result.success


class TestResolveNode:
    def test_exact_id_match(self):
        g = _make_chain()
        result = resolve_node(g, "a")
        assert result[0] == "a"

    def test_name_match(self):
        g = _make_chain()
        result = resolve_node(g, "step_b")
        assert "b" in result

    def test_prompt_keyword_match(self):
        g = _make_chain()
        result = resolve_node(g, "Research")
        assert "a" in result

    def test_last_step(self):
        g = _make_chain()
        result = resolve_node(g, "the last step")
        assert result[0] == "c"

    def test_step_number(self):
        g = _make_chain()
        result = resolve_node(g, "step 2")
        assert result[0] == "b"


class TestDispatcher:
    def test_review_loop_dispatch(self):
        g = _make_chain()
        result = dispatch_structural_mutation(g, "add a review loop after step_b", target_node_hint="b")
        assert result.matched
        assert result.macro_name == "wrap_in_review_loop"
        assert result.result.success

    def test_no_match(self):
        g = _make_chain()
        result = dispatch_structural_mutation(g, "make it faster")
        assert not result.matched

    def test_fan_out_dispatch(self):
        g = _make_chain()
        result = dispatch_structural_mutation(g, "fan out the analysis step", target_node_hint="b")
        assert result.matched
        assert result.macro_name == "fan_out_node"


    def test_review_loop_extracts_max_rounds(self):
        """'add a review loop with 5 rounds' should pass max_rounds=5."""
        g = _make_chain()
        result = dispatch_structural_mutation(g, "add a review loop with 5 rounds", target_node_hint="b")
        assert result.matched
        assert result.result.success
        gate = next(n for n in g["nodes"] if n.get("node_type") == "gate")
        assert gate["config"]["max_iterations"] == 5

    def test_fan_out_extracts_parallelism(self):
        """'fan out with parallelism 10' should pass parallelism=10."""
        g = _make_chain()
        result = dispatch_structural_mutation(g, "fan out with parallelism 10", target_node_hint="b")
        assert result.matched
        assert result.result.success
        fe = next(n for n in g["nodes"] if n.get("node_type") == "for_each")
        assert fe["config"]["parallelism"] == 10

    def test_default_rounds_without_number(self):
        """Without a number, review loop should use default max_rounds=3."""
        g = _make_chain()
        result = dispatch_structural_mutation(g, "add a review loop", target_node_hint="b")
        assert result.result.success
        gate = next(n for n in g["nodes"] if n.get("node_type") == "gate")
        assert gate["config"]["max_iterations"] == 3

    def test_nl_target_with_trailing_words(self):
        """Regex should handle trailing words like 'please' (M-6 fix)."""
        g = _make_chain()
        result = dispatch_structural_mutation(g, "add a review loop to the step_b step please")
        assert result.matched
        assert result.result.success


class TestSummarizeGraph:
    def test_basic(self):
        g = _make_chain()
        summary = summarize_graph(g)
        assert "test_workflow" in summary
        assert "step_a" in summary or "a" in summary
        assert "Nodes (3)" in summary
        assert "Edges (2)" in summary
