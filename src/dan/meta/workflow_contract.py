"""Workflow build-boundary validation and bounded mechanical repair."""

from __future__ import annotations

import ast
import copy
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from dan.models.graph import Graph
from dan.validation.graph import validate_graph

IssueCategory = Literal[
    "workflow_identity",
    "workflow_metadata",
    "node_identity",
    "port_endpoint",
    "schema",
    "taxonomy",
    "subgraph",
    "entry_exit",
    "run_readiness",
]
IssueSeverity = Literal["repairable", "fatal"]
FailureBucket = Literal[
    "mechanical_auto_fix",
    "semantic_reprompt_or_diagnosis",
    "hard_fail",
]

_WARNING_KEYWORDS = ("warning", "deprecated", "untyped")
_PLACEHOLDER_STATUS_VALUE = "placeholder"
_FOREACH_ITERABLE_PORTS = frozenset({"items", "input", "data"})
_LLM_EXTERNAL_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:web search|search the web|browse the web|search online)\b", re.IGNORECASE),
        "web search or browsing",
    ),
    (
        re.compile(
            r"\b(?:fetch|download|pull|retrieve|scrape)\b.*\b(?:api|apis|url|website|web|news|data)\b",
            re.IGNORECASE,
        ),
        "external data retrieval",
    ),
    (
        re.compile(
            r"\b(?:read|load|open|parse)\b.*\b(?:file|files|csv|pdf|spreadsheet|folder|directory|path)\b",
            re.IGNORECASE,
        ),
        "file or document access",
    ),
    (
        re.compile(
            r"\b(?:write|save|store|archive|persist|export|append)\b.*\b(?:file|files|path|paths|folder|directory|csv|json|markdown|disk|archive)\b",
            re.IGNORECASE,
        ),
        "file persistence",
    ),
)


@dataclass(frozen=True)
class WorkflowBuildIssue:
    category: IssueCategory
    message: str
    severity: IssueSeverity = "repairable"
    artifact_id: str | None = None


