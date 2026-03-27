"""Deterministic sectioning helpers for staged workflow generation."""

from __future__ import annotations

import re
from collections import defaultdict
from heapq import heappop, heappush
from typing import Iterable

from dan.meta.workflow_spec import (
    StructuredSectioningConfig,
    WorkflowSection,
    WorkflowSpec,
    WorkflowSpecNode,
)

__all__ = [
    "node_section_map",
    "partition_workflow_spec",
    "section_weight",
    "topological_order_nodes",
]


_FAMILY_WEIGHTS = {
    "llm": 1.8,
    "tool": 1.4,
    "code": 1.6,
    "control_flow": 0.9,
}


def section_weight(node: WorkflowSpecNode) -> float:
    """Return a deterministic weight for a node."""

    base = 1.0 + _FAMILY_WEIGHTS.get(node.execution_family.value, 1.0)
    base += min(len(node.inputs) * 0.1, 0.4)
    base += min(len(node.dependencies) * 0.15, 0.6)
    return round(base, 3)


def topological_order_nodes(nodes: Iterable[WorkflowSpecNode]) -> list[WorkflowSpecNode]:
    """Return nodes in deterministic topological order."""

    node_list = list(nodes)
    node_ids = {node.node_id for node in node_list}
    outgoing: dict[str, list[str]] = {node.node_id: [] for node in node_list}
    incoming_count: dict[str, int] = {node.node_id: 0 for node in node_list}
    node_by_id = {node.node_id: node for node in node_list}

    for node in node_list:
        for dependency in node.dependencies:
            if dependency in node_ids:
                outgoing[dependency].append(node.node_id)
                incoming_count[node.node_id] += 1

    ready: list[str] = []
    for node_id, count in incoming_count.items():
        if count == 0:
            heappush(ready, node_id)

    ordered: list[WorkflowSpecNode] = []
    while ready:
        node_id = heappop(ready)
        ordered.append(node_by_id[node_id])
        for child in sorted(outgoing[node_id]):
            incoming_count[child] -= 1
            if incoming_count[child] == 0:
                heappush(ready, child)

    if len(ordered) != len(node_list):
        raise ValueError("Circular dependency detected while sectioning workflow spec")
    return ordered


def partition_workflow_spec(
    spec: WorkflowSpec,
    config: StructuredSectioningConfig | None = None,
) -> list[WorkflowSection]:
    """Partition a workflow spec into deterministic debug-friendly sections."""

    config = config or StructuredSectioningConfig.from_env()
    ordered_nodes = topological_order_nodes(spec.nodes)
    downstream_map = _build_downstream_map(ordered_nodes)

    sections: list[WorkflowSection] = []
    current_nodes: list[WorkflowSpecNode] = []
    current_ids: set[str] = set()
    current_weight = 0.0
    current_label_key: tuple[str | None, str | None] | None = None

    def _flush() -> None:
        nonlocal current_nodes, current_ids, current_weight, current_label_key
        if not current_nodes:
            return
        section_index = len(sections)
        section_label = current_label_key[1] if current_label_key else None
        chapter_label = current_label_key[0] if current_label_key else None
        upstream_dependencies = sorted(
            {
                dependency
                for node in current_nodes
                for dependency in node.dependencies
                if dependency not in current_ids
            }
        )
        downstream_dependents = sorted(
            {
                dependent
                for node in current_nodes
                for dependent in downstream_map.get(node.node_id, ())
                if dependent not in current_ids
            }
        )
        section_id = _build_section_id(
            section_index=section_index,
            chapter_label=chapter_label,
            section_label=section_label,
            first_node=current_nodes[0],
        )
        sections.append(
            WorkflowSection(
                section_id=section_id,
                index=section_index,
                node_ids=[node.node_id for node in current_nodes],
                nodes=list(current_nodes),
                estimated_weight=round(current_weight, 3),
                chapter_label=chapter_label,
                section_label=section_label,
                upstream_dependencies=upstream_dependencies,
                downstream_dependents=downstream_dependents,
                notes=[
                    f"node_count={len(current_nodes)}",
                    f"weight={round(current_weight, 3)}",
                ],
            )
        )
        current_nodes = []
        current_ids = set()
        current_weight = 0.0
        current_label_key = None

    for node in ordered_nodes:
        node_label_key = _label_key(node)
        node_weight = section_weight(node)
        dependency_overlap = len(set(node.dependencies) & current_ids)
        explicit_boundary = (
            current_nodes
            and node_label_key != current_label_key
            and (current_label_key is not None or node_label_key != (None, None))
        )
        would_overflow = current_weight + node_weight > (
            config.max_section_size
            + (config.cross_section_penalty if dependency_overlap > 0 else 0.0)
        )
        if current_nodes and (explicit_boundary or (would_overflow and len(current_nodes) >= config.min_section_size)):
            _flush()
        if not current_nodes:
            current_label_key = node_label_key if node_label_key != (None, None) else None
        current_nodes.append(node)
        current_ids.add(node.node_id)
        current_weight += node_weight

    _flush()
    return sections


def node_section_map(sections: Iterable[WorkflowSection]) -> dict[str, str]:
    """Return ``node_id -> section_id`` for a section list."""

    mapping: dict[str, str] = {}
    for section in sections:
        for node_id in section.node_ids:
            mapping[node_id] = section.section_id
    return mapping


def _build_downstream_map(nodes: Iterable[WorkflowSpecNode]) -> dict[str, set[str]]:
    downstream: dict[str, set[str]] = defaultdict(set)
    node_ids = {node.node_id for node in nodes}
    for node in nodes:
        for dependency in node.dependencies:
            if dependency in node_ids:
                downstream[dependency].add(node.node_id)
    return downstream


def _label_key(node: WorkflowSpecNode) -> tuple[str | None, str | None]:
    chapter = _normalize_label(node.chapter_label)
    section = _normalize_label(node.section_label)
    return chapter, section


def _normalize_label(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _build_section_id(
    *,
    section_index: int,
    chapter_label: str | None,
    section_label: str | None,
    first_node: WorkflowSpecNode,
) -> str:
    label = section_label or chapter_label or first_node.node_id
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    if not slug:
        slug = first_node.node_id
    return f"section-{section_index + 1:02d}-{slug}"
