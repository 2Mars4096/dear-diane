"""Tests for LLM mutation plan normalization (mutation_parser)."""

from __future__ import annotations

from dan.server.chat.mutation_parser import (
    _build_dry_run_preview,
    _normalize_generated_mutation_ops,
)
from dan.server.graph_mutator import GraphMutator, MutationPlan, MutationResult


def _empty_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "t", "description": ""},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "hyperedges": [],
        "shared_context": [],
        "artifact_refs": [],
    }


def test_normalize_type_to_node_type_on_add_node() -> None:
    raw = [
        {
            "op": "add_node",
            "id": "a",
            "type": "llm_operator",
            "name": "A",
            "config": {"prompt_template": "hi"},
        },
    ]
    norm = _normalize_generated_mutation_ops(raw)
    assert norm[0]["node_type"] == "llm_operator"
    assert "type" not in norm[0]


def test_normalize_tool_operator_tool_to_tool_id_and_nested_config() -> None:
    raw = [
        {
            "op": "add_node",
            "id": "t1",
            "node_type": "tool_operator",
            "name": "HTTP",
            "config": {
                "tool": "http_request",
                "config": {"method": "GET", "url": "https://example.com"},
            },
        },
    ]
    norm = _normalize_generated_mutation_ops(raw)
    cfg = norm[0]["config"]
    assert cfg.get("tool_id") == "http_request"
    assert "tool" not in cfg
    assert cfg.get("tool_config") == {"method": "GET", "url": "https://example.com"}


def test_normalize_replace_body_coerces_shapeless_node_dicts() -> None:
    raw = [
        {
            "op": "add_node",
            "id": "fe",
            "node_type": "for_each",
            "name": "Loop",
            "config": {"parallelism": 2},
        },
        {
            "op": "replace_body_graph",
            "node_id": "fe",
            "operations": [
                {
                    "id": "fetch",
                    "type": "tool_operator",
                    "name": "Fetch",
                    "config": {"tool": "file_read", "config": {"path": "x"}},
                },
            ],
            "entry_ids": ["fetch"],
            "exit_ids": ["fetch"],
        },
    ]
    norm = _normalize_generated_mutation_ops(raw)
    inner = norm[1]["operations"]
    assert inner[0]["op"] == "add_node"
    assert inner[0]["node_type"] == "tool_operator"
    assert inner[0]["config"].get("tool_id") == "file_read"

    plan = MutationPlan.model_validate({"operations": norm})
    result = GraphMutator().dry_run(_empty_graph(), plan)
    assert result.success, result.errors


def test_mutator_rejects_output_node_type() -> None:
    plan = MutationPlan(
        operations=[
            {
                "op": "add_node",
                "id": "bad",
                "node_type": "output",
                "name": "Bad",
                "config": {},
            },
        ],
        description="",
    )
    result = GraphMutator().dry_run(_empty_graph(), plan)
    assert not result.success
    assert result.errors
    assert "output" in result.errors[0].message.lower() or "invalid" in result.errors[0].message.lower()


def test_build_dry_run_preview_reports_applied_ops_count() -> None:
    status, preview = _build_dry_run_preview(
        MutationResult(success=True, applied_ops=[0, 1], new_graph=_empty_graph())
    )
    assert status == "success"
    assert preview == "Dry run passed (2 operations applied)"
