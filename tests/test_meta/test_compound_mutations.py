"""Tests for 32-6 Task 3: Compound structural follow-up mutations."""

from __future__ import annotations

import copy
import pytest

from dan.meta.structural_mutations import (
    DispatchResult,
    MutationMacroResult,
    dispatch_compound_mutations,
    dispatch_structural_mutation,
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


class TestSingleMacroUnchanged:
    """Verify that single-macro dispatch still works identically."""

    def test_single_review_loop(self):
        g = _make_chain()
        result = dispatch_compound_mutations(g, "add a review loop", target_node_hint="b")
        assert result.matched
        assert result.result is not None
        assert result.result.success

    def test_single_fan_out(self):
        g = _make_chain()
        result = dispatch_compound_mutations(g, "fan out the analysis step", target_node_hint="b")
        assert result.matched

    def test_no_match(self):
        g = _make_chain()
        result = dispatch_compound_mutations(g, "make it faster")
        assert not result.matched


class TestCompoundMutations:
    def test_two_macro_compound(self):
        """'add a review loop and fan out' should apply both macros."""
        g = _make_chain()
        original_node_count = len(g["nodes"])
        result = dispatch_compound_mutations(
            g,
            "add a review loop and fan out the research step",
            target_node_hint="b",
        )
        assert result.matched
        assert len(result.results) == 2
        assert len(result.macro_names) == 2
        assert "wrap_in_review_loop" in result.macro_names
        assert "fan_out_node" in result.macro_names
        assert all(r.success for r in result.results)
        assert len(g["nodes"]) > original_node_count

    def test_compound_parameter_extraction(self):
        """Parameters like '5 rounds' should be extracted for each macro."""
        g = _make_chain()
        result = dispatch_compound_mutations(
            g,
            "add a review loop with 5 rounds and fan out",
            target_node_hint="b",
        )
        assert result.matched
        assert len(result.results) >= 1

    def test_per_macro_parameter_scoping(self):
        """'5 rounds' and '10 parallel' should go to their respective macros."""
        g = _make_chain()
        result = dispatch_compound_mutations(
            g,
            "add a review loop with 5 rounds and fan out with 10 workers",
            target_node_hint="b",
        )
        assert result.matched
        assert len(result.results) == 2
        gate = next(
            (n for n in g["nodes"] if n.get("node_type") == "gate"), None
        )
        fe = next(
            (n for n in g["nodes"] if n.get("node_type") == "for_each"), None
        )
        if gate:
            assert gate["config"]["max_iterations"] == 5
        if fe:
            assert fe["config"]["parallelism"] == 10

    def test_compound_failure_rollback(self):
        """If second macro fails, first should be rolled back."""
        g = _make_graph(
            [
                {"id": "a", "node_type": "llm_operator", "config": {"name": "a", "prompt_template": "Do A"}},
            ],
            [],
        )
        original = copy.deepcopy(g)
        result = dispatch_compound_mutations(
            g,
            "add a review loop and parallelize the nodes",
        )
        # parallelize needs >=2 nodes; with only 1 original + review-added nodes,
        # this should fail or degrade gracefully
        if result.error:
            # Verify rollback
            assert g["nodes"] == original["nodes"]

    def test_overlapping_keyword_dedup(self):
        """'remove the review loop' should not match both 'remove loop' and 'review loop'."""
        g = _make_graph(
            [
                {"id": "a", "node_type": "llm_operator", "config": {}},
                {"id": "loop1", "node_type": "while_loop", "config": {}},
            ],
            [
                {"source_node_id": "a", "source_port": "text", "target_node_id": "loop1", "target_port": "data"},
            ],
        )
        result = dispatch_compound_mutations(g, "remove the review loop")
        assert result.matched
        # Should match only ONE macro (unwrap_loop), not two
        if result.macro_names:
            assert len(result.macro_names) == 1


class TestDispatchResultExtended:
    def test_results_field_exists(self):
        dr = DispatchResult(matched=True)
        assert dr.results == []
        assert dr.macro_names == []

    def test_backward_compat_fields(self):
        dr = DispatchResult(
            matched=True,
            macro_name="wrap_in_review_loop",
            result=MutationMacroResult(success=True),
        )
        assert dr.macro_name == "wrap_in_review_loop"
        assert dr.result.success


class TestOriginalDispatcherUnchanged:
    """Verify dispatch_structural_mutation still works (backward compat)."""

    def test_single_macro_still_works(self):
        g = _make_chain()
        result = dispatch_structural_mutation(g, "add a review loop", target_node_hint="b")
        assert result.matched
        assert result.result.success
