from __future__ import annotations

import json

from dan.models.graph import Graph
from dan.server.graph_mutator import GraphMutator, MutationPlan, _default_ports


def _empty_graph() -> dict:
    return json.loads(Graph().model_dump_json())


def test_remove_missing_node_fails_in_dry_run() -> None:
    plan = MutationPlan.model_validate(
        {
            "operations": [
                {"op": "remove_node", "node_id": "ghost"},
            ]
        }
    )

    result = GraphMutator().dry_run(_empty_graph(), plan)

    assert result.success is False
    assert result.errors and result.errors[0].message == "Node 'ghost' not found"
    assert result.new_graph is None
    assert result.diagnostics == []


def test_if_else_add_node_uses_true_false_output_ports() -> None:
    plan = MutationPlan.model_validate(
        {
            "operations": [
                {
                    "op": "add_node",
                    "id": "check",
                    "node_type": "if_else",
                    "name": "Check",
                    "config": {"condition": "x > 0"},
                },
            ]
        }
    )

    result = GraphMutator().dry_run(_empty_graph(), plan)

    assert result.success is True
    assert result.new_graph is not None
    node = next(node for node in result.new_graph["nodes"] if node["id"] == "check")
    assert {port["name"] for port in node["output_ports"]} == {"true", "false"}


