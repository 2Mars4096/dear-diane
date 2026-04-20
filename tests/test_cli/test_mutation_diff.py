"""Tests for dan.cli.mutation_diff — mutation diff display."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dan.cli.mutation_diff import format_mutation_diff


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _add_node_plan() -> dict:
    return {
        "description": "Add research node",
        "operations": [
            {
                "op": "add_node",
                "name": "Research",
                "node_type": "llm_operator",
            },
        ],
    }


def _remove_node_plan() -> dict:
    return {
        "description": "Remove stale node",
        "operations": [
            {
                "op": "remove_node",
                "name": "OldNode",
                "node_id": "n_old",
            },
        ],
    }


def _edit_node_plan() -> dict:
    return {
        "description": "Update prompt",
        "operations": [
            {
                "op": "edit_node",
                "name": "Writer",
                "changes": {"prompt_template": "new prompt", "model": "gpt-4"},
            },
        ],
    }


def _mixed_plan() -> dict:
    return {
        "description": "Restructure pipeline",
        "operations": [
            {"op": "add_node", "name": "Fetcher", "node_type": "tool_operator"},
            {"op": "remove_node", "name": "LegacyFetch"},
            {"op": "edit_node", "name": "Analyzer", "changes": {"model": "claude-4"}},
            {
                "op": "add_edge",
                "source_node_id": "fetcher",
                "source_port": "output",
                "target_node_id": "analyzer",
                "target_port": "input",
            },
            {
                "op": "remove_edge",
                "source_node_id": "legacy",
                "source_port": "out",
                "target_node_id": "analyzer",
                "target_port": "in",
            },
        ],
    }


def _empty_plan() -> dict:
    return {"description": "No-op", "operations": []}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFormatMutationDiff:
    def test_add_node(self):
        result = format_mutation_diff(_add_node_plan())
        assert "+ Node: Research (llm_operator)" in result
        assert "+1" in result

    def test_remove_node(self):
        result = format_mutation_diff(_remove_node_plan())
        assert "- Node: OldNode" in result
        assert "-1" in result

    def test_edit_node_shows_changed_keys(self):
        result = format_mutation_diff(_edit_node_plan())
        assert "~ Node: Writer" in result
        assert "prompt_template" in result

    def test_add_edge(self):
        plan = {
            "description": "Wire nodes",
            "operations": [
                {
                    "op": "add_edge",
                    "source_node_id": "a",
                    "source_port": "text",
                    "target_node_id": "b",
                    "target_port": "input",
                },
            ],
        }
        result = format_mutation_diff(plan)
        assert "+ Edge:" in result
        assert "a.text" in result
        assert "b.input" in result

    def test_remove_edge(self):
        plan = {
            "description": "Disconnect",
            "operations": [
                {
                    "op": "remove_edge",
                    "source_node_id": "x",
                    "source_port": "out",
                    "target_node_id": "y",
                    "target_port": "in",
                },
            ],
        }
        result = format_mutation_diff(plan)
        assert "- Edge:" in result
        assert "x.out" in result

    def test_mixed_plan_summary(self):
        result = format_mutation_diff(_mixed_plan())
        assert "+2" in result  # add_node + add_edge
        assert "-2" in result  # remove_node + remove_edge
        assert "~1" in result  # edit_node

    def test_empty_plan(self):
        result = format_mutation_diff(_empty_plan())
        assert "no changes" in result

    def test_description_shown(self):
        result = format_mutation_diff(_mixed_plan())
        assert "Restructure pipeline" in result

    def test_no_color_when_not_tty(self):
        with patch("dan.cli.mutation_diff._supports_color", return_value=False):
            result = format_mutation_diff(_add_node_plan())
            assert "\033[" not in result  # no ANSI escapes
            assert "+ Node: Research" in result

    def test_color_when_tty(self):
        with patch("dan.cli.mutation_diff._supports_color", return_value=True):
            result = format_mutation_diff(_add_node_plan())
            assert "\033[32m" in result  # green

    def test_arrow_in_edge_display(self):
        plan = {
            "description": "Wire",
            "operations": [
                {
                    "op": "add_edge",
                    "source_node_id": "a",
                    "source_port": "out",
                    "target_node_id": "b",
                    "target_port": "in",
                },
            ],
        }
        with patch("dan.cli.mutation_diff._supports_color", return_value=False):
            result = format_mutation_diff(plan)
            assert "→" in result
