"""GraphMutator-specific helper utilities."""

from __future__ import annotations

import copy
import os
import re
from typing import Any

from dan.models.node_taxonomy import worker_builder_uses_workers

__all__ = [
    "TOOL_PORT_MANIFESTS",
    "_default_node_config",
    "_default_ports",
    "_ensure_input_variable_output_port",
    "_generate_node_id",
    "_graph_mutator_uses_workers",
    "_node_is_input_like",
    "_resolve_source_output_port",
    "_resolve_target_input_port",
    "_slugify",
    "_workerize_mutation_node_config",
]

_INPUT_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_WORKER_MUTATOR_LEGACY_COMPUTE_TYPES: frozenset[str] = frozenset({
    "llm_operator",
    "tool_operator",
    "code_operator",
    "rag_operator",
    "router",
    "validator",
    "reflection",
    "input",
    "human",
    "human_in_the_loop",
    "vote",
    "reduce",
})

TOOL_PORT_MANIFESTS: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {
    "csv_read": (
        [
            {"name": "path", "schema": {}, "required": True},
            {"name": "delimiter", "schema": {}, "required": False},
            {"name": "max_rows", "schema": {}, "required": False},
            {"name": "columns", "schema": {}, "required": False},
            {"name": "encoding", "schema": {}, "required": False},
        ],
        [
            {"name": "headers", "schema": {}},
            {"name": "rows", "schema": {}},
            {"name": "row_count", "schema": {}},
            {"name": "total_rows", "schema": {}},
            {"name": "column_count", "schema": {}},
            {"name": "truncated", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "file_read": (
        [{"name": "path", "schema": {}, "required": True}],
        [{"name": "content", "schema": {}}, {"name": "result", "schema": {}}],
    ),
    "file_write": (
        [
            {"name": "path", "schema": {}, "required": True},
            {"name": "content", "schema": {}, "required": True},
            {"name": "mode", "schema": {}, "required": False},
            {"name": "encoding", "schema": {}, "required": False},
        ],
        [
            {"name": "bytes_written", "schema": {}},
            {"name": "path", "schema": {}},
            {"name": "mode", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "list_directory": (
        [{"name": "path", "schema": {}, "required": False}],
        [{"name": "entries", "schema": {}}, {"name": "result", "schema": {}}],
    ),
    "pdf_read": (
        [{"name": "path", "schema": {}, "required": True}],
        [{"name": "text", "schema": {}}, {"name": "result", "schema": {}}],
    ),
    "http_request": (
        [
            {"name": "url", "schema": {}, "required": True},
            {"name": "method", "schema": {}, "required": False},
            {"name": "headers", "schema": {}, "required": False},
            {"name": "body", "schema": {}, "required": False},
            {"name": "timeout", "schema": {}, "required": False},
        ],
        [
            {"name": "status_code", "schema": {}},
            {"name": "headers", "schema": {}},
            {"name": "body", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "web_fetch": (
        [
            {"name": "url", "schema": {}, "required": True},
            {"name": "timeout", "schema": {}, "required": False},
            {"name": "max_length", "schema": {}, "required": False},
        ],
        [
            {"name": "url", "schema": {}},
            {"name": "text", "schema": {}},
            {"name": "status_code", "schema": {}},
            {"name": "content_type", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "web_search": (
        [
            {"name": "query", "schema": {}, "required": True},
            {"name": "num_results", "schema": {}, "required": False},
            {"name": "search_depth", "schema": {}, "required": False},
            {"name": "allowed_domains", "schema": {}, "required": False},
            {"name": "blocked_domains", "schema": {}, "required": False},
            {"name": "location", "schema": {}, "required": False},
            {"name": "max_provider_searches", "schema": {}, "required": False},
            {"name": "multi_provider", "schema": {}, "required": False},
        ],
        [
            {"name": "results", "schema": {}},
            {"name": "count", "schema": {}},
            {"name": "provider", "schema": {}},
            {"name": "providers", "schema": {}},
            {"name": "provider_failures", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "compile_latex": (
        [{"name": "content", "schema": {}, "required": True},
         {"name": "title", "schema": {}, "required": True}],
        [{"name": "result", "schema": {}},
         {"name": "pdf_path", "schema": {}},
         {"name": "compile_log", "schema": {}},
         {"name": "compile_success", "schema": {}}],
    ),
    "save_paper": (
        [{"name": "content", "schema": {}, "required": True},
         {"name": "title", "schema": {}, "required": True},
         {"name": "pdf_path", "schema": {}, "required": False}],
        [{"name": "result", "schema": {}},
         {"name": "tex_path", "schema": {}},
         {"name": "bib_path", "schema": {}},
         {"name": "title", "schema": {}}],
    ),
    "package_submission": (
        [{"name": "title", "schema": {}, "required": True},
         {"name": "tex_path", "schema": {}, "required": True},
         {"name": "bib_path", "schema": {}, "required": True}],
        [{"name": "result", "schema": {}}],
    ),
    "citation_verifier": (
        [{"name": "input", "schema": {}, "required": False}],
        [{"name": "result", "schema": {}}],
    ),
    "check_latex_deps": (
        [{"name": "input", "schema": {}, "required": False}],
        [{"name": "result", "schema": {}}],
    ),
    "rag_index_documents": (
        [{"name": "pdf_dir", "schema": {}, "required": True}],
        [{"name": "collection", "schema": {}}, {"name": "result", "schema": {}}],
    ),
    "search_papers": (
        [{"name": "query", "schema": {}, "required": True}],
        [{"name": "result", "schema": {}}],
    ),
}


def _graph_mutator_uses_workers() -> bool:
    return worker_builder_uses_workers(mode=os.environ.get("DAN_WORKER_BUILDER"))


def _workerize_mutation_node_config(
    node_type: str,
    config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if not _graph_mutator_uses_workers() or node_type not in _WORKER_MUTATOR_LEGACY_COMPUTE_TYPES:
        return node_type, config

    worker_config = copy.deepcopy(config)
    metadata = dict(worker_config.pop("metadata", {}) or {})

    if node_type == "llm_operator":
        llm_hints = dict(worker_config.pop("llm_hints", {}) or {})
        for key in (
            "prompt_template",
            "system_prompt",
            "temperature",
            "max_tokens",
            "output_json_schema",
            "tools",
            "max_tool_rounds",
            "history_policy",
            "task_tier",
        ):
            if key in worker_config:
                llm_hints[key] = worker_config.pop(key)
        worker_config["llm_hints"] = llm_hints
        if metadata:
            worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "tool_operator":
        tool_id = worker_config.pop("tool_id", "")
        worker_config["tool_ids"] = [tool_id] if tool_id else []
        tool_config = worker_config.pop("tool_config", None)
        if tool_config:
            metadata["tool_config"] = tool_config
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "code_operator":
        worker_config.pop("sandbox_config", None)
        if metadata:
            worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "rag_operator":
        worker_config["role"] = "rag"
        metadata.update({
            "rag_collection": worker_config.pop("collection", ""),
            "rag_top_k": worker_config.pop("top_k", 5),
            "rag_query_template": worker_config.pop("query_template", "{query}"),
            "rag_include_metadata": worker_config.pop("include_metadata", True),
            "rag_rerank": worker_config.pop("rerank", False),
        })
        if "similarity_threshold" in worker_config:
            metadata["rag_similarity_threshold"] = worker_config.pop("similarity_threshold")
        if worker_config.get("embedding_model"):
            metadata["rag_embedding_model"] = worker_config.pop("embedding_model")
        if worker_config.get("vector_store_config"):
            metadata["rag_vector_store_config"] = worker_config.pop("vector_store_config")
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "router":
        worker_config["role"] = "router"
        metadata["route_descriptions"] = dict(worker_config.pop("route_descriptions", {}) or {})
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "validator":
        worker_config["role"] = "validator"
        worker_config["validation_rules"] = list(worker_config.pop("validation_rules", []))
        metadata["validator_on_failure"] = worker_config.pop("on_failure", "route")
        metadata["validator_strict_mode"] = bool(worker_config.pop("strict_mode", False))
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "reflection":
        worker_config["role"] = "reflection"
        reflection_model = worker_config.pop("reflection_model", None)
        source = worker_config.pop("source", None)
        source_config = worker_config.pop("source_config", None)
        output_format = worker_config.pop("output_format", None)
        max_principles = worker_config.pop("max_principles", None)
        min_confidence = worker_config.pop("min_confidence", None)
        dedup_strategy = worker_config.pop("dedup_strategy", None)
        metadata["reflection_prompt"] = worker_config.pop("reflection_prompt", "")
        if reflection_model is not None:
            worker_config["model"] = reflection_model
            metadata["reflection_model"] = reflection_model
        if source is not None:
            metadata["reflection_source"] = source
        if source_config is not None:
            metadata["reflection_source_config"] = source_config
        if output_format is not None:
            metadata["reflection_output_format"] = output_format
        if max_principles is not None:
            metadata["reflection_max_principles"] = max_principles
        if min_confidence is not None:
            metadata["reflection_min_confidence"] = min_confidence
        if dedup_strategy is not None:
            metadata["reflection_dedup_strategy"] = dedup_strategy
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "input":
        metadata["input_variables"] = worker_config.pop("variables", [])
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type in {"human", "human_in_the_loop"}:
        worker_config["role"] = node_type
        metadata["human_prompt"] = worker_config.pop("prompt", "")
        if "timeout_seconds" in worker_config:
            metadata["human_timeout_seconds"] = worker_config.pop("timeout_seconds")
        if "default_action" in worker_config:
            metadata["human_default_action"] = worker_config.pop("default_action")
        if "input_schema" in worker_config:
            metadata["human_input_schema"] = worker_config.pop("input_schema")
        if "output_schema" in worker_config:
            metadata["human_output_schema"] = worker_config.pop("output_schema")
        metadata["human_render_mode"] = worker_config.pop("render_mode", "text")
        if "options" in worker_config:
            metadata["human_options"] = worker_config.pop("options")
        metadata["human_instructions"] = worker_config.pop("instructions", "")
        metadata["human_render_target"] = worker_config.pop("render_target", "dialog")
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "vote":
        worker_config["role"] = "vote"
        metadata.update({
            "vote_candidates": worker_config.pop("candidates", []),
            "vote_num_votes": worker_config.pop("num_votes", 3),
            "vote_prompt_template": worker_config.pop("prompt_template", ""),
            "vote_system_prompt": worker_config.pop("system_prompt", ""),
            "vote_temperature": worker_config.pop("temperature", 0.7),
            "vote_strategy": worker_config.pop("vote_strategy", "majority"),
            "vote_parallelism": worker_config.pop("parallelism", 3),
        })
        if "output_json_schema" in worker_config:
            metadata["vote_output_json_schema"] = worker_config.pop("output_json_schema")
        if "vote_config" in worker_config:
            metadata["vote_config"] = worker_config.pop("vote_config")
        if "timeout_seconds" in worker_config:
            metadata["vote_timeout_seconds"] = worker_config.pop("timeout_seconds")
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "reduce":
        worker_config["role"] = "reduce"
        metadata["reduce_expression"] = worker_config.pop("reducer", "")
        worker_config["metadata"] = metadata
        return "worker", worker_config

    return node_type, config


def _node_is_input_like(node: dict[str, Any] | None) -> bool:
    if not isinstance(node, dict):
        return False
    node_type = str(node.get("node_type") or "").strip()
    if node_type == "input":
        return True
    if node_type != "worker":
        return False
    metadata = node.get("metadata")
    return isinstance(metadata, dict) and "input_variables" in metadata


def _ensure_input_variable_output_port(
    node: dict[str, Any],
    *,
    port_name: str,
    node_id: str,
) -> str | None:
    """Add a missing named output port for an input-like node when unambiguous."""
    if not _node_is_input_like(node):
        return None

    normalized_port = str(port_name or "").strip()
    if (
        not normalized_port
        or normalized_port == "input"
        or not _INPUT_VARIABLE_NAME_RE.fullmatch(normalized_port)
    ):
        return None

    output_ports = node.setdefault("output_ports", [])
    existing_output_names = {
        str(port.get("name") or "").strip()
        for port in output_ports
        if isinstance(port, dict)
    }
    if normalized_port in existing_output_names:
        return None

    if str(node.get("node_type") or "").strip() == "input":
        variables = node.setdefault("variables", [])
    else:
        metadata = node.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            node["metadata"] = metadata
        variables = metadata.setdefault("input_variables", [])

    existing_variable_names = set()
    if isinstance(variables, list):
        for item in variables:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                existing_variable_names.add(name)
    else:
        variables = []
        if str(node.get("node_type") or "").strip() == "input":
            node["variables"] = variables
        else:
            node.setdefault("metadata", {})["input_variables"] = variables

    if normalized_port not in existing_variable_names:
        variables.append({
            "name": normalized_port,
            "type": "string",
            "default": "",
        })

    output_ports.append({"name": normalized_port, "schema": {}})
    return (
        f"Auto-added input variable/output port '{normalized_port}' on input node "
        f"'{node_id}' because a later edge referenced it."
    )


def _resolve_source_output_port(
    node: dict[str, Any],
    *,
    requested_port: str,
    node_id: str,
    diagnostics: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve/repair a source output port for edge authoring.

    Returns ``(resolved_port, error_message)``. Compatibility repair diagnostics
    are appended to ``diagnostics`` when provided.
    """
    source_ports = [p["name"] for p in node.get("output_ports", [])]
    resolved_port = requested_port
    if resolved_port in source_ports:
        return resolved_port, None

    if _node_is_input_like(node) and resolved_port == "input":
        node.setdefault("output_ports", []).append({"name": "input", "schema": {}})
        msg = (
            f"Auto-created output port 'input' on input node '{node_id}' "
            "(compat for aggregate input wiring)."
        )
        if diagnostics is not None:
            diagnostics.append(msg)
        return resolved_port, None

    repair_message = _ensure_input_variable_output_port(
        node,
        port_name=resolved_port,
        node_id=node_id,
    )
    if repair_message is not None:
        if diagnostics is not None:
            diagnostics.append(repair_message)
        return resolved_port, None

    node_type = str(node.get("node_type") or "")
    source_ports = [p["name"] for p in node.get("output_ports", [])]
    aliased_port = None

    if resolved_port == "response":
        for candidate in ("body", "text", "result"):
            if candidate in source_ports:
                aliased_port = candidate
                break
    elif resolved_port == "output" and "result" in source_ports:
        aliased_port = "result"

    if aliased_port is None:
        alias_map = {
            "for_each": {"item": "results"},
            "parallel_subagents": {"item": "results"},
            "orchestrator": {"item": "results"},
            "if_else": {"branch": "true"},
        }
        aliased_port = alias_map.get(node_type, {}).get(resolved_port)

    if aliased_port and aliased_port in source_ports:
        if diagnostics is not None:
            diagnostics.append(
                f"Normalized source port '{resolved_port}' to '{aliased_port}' "
                f"for {node_type} node '{node_id}'."
            )
        return aliased_port, None

    return None, (
        f"Source node '{node_id}' has no output port '{requested_port}' "
        f"(available: {source_ports})"
    )


def _resolve_target_input_port(
    node: dict[str, Any],
    *,
    requested_port: str,
    node_id: str,
    strict: bool,
    diagnostics: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve or auto-create a target input port during edge application."""
    target_ports = [p["name"] for p in node.get("input_ports", [])]
    if requested_port in target_ports:
        return requested_port, None

    if strict:
        return None, (
            f"Target node '{node_id}' has no input port '{requested_port}'. "
            f"Available ports: {target_ports}. "
            "Use strict=False to auto-create (not recommended)."
        )

    node.setdefault("input_ports", []).append(
        {"name": requested_port, "schema": {}, "required": False}
    )
    message = (
        f"Auto-created input port '{requested_port}' on node '{node_id}' "
        "(port not declared). Verify spelling."
    )
    if diagnostics is not None:
        diagnostics.append(message)
    return requested_port, None


def _default_ports(
    node_type: str, config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return default (input_ports, output_ports) for a node type."""
    table: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {
        "llm_operator": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "text", "schema": {}}],
        ),
        "tool_operator": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "code_operator": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "worker": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "if_else": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "true", "schema": {}},
                {"name": "false", "schema": {}},
            ],
        ),
        "gate": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "true", "schema": {}}, {"name": "false", "schema": {}}],
        ),
        "gate__while": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "continue", "schema": {}}, {"name": "done", "schema": {}}],
        ),
        "while_loop": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "goal_loop": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "result", "schema": {}},
                {"name": "goal_met", "schema": {}},
                {"name": "iterations", "schema": {}},
                {"name": "best_score", "schema": {}},
            ],
        ),
        "for_each": (
            [{"name": "items", "schema": {}, "required": False}],
            [{"name": "results", "schema": {}}],
        ),
        "parallel_subagents": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "results", "schema": {}}],
        ),
        "orchestrator": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "results", "schema": {}}],
        ),
        "reduce": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "router": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "route", "schema": {}}],
        ),
        "human": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "response", "schema": {}}],
        ),
        "human_in_the_loop": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "response", "schema": {}}],
        ),
        "agent_team": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "result", "schema": {}},
                {"name": "agent_contributions", "schema": {}},
                {"name": "consensus_reached", "schema": {}},
                {"name": "total_turns", "schema": {}},
                {"name": "conversation", "schema": {}},
            ],
        ),
        "composite": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "output", "schema": {}}],
        ),
        "input": (
            [],
            [{"name": "input", "schema": {}}],
        ),
        "rag_operator": (
            [{"name": "query", "schema": {}, "required": False}],
            [{"name": "chunks", "schema": {}}, {"name": "scores", "schema": {}}],
        ),
        "validator": (
            [{"name": "data", "schema": {}, "required": False}],
            [{"name": "valid", "schema": {}}, {"name": "invalid", "schema": {}}],
        ),
        "reflection": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "principles", "schema": {}},
                {"name": "principle_count", "schema": {}},
                {"name": "source", "schema": {}},
                {"name": "text", "schema": {}},
            ],
        ),
        "vote": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "winner", "schema": {}},
                {"name": "winner_model", "schema": {}},
                {"name": "winner_index", "schema": {}},
                {"name": "all_votes", "schema": {}},
                {"name": "consensus_reached", "schema": {}},
                {"name": "vote_count", "schema": {}},
                {"name": "total_cost", "schema": {}},
                {"name": "strategy_used", "schema": {}},
            ],
        ),
    }
    fallback: tuple[list[dict[str, Any]], list[dict[str, Any]]] = (
        [{"name": "input", "schema": {}, "required": False}],
        [{"name": "output", "schema": {}}],
    )
    if node_type == "tool_operator" and config:
        tool_id = config.get("tool_id", "")
        if tool_id in TOOL_PORT_MANIFESTS:
            inputs, outputs = TOOL_PORT_MANIFESTS[tool_id]
            return copy.deepcopy(inputs), copy.deepcopy(outputs)

    if node_type == "input" and config and config.get("variables"):
        out_ports = []
        for var in config["variables"]:
            name = var.get("name", "input") if isinstance(var, dict) else str(var)
            out_ports.append({"name": name, "schema": {}})
        if out_ports:
            names = {p["name"] for p in out_ports}
            if "input" not in names:
                out_ports.insert(0, {"name": "input", "schema": {}})
            return [], copy.deepcopy(out_ports)

    lookup_key = node_type
    if node_type == "gate" and config and config.get("gate_mode") == "while":
        lookup_key = "gate__while"
    inputs, outputs = table.get(lookup_key, fallback)
    return copy.deepcopy(inputs), copy.deepcopy(outputs)


def _default_node_config(node_type: str) -> dict[str, Any]:
    """Return sensible default config fields for a node type."""
    table: dict[str, dict[str, Any]] = {
        "llm_operator": {
            "model": "claude-sonnet-4-6",
            "prompt_template": "",
            "system_prompt": "",
            "temperature": 0.7,
        },
        "code_operator": {
            "code": "",
            "language": "python",
            "sandbox_config": {},
        },
        "tool_operator": {
            "tool_id": "",
            "tool_config": {},
        },
        "worker": {
            "role": "",
            "instruction": "",
            "persona": "",
            "authority": "leaf",
            "model": None,
            "tool_ids": [],
            "code": "",
            "language": "python",
            "sub_workers": {},
        },
        "gate": {
            "gate_mode": "if_else",
            "condition": "",
            "max_iterations": 10,
        },
        "if_else": {
            "condition": "",
        },
        "while_loop": {
            "condition": "",
            "body_graph": "",
            "max_iterations": 10,
        },
        "goal_loop": {
            "goal_text": "Reach the target",
            "metric_name": "score",
            "target_value": 1.0,
            "comparison": ">=",
            "max_iterations": 10,
            "evaluator": "llm_judge",
            "success_criteria": None,
            "body_graph": "",
        },
        "for_each": {
            "body_graph": "",
            "parallelism": 1,
            "merge_strategy": "append",
        },
        "parallel_subagents": {
            "branch_graphs": [],
            "parallelism": 1,
            "merge_strategy": "append",
            "input_mappings": {},
            "branch_inputs": {},
        },
        "orchestrator": {
            "teams": {},
            "orchestrator_prompt": "",
            "completion_condition": "all_done",
            "max_iterations": 100,
        },
        "reduce": {
            "reducer": "",
        },
        "router": {
            "model": "claude-sonnet-4-6",
            "route_descriptions": {},
        },
        "human": {
            "prompt": "",
            "timeout_seconds": None,
            "default_action": None,
        },
        "human_in_the_loop": {
            "prompt": "",
            "timeout_seconds": None,
            "default_action": None,
        },
        "agent_team": {
            "agents": {},
            "moderator_prompt": "",
            "turn_strategy": "round_robin",
            "max_turns": 20,
            "completion_condition": "max_turns",
            "shared_context_keys": [],
            "handoff_policy": "explicit",
            "input_mappings": {},
            "agent_inputs": {},
        },
        "composite": {
            "body_graph": "",
            "input_mappings": {},
            "output_mappings": {},
        },
        "rag_operator": {
            "collection": "",
            "top_k": 5,
            "query_template": "{query}",
            "include_metadata": True,
            "rerank": False,
        },
        "validator": {
            "validation_rules": [],
            "on_failure": "route",
            "strict_mode": False,
        },
        "reflection": {
            "reflection_prompt": "",
            "source": "last_run",
            "source_config": {},
            "output_format": "principles",
            "max_principles": 10,
            "min_confidence": 0.3,
            "dedup_strategy": "embedding_similarity",
        },
        "vote": {
            "candidates": ["claude-sonnet-4-6"],
            "num_votes": 3,
            "prompt_template": "",
            "system_prompt": "",
            "temperature": 0.7,
            "vote_strategy": "majority",
            "parallelism": 3,
        },
        "input": {
            "variables": [],
        },
    }
    return copy.deepcopy(table.get(node_type, {}))


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug for node IDs."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "node"


def _generate_node_id(name: str, existing_ids: set[str]) -> str:
    """Generate a unique node ID from a name, avoiding collisions."""
    base = _slugify(name)
    if base not in existing_ids:
        return base
    counter = 2
    while f"{base}-{counter}" in existing_ids:
        counter += 1
    return f"{base}-{counter}"
