"""Canonical helpers for mutation-preview compilation and repair flows."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any, Callable, Sequence

from dan.agent_runtime.followup import build_assistant_followup_message
from dan.agent_runtime.graph_summary import serialize_for_prompt
from dan.graph_mutator import (
    GraphMutator,
    MutationPlan,
    MutationResult,
    OperationError,
    TOOL_PORT_MANIFESTS,
)
from dan.workflow_generation_guidance import (
    render_workflow_generation_contract,
    workflow_generation_contract_enabled,
)

logger = logging.getLogger(__name__)
_CHAT_NODE_ID_RE = re.compile(r"[^a-z0-9]+")
_STALE_REPLAN_PROMPT = (
    "The graph has changed since your last plan. "
    "Please re-plan the requested changes against "
    "the updated workflow."
)


@dataclass(frozen=True)
class CompiledMutationPreview:
    """Normalized mutation payload plus the validated plan and dry-run result."""

    plan_payload: dict[str, Any]
    plan: Any | None
    dry_result: Any

    def __iter__(self):
        yield self.plan_payload
        yield self.plan
        yield self.dry_result


@dataclass(frozen=True)
class PreparedMutationAutoApply:
    """Auto-apply evaluation result before any graph-store write occurs."""

    status: str
    graph_to_save: dict[str, Any] | None = None
    validation_errors: tuple[str, ...] = ()
    contract_report: Any | None = None


def resolve_mutation_auto_apply_requested(
    *,
    explicit_auto_apply: bool | None,
    is_empty_graph: bool,
    generation_fallback_active: bool,
    dry_result: Any,
    plan: Any | None,
) -> bool:
    """Decide whether a mutation preview should be auto-applied.

    Normal mutation previews remain opt-in. The one exception is the
    empty-graph build lane after the fast workflow-generation path already
    failed: if the fallback mutation preview is dry-run clean, treat it like a
    recovered build and auto-apply it.
    """

    if explicit_auto_apply is True:
        return True
    if explicit_auto_apply is False:
        return False
    if not generation_fallback_active or not is_empty_graph:
        return False
    if plan is None:
        return False
    if not getattr(dry_result, "success", False):
        return False
    return getattr(dry_result, "new_graph", None) is not None


def _coerce_strict_edges(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For build-mode (empty-graph) flows, enforce strict edge validation."""
    result = []
    for op in operations:
        op = dict(op)
        if op.get("op") == "add_edge":
            op.setdefault("strict", True)
        result.append(op)
    return result


