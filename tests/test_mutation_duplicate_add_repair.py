"""Tests for duplicate add_node -> edit_node self-repair (chat mutation path)."""

from __future__ import annotations

import json

from dan.models.graph import Graph
from dan.server.chat.mutation_parser import normalize_mutation_ops_for_chat
from dan.server.graph_mutator import GraphMutator, MutationPlan


def _empty_graph() -> dict:
    return json.loads(Graph().model_dump_json())


def _minimal_input_node(node_id: str = "read_watchlist", name: str = "Read") -> dict:
    return {
        "id": node_id,
        "name": name,
        "node_type": "input",
        "input_ports": [],
        "output_ports": [{"name": "input", "schema": {}}],
        "position": {"x": 0, "y": 0},
        "ui": {},
        "metadata": {},
        "variables": [],
    }


def _minimal_code_node(
    node_id: str,
    name: str,
    *,
    input_ports: list[dict] | None = None,
    output_ports: list[dict] | None = None,
) -> dict:
    return {
        "id": node_id,
        "name": name,
        "node_type": "code_operator",
        "code": "result = input",
        "input_ports": input_ports or [{"name": "input", "schema": {}, "required": False}],
        "output_ports": output_ports or [{"name": "result", "schema": {}}],
        "position": {"x": 0, "y": 0},
        "ui": {},
        "metadata": {},
    }


def test_repair_add_node_when_id_exists_in_graph_becomes_edit_node() -> None:
    graph = _empty_graph()
    graph["nodes"] = [_minimal_input_node()]
    graph["entry_points"] = ["read_watchlist"]
    graph["exit_points"] = ["read_watchlist"]

    raw_ops = [
        {
            "op": "add_node",
            "id": "read_watchlist",
            "node_type": "input",
            "name": "Updated label",
            "config": {"path": "watchlist.csv"},
        },
        {
            "op": "add_node",
            "id": "new_node",
            "node_type": "code_operator",
            "name": "X",
        },
    ]
    ops, repairs = normalize_mutation_ops_for_chat(graph, raw_ops)
    assert len(repairs) >= 1
    assert any("read_watchlist" in r and "edit_node" in r for r in repairs)
    assert ops[0]["op"] == "edit_node"
    assert ops[0]["node_id"] == "read_watchlist"
    assert ops[0]["updates"]["name"] == "Updated label"
    assert ops[0]["updates"]["config"] == {"path": "watchlist.csv"}
    assert ops[1]["op"] == "add_node"

    plan = MutationPlan.model_validate({"operations": ops})
    result = GraphMutator().dry_run(graph, plan)
    assert result.success, result.errors


def test_repair_second_add_same_id_in_plan_only() -> None:
    graph = _empty_graph()
    raw_ops = [
        {"op": "add_node", "id": "a", "node_type": "input", "name": "A"},
        {"op": "add_node", "id": "a", "node_type": "input", "name": "A2"},
    ]
    ops, repairs = normalize_mutation_ops_for_chat(graph, raw_ops)
    assert len(repairs) == 1
    assert ops[0]["op"] == "add_node"
    assert ops[1]["op"] == "edit_node"
    assert ops[1]["node_id"] == "a"
    assert ops[1]["updates"]["name"] == "A2"

    plan = MutationPlan.model_validate({"operations": ops})
    result = GraphMutator().dry_run(graph, plan)
    assert result.success, result.errors
    assert result.new_graph is not None
    ids = [n["id"] for n in result.new_graph["nodes"]]
    assert ids.count("a") == 1


def test_repair_existing_duplicate_add_edge_becomes_noop() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        _minimal_input_node("source", "Source"),
        _minimal_input_node("target", "Target"),
    ]
    graph["edges"] = [
        {
            "id": "source.input->target.input",
            "edge_type": "data",
            "source_node_id": "source",
            "source_port": "input",
            "target_node_id": "target",
            "target_port": "input",
        }
    ]

    ops, repairs = normalize_mutation_ops_for_chat(
        graph,
        [
            {
                "op": "add_edge",
                "source_id": "source",
                "source_port": "input",
                "target_id": "target",
                "target_port": "input",
            }
        ],
    )

    assert ops == []
    assert any("Dropped redundant add_edge" in repair for repair in repairs)


