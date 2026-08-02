from __future__ import annotations

import json

from dan.models.graph import Graph
from dan.server.chat.mutation_parser import normalize_mutation_ops_for_chat
from dan.server.graph_mutator import GraphMutator, MutationPlan


def test_normalize_mutation_ops_fills_blank_node_ids_and_rewrites_body_edges() -> None:
    ops = [
        {
            "op": "replace_body_graph",
            "node_id": "process_tickers",
            "operations": [
                {
                    "op": "add_node",
                    "id": "",
                    "node_type": "code_operator",
                    "name": "validate_ticker",
                    "config": {},
                },
                {
                    "op": "add_node",
                    "id": "",
                    "node_type": "code_operator",
                    "name": "generate_report",
                    "config": {},
                },
                {
                    "op": "add_edge",
                    "edge_type": "data",
                    "source_id": "validate_ticker",
                    "source_port": "output",
                    "target_id": "generate_report",
                    "target_port": "input",
                },
            ],
        }
    ]

    normalized, repairs = normalize_mutation_ops_for_chat({}, ops)

    body_ops = normalized[0]["operations"]
    assert body_ops[0]["id"] == "validate-ticker"
    assert body_ops[1]["id"] == "generate-report"
    assert body_ops[2]["source_id"] == "validate-ticker"
    assert body_ops[2]["target_id"] == "generate-report"
    assert any("Filled missing add_node id" in repair for repair in repairs)


def test_normalize_mutation_ops_repairs_http_request_response_port_alias_in_body_graph() -> None:
    graph = json.loads(Graph().model_dump_json())
    graph["nodes"] = [
        {
            "id": "process_tickers",
            "name": "process_tickers",
            "node_type": "for_each",
            "input_ports": [{"name": "items", "json_schema": {}, "required": False}],
            "output_ports": [{"name": "results", "json_schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "body_graph": "process_tickers__body",
            "parallelism": 1,
            "merge_strategy": "append",
        }
    ]
    graph["entry_points"] = ["process_tickers"]
    graph["exit_points"] = ["process_tickers"]

    ops = [
        {
            "op": "replace_body_graph",
            "node_id": "process_tickers",
            "operations": [
                {
                    "op": "add_node",
                    "id": "fetch_stock_overview",
                    "node_type": "tool_operator",
                    "name": "fetch_stock_overview",
                    "config": {"tool_id": "http_request"},
                },
                {
                    "op": "add_node",
                    "id": "consume_body",
                    "node_type": "code_operator",
                    "name": "consume_body",
                    "config": {"code": "result = input"},
                },
                {
                    "op": "add_edge",
                    "edge_type": "data",
                    "source_id": "fetch_stock_overview",
                    "source_port": "response",
                    "target_id": "consume_body",
                    "target_port": "input",
                },
            ],
        }
    ]

    normalized, repairs = normalize_mutation_ops_for_chat(graph, ops)

    body_ops = normalized[0]["operations"]
    assert body_ops[2]["source_port"] == "body"
    assert any("source_port 'response'" in repair for repair in repairs)

    plan = MutationPlan.model_validate({"operations": normalized})
    result = GraphMutator().dry_run(graph, plan)
    assert result.success, result.errors
