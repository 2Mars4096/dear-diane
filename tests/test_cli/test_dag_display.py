"""Tests for dan.cli.dag_display — ASCII DAG renderer, stats, and workflow table."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from dan.cli.dag_display import (
    render_dag,
    render_stats,
    format_workflow_table,
    _topological_levels,
    _node_label,
    _supports_unicode,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _empty_graph() -> dict:
    return {"nodes": [], "edges": []}


def _single_node_graph() -> dict:
    return {
        "nodes": [
            {"id": "n1", "name": "Research", "node_type": "llm_operator"},
        ],
        "edges": [],
    }


def _linear_chain() -> dict:
    return {
        "nodes": [
            {"id": "n1", "name": "Gather", "node_type": "tool_operator"},
            {"id": "n2", "name": "Analyze", "node_type": "llm_operator"},
            {"id": "n3", "name": "Report", "node_type": "code_operator"},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "n1", "target_node_id": "n2", "edge_type": "data"},
            {"id": "e2", "source_node_id": "n2", "target_node_id": "n3", "edge_type": "data"},
        ],
    }


def _fan_out_graph() -> dict:
    return {
        "nodes": [
            {"id": "n1", "name": "Input", "node_type": "input_node"},
            {"id": "n2", "name": "BranchA", "node_type": "llm_operator"},
            {"id": "n3", "name": "BranchB", "node_type": "llm_operator"},
            {"id": "n4", "name": "BranchC", "node_type": "llm_operator"},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "n1", "target_node_id": "n2", "edge_type": "data"},
            {"id": "e2", "source_node_id": "n1", "target_node_id": "n3", "edge_type": "data"},
            {"id": "e3", "source_node_id": "n1", "target_node_id": "n4", "edge_type": "data"},
        ],
    }


def _diamond_graph() -> dict:
    return {
        "nodes": [
            {"id": "n1", "name": "Start", "node_type": "input_node"},
            {"id": "n2", "name": "Left", "node_type": "llm_operator"},
            {"id": "n3", "name": "Right", "node_type": "llm_operator"},
            {"id": "n4", "name": "Merge", "node_type": "reduce"},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "n1", "target_node_id": "n2", "edge_type": "data"},
            {"id": "e2", "source_node_id": "n1", "target_node_id": "n3", "edge_type": "data"},
            {"id": "e3", "source_node_id": "n2", "target_node_id": "n4", "edge_type": "data"},
            {"id": "e4", "source_node_id": "n3", "target_node_id": "n4", "edge_type": "data"},
        ],
    }


def _graph_with_subgraphs() -> dict:
    return {
        "nodes": [
            {"id": "n1", "name": "Orchestrator", "node_type": "composite"},
        ],
        "edges": [],
        "sub_graphs": {
            "n1__body": {"nodes": [], "edges": []},
            "n1__review": {"nodes": [], "edges": []},
        },
        "entry_points": ["n1"],
        "exit_points": ["n1"],
    }


# ---------------------------------------------------------------------------
# render_dag tests
# ---------------------------------------------------------------------------

class TestRenderDag:
    def test_empty_graph(self):
        result = render_dag(_empty_graph(), use_color=False)
        assert "empty graph" in result

    def test_single_node(self):
        result = render_dag(_single_node_graph(), use_color=False)
        assert "Research" in result
        assert "llm" in result

    def test_linear_chain_all_nodes_present(self):
        result = render_dag(_linear_chain(), use_color=False)
        assert "Gather" in result
        assert "Analyze" in result
        assert "Report" in result

    def test_fan_out_all_branches(self):
        result = render_dag(_fan_out_graph(), use_color=False)
        assert "Input" in result
        assert "BranchA" in result
        assert "BranchB" in result
        assert "BranchC" in result

    def test_diamond_all_nodes(self):
        result = render_dag(_diamond_graph(), use_color=False)
        assert "Start" in result
        assert "Left" in result
        assert "Right" in result
        assert "Merge" in result

    def test_subgraph_count_shown(self):
        result = render_dag(_graph_with_subgraphs(), use_color=False)
        assert "Orchestrator" in result
        assert "sub" in result

    def test_max_width_respected(self):
        result = render_dag(_fan_out_graph(), use_color=False, max_width=60)
        for line in result.split("\n"):
            assert len(line) <= 61  # +1 tolerance for truncation char

    def test_uses_box_drawing_with_unicode(self):
        with patch("dan.cli.dag_display._supports_unicode", return_value=True):
            result = render_dag(_single_node_graph(), use_color=False)
            assert "┌" in result or "│" in result or "└" in result

    def test_uses_ascii_fallback(self):
        with patch("dan.cli.dag_display._supports_unicode", return_value=False):
            result = render_dag(_single_node_graph(), use_color=False)
            assert "+" in result or "|" in result


# ---------------------------------------------------------------------------
# _topological_levels tests
# ---------------------------------------------------------------------------

class TestTopologicalLevels:
    def test_empty(self):
        assert _topological_levels(_empty_graph()) == []

    def test_single_node(self):
        levels = _topological_levels(_single_node_graph())
        assert len(levels) == 1
        assert len(levels[0]) == 1

    def test_linear_chain_ordering(self):
        levels = _topological_levels(_linear_chain())
        assert len(levels) == 3
        ids = [[n["id"] for n in level] for level in levels]
        assert ids == [["n1"], ["n2"], ["n3"]]

    def test_fan_out(self):
        levels = _topological_levels(_fan_out_graph())
        assert len(levels) == 2
        assert len(levels[0]) == 1  # root
        assert len(levels[1]) == 3  # branches

    def test_diamond(self):
        levels = _topological_levels(_diamond_graph())
        assert len(levels) == 3
        assert len(levels[0]) == 1  # Start
        assert len(levels[1]) == 2  # Left, Right
        assert len(levels[2]) == 1  # Merge


# ---------------------------------------------------------------------------
# render_stats tests
# ---------------------------------------------------------------------------

class TestRenderStats:
    def test_empty_graph(self):
        result = render_stats(_empty_graph())
        assert "Nodes: 0" in result
        assert "Edges: 0" in result

    def test_linear_chain_counts(self):
        result = render_stats(_linear_chain())
        assert "Nodes: 3" in result
        assert "Edges: 2" in result

    def test_subgraph_count(self):
        result = render_stats(_graph_with_subgraphs())
        assert "Sub-graphs: 2" in result

    def test_entry_exit_points(self):
        result = render_stats(_graph_with_subgraphs())
        assert "Entry points:" in result
        assert "Exit points:" in result

    def test_node_type_breakdown(self):
        result = render_stats(_linear_chain())
        assert "tool_operator: 1" in result
        assert "llm_operator: 1" in result
        assert "code_operator: 1" in result

    def test_edge_type_breakdown(self):
        result = render_stats(_linear_chain())
        assert "data: 2" in result


# ---------------------------------------------------------------------------
# format_workflow_table tests
# ---------------------------------------------------------------------------

class TestFormatWorkflowTable:
    def test_empty_list(self):
        result = format_workflow_table([])
        assert "No saved workflows" in result

    def test_single_workflow(self):
        graphs = [
            {
                "graph_id": "my-wf",
                "name": "My Workflow",
                "node_count": 5,
                "edge_count": 4,
                "updated_at": "2026-03-06T10:00:00",
            }
        ]
        result = format_workflow_table(graphs)
        assert "my-wf" in result
        assert "My Workflow" in result
        assert "5" in result
        assert "4" in result

    def test_current_id_marker(self):
        graphs = [
            {"graph_id": "wf1", "name": "First", "updated_at": "2026-03-06T10:00:00"},
            {"graph_id": "wf2", "name": "Second", "updated_at": "2026-03-06T11:00:00"},
        ]
        result = format_workflow_table(graphs, current_id="wf1")
        assert "wf1 *" in result
        # wf2 should NOT have marker
        assert "wf2 *" not in result

    def test_sorted_by_updated_descending(self):
        graphs = [
            {"graph_id": "old", "name": "Old", "updated_at": "2026-03-01T10:00:00"},
            {"graph_id": "new", "name": "New", "updated_at": "2026-03-06T10:00:00"},
        ]
        result = format_workflow_table(graphs)
        lines = result.split("\n")
        data_lines = [l for l in lines if "old" in l.lower() or "new" in l.lower()]
        # "new" should appear before "old"
        new_idx = next(i for i, l in enumerate(data_lines) if "new" in l.lower())
        old_idx = next(i for i, l in enumerate(data_lines) if "old" in l.lower())
        assert new_idx < old_idx

    def test_headers_present(self):
        graphs = [{"graph_id": "x", "name": "X", "updated_at": "2026-03-06"}]
        result = format_workflow_table(graphs)
        assert "ID" in result
        assert "Name" in result
        assert "Nodes" in result
        assert "Edges" in result
        assert "Last Modified" in result


# ---------------------------------------------------------------------------
# _node_label tests
# ---------------------------------------------------------------------------

class TestNodeLabel:
    def test_basic_label(self):
        label = _node_label({"name": "Foo", "node_type": "llm_operator"})
        assert "[llm] Foo" == label

    def test_truncates_long_name(self):
        label = _node_label({"name": "A" * 50, "node_type": "tool_operator"}, max_len=10)
        assert len(label) < 25  # [tool] + truncated name

    def test_type_shortening(self):
        label = _node_label({"name": "X", "node_type": "code_operator"})
        assert "[code]" in label
        assert "_operator" not in label


# ---------------------------------------------------------------------------
# Unicode detection
# ---------------------------------------------------------------------------

class TestUnicodeDetection:
    def test_utf8_detected(self):
        with patch.object(sys, "stdout") as mock_out:
            mock_out.encoding = "utf-8"
            assert _supports_unicode() is True

    def test_ascii_not_unicode(self):
        with patch.object(sys, "stdout") as mock_out:
            mock_out.encoding = "ascii"
            assert _supports_unicode() is False

    def test_none_encoding(self):
        with patch.object(sys, "stdout") as mock_out:
            mock_out.encoding = None
            assert _supports_unicode() is False
