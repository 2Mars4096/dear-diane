"""Tests for legacy graph migration to GateNode patterns."""

from __future__ import annotations

import copy

import pytest

from dan.migration.gate_migration import (
    migrate_graph,
    migrate_if_else_to_gate,
    migrate_while_loop_to_flat_gate,
)


def _make_if_else_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "nodes": [
            {
                "id": "check",
                "name": "check",
                "node_type": "if_else",
                "condition": "score > 0.5",
                "input_ports": [{"name": "input", "schema": {}}],
                "output_ports": [{"name": "branch"}],
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "a",
                "name": "a",
                "node_type": "code_operator",
                "code": "result = 'yes'",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
                "position": {"x": 100, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "edge_type": "data",
                "source_node_id": "check",
                "source_port": "branch",
                "target_node_id": "a",
                "target_port": "input",
            },
        ],
        "entry_points": ["check"],
        "exit_points": ["a"],
        "sub_graphs": {},
    }


def _make_while_loop_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "nodes": [
            {
                "id": "setup",
                "name": "setup",
                "node_type": "code_operator",
                "code": "result = {'x': 0}",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "loop",
                "name": "loop",
                "node_type": "while_loop",
                "condition": "x < 3",
                "body_graph": "loop_body",
                "max_iterations": 5,
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
                "position": {"x": 100, "y": 0},
            },
            {
                "id": "finish",
                "name": "finish",
                "node_type": "code_operator",
                "code": "result = {'done': True}",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "result"}],
                "position": {"x": 200, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "edge_type": "data",
                "source_node_id": "setup",
                "source_port": "result",
                "target_node_id": "loop",
                "target_port": "input",
            },
            {
                "id": "e2",
                "edge_type": "data",
                "source_node_id": "loop",
                "source_port": "result",
                "target_node_id": "finish",
                "target_port": "input",
            },
        ],
        "entry_points": ["setup"],
        "exit_points": ["finish"],
        "sub_graphs": {
            "loop_body": {
                "version": "dan_graph_v1",
                "nodes": [
                    {
                        "id": "inc",
                        "name": "inc",
                        "node_type": "code_operator",
                        "code": "result = {'x': x + 1}",
                        "input_ports": [{"name": "input"}],
                        "output_ports": [{"name": "result"}],
                        "position": {"x": 0, "y": 0},
                    },
                ],
                "edges": [],
                "entry_points": ["inc"],
                "exit_points": ["inc"],
                "sub_graphs": {},
                "metadata": {"name": "loop_body"},
            },
        },
    }


def _make_clean_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "nodes": [
            {
                "id": "gen",
                "name": "gen",
                "node_type": "llm_operator",
                "model": "gpt-4o",
                "prompt_template": "Hello",
                "input_ports": [{"name": "input"}],
                "output_ports": [{"name": "text"}],
                "position": {"x": 0, "y": 0},
            },
        ],
        "edges": [],
        "entry_points": ["gen"],
        "exit_points": ["gen"],
        "sub_graphs": {},
    }


class TestMigrateIfElseToGate:
    def test_migrate_if_else_to_gate(self):
        graph = _make_if_else_graph()
        result = migrate_if_else_to_gate(graph)
        node = result["nodes"][0]
        assert node["node_type"] == "gate"
        assert node["gate_mode"] == "if_else"

    def test_migrate_if_else_preserves_condition(self):
        graph = _make_if_else_graph()
        result = migrate_if_else_to_gate(graph)
        node = result["nodes"][0]
        assert node["condition"] == "score > 0.5"

    def test_migrate_if_else_adds_output_ports(self):
        graph = _make_if_else_graph()
        result = migrate_if_else_to_gate(graph)
        node = result["nodes"][0]
        port_names = {p["name"] for p in node["output_ports"]}
        assert "true" in port_names
        assert "false" in port_names

    def test_migrate_if_else_remaps_branch_edges(self):
        graph = _make_if_else_graph()
        result = migrate_if_else_to_gate(graph)
        edge = result["edges"][0]
        assert edge["source_port"] == "true"


class TestMigrateWhileLoopToFlatGate:
    def test_migrate_while_loop_flattens(self):
        graph = _make_while_loop_graph()
        result = migrate_while_loop_to_flat_gate(graph)

        node_ids = {n["id"] for n in result["nodes"]}
        assert "loop" not in node_ids, "while_loop container should be removed"
        assert "loop__inc" in node_ids, "body node should appear with prefix"
        assert "loop__gate" in node_ids, "gate node should be created"

        gate = next(n for n in result["nodes"] if n["id"] == "loop__gate")
        assert gate["gate_mode"] == "while"
        assert gate["condition"] == "x < 3"
        assert gate["max_iterations"] == 5

        back_edge = next(
            (e for e in result["edges"]
             if e.get("source_node_id") == "loop__gate"
             and e.get("source_port") == "continue"),
            None,
        )
        assert back_edge is not None
        assert back_edge["target_node_id"] == "loop__inc"

    def test_migrate_while_loop_namespaces_ids(self):
        graph = _make_while_loop_graph()
        result = migrate_while_loop_to_flat_gate(graph)
        node_ids = {n["id"] for n in result["nodes"]}
        assert "loop__inc" in node_ids
        assert "inc" not in node_ids

    def test_migrate_while_loop_wires_downstream(self):
        graph = _make_while_loop_graph()
        result = migrate_while_loop_to_flat_gate(graph)
        done_edge = next(
            (e for e in result["edges"]
             if e.get("source_node_id") == "loop__gate"
             and e.get("source_port") == "done"),
            None,
        )
        assert done_edge is not None
        assert done_edge["target_node_id"] == "finish"

    def test_migrate_while_loop_wires_upstream(self):
        graph = _make_while_loop_graph()
        result = migrate_while_loop_to_flat_gate(graph)
        upstream_edge = next(
            (e for e in result["edges"]
             if e.get("source_node_id") == "setup"
             and e.get("target_node_id") == "loop__inc"),
            None,
        )
        assert upstream_edge is not None

    def test_migrate_while_loop_removes_sub_graph(self):
        graph = _make_while_loop_graph()
        result = migrate_while_loop_to_flat_gate(graph)
        assert "loop_body" not in result.get("sub_graphs", {})