def _normalize_generated_mutation_ops(
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize common LLM schema drifts in mutation operations."""

    def normalize_one(raw: dict[str, Any]) -> dict[str, Any]:
        op_norm = dict(raw)
        kind = str(op_norm.get("op") or "").strip()

        if not kind and (
            op_norm.get("node_type")
            or op_norm.get("type")
        ) and (op_norm.get("id") or op_norm.get("name")):
            kind = "add_node"
            op_norm["op"] = kind

        if kind == "set_body_graph":
            kind = "replace_body_graph"
            op_norm["op"] = kind

        if kind == "replace_body_graph":
            inner = op_norm.get("operations")
            if isinstance(inner, list):
                op_norm["operations"] = [
                    normalize_one(item)
                    for item in inner
                    if isinstance(item, dict)
                ]
            return op_norm

        if kind == "add_edge":
            cfg = op_norm.get("config")
            if isinstance(cfg, dict):
                for key in ("source_id", "source_port", "target_id", "target_port"):
                    if key not in op_norm and key in cfg:
                        op_norm[key] = cfg[key]
                op_norm.pop("config", None)
            return op_norm

        if kind != "add_node":
            return op_norm

        if "node_type" not in op_norm and isinstance(op_norm.get("type"), str):
            op_norm["node_type"] = op_norm["type"]
        op_norm.pop("type", None)

        config = op_norm.get("config")
        if not isinstance(config, dict):
            return op_norm
        cfg = dict(config)
        node_type = str(op_norm.get("node_type") or "")

        if node_type == "tool_operator":
            tool_id = cfg.get("tool_id")
            if not tool_id and isinstance(cfg.get("tool"), str):
                cfg["tool_id"] = cfg.pop("tool")
            elif "tool" in cfg:
                cfg.pop("tool", None)
            nested_tool_cfg = cfg.get("config")
            if "tool_config" not in cfg and isinstance(nested_tool_cfg, dict):
                cfg["tool_config"] = nested_tool_cfg
                cfg.pop("config", None)

        elif node_type == "parallel_subagents":
            branch_graphs = cfg.get("branch_graphs")
            if isinstance(branch_graphs, dict):
                cfg["branch_graphs"] = list(branch_graphs.keys())
            elif isinstance(branch_graphs, str):
                cfg["branch_graphs"] = [branch_graphs]

            branch_inputs = cfg.get("branch_inputs")
            if isinstance(branch_inputs, dict):
                remapped: dict[str, dict[str, Any]] = {}
                for branch, val in branch_inputs.items():
                    if isinstance(val, dict):
                        remapped[branch] = val
                    else:
                        remapped[branch] = {"input": val}
                cfg["branch_inputs"] = remapped

        elif node_type == "validator":
            on_failure = cfg.get("on_failure")
            if isinstance(on_failure, str):
                alias = {
                    "retry": "route",
                    "continue": "warn",
                    "ignore": "warn",
                    "stop": "halt",
                    "fail": "halt",
                }
                cfg["on_failure"] = alias.get(on_failure, on_failure)

            rules = cfg.get("validation_rules")
            if isinstance(rules, list):
                fixed_rules: list[dict[str, Any]] = []
                for item in rules:
                    if not isinstance(item, dict):
                        continue
                    rule = dict(item)
                    rule_type = rule.get("rule_type")
                    if rule_type == "format_check":
                        rule["rule_type"] = "required_keys"
                        rule_cfg = rule.get("config")
                        if not isinstance(rule_cfg, dict):
                            rule_cfg = {}
                        rule_cfg.setdefault("keys", [])
                        rule["config"] = rule_cfg
                        rule_type = rule["rule_type"]
                    if rule_type in ("required_field", "required_fields"):
                        rule["rule_type"] = "required_keys"
                        rule_cfg = rule.get("config")
                        if not isinstance(rule_cfg, dict):
                            rule_cfg = {}
                        if "keys" not in rule_cfg:
                            if isinstance(rule_cfg.get("field"), str):
                                rule_cfg["keys"] = [rule_cfg["field"]]
                            elif isinstance(rule_cfg.get("key"), str):
                                rule_cfg["keys"] = [rule_cfg["key"]]
                            elif isinstance(rule_cfg.get("fields"), list):
                                rule_cfg["keys"] = list(rule_cfg["fields"])
                            elif isinstance(rule_cfg.get("required"), list):
                                rule_cfg["keys"] = list(rule_cfg["required"])
                            elif isinstance(rule.get("field"), str):
                                rule_cfg["keys"] = [rule["field"]]
                            elif isinstance(rule.get("key"), str):
                                rule_cfg["keys"] = [rule["key"]]
                        rule["config"] = rule_cfg
                    fixed_rules.append(rule)
                cfg["validation_rules"] = fixed_rules

        op_norm["config"] = cfg
        return op_norm

    normalized: list[dict[str, Any]] = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        normalized.append(normalize_one(op))
    return normalized


def _suggest_chat_node_id(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    candidate = _CHAT_NODE_ID_RE.sub("-", raw.lower()).strip("-")
    if not candidate:
        return ""
    return candidate


def _chat_node_id_aliases(*values: str) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        raw = str(value or "").strip()
        if not raw:
            continue
        aliases.add(raw)
        aliases.add(raw.lower())
        suggested = _suggest_chat_node_id(raw)
        if suggested:
            aliases.add(suggested)
            aliases.add(suggested.lower())
        slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
        if slug:
            aliases.add(slug)
    return aliases


def _repair_missing_node_ids(
    operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    diagnostics: list[str] = []

    def walk(ops: list[Any]) -> list[dict[str, Any]]:
        alias_map: dict[str, str] = {}
        used_ids: set[str] = set()
        normalized: list[dict[str, Any]] = []

        for raw in ops:
            if not isinstance(raw, dict):
                continue
            op = dict(raw)
            kind = str(op.get("op") or "").strip()

            if kind == "replace_body_graph":
                inner = op.get("operations")
                if isinstance(inner, list):
                    op["operations"] = walk(inner)
                normalized.append(op)
                continue

            if kind == "add_node":
                current_id = str(op.get("id") or "").strip()
                name = str(op.get("name") or "").strip()
                if not current_id and name:
                    suggested = _suggest_chat_node_id(name)
                    if suggested:
                        candidate = suggested
                        suffix = 2
                        while candidate in used_ids:
                            candidate = f"{suggested}_{suffix}"
                            suffix += 1
                        op["id"] = candidate
                        current_id = candidate
                        diagnostics.append(
                            f"Filled missing add_node id for '{name}' with '{candidate}'."
                        )
                        logger.info(
                            "Mutation self-repair: filled missing add_node id for %r with %r",
                            name,
                            candidate,
                        )
                if current_id:
                    used_ids.add(current_id)
                    for alias in _chat_node_id_aliases(current_id):
                        alias_map[alias] = current_id
                if name and current_id:
                    for alias in _chat_node_id_aliases(name):
                        alias_map[alias] = current_id
                normalized.append(op)
                continue

            normalized.append(op)

        repaired: list[dict[str, Any]] = []
        for raw in normalized:
            op = dict(raw)
            if str(op.get("op") or "").strip() == "add_edge":
                for field in ("source_id", "target_id"):
                    ref = str(op.get(field) or "").strip()
                    replacement = alias_map.get(ref)
                    if replacement is None:
                        replacement = alias_map.get(ref.lower())
                    if replacement and replacement != ref:
                        op[field] = replacement
                        diagnostics.append(
                            f"Rewrote {field} reference '{ref}' to '{replacement}'."
                        )
                        logger.info(
                            "Mutation self-repair: rewrote %s reference %r to %r",
                            field,
                            ref,
                            replacement,
                        )
            repaired.append(op)
        return repaired

    return walk(operations), diagnostics


def _graph_node_id_types(graph: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and node.get("id"):
            out[str(node["id"])] = str(node.get("node_type") or "")
    return out


def _edge_key_from_graph_edge(edge: dict[str, Any]) -> tuple[str, str, str, str] | None:
    source_id = str(edge.get("source_node_id") or "").strip()
    source_port = str(edge.get("source_port") or "").strip()
    target_id = str(edge.get("target_node_id") or "").strip()
    target_port = str(edge.get("target_port") or "").strip()
    if not (source_id and source_port and target_id and target_port):
        return None
    return (source_id, source_port, target_id, target_port)


def _edge_key_from_op(op: dict[str, Any]) -> tuple[str, str, str, str] | None:
    source_id = str(op.get("source_id") or "").strip()
    source_port = str(op.get("source_port") or "").strip()
    target_id = str(op.get("target_id") or "").strip()
    target_port = str(op.get("target_port") or "").strip()
    if not (source_id and source_port and target_id and target_port):
        return None
    return (source_id, source_port, target_id, target_port)


def _edge_semantics_from_graph_edge(edge: dict[str, Any]) -> tuple[str, bool]:
    return (
        str(edge.get("edge_type") or "data"),
        bool(edge.get("spread", False)),
    )


def _edge_semantics_from_op(op: dict[str, Any]) -> tuple[str, bool]:
    return (
        str(op.get("edge_type") or "data"),
        bool(op.get("spread", False)),
    )


def _node_output_ports(node: dict[str, Any]) -> list[str]:
    ports = node.get("output_ports")
    if not isinstance(ports, list):
        return []
    out: list[str] = []
    for port in ports:
        if isinstance(port, dict):
            name = str(port.get("name") or "").strip()
            if name:
                out.append(name)
    return out


def _existing_body_graph(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    if not node_id:
        return None
    body_key: str | None = None
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("id") or "").strip() != node_id:
            continue
        candidate = str(node.get("body_graph") or "").strip()
        if candidate:
            body_key = candidate
        break
    if body_key is None:
        body_key = f"{node_id}__body"
    body = (graph.get("sub_graphs") or {}).get(body_key)
    return body if isinstance(body, dict) else None


def _infer_added_node_spec(op: dict[str, Any]) -> dict[str, Any] | None:
    if str(op.get("op") or "").strip() != "add_node":
        return None
    node_id = str(op.get("id") or "").strip()
    if not node_id:
        return None
    node_type = str(op.get("node_type") or "").strip()
    cfg = op.get("config")
    config = cfg if isinstance(cfg, dict) else {}
    ports: list[str] = []
    tool_id = ""

    if node_type == "tool_operator":
        tool_id = str(config.get("tool_id") or config.get("tool") or "").strip()
        manifest = TOOL_PORT_MANIFESTS.get(tool_id)
        if manifest is not None:
            ports = [
                str(item.get("name") or "").strip()
                for item in manifest[1]
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]

    if not ports:
        outputs = config.get("outputs")
        if isinstance(outputs, list):
            for item in outputs:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                else:
                    name = str(item or "").strip()
                if name:
                    ports.append(name)

    if not ports:
        raw_ports = op.get("output_ports")
        if isinstance(raw_ports, list):
            for item in raw_ports:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                else:
                    name = str(item or "").strip()
                if name:
                    ports.append(name)

    return {
        "node_id": node_id,
        "node_type": node_type,
        "tool_id": tool_id,
        "output_ports": ports,
    }


def _seed_node_specs_from_graph(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        specs[node_id] = {
            "node_id": node_id,
            "node_type": str(node.get("node_type") or "").strip(),
            "tool_id": str(node.get("tool_id") or "").strip(),
            "output_ports": _node_output_ports(node),
        }
    return specs


def _repair_stale_source_port_aliases(
    graph: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    diagnostics: list[str] = []

    def choose_alias(requested: str, spec: dict[str, Any] | None) -> str | None:
        if spec is None:
            return None
        ports = [str(port).strip() for port in spec.get("output_ports") or [] if str(port).strip()]
        if not ports or requested in ports:
            return None
        requested_lc = requested.lower()
        tool_id = str(spec.get("tool_id") or "").strip()

        if requested_lc == "response":
            if tool_id == "http_request" and "body" in ports:
                return "body"
            if tool_id == "web_fetch" and "text" in ports:
                return "text"
            if "result" in ports:
                return "result"

        if requested_lc == "output" and "result" in ports:
            return "result"

        return None

    def walk(
        ops: list[Any],
        known_specs: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        local_specs = dict(known_specs)
        repaired: list[dict[str, Any]] = []
        for raw in ops:
            if not isinstance(raw, dict):
                continue
            op = dict(raw)
            kind = str(op.get("op") or "").strip()

            if kind == "replace_body_graph":
                inner = op.get("operations")
                if isinstance(inner, list):
                    body_graph = _existing_body_graph(graph, str(op.get("node_id") or "").strip()) or {}
                    op["operations"] = walk(inner, _seed_node_specs_from_graph(body_graph))
                repaired.append(op)
                continue

            added_spec = _infer_added_node_spec(op)
            if added_spec is not None:
                local_specs[str(added_spec["node_id"])] = added_spec
                repaired.append(op)
                continue

            if kind == "add_edge":
                source_id = str(op.get("source_id") or "").strip()
                source_port = str(op.get("source_port") or "").strip()
                replacement = choose_alias(source_port, local_specs.get(source_id))
                if replacement and replacement != source_port:
                    op["source_port"] = replacement
                    message = (
                        f"Rewrote stale source_port '{source_port}' to '{replacement}' "
                        f"for node '{source_id}'."
                    )
                    diagnostics.append(message)
                    logger.info("Mutation self-repair: %s", message)
                repaired.append(op)
                continue

            repaired.append(op)
        return repaired

    return walk(operations, _seed_node_specs_from_graph(graph)), diagnostics


def _repair_duplicate_add_edge_operations(
    graph: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop redundant add_edge ops when the exact edge already exists."""
    diagnostics: list[str] = []
    edge_state: dict[tuple[str, str, str, str], tuple[str, bool]] = {}

    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        key = _edge_key_from_graph_edge(edge)
        if key is None:
            continue
        edge_state[key] = _edge_semantics_from_graph_edge(edge)

    def walk(
        ops: list[Any],
        active_edges: dict[tuple[str, str, str, str], tuple[str, bool]],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in ops:
            if not isinstance(raw, dict):
                continue
            op = dict(raw)
            kind = str(op.get("op") or "").strip()

            if kind == "replace_body_graph":
                inner = op.get("operations")
                if isinstance(inner, list):
                    op["operations"] = walk(inner, {})
                out.append(op)
                continue

            if kind == "remove_edge":
                key = _edge_key_from_op(op)
                if key is not None:
                    active_edges.pop(key, None)
                out.append(op)
                continue

            if kind == "remove_node":
                node_id = str(op.get("node_id") or "").strip()
                if node_id:
                    active_edges = {
                        key: semantics
                        for key, semantics in active_edges.items()
                        if key[0] != node_id and key[2] != node_id
                    }
                out.append(op)
                continue

            if kind == "add_edge":
                key = _edge_key_from_op(op)
                if key is not None:
                    semantics = _edge_semantics_from_op(op)
                    existing = active_edges.get(key)
                    if existing == semantics:
                        message = (
                            "Dropped redundant add_edge "
                            f"'{key[0]}.{key[1]}->{key[2]}.{key[3]}' because it already exists."
                        )
                        diagnostics.append(message)
                        logger.info("Mutation self-repair: %s", message)
                        continue
                    active_edges[key] = semantics
                out.append(op)
                continue

            out.append(op)
        return out

    repaired = walk(operations, edge_state)
    return repaired, diagnostics


def _repair_duplicate_add_node_operations(
    graph: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Turn duplicate add_node ops into edit_node when safe."""
    diagnostics: list[str] = []

    def walk(
        ops: list[Any],
        seen: set[str],
        types: dict[str, str],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in ops:
            if not isinstance(raw, dict):
                continue
            op = dict(raw)
            kind = op.get("op")

            if kind == "replace_body_graph":
                inner = op.get("operations")
                if isinstance(inner, list):
                    op["operations"] = walk(inner, set(), {})
                out.append(op)
                continue

            if kind == "remove_node":
                node_id = str(op.get("node_id") or "").strip()
                if node_id:
                    seen.discard(node_id)
                    types.pop(node_id, None)
                out.append(op)
                continue

            if kind == "add_node":
                node_id = str(op.get("id") or "").strip()
                node_type = str(op.get("node_type") or "")
                if node_id and node_id in seen:
                    existing = types.get(node_id, "")
                    if existing and node_type and existing != node_type:
                        message = (
                            f"Rejected duplicate add_node self-repair for '{node_id}' because "
                            f"node_type would change from {existing!r} to {node_type!r}; leaving "
                            "the operation unchanged so dry-run can fail and trigger re-planning."
                        )
                        diagnostics.append(message)
                        logger.info("Mutation self-repair: %s", message)
                        out.append(op)
                        continue
                    updates: dict[str, Any] = {}
                    if op.get("name"):
                        updates["name"] = op["name"]
                    cfg = op.get("config")
                    if isinstance(cfg, dict) and cfg:
                        updates["config"] = cfg
                    message = (
                        f"Coerced add_node '{node_id}' to edit_node (id already exists in workflow "
                        f"or earlier in this plan)."
                    )
                    diagnostics.append(message)
                    logger.info("Mutation self-repair: %s", message)
                    out.append({
                        "op": "edit_node",
                        "node_id": node_id,
                        "updates": updates,
                    })
                    continue
                if node_id:
                    seen.add(node_id)
                    types[node_id] = node_type
                out.append(op)
                continue

            out.append(op)
        return out

    id_types = _graph_node_id_types(graph)
    repaired = walk(operations, set(id_types.keys()), dict(id_types))
    return repaired, diagnostics


def normalize_mutation_ops_for_chat(
    graph: dict[str, Any] | None,
    operations: list[dict[str, Any]],
    *,
    is_empty_graph: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize LLM mutation ops, apply self-repairs, and force strict edges for builds."""
    ops = _normalize_generated_mutation_ops(operations)
    ops, id_repairs = _repair_missing_node_ids(ops)
    all_repairs = list(id_repairs)
    if graph is not None:
        ops, port_repairs = _repair_stale_source_port_aliases(graph, ops)
        all_repairs.extend(port_repairs)
        ops, duplicate_repairs = _repair_duplicate_add_node_operations(graph, ops)
        all_repairs.extend(duplicate_repairs)
        ops, edge_repairs = _repair_duplicate_add_edge_operations(graph, ops)
        all_repairs.extend(edge_repairs)
    if is_empty_graph:
        ops = _coerce_strict_edges(ops)
    return ops, all_repairs


def _build_compilation_error_result(error_message: str) -> MutationResult:
    return MutationResult(
        success=False,
        new_graph=None,
        errors=[
            OperationError(
                op_index=-1,
                op_type="compilation",
                message=error_message,
            )
        ],
    )


def compile_mutation_preview(
    mutation_payload: dict[str, Any],
    *,
    graph_snapshot: dict[str, Any],
    base_revision: str,
    is_empty_graph: bool,
    normalize_ops: Callable[..., tuple[list[dict[str, Any]], list[str]]] | None = None,
    normalize_mutation_ops: Callable[..., tuple[list[dict[str, Any]], list[str]]] | None = None,
    validate_plan: Callable[[dict[str, Any]], Any] | None = None,
    dry_run: Callable[[dict[str, Any], Any, str], Any] | None = None,
    make_failed_result: Callable[[str], Any] | None = None,
    mutator_factory: Callable[[], Any] | None = None,
    log_repair: Callable[[str], None] | None = None,
    logger_override: logging.Logger | None = None,
    log: logging.Logger | None = None,
) -> CompiledMutationPreview:
    """Normalize, validate, and dry-run one mutation payload."""
    active_log = logger_override or log or logger
    normalize_ops = normalize_mutation_ops or normalize_ops or normalize_mutation_ops_for_chat
    if log_repair is None:
        log_repair = lambda line: active_log.info("%s", line)
    if validate_plan is None:
        validate_plan = MutationPlan.model_validate
    if dry_run is None:
        mutator_factory = mutator_factory or GraphMutator

        def _default_dry_run(graph: dict[str, Any], plan: Any, revision: str) -> Any:
            return mutator_factory().dry_run(
                graph,
                plan,
                current_revision=revision,
            )

        dry_run = _default_dry_run
    if make_failed_result is None:
        make_failed_result = _build_compilation_error_result
    fallback_payload = {
        "operations": mutation_payload.get("operations", []),
        "description": mutation_payload.get("description", ""),
        "reasoning": mutation_payload.get("reasoning", ""),
        "base_graph_revision": base_revision,
    }
    try:
        try:
            ops, mechanical_repairs = normalize_ops(
                graph_snapshot,
                mutation_payload.get("operations", []),
                is_empty_graph=is_empty_graph,
            )
        except TypeError as exc:
            signature_error = str(exc)
            if (
                "is_empty_graph" not in signature_error
                and "unexpected keyword" not in signature_error
                and "positional" not in signature_error
            ):
                raise
            ops, mechanical_repairs = normalize_ops(
                graph_snapshot,
                mutation_payload.get("operations", []),
                is_empty_graph,
            )
        for line in mechanical_repairs:
            log_repair(line)
        plan_payload = {
            "operations": ops,
            "mechanical_repairs": mechanical_repairs,
            "description": mutation_payload.get("description", ""),
            "reasoning": mutation_payload.get("reasoning", ""),
            "base_graph_revision": base_revision,
        }
        plan = validate_plan(plan_payload)
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        return CompiledMutationPreview(
            plan_payload=fallback_payload,
            plan=None,
            dry_result=make_failed_result(error_message),
        )

    try:
        dry_result = dry_run(graph_snapshot, plan, base_revision)
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        dry_result = make_failed_result(error_message)
    return CompiledMutationPreview(
        plan_payload=plan_payload,
        plan=plan,
        dry_result=dry_result,
    )


def build_mutation_repair_messages(
    *,
    user_message: str,
    graph_summary: Any | None = None,
    workflow_summary: Any | None = None,
    current_mutation: dict[str, Any],
    current_plan_payload: dict[str, Any],
    current_dry_result: Any,
    contract_enabled: Callable[[], bool] | None = None,
    render_contract: Callable[..., str] | None = None,
    serialize_summary: Callable[[Any], str] | None = None,
) -> list[dict[str, str]]:
    """Build the internal repair prompt for a failed mutation preview."""
    summary_value = graph_summary if graph_summary is not None else workflow_summary
    if summary_value is None:
        raise ValueError("graph_summary or workflow_summary is required")
    contract_enabled = contract_enabled or workflow_generation_contract_enabled
    render_contract = render_contract or render_workflow_generation_contract
    serialize_summary = serialize_summary or serialize_for_prompt
    base = (
        "You repair DAN workflow mutation plans. Preserve the user's requested workflow "
        "behavior and only fix mechanical graph-compilation, schema, or validation issues "
        "in the plan. Do not broaden scope, do not ask the user for clarification, and "
        "do not change the requested outcome unless a minimal structural adjustment is "
        "strictly required for a valid graph."
    )
    if not contract_enabled():
        system_content = (
            f"{base} Return ONLY a `plan_graph_mutations` tool call or a JSON object "
            "matching that tool."
        )
    else:
        system_content = (
            f"{base}\n\n"
            f"{render_contract('repair', tools_available=False)}\n\n"
            "Return ONLY a `plan_graph_mutations` tool call or a JSON object matching that tool."
        )

    error_payload = {
        "errors": [
            error.model_dump() if hasattr(error, "model_dump") else str(error)
            for error in getattr(current_dry_result, "errors", []) or []
        ],
        "diagnostics": list(getattr(current_dry_result, "diagnostics", []) or []),
        "stale_plan": bool(getattr(current_dry_result, "stale_plan", False)),
    }
    return [
        {"role": "system", "content": system_content},
        {
            "role": "user",
            "content": (
                f"User request:\n{user_message}\n\n"
                f"Current workflow summary:\n{serialize_summary(summary_value)}\n\n"
                "Current mutation proposal:\n"
                f"```json\n{json.dumps(current_plan_payload or current_mutation, indent=2)}\n```\n\n"
                "Compilation or validation failures:\n"
                f"```json\n{json.dumps(error_payload, indent=2)}\n```\n\n"
                "Produce a corrected `plan_graph_mutations` call that keeps the same requested "
                "workflow behavior while fixing only the mechanical issues above."
            ),
        },
    ]


def build_stale_replan_messages(
    base_messages: Sequence[dict[str, Any]],
    *,
    prompt: str = _STALE_REPLAN_PROMPT,
) -> list[dict[str, Any]]:
    """Append the stale-plan replan instruction to an existing message set."""
    messages = list(base_messages)
    messages.append({"role": "user", "content": prompt})
    return messages


def build_auto_apply_followup_messages(
    *,
    messages: Sequence[dict[str, Any]],
    assistant_text: str,
    mutation_tool_call: dict[str, Any] | None,
    tool_call_id: str,
    apply_result_text: str,
) -> list[dict[str, Any]]:
    """Append the follow-up assistant/tool or user continuation after auto-apply."""
    prepared_messages = list(messages)
    if mutation_tool_call is not None:
        followup_tool_call_id = str(mutation_tool_call.get("id") or tool_call_id)
        assistant_msg = build_assistant_followup_message(
            text=assistant_text,
            tool_calls=[mutation_tool_call],
        )
        if assistant_msg is not None:
            prepared_messages.append(assistant_msg)
        prepared_messages.append({
            "role": "tool",
            "tool_call_id": followup_tool_call_id,
            "content": apply_result_text,
        })
        return prepared_messages

    if assistant_text.strip():
        prepared_messages.append({"role": "assistant", "content": assistant_text})
    prepared_messages.append({
        "role": "user",
        "content": "The workflow mutation was applied automatically. " + apply_result_text,
    })
    return prepared_messages


def prepare_mutation_auto_apply(
    *,
    auto_apply_requested: bool,
    dry_result: Any,
    graph_snapshot: dict[str, Any],
    plan: Any | None,
    current_revision: str,
    apply_mutation: Callable[[dict[str, Any], Any, str], Any],
    validate_graph: Callable[[dict[str, Any]], Any],
    collect_contract_errors: Callable[..., list[str]] | None = None,
    blocked_default_message: str = (
        "Auto-apply was blocked because the workflow is not run-ready."
    ),
) -> PreparedMutationAutoApply:
    """Evaluate auto-apply viability up to the point of saving the graph."""
    collect_contract_errors = collect_contract_errors or workflow_contract_errors
    if (
        not auto_apply_requested
        or not getattr(dry_result, "success", False)
        or getattr(dry_result, "new_graph", None) is None
        or plan is None
    ):
        return PreparedMutationAutoApply(status="skipped")

    apply_result = apply_mutation(graph_snapshot, plan, current_revision)
    if not getattr(apply_result, "success", False) or getattr(apply_result, "new_graph", None) is None:
        return PreparedMutationAutoApply(status="apply_failed")

    try:
        contract_report = validate_graph(apply_result.new_graph)
    except Exception as exc:
        return PreparedMutationAutoApply(
            status="validation_error",
            validation_errors=(str(exc),),
        )

    if getattr(contract_report, "validated", False) and getattr(contract_report, "run_ready", False):
        return PreparedMutationAutoApply(
            status="ready_to_save",
            graph_to_save=contract_report.graph_dict or apply_result.new_graph,
            contract_report=contract_report,
        )

    return PreparedMutationAutoApply(
        status="blocked",
        validation_errors=tuple(
            collect_contract_errors(
                contract_report,
                default_message=blocked_default_message,
            )
        ),
        contract_report=contract_report,
    )


def format_mutation_preview_content(
    *,
    description: str,
    dry_result: Any,
    is_empty_graph: bool,
    applied: bool = False,
) -> str:
    """Render the final preview/apply status text shown in chat."""
    preview_kind = "workflow build preview" if is_empty_graph else "workflow update preview"
    summary = str(description or "").strip()

    if applied and getattr(dry_result, "success", False):
        action = "Built" if is_empty_graph else "Updated"
        lead = f"{action} and applied the workflow. Dry-run validation passed."
        tail = "The workflow is saved, validated, and ready to run."
    elif getattr(dry_result, "success", False):
        lead = f"Prepared a {preview_kind}. Dry-run validation passed."
        tail = "These changes are proposed, not applied yet."
    elif getattr(dry_result, "stale_plan", False):
        lead = f"Prepared a {preview_kind}, but it is based on a stale graph revision."
        tail = "These changes are still only proposed, not applied yet."
    else:
        lead = f"Prepared a {preview_kind}. Dry-run validation found issues."
        tail = "These changes are proposed, not applied yet."

    if summary:
        return f"{lead} {tail}\n\nPlanned changes: {summary}"
    return f"{lead} {tail}"


def workflow_contract_errors(
    contract_report: Any,
    *,
    default_message: str,
) -> list[str]:
    """Collect the most useful workflow-contract validation errors."""
    errors: list[str] = []
    for issue in getattr(contract_report, "errors", [])[:5]:
        message = str(getattr(issue, "message", "") or "").strip()
        if message:
            errors.append(message)
    for issue in getattr(contract_report, "run_readiness_issues", [])[:5]:
        message = str(issue or "").strip()
        if message and message not in errors:
            errors.append(message)
    if errors:
        return errors
    return [default_message]


__all__ = [
    "CompiledMutationPreview",
    "PreparedMutationAutoApply",
    "_coerce_strict_edges",
    "_normalize_generated_mutation_ops",
    "build_auto_apply_followup_messages",
    "build_mutation_repair_messages",
    "build_stale_replan_messages",
    "compile_mutation_preview",
    "format_mutation_preview_content",
    "normalize_mutation_ops_for_chat",
    "prepare_mutation_auto_apply",
    "resolve_mutation_auto_apply_requested",
    "workflow_contract_errors",
]