def test_repair_second_identical_add_edge_in_plan_only_becomes_noop() -> None:
    graph = _empty_graph()
    raw_ops = [
        {
            "op": "add_edge",
            "source_id": "a",
            "source_port": "out",
            "target_id": "b",
            "target_port": "input",
        },
        {
            "op": "add_edge",
            "source_id": "a",
            "source_port": "out",
            "target_id": "b",
            "target_port": "input",
        },
    ]

    ops, repairs = normalize_mutation_ops_for_chat(graph, raw_ops)

    assert len(ops) == 1
    assert ops[0]["op"] == "add_edge"
    assert any("Dropped redundant add_edge" in repair for repair in repairs)


def test_remove_edge_then_readd_same_edge_is_not_dropped() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        _minimal_input_node("source", "Source"),
        _minimal_input_node("target", "Target"),
    ]
    graph["edges"] = [
        {
            "id": "source.input->target.input",
            "edge_type": "data",
            "source_node_id": "source",
            "source_port": "input",
            "target_node_id": "target",
            "target_port": "input",
        }
    ]

    raw_ops = [
        {
            "op": "remove_edge",
            "source_id": "source",
            "source_port": "input",
            "target_id": "target",
            "target_port": "input",
        },
        {
            "op": "add_edge",
            "source_id": "source",
            "source_port": "input",
            "target_id": "target",
            "target_port": "input",
        },
    ]

    ops, repairs = normalize_mutation_ops_for_chat(graph, raw_ops)

    assert repairs == []
    assert [op["op"] for op in ops] == ["remove_edge", "add_edge"]


def test_required_input_disconnect_keeps_original_edge_when_no_replacement_exists() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        _minimal_code_node(
            "summarize",
            "Summarize",
            output_ports=[{"name": "summary", "schema": {}}],
        ),
        _minimal_code_node(
            "metrics",
            "Metrics",
            input_ports=[{"name": "summary", "schema": {}, "required": True}],
        ),
    ]
    graph["edges"] = [
        {
            "id": "summarize.summary->metrics.summary",
            "edge_type": "data",
            "source_node_id": "summarize",
            "source_port": "summary",
            "target_node_id": "metrics",
            "target_port": "summary",
        }
    ]
    graph["entry_points"] = ["summarize"]
    graph["exit_points"] = ["metrics"]

    raw_ops = [
        {
            "op": "remove_edge",
            "source_id": "summarize",
            "source_port": "summary",
            "target_id": "metrics",
            "target_port": "summary",
        }
    ]

    ops, repairs = normalize_mutation_ops_for_chat(graph, raw_ops)

    assert ops == []
    assert any("disconnect required input port 'summary' on node 'metrics'" in repair for repair in repairs)

    plan = MutationPlan.model_validate({"operations": ops})
    result = GraphMutator().dry_run(graph, plan)
    assert result.success, result.errors


def test_required_input_disconnect_not_repaired_when_replacement_edge_exists() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        _minimal_code_node(
            "summarize",
            "Summarize",
            output_ports=[{"name": "summary", "schema": {}}],
        ),
        _minimal_code_node(
            "rewrite",
            "Rewrite",
            output_ports=[{"name": "summary", "schema": {}}],
        ),
        _minimal_code_node(
            "metrics",
            "Metrics",
            input_ports=[{"name": "summary", "schema": {}, "required": True}],
        ),
    ]
    graph["edges"] = [
        {
            "id": "summarize.summary->metrics.summary",
            "edge_type": "data",
            "source_node_id": "summarize",
            "source_port": "summary",
            "target_node_id": "metrics",
            "target_port": "summary",
        }
    ]
    graph["entry_points"] = ["summarize", "rewrite"]
    graph["exit_points"] = ["metrics"]

    raw_ops = [
        {
            "op": "remove_edge",
            "source_id": "summarize",
            "source_port": "summary",
            "target_id": "metrics",
            "target_port": "summary",
        },
        {
            "op": "add_edge",
            "source_id": "rewrite",
            "source_port": "summary",
            "target_id": "metrics",
            "target_port": "summary",
        },
    ]

    ops, repairs = normalize_mutation_ops_for_chat(graph, raw_ops)

    assert [op["op"] for op in ops] == ["remove_edge", "add_edge"]
    assert not any("disconnect required input port 'summary' on node 'metrics'" in repair for repair in repairs)

    plan = MutationPlan.model_validate({"operations": ops})
    result = GraphMutator().dry_run(graph, plan)
    assert result.success, result.errors