def test_remove_then_recreate_same_node_id_succeeds() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        {
            "id": "workflow_input",
            "name": "Workflow Input",
            "node_type": "input",
            "input_ports": [],
            "output_ports": [{"name": "input", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "variables": [],
        },
        {
            "id": "sink",
            "name": "Sink",
            "node_type": "code_operator",
            "code": "result = input",
            "input_ports": [{"name": "input", "schema": {}, "required": False}],
            "output_ports": [{"name": "result", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        },
    ]
    graph["edges"] = [
        {
            "id": "edge-1",
            "edge_type": "data",
            "source_node_id": "workflow_input",
            "source_port": "input",
            "target_node_id": "sink",
            "target_port": "input",
        }
    ]
    graph["entry_points"] = ["workflow_input"]
    graph["exit_points"] = ["sink"]

    plan = MutationPlan.model_validate(
        {
            "operations": [
                {"op": "remove_node", "node_id": "workflow_input"},
                {
                    "op": "add_node",
                    "id": "workflow_input",
                    "node_type": "input",
                    "name": "Workflow Input",
                },
                {
                    "op": "add_edge",
                    "source_id": "workflow_input",
                    "source_port": "input",
                    "target_id": "sink",
                    "target_port": "input",
                    "edge_type": "data",
                },
            ]
        }
    )

    result = GraphMutator().dry_run(graph, plan)

    assert result.success is True
    assert result.new_graph is not None
    assert [node["id"] for node in result.new_graph["nodes"]].count("workflow_input") == 1
    assert any(
        edge["source_node_id"] == "workflow_input"
        and edge["target_node_id"] == "sink"
        for edge in result.new_graph["edges"]
    )


def test_for_each_item_source_port_aliases_to_results() -> None:
    graph = _empty_graph()
    graph["sub_graphs"] = {
        "fan_body": {
            "version": "dan_graph_v1",
            "metadata": {"name": "Fan Body"},
            "nodes": [],
            "edges": [],
        }
    }
    graph["nodes"] = [
        {
            "id": "fan",
            "name": "Fan Out",
            "node_type": "for_each",
            "body_graph": "fan_body",
            "input_ports": [{"name": "items", "schema": {}, "required": False}],
            "output_ports": [{"name": "results", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        },
        {
            "id": "sink",
            "name": "Sink",
            "node_type": "code_operator",
            "code": "output = input_data",
            "input_ports": [{"name": "input", "schema": {}, "required": False}],
            "output_ports": [{"name": "result", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        },
    ]
    plan = MutationPlan.model_validate(
        {
            "operations": [
                {
                    "op": "add_edge",
                    "source_id": "fan",
                    "source_port": "item",
                    "target_id": "sink",
                    "target_port": "input",
                    "edge_type": "data",
                    "strict": True,
                },
            ]
        }
    )

    result = GraphMutator().dry_run(graph, plan)

    assert result.success is True
    assert result.new_graph is not None
    assert any(
        "Normalized source port 'item' to 'results'" in diagnostic
        for diagnostic in result.diagnostics
    )
    assert result.new_graph["edges"][0]["source_port"] == "results"


def test_tool_operator_core_manifests_expose_specific_ports() -> None:
    csv_in, csv_out = _default_ports("tool_operator", {"tool_id": "csv_read"})
    assert [port["name"] for port in csv_in] == [
        "path",
        "delimiter",
        "max_rows",
        "columns",
        "encoding",
    ]
    assert [port["name"] for port in csv_out] == [
        "headers",
        "rows",
        "row_count",
        "total_rows",
        "column_count",
        "truncated",
        "result",
    ]

    fetch_in, fetch_out = _default_ports("tool_operator", {"tool_id": "web_fetch"})
    assert [port["name"] for port in fetch_in] == ["url", "timeout", "max_length"]
    assert [port["name"] for port in fetch_out] == [
        "url",
        "text",
        "status_code",
        "content_type",
        "result",
    ]

    write_in, write_out = _default_ports("tool_operator", {"tool_id": "file_write"})
    assert [port["name"] for port in write_in] == ["path", "content", "mode", "encoding"]
    assert [port["name"] for port in write_out] == [
        "bytes_written",
        "path",
        "mode",
        "result",
    ]


def test_single_output_source_port_typo_normalizes_to_result() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        {
            "id": "producer",
            "name": "Producer",
            "node_type": "code_operator",
            "code": "output = input_data",
            "input_ports": [{"name": "input", "schema": {}, "required": False}],
            "output_ports": [{"name": "result", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        },
        {
            "id": "sink",
            "name": "Sink",
            "node_type": "code_operator",
            "code": "output = input_data",
            "input_ports": [{"name": "input", "schema": {}, "required": False}],
            "output_ports": [{"name": "result", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        },
    ]
    plan = MutationPlan.model_validate(
        {
            "operations": [
                {
                    "op": "add_edge",
                    "source_id": "producer",
                    "source_port": "output",
                    "target_id": "sink",
                    "target_port": "input",
                    "edge_type": "data",
                    "strict": True,
                },
            ]
        }
    )

    result = GraphMutator().dry_run(graph, plan)

    assert result.success is True
    assert result.errors == []
    assert result.new_graph is not None
    assert result.new_graph["edges"][0]["source_port"] == "result"
    assert any(
        "Normalized source port 'output' to 'result'" in diagnostic
        for diagnostic in result.diagnostics
    )


def test_if_else_branch_source_port_aliases_to_true() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        {
            "id": "check",
            "name": "Check",
            "node_type": "if_else",
            "condition": "x > 0",
            "input_ports": [{"name": "input", "schema": {}, "required": False}],
            "output_ports": [
                {"name": "true", "schema": {}},
                {"name": "false", "schema": {}},
            ],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        },
        {
            "id": "sink",
            "name": "Sink",
            "node_type": "code_operator",
            "code": "output = input_data",
            "input_ports": [{"name": "input", "schema": {}, "required": False}],
            "output_ports": [{"name": "result", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        },
    ]
    plan = MutationPlan.model_validate(
        {
            "operations": [
                {
                    "op": "add_edge",
                    "source_id": "check",
                    "source_port": "branch",
                    "target_id": "sink",
                    "target_port": "input",
                    "edge_type": "data",
                    "strict": True,
                },
            ]
        }
    )

    result = GraphMutator().dry_run(graph, plan)

    assert result.success is True
    assert result.new_graph is not None
    assert any(
        "Normalized source port 'branch' to 'true'" in diagnostic
        for diagnostic in result.diagnostics
    )
    assert result.new_graph["edges"][0]["source_port"] == "true"


def test_add_for_each_scaffolds_empty_body_graph() -> None:
    plan = MutationPlan.model_validate(
        {
            "operations": [
                {
                    "op": "add_node",
                    "id": "fan",
                    "node_type": "for_each",
                    "name": "Fan Out",
                    "config": {"parallelism": 2},
                },
            ]
        }
    )

    result = GraphMutator().dry_run(_empty_graph(), plan)

    assert result.success is True
    assert result.new_graph is not None
    fan = next(node for node in result.new_graph["nodes"] if node["id"] == "fan")
    assert fan["body_graph"] == "fan__body"
    assert "fan__body" in result.new_graph["sub_graphs"]
    assert result.new_graph["sub_graphs"]["fan__body"]["nodes"] == []


def test_replace_body_graph_populates_for_each_subgraph() -> None:
    plan = MutationPlan.model_validate(
        {
            "operations": [
                {
                    "op": "add_node",
                    "id": "fan",
                    "node_type": "for_each",
                    "name": "Fan Out",
                    "config": {"parallelism": 2},
                },
                {
                    "op": "replace_body_graph",
                    "node_id": "fan",
                    "operations": [
                        {
                            "op": "add_node",
                            "id": "unpack_item",
                            "node_type": "code_operator",
                            "name": "Unpack Item",
                            "config": {
                                "code": "result = {'ticker': str(item)}",
                                "input_ports": [
                                    {"name": "item", "schema": {}, "required": False}
                                ],
                                "output_ports": [{"name": "result", "schema": {}}],
                            },
                        },
                    ],
                    "entry_ids": ["unpack_item"],
                    "exit_ids": ["unpack_item"],
                },
            ]
        }
    )

    result = GraphMutator().dry_run(_empty_graph(), plan)

    assert result.success is True
    assert result.new_graph is not None
    subgraph = result.new_graph["sub_graphs"]["fan__body"]
    assert [node["id"] for node in subgraph["nodes"]] == ["unpack_item"]
    assert subgraph["entry_points"] == ["unpack_item"]
    assert subgraph["exit_points"] == ["unpack_item"]


def test_expand_pattern_fan_out_creates_valid_body_graph() -> None:
    plan = MutationPlan.model_validate(
        {
            "operations": [
                {
                    "op": "expand_pattern",
                    "pattern": "fan_out",
                    "params": {
                        "source_name": "Ticker Source",
                        "body_name": "Summarize Ticker",
                        "body_prompt": "Summarize {item}",
                        "parallelism": 3,
                    },
                },
            ]
        }
    )

    result = GraphMutator().dry_run(_empty_graph(), plan)

    assert result.success is True
    assert result.new_graph is not None
    fan = next(node for node in result.new_graph["nodes"] if node["id"] == "fan-out")
    assert fan["body_graph"] == "fan-out__body"
    body = result.new_graph["sub_graphs"]["fan-out__body"]
    assert body["entry_points"] == ["summarize-ticker"]
    assert body["exit_points"] == ["summarize-ticker"]
    assert [node["id"] for node in body["nodes"]] == ["summarize-ticker"]
