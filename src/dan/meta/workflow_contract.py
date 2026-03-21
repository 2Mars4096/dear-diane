"""Workflow build-boundary validation and bounded mechanical repair."""

from __future__ import annotations

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

_WARNING_KEYWORDS = ("warning", "deprecated", "untyped")


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
            message=f"Duplicate node id '{node_id}' is not safe to auto-fix without changing references.",
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


def _check_run_readiness(graph: Graph) -> list[str]:
    issues: list[str] = []
    if not graph.nodes:
        issues.append("Workflow has no nodes, so it is not run-ready.")
        return issues
    if not graph.entry_points:
        issues.append("Workflow has no entry points, so it is not run-ready.")
    if not graph.exit_points:
        issues.append("Workflow has no exit points, so it is not run-ready.")
    if issues:
        return issues
    reachable = _reachable_nodes(graph)
    if not any(exit_id in reachable for exit_id in graph.exit_points):
        issues.append("No exit point is reachable from the current entry points.")
    return issues


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
        _normalize_node_names(candidate, auto_fixes=auto_fixes)
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