def test_repair_duplicate_add_updates_flat_node_fields() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        {
            "id": "draft",
            "name": "Draft",
            "node_type": "llm_operator",
            "model": "claude-sonnet-4-6",
            "prompt_template": "old prompt",
            "system_prompt": "",
            "temperature": 0.3,
            "input_ports": [{"name": "input", "schema": {}, "required": False}],
            "output_ports": [{"name": "text", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        }
    ]
    graph["entry_points"] = ["draft"]
    graph["exit_points"] = ["draft"]

    ops, _repairs = normalize_mutation_ops_for_chat(
        graph,
        [
            {
                "op": "add_node",
                "id": "draft",
                "node_type": "llm_operator",
                "name": "Draft",
                "config": {"prompt_template": "new prompt"},
            }
        ],
    )

    plan = MutationPlan.model_validate({"operations": ops})
    result = GraphMutator().dry_run(graph, plan)
    assert result.success, result.errors
    assert result.new_graph is not None
    assert result.new_graph["nodes"][0]["prompt_template"] == "new prompt"
    assert "config" not in result.new_graph["nodes"][0]


def test_repair_duplicate_add_type_change_is_not_coerced() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        {
            "id": "worker",
            "name": "Worker",
            "node_type": "code_operator",
            "code": "result = 1",
            "language": "python",
            "sandbox_config": {},
            "input_ports": [{"name": "input", "schema": {}, "required": False}],
            "output_ports": [{"name": "result", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
        }
    ]
    graph["entry_points"] = ["worker"]
    graph["exit_points"] = ["worker"]

    ops, repairs = normalize_mutation_ops_for_chat(
        graph,
        [
            {
                "op": "add_node",
                "id": "worker",
                "node_type": "llm_operator",
                "name": "Worker",
                "config": {"prompt_template": "rewrite as llm"},
            }
        ],
    )

    assert len(repairs) == 1
    assert "node_type would change" in repairs[0]
    assert ops[0]["op"] == "add_node"

    plan = MutationPlan.model_validate({"operations": ops})
    result = GraphMutator().dry_run(graph, plan)
    assert not result.success
    assert any("already exists" in error.message for error in result.errors)


def test_remove_node_then_add_same_id_not_coerced_to_edit() -> None:
    graph = _empty_graph()
    graph["nodes"] = [_minimal_input_node()]
    graph["entry_points"] = ["read_watchlist"]
    graph["exit_points"] = ["read_watchlist"]

    raw_ops = [
        {"op": "remove_node", "node_id": "read_watchlist"},
        {
            "op": "add_node",
            "id": "read_watchlist",
            "node_type": "input",
            "name": "Fresh",
        },
    ]
    ops, repairs = normalize_mutation_ops_for_chat(graph, raw_ops)
    assert repairs == []
    assert ops[0]["op"] == "remove_node"
    assert ops[1]["op"] == "add_node"

    plan = MutationPlan.model_validate({"operations": ops})
    result = GraphMutator().dry_run(graph, plan)
    assert result.success, result.errors


def test_replace_body_graph_inner_duplicate_add_repaired() -> None:
    graph = _empty_graph()
    raw_ops = [
        {
            "op": "replace_body_graph",
            "node_id": "fan",
            "body_graph": "body1",
            "operations": [
                {"op": "add_node", "id": "x", "node_type": "input", "name": "1"},
                {"op": "add_node", "id": "x", "node_type": "input", "name": "2"},
            ],
        }
    ]
    ops, repairs = normalize_mutation_ops_for_chat(graph, raw_ops)
    assert len(repairs) == 1
    inner = ops[0]["operations"]
    assert inner[0]["op"] == "add_node"
    assert inner[1]["op"] == "edit_node"
    assert inner[1]["node_id"] == "x"


def test_duplicate_add_edge_with_different_semantics_not_dropped() -> None:
    graph = _empty_graph()
    graph["nodes"] = [
        _minimal_input_node("source", "Source"),
        _minimal_input_node("target", "Target"),
    ]
    graph["edges"] = [
        {
            "id": "source.input->target.input",
            "edge_type": "data",
            "source_node_id": "source",
            "source_port": "input",
            "target_node_id": "target",
            "target_port": "input",
        }
    ]

    ops, repairs = normalize_mutation_ops_for_chat(
        graph,
        [
            {
                "op": "add_edge",
                "edge_type": "control",
                "source_id": "source",
                "source_port": "input",
                "target_id": "target",
                "target_port": "input",
            }
        ],
    )

    assert repairs == []
    assert len(ops) == 1
    plan = MutationPlan.model_validate({"operations": ops})
    result = GraphMutator().dry_run(graph, plan)
    assert not result.success
    assert any("already exists" in error.message for error in result.errors)