@dataclass
class WorkflowBuildContractReport:
    workflow_id: str = ""
    normalized_workflow_id: str = ""
    display_name: str = ""
    validated: bool = False
    run_ready: bool = False
    graph: Graph | None = None
    graph_dict: dict[str, Any] | None = None
    errors: list[WorkflowBuildIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    auto_fixes_applied: list[str] = field(default_factory=list)
    run_readiness_issues: list[str] = field(default_factory=list)


def workflow_build_status(
    report: Any,
) -> Literal["invalid", "validated", "run_ready"] | None:
    if report is None:
        return None
    if bool(getattr(report, "validated", False)):
        if bool(getattr(report, "run_ready", False)):
            return "run_ready"
        return "validated"
    return "invalid"


def workflow_failure_bucket(report: Any) -> FailureBucket | None:
    if report is None:
        return None
    if bool(getattr(report, "validated", False)) and bool(getattr(report, "run_ready", False)):
        return None

    errors = list(getattr(report, "errors", []) or [])
    if any(str(getattr(issue, "severity", "") or "").strip().lower() == "fatal" for issue in errors):
        return "hard_fail"

    categories = {
        str(getattr(issue, "category", "") or "").strip()
        for issue in errors
    }
    mechanical_categories = {
        "workflow_identity",
        "workflow_metadata",
        "node_identity",
        "port_endpoint",
        "schema",
        "taxonomy",
        "subgraph",
        "entry_exit",
    }
    if categories and categories.issubset(mechanical_categories):
        return "mechanical_auto_fix"
    if list(getattr(report, "run_readiness_issues", []) or []) or "run_readiness" in categories:
        return "semantic_reprompt_or_diagnosis"
    if errors:
        return "mechanical_auto_fix"
    return "hard_fail"


def workflow_handoff_reason(report: Any) -> str | None:
    bucket = workflow_failure_bucket(report)
    if bucket is None:
        return None

    errors = list(getattr(report, "errors", []) or [])
    categories = {
        str(getattr(issue, "category", "") or "").strip()
        for issue in errors
    }
    messages = [
        str(getattr(issue, "message", "") or "").strip().lower()
        for issue in errors
    ]

    if bucket == "hard_fail":
        if "schema" in categories:
            return "fatal_schema_error"
        if any("duplicate node id" in message for message in messages):
            return "duplicate_node_id"
        return "fatal_contract_error"
    if bucket == "mechanical_auto_fix":
        return "repairable_contract_error"
    if list(getattr(report, "run_readiness_issues", []) or []):
        return "run_readiness_gap"
    return "needs_diagnosis"


def workflow_build_summary(report: Any) -> str | None:
    status = workflow_build_status(report)
    if status is None:
        return None

    parts: list[str] = []
    if status == "run_ready":
        parts.append("Validated and run-ready.")
    elif status == "validated":
        parts.append("Validated, but not run-ready.")
    else:
        parts.append("Build contract validation failed.")

    auto_fix_count = len(list(getattr(report, "auto_fixes_applied", []) or []))
    if auto_fix_count:
        issue_word = "issue" if auto_fix_count == 1 else "issues"
        parts.append(f"Auto-fixed {auto_fix_count} mechanical {issue_word}.")

    if status != "run_ready":
        primary_issue = ""
        for issue in list(getattr(report, "errors", []) or []):
            message = str(getattr(issue, "message", "") or "").strip()
            if message:
                primary_issue = message
                break
        if not primary_issue:
            for issue in list(getattr(report, "run_readiness_issues", []) or []):
                message = str(issue or "").strip()
                if message:
                    primary_issue = message
                    break
        if primary_issue:
            parts.append(f"Next issue: {primary_issue}")

    return " ".join(parts)


def workflow_build_provenance(
    report: Any,
    *,
    auto_fix_limit: int = 5,
) -> dict[str, Any]:
    if report is None:
        return {
            "build_status": None,
            "auto_fix_count": 0,
            "auto_fixes": [],
            "failure_bucket": None,
            "handoff_reason": None,
            "build_summary": None,
        }

    auto_fixes = [
        str(item).strip()
        for item in (getattr(report, "auto_fixes_applied", []) or [])
        if str(item).strip()
    ]
    return {
        "build_status": workflow_build_status(report),
        "auto_fix_count": len(auto_fixes),
        "auto_fixes": auto_fixes[:auto_fix_limit],
        "failure_bucket": workflow_failure_bucket(report),
        "handoff_reason": workflow_handoff_reason(report),
        "build_summary": workflow_build_summary(report),
    }


def normalize_workflow_id(raw: str | None) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _node_dicts(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = candidate.get("nodes")
    return nodes if isinstance(nodes, list) else []


def _edge_dicts(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    edges = candidate.get("edges")
    return edges if isinstance(edges, list) else []


def _port_names(node: dict[str, Any], key: str) -> list[str]:
    ports = node.get(key)
    if not isinstance(ports, list):
        return []
    return [
        str(port.get("name") or "").strip()
        for port in ports
        if isinstance(port, dict) and str(port.get("name") or "").strip()
    ]


def _port_name_set(node: dict[str, Any], key: str) -> set[str]:
    return set(_port_names(node, key))


def _generate_deduped_node_id(base_id: str, used_ids: set[str]) -> str:
    counter = 2
    candidate = f"{base_id}-{counter}"
    while candidate in used_ids:
        counter += 1
        candidate = f"{base_id}-{counter}"
    used_ids.add(candidate)
    return candidate


def _resolve_point_reference_candidates(
    node_indexes: list[int],
    *,
    expected_count: int,
    degree_counts: dict[int, int],
    reference_counts: dict[int, int],
) -> list[int] | None:
    candidates = [index for index in node_indexes if degree_counts[index] == 0]
    if len(candidates) == expected_count:
        return candidates
    referenced_candidates = [
        index for index in candidates if reference_counts[index] > 0
    ]
    if len(referenced_candidates) == expected_count:
        return referenced_candidates
    return None


def _repair_duplicate_node_id_group(
    candidate: dict[str, Any],
    *,
    node_id: str,
    node_indexes: list[int],
    auto_fixes: list[str],
) -> bool:
    nodes = _node_dicts(candidate)
    edges = _edge_dicts(candidate)
    input_ports = {
        index: _port_name_set(nodes[index], "input_ports") for index in node_indexes
    }
    output_ports = {
        index: _port_name_set(nodes[index], "output_ports") for index in node_indexes
    }
    incoming_counts = {index: 0 for index in node_indexes}
    outgoing_counts = {index: 0 for index in node_indexes}
    reference_counts = {index: 0 for index in node_indexes}
    edge_source_assignments: dict[int, int] = {}
    edge_target_assignments: dict[int, int] = {}

    for edge_index, edge in enumerate(edges):
        source_id = str(edge.get("source_node_id") or "")
        if source_id == node_id:
            source_port = str(edge.get("source_port") or "").strip()
            if not source_port:
                return False
            candidates = [
                index
                for index in node_indexes
                if source_port in output_ports.get(index, set())
            ]
            if len(candidates) != 1:
                return False
            edge_source_assignments[edge_index] = candidates[0]
            outgoing_counts[candidates[0]] += 1
            reference_counts[candidates[0]] += 1

        target_id = str(edge.get("target_node_id") or "")
        if target_id == node_id:
            target_port = str(edge.get("target_port") or "").strip()
            if not target_port:
                return False
            candidates = [
                index
                for index in node_indexes
                if target_port in input_ports.get(index, set())
            ]
            if len(candidates) != 1:
                return False
            edge_target_assignments[edge_index] = candidates[0]
            incoming_counts[candidates[0]] += 1
            reference_counts[candidates[0]] += 1

    hyperedges = candidate.get("hyperedges")
    if isinstance(hyperedges, list):
        for hyperedge in hyperedges:
            if not isinstance(hyperedge, dict):
                continue
            attach_to = hyperedge.get("attach_to")
            if isinstance(attach_to, list) and any(
                str(item or "") == node_id for item in attach_to
            ):
                return False
            attach_to_subgraph = hyperedge.get("attach_to_subgraph")
            if isinstance(attach_to_subgraph, list) and any(
                str(item or "") == node_id for item in attach_to_subgraph
            ):
                return False

    entry_points = candidate.get("entry_points")
    entry_assignments: dict[int, int] = {}
    if isinstance(entry_points, list):
        entry_positions = [
            position
            for position, value in enumerate(entry_points)
            if str(value or "") == node_id
        ]
        if entry_positions:
            entry_candidates = _resolve_point_reference_candidates(
                node_indexes,
                expected_count=len(entry_positions),
                degree_counts=incoming_counts,
                reference_counts=reference_counts,
            )
            if not entry_candidates:
                return False
            for position, index in zip(entry_positions, entry_candidates):
                entry_assignments[position] = index
                reference_counts[index] += 1

    exit_points = candidate.get("exit_points")
    exit_assignments: dict[int, int] = {}
    if isinstance(exit_points, list):
        exit_positions = [
            position
            for position, value in enumerate(exit_points)
            if str(value or "") == node_id
        ]
        if exit_positions:
            exit_candidates = _resolve_point_reference_candidates(
                node_indexes,
                expected_count=len(exit_positions),
                degree_counts=outgoing_counts,
                reference_counts=reference_counts,
            )
            if not exit_candidates:
                return False
            for position, index in zip(exit_positions, exit_candidates):
                exit_assignments[position] = index
                reference_counts[index] += 1

    primary_index = max(node_indexes, key=lambda index: (reference_counts[index], -index))
    used_ids = {
        str(node.get("id") or "")
        for node in nodes
        if str(node.get("id") or "").strip()
    }
    resolved_ids = {primary_index: node_id}
    for index in node_indexes:
        if index == primary_index:
            continue
        resolved_ids[index] = _generate_deduped_node_id(node_id, used_ids)

    for index in node_indexes:
        nodes[index]["id"] = resolved_ids[index]

    for edge_index, index in edge_source_assignments.items():
        edges[edge_index]["source_node_id"] = resolved_ids[index]
    for edge_index, index in edge_target_assignments.items():
        edges[edge_index]["target_node_id"] = resolved_ids[index]
    if isinstance(entry_points, list):
        for position, index in entry_assignments.items():
            entry_points[position] = resolved_ids[index]
    if isinstance(exit_points, list):
        for position, index in exit_assignments.items():
            exit_points[position] = resolved_ids[index]

    renamed_ids = [resolved_ids[index] for index in node_indexes if index != primary_index]
    auto_fixes.append(
        f"Deduped colliding node id '{node_id}' via local reference rewrites: {renamed_ids}."
    )
    return True


def _repair_colliding_node_ids(
    candidate: dict[str, Any],
    *,
    auto_fixes: list[str],
) -> None:
    node_indexes_by_id: dict[str, list[int]] = {}
    for index, node in enumerate(_node_dicts(candidate)):
        node_id = str(node.get("id") or "")
        if not node_id.strip():
            continue
        node_indexes_by_id.setdefault(node_id, []).append(index)

    for node_id, node_indexes in node_indexes_by_id.items():
        if len(node_indexes) < 2:
            continue
        _repair_duplicate_node_id_group(
            candidate,
            node_id=node_id,
            node_indexes=node_indexes,
            auto_fixes=auto_fixes,
        )


def _normalize_legacy_edge_fields(
    candidate: dict[str, Any],
    *,
    auto_fixes: list[str],
) -> None:
    nodes = {
        str(node.get("id") or "").strip(): node
        for node in _node_dicts(candidate)
        if str(node.get("id") or "").strip()
    }
    field_aliases = {
        "source_node_id": ("source", "from", "sourceNodeId", "source_id"),
        "target_node_id": ("target", "to", "targetNodeId", "target_id"),
        "source_port": ("sourcePort", "from_port"),
        "target_port": ("targetPort", "to_port"),
        "edge_type": ("type",),
    }

    for index, edge in enumerate(_edge_dicts(candidate), start=1):
        if not isinstance(edge, dict):
            continue

        for canonical, aliases in field_aliases.items():
            current = str(edge.get(canonical) or "").strip()
            if current:
                normalized = current.lower() if canonical == "edge_type" else current
                if edge.get(canonical) != normalized:
                    edge[canonical] = normalized
                continue
            for alias in aliases:
                value = str(edge.get(alias) or "").strip()
                if not value:
                    continue
                edge[canonical] = value.lower() if canonical == "edge_type" else value
                auto_fixes.append(
                    f"Normalized legacy edge field '{alias}' to '{canonical}' on edge {index}."
                )
                break

        if not str(edge.get("edge_type") or "").strip():
            edge["edge_type"] = "data"
            auto_fixes.append(f"Filled missing edge_type with 'data' on edge {index}.")

        source_id = str(edge.get("source_node_id") or "").strip()
        target_id = str(edge.get("target_node_id") or "").strip()
        source_node = nodes.get(source_id)
        target_node = nodes.get(target_id)

        source_port = str(edge.get("source_port") or "").strip()
        if not source_port and isinstance(source_node, dict):
            output_ports = _port_names(source_node, "output_ports")
            if len(output_ports) == 1:
                edge["source_port"] = output_ports[0]
                auto_fixes.append(
                    f"Inferred missing source_port on edge {index} from node '{source_id}'."
                )

        target_port = str(edge.get("target_port") or "").strip()
        if not target_port and isinstance(target_node, dict):
            input_ports = _port_names(target_node, "input_ports")
            if len(input_ports) == 1:
                edge["target_port"] = input_ports[0]
                auto_fixes.append(
                    f"Inferred missing target_port on edge {index} from node '{target_id}'."
                )

        edge_id = str(edge.get("id") or "").strip()
        if edge_id:
            if edge.get("id") != edge_id:
                edge["id"] = edge_id
            continue

        source_port = str(edge.get("source_port") or "").strip()
        target_port = str(edge.get("target_port") or "").strip()
        if source_id and target_id and source_port and target_port:
            edge["id"] = f"{source_id}.{source_port}->{target_id}.{target_port}"
        else:
            edge["id"] = f"edge-{index}"
        auto_fixes.append(f"Filled missing edge id on edge {index}.")


def _normalize_graph_metadata_name(
    candidate: dict[str, Any],
    *,
    display_fallback: str,
    auto_fixes: list[str],
) -> None:
    metadata = candidate.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        candidate["metadata"] = metadata
    current = str(metadata.get("name") or "").strip()
    if current:
        if metadata.get("name") != current:
            metadata["name"] = current
            auto_fixes.append("Trimmed workflow metadata.name whitespace.")
        return
    if display_fallback:
        metadata["name"] = display_fallback
        auto_fixes.append(f"Filled missing workflow metadata.name with '{display_fallback}'.")


def _normalize_node_names(
    candidate: dict[str, Any],
    *,
    auto_fixes: list[str],
) -> None:
    for node in _node_dicts(candidate):
        node_id = str(node.get("id") or "").strip()
        current_name = str(node.get("name") or "").strip()
        if current_name:
            if node.get("name") != current_name:
                node["name"] = current_name
                auto_fixes.append(f"Trimmed node name for '{node_id or current_name}'.")
            continue
        fallback_name = node_id or str(node.get("node_type") or "node").strip() or "node"
        node["name"] = fallback_name
        auto_fixes.append(f"Filled missing node name on '{fallback_name}'.")


def _normalize_entry_exit_points(
    candidate: dict[str, Any],
    *,
    auto_fixes: list[str],
) -> None:
    node_ids = [str(node.get("id") or "").strip() for node in _node_dicts(candidate)]
    node_ids = [node_id for node_id in node_ids if node_id]
    if not node_ids:
        return

    edges = _edge_dicts(candidate)
    incoming = {
        str(edge.get("target_node_id") or "").strip()
        for edge in edges
        if str(edge.get("target_node_id") or "").strip()
    }
    outgoing = {
        str(edge.get("source_node_id") or "").strip()
        for edge in edges
        if str(edge.get("source_node_id") or "").strip()
    }

    entry_points = candidate.get("entry_points")
    if not isinstance(entry_points, list):
        entry_points = []
        candidate["entry_points"] = entry_points
    normalized_entries = [str(item or "").strip() for item in entry_points if str(item or "").strip()]
    if normalized_entries != entry_points:
        candidate["entry_points"] = normalized_entries
    if not normalized_entries:
        computed_entries = [node_id for node_id in node_ids if node_id not in incoming]
        if computed_entries:
            candidate["entry_points"] = computed_entries
            auto_fixes.append("Filled missing entry_points from nodes without incoming edges.")

    exit_points = candidate.get("exit_points")
    if not isinstance(exit_points, list):
        exit_points = []
        candidate["exit_points"] = exit_points
    normalized_exits = [str(item or "").strip() for item in exit_points if str(item or "").strip()]
    if normalized_exits != exit_points:
        candidate["exit_points"] = normalized_exits
    if not normalized_exits:
        computed_exits = [node_id for node_id in node_ids if node_id not in outgoing]
        if computed_exits:
            candidate["exit_points"] = computed_exits
            auto_fixes.append("Filled missing exit_points from nodes without outgoing edges.")


def _normalize_single_port_aliases(
    candidate: dict[str, Any],
    *,
    auto_fixes: list[str],
) -> None:
    node_ids = [
        str(node.get("id") or "")
        for node in _node_dicts(candidate)
        if str(node.get("id") or "")
    ]
    if len(node_ids) != len(set(node_ids)):
        return
    nodes = {
        str(node.get("id") or "").strip(): node
        for node in _node_dicts(candidate)
        if str(node.get("id") or "").strip()
    }
    for edge in _edge_dicts(candidate):
        source_id = str(edge.get("source_node_id") or "").strip()
        target_id = str(edge.get("target_node_id") or "").strip()
        source_node = nodes.get(source_id)
        target_node = nodes.get(target_id)
        if isinstance(source_node, dict):
            output_ports = source_node.get("output_ports")
            if isinstance(output_ports, list):
                port_names = [
                    str(port.get("name") or "").strip()
                    for port in output_ports
                    if isinstance(port, dict) and str(port.get("name") or "").strip()
                ]
                current_source = str(edge.get("source_port") or "").strip()
                if current_source not in port_names and len(port_names) == 1:
                    edge["source_port"] = port_names[0]
                    auto_fixes.append(
                        f"Normalized edge source_port on '{source_id}' to '{port_names[0]}'."
                    )
        if isinstance(target_node, dict):
            input_ports = target_node.get("input_ports")
            if isinstance(input_ports, list):
                port_names = [
                    str(port.get("name") or "").strip()
                    for port in input_ports
                    if isinstance(port, dict) and str(port.get("name") or "").strip()
                ]
                current_target = str(edge.get("target_port") or "").strip()
                if current_target not in port_names and len(port_names) == 1:
                    edge["target_port"] = port_names[0]
                    auto_fixes.append(
                        f"Normalized edge target_port on '{target_id}' to '{port_names[0]}'."
                    )


def _classify_validation_issue(message: str) -> WorkflowBuildIssue:
    lower = message.lower()
    if "cycle" in lower:
        return WorkflowBuildIssue("run_readiness", message, severity="fatal")
    if "entry point" in lower or "exit point" in lower:
        return WorkflowBuildIssue("entry_exit", message, severity="repairable")
    if "sub-graph" in lower:
        return WorkflowBuildIssue("subgraph", message, severity="repairable")
    if "schema" in lower:
        return WorkflowBuildIssue("schema", message, severity="repairable")
    if "node" in lower and "unreachable" in lower:
        return WorkflowBuildIssue("run_readiness", message, severity="repairable")
    if "required input port" in lower or "has no output port" in lower or "has no input port" in lower:
        return WorkflowBuildIssue("port_endpoint", message, severity="repairable")
    if "source node" in lower or "target node" in lower:
        return WorkflowBuildIssue("node_identity", message, severity="repairable")
    return WorkflowBuildIssue("run_readiness", message, severity="repairable")


def _check_duplicate_node_ids(graph: Graph) -> list[WorkflowBuildIssue]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for node in graph.nodes:
        if node.id in seen and node.id not in duplicates:
            duplicates.append(node.id)
        seen.add(node.id)
    return [
        WorkflowBuildIssue(
            category="node_identity",
            message=(
                f"Duplicate node id '{node_id}' is not safely auto-repairable "
                "with local reference rewrites."
            ),
            severity="fatal",
            artifact_id=node_id,
        )
        for node_id in duplicates
    ]


def _check_node_name_quality(graph: Graph) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for node in graph.nodes:
        name = str(node.name or "").strip().lower()
        if not name:
            continue
        if name in seen and node.name not in duplicates:
            duplicates.append(node.name)
        seen.add(name)
    return [
        f"Duplicate node name '{name}' may make workflow summaries harder to read."
        for name in duplicates
    ]


def _reachable_nodes(graph: Graph) -> set[str]:
    adjacency: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        if edge.source_node_id in adjacency:
            adjacency[edge.source_node_id].add(edge.target_node_id)
    queue = list(graph.entry_points)
    visited: set[str] = set()
    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        queue.extend(neighbor for neighbor in adjacency.get(node_id, set()) if neighbor not in visited)
    return visited


def _contains_placeholder_status_payload(parsed: ast.AST) -> bool:
    for node in ast.walk(parsed):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if not isinstance(key, ast.Constant):
                continue
            if str(key.value).strip().lower() != "status":
                continue
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.strip().lower() == _PLACEHOLDER_STATUS_VALUE
            ):
                return True
    return False


def describe_code_readiness_issue(
    code: str | None,
    *,
    artifact_label: str,
) -> str | None:
    snippet = str(code or "")
    if not snippet.strip():
        return f"{artifact_label} has empty code, so it is not run-ready."
    try:
        parsed = ast.parse(snippet, mode="exec")
    except SyntaxError as exc:
        line = f" at line {exc.lineno}" if exc.lineno is not None else ""
        return (
            f"{artifact_label} contains non-runnable Python "
            f"({exc.msg}{line})."
        )
    if _contains_placeholder_status_payload(parsed):
        return (
            f"{artifact_label} contains placeholder status payload code "
            "instead of runnable logic."
        )
    return None


def _connected_input_ports(graph: Graph) -> dict[str, set[str]]:
    connected: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        if edge.target_node_id in connected:
            connected[edge.target_node_id].add(edge.target_port)
    return connected


def _input_port_names(node: Any) -> set[str]:
    return {
        str(getattr(port, "name", "") or "").strip()
        for port in list(getattr(node, "input_ports", []) or [])
        if str(getattr(port, "name", "") or "").strip()
    }


def _describe_foreach_readiness_issue(
    node: Any,
    *,
    graph: Graph,
    connected_inputs: dict[str, set[str]],
    graph_label: str,
) -> str | None:
    if getattr(node, "node_type", "") != "for_each":
        return None
    declared_inputs = _input_port_names(node)
    connected = connected_inputs.get(node.id, set())
    if connected & _FOREACH_ITERABLE_PORTS:
        return None
    if getattr(node, "external_input_schema", None):
        return None
    if (declared_inputs & _FOREACH_ITERABLE_PORTS) and node.id in graph.entry_points:
        return None
    if declared_inputs & _FOREACH_ITERABLE_PORTS:
        return (
            f"{graph_label} for-each node '{node.id}' declares iterable input "
            "ports but none are connected, so it is not run-ready."
        )
    return (
        f"{graph_label} for-each node '{node.id}' has no iterable input source. "
        "Connect an incoming 'items'/'input'/'data' edge or declare one of those "
        "entry input ports."
    )


def _describe_llm_external_action_issue(
    node: Any,
    *,
    graph_label: str,
) -> str | None:
    if getattr(node, "node_type", "") != "llm_operator":
        return None
    if list(getattr(node, "tools", []) or []):
        return None
    text = " ".join(
        str(part).strip()
        for part in (
            getattr(node, "name", ""),
            getattr(node, "description", ""),
            getattr(node, "prompt_template", ""),
            getattr(node, "system_prompt", ""),
        )
        if str(part).strip()
    )
    if not text:
        return None
    for pattern, capability in _LLM_EXTERNAL_ACTION_PATTERNS:
        if pattern.search(text):
            return (
                f"{graph_label} LLM node '{node.id}' has no tools, but its prompt "
                f"implies {capability}. Use a tool node, a tool-enabled LLM node, "
                "or a code node instead."
            )
    return None


def _required_tool_args(tool_id: str) -> set[str]:
    try:
        from dan.tools import get_all_tools

        entry = get_all_tools().get(tool_id)
    except Exception:
        return set()
    if not entry:
        return set()
    metadata = entry[1]
    parameters = metadata.get("parameters")
    if not isinstance(parameters, dict):
        return set()
    required = parameters.get("required")
    if not isinstance(required, list):
        return set()
    return {
        str(item).strip()
        for item in required
        if str(item).strip()
    }


def _check_graph_run_readiness(
    graph: Graph,
    *,
    graph_label: str,
    require_entry_exit: bool,
) -> list[str]:
    issues: list[str] = []
    if not graph.nodes:
        issues.append(f"{graph_label} has no nodes, so it is not run-ready.")
        return issues
    connected_inputs = _connected_input_ports(graph)
    for node in graph.nodes:
        if getattr(node, "node_type", "") != "code_operator":
            if getattr(node, "node_type", "") == "tool_operator":
                required_args = _required_tool_args(getattr(node, "tool_id", ""))
                tool_config = getattr(node, "tool_config", {}) or {}
                available_args = {
                    key
                    for key, value in dict(tool_config).items()
                    if value not in (None, "", [], {})
                }
                available_args.update(connected_inputs.get(node.id, set()))
                missing_args = sorted(required_args - available_args)
                for arg_name in missing_args:
                    issues.append(
                        f"{graph_label} tool node '{node.id}' ({node.tool_id}) is missing required argument "
                        f"'{arg_name}'; provide it via tool_config or an incoming edge."
                    )
            foreach_issue = _describe_foreach_readiness_issue(
                node,
                graph=graph,
                connected_inputs=connected_inputs,
                graph_label=graph_label,
            )
            if foreach_issue:
                issues.append(foreach_issue)
            llm_issue = _describe_llm_external_action_issue(
                node,
                graph_label=graph_label,
            )
            if llm_issue:
                issues.append(llm_issue)
            continue
        issue = describe_code_readiness_issue(
            getattr(node, "code", ""),
            artifact_label=f"{graph_label} code node '{node.id}'",
        )
        if issue:
            issues.append(issue)
    if require_entry_exit:
        if not graph.entry_points:
            issues.append(f"{graph_label} has no entry points, so it is not run-ready.")
        if not graph.exit_points:
            issues.append(f"{graph_label} has no exit points, so it is not run-ready.")
        if not issues:
            reachable = _reachable_nodes(graph)
            if not any(exit_id in reachable for exit_id in graph.exit_points):
                issues.append(
                    f"No exit point in {graph_label.lower()} is reachable from the current entry points."
                )
    for subgraph_key, subgraph in graph.sub_graphs.items():
        issues.extend(
            _check_graph_run_readiness(
                subgraph,
                graph_label=f"Subgraph '{subgraph_key}'",
                require_entry_exit=False,
            )
        )
    return issues


def _check_run_readiness(graph: Graph) -> list[str]:
    return _check_graph_run_readiness(
        graph,
        graph_label="Workflow",
        require_entry_exit=True,
    )


def classify_run_readiness_issues(issues: list[str]) -> str | None:
    normalized = [
        str(issue).strip().lower()
        for issue in issues
        if str(issue).strip()
    ]
    if not normalized:
        return None
    if any(
        marker in issue
        for issue in normalized
        for marker in ("empty code", "missing code", "unresolved code")
    ):
        return "unresolved_code"
    if any(
        marker in issue
        for issue in normalized
        for marker in ("placeholder", "non-runnable", "not runnable")
    ):
        return "non_runnable_code"
    return "not_run_ready"


def validate_workflow_build_contract(
    graph_dict: dict[str, Any],
    *,
    workflow_id: str | None = None,
    apply_repairs: bool = True,
) -> WorkflowBuildContractReport:
    candidate = copy.deepcopy(graph_dict if isinstance(graph_dict, dict) else {})
    auto_fixes: list[str] = []
    warnings: list[str] = []
    normalized_workflow_id = normalize_workflow_id(workflow_id)
    display_fallback = str(workflow_id or normalized_workflow_id or "").strip()

    if workflow_id and normalized_workflow_id and normalized_workflow_id != workflow_id:
        auto_fixes.append(
            f"Normalized workflow id '{workflow_id}' -> '{normalized_workflow_id}'."
        )

    if apply_repairs:
        _normalize_graph_metadata_name(
            candidate,
            display_fallback=display_fallback,
            auto_fixes=auto_fixes,
        )
        _normalize_legacy_edge_fields(candidate, auto_fixes=auto_fixes)
        _normalize_node_names(candidate, auto_fixes=auto_fixes)
        _repair_colliding_node_ids(candidate, auto_fixes=auto_fixes)
        _normalize_entry_exit_points(candidate, auto_fixes=auto_fixes)

    try:
        graph = Graph.model_validate(candidate)
    except Exception as exc:
        return WorkflowBuildContractReport(
            workflow_id=str(workflow_id or ""),
            normalized_workflow_id=normalized_workflow_id,
            display_name=display_fallback,
            validated=False,
            run_ready=False,
            errors=[
                WorkflowBuildIssue(
                    category="schema",
                    message=str(exc),
                    severity="fatal",
                )
            ],
            auto_fixes_applied=auto_fixes,
        )

    if apply_repairs:
        candidate = graph.model_dump(mode="json")
        _normalize_single_port_aliases(candidate, auto_fixes=auto_fixes)
        _normalize_graph_metadata_name(
            candidate,
            display_fallback=str(workflow_id or candidate.get("metadata", {}).get("name") or "").strip(),
            auto_fixes=auto_fixes,
        )
        _normalize_node_names(candidate, auto_fixes=auto_fixes)
        _normalize_entry_exit_points(candidate, auto_fixes=auto_fixes)
        graph = Graph.model_validate(candidate)

    errors = _check_duplicate_node_ids(graph)
    warnings.extend(_check_node_name_quality(graph))

    for message in validate_graph(graph):
        if any(keyword in message.lower() for keyword in _WARNING_KEYWORDS):
            warnings.append(message)
            continue
        errors.append(_classify_validation_issue(message))

    run_readiness_issues = _check_run_readiness(graph)
    display_name = str(graph.metadata.name or workflow_id or normalized_workflow_id or "").strip()
    report = WorkflowBuildContractReport(
        workflow_id=str(workflow_id or ""),
        normalized_workflow_id=normalized_workflow_id,
        display_name=display_name,
        validated=not errors,
        run_ready=not errors and not run_readiness_issues,
        graph=graph,
        graph_dict=graph.model_dump(mode="json"),
        errors=errors,
        warnings=warnings,
        auto_fixes_applied=auto_fixes,
        run_readiness_issues=run_readiness_issues,
    )
    return report