class TestMigrateGraph:
    def test_migrate_graph_applies_all(self):
        graph: dict = {
            "version": "dan_graph_v1",
            "nodes": [
                {
                    "id": "cond",
                    "name": "cond",
                    "node_type": "if_else",
                    "condition": "x > 0",
                    "input_ports": [{"name": "input"}],
                    "output_ports": [{"name": "branch"}],
                    "position": {"x": 0, "y": 0},
                },
                {
                    "id": "loop",
                    "name": "loop",
                    "node_type": "while_loop",
                    "condition": "i < 2",
                    "body_graph": "loop_body",
                    "max_iterations": 3,
                    "input_ports": [{"name": "input"}],
                    "output_ports": [{"name": "result"}],
                    "position": {"x": 100, "y": 0},
                },
            ],
            "edges": [],
            "entry_points": ["cond", "loop"],
            "exit_points": ["cond", "loop"],
            "sub_graphs": {
                "loop_body": {
                    "version": "dan_graph_v1",
                    "nodes": [
                        {
                            "id": "step",
                            "name": "step",
                            "node_type": "code_operator",
                            "code": "result = {}",
                            "input_ports": [{"name": "input"}],
                            "output_ports": [{"name": "result"}],
                            "position": {"x": 0, "y": 0},
                        },
                    ],
                    "edges": [],
                    "entry_points": ["step"],
                    "exit_points": ["step"],
                    "sub_graphs": {},
                    "metadata": {"name": "loop_body"},
                },
            },
        }
        original = copy.deepcopy(graph)
        result = migrate_graph(graph)

        assert graph == original, "migrate_graph must not mutate the original"

        node_types = {n["node_type"] for n in result["nodes"]}
        assert "if_else" not in node_types
        assert "while_loop" not in node_types
        assert "gate" in node_types

        gate_modes = {
            n.get("gate_mode") for n in result["nodes"] if n["node_type"] == "gate"
        }
        assert "if_else" in gate_modes
        assert "while" in gate_modes

    def test_migrate_noop_on_clean_graph(self):
        graph = _make_clean_graph()
        original = copy.deepcopy(graph)
        result = migrate_graph(graph)
        assert result == original


class TestMigrationEdgeCases:
    def test_migrate_while_loop_infers_exit_port(self):
        """Body exit node with non-default output port name is used correctly."""
        graph = _make_while_loop_graph()
        body = graph["sub_graphs"]["loop_body"]
        body["nodes"][0]["output_ports"] = [{"name": "text"}]
        result = migrate_while_loop_to_flat_gate(graph)
        exit_edge = next(
            (e for e in result["edges"]
             if e.get("id") == "loop__exit_to_gate"),
            None,
        )
        assert exit_edge is not None
        assert exit_edge["source_port"] == "text"

    def test_migrate_while_loop_infers_entry_port(self):
        """Body entry node with non-default input port name is used correctly."""
        graph = _make_while_loop_graph()
        body = graph["sub_graphs"]["loop_body"]
        body["nodes"][0]["input_ports"] = [{"name": "data"}]
        result = migrate_while_loop_to_flat_gate(graph)
        continue_edge = next(
            (e for e in result["edges"]
             if e.get("id") == "loop__gate_continue"),
            None,
        )
        assert continue_edge is not None
        assert continue_edge["target_port"] == "data"

    def test_migrate_while_loop_skips_empty_body(self):
        """While-loop with empty entry/exit points is skipped gracefully."""
        graph = _make_while_loop_graph()
        body = graph["sub_graphs"]["loop_body"]
        body["entry_points"] = []
        body["exit_points"] = []
        result = migrate_while_loop_to_flat_gate(graph)
        node_ids = {n["id"] for n in result["nodes"]}
        assert "loop" in node_ids, "while_loop node should remain when skipped"
        assert "loop__gate" not in node_ids

    def test_migrate_if_else_logs_warning(self, caplog):
        """Warning is logged when remapping branch edges."""
        import logging
        graph = _make_if_else_graph()
        with caplog.at_level(logging.WARNING, logger="dan.migration.gate_migration"):
            migrate_if_else_to_gate(graph)
        assert any("remapped source_port 'branch'" in msg for msg in caplog.messages)
