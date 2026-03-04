"""Derive a workflow's public interface (inputs/outputs) from its graph.

Shared by 21-5 (shareable blocks) and 21-3 (publish-as-service).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from dan.models.graph import Graph

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

HUMAN_NODE_TYPES = frozenset({"human", "human_in_the_loop"})


class WorkflowInterface(BaseModel):
    """Public contract of a workflow — what goes in and what comes out."""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
    )
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
    )
    has_human_nodes: bool = False
    estimated_duration: float | None = None


def derive_workflow_interface(graph: Graph) -> WorkflowInterface:
    """Extract input/output schemas and metadata from a workflow graph.

    Input detection priority:
    1. ``InputNode.variables`` — each variable becomes a property.
    2. ``{placeholder}`` patterns in entry-node ``prompt_template`` fields.
    3. Falls back to empty schema if neither source yields variables.

    Output detection:
    - Collect output ports from exit nodes (nodes with no outgoing edges).
    """
    has_human = any(n.node_type in HUMAN_NODE_TYPES for n in graph.nodes)

    input_props = _collect_inputs(graph)
    output_props = _collect_outputs(graph)

    input_schema: dict[str, Any] = {"type": "object", "properties": input_props}
    if input_props:
        input_schema["required"] = list(input_props.keys())

    output_schema: dict[str, Any] = {"type": "object", "properties": output_props}

    return WorkflowInterface(
        name=graph.metadata.name,
        description=graph.metadata.description,
        input_schema=input_schema,
        output_schema=output_schema,
        has_human_nodes=has_human,
    )


def _collect_inputs(graph: Graph) -> dict[str, dict[str, Any]]:
    """Gather input properties from InputNode variables or prompt placeholders."""
    props: dict[str, dict[str, Any]] = {}

    for node in graph.nodes:
        if node.node_type == "input":
            for var in node.variables:
                json_type = _var_type_to_json(var.type)
                prop: dict[str, Any] = {"type": json_type}
                if var.description:
                    prop["description"] = var.description
                if var.default is not None:
                    prop["default"] = var.default
                props[var.name] = prop

    if props:
        return props

    entry_ids = set(graph.entry_points) if graph.entry_points else _infer_entry_ids(graph)
    for node in graph.nodes:
        if node.id not in entry_ids:
            continue
        template = getattr(node, "prompt_template", "") or ""
        for match in _PLACEHOLDER_RE.finditer(template):
            var_name = match.group(1)
            if var_name not in props:
                props[var_name] = {"type": "string"}

    return props


def _collect_outputs(graph: Graph) -> dict[str, dict[str, Any]]:
    """Gather output properties from exit nodes (nodes with no outgoing edges)."""
    exit_ids = set(graph.exit_points) if graph.exit_points else _infer_exit_ids(graph)
    props: dict[str, dict[str, Any]] = {}

    for node in graph.nodes:
        if node.id not in exit_ids:
            continue
        for port in node.output_ports:
            key = port.name if port.name not in props else f"{node.id}__{port.name}"
            schema = dict(port.json_schema) if port.json_schema else {"type": "string"}
            if port.description:
                schema["description"] = port.description
            props[key] = schema

    return props


def _infer_entry_ids(graph: Graph) -> set[str]:
    """Nodes with no incoming edges are entry candidates."""
    targets = {e.target_node_id for e in graph.edges}
    return {n.id for n in graph.nodes if n.id not in targets}


def _infer_exit_ids(graph: Graph) -> set[str]:
    """Nodes with no outgoing edges are exit candidates."""
    sources = {e.source_node_id for e in graph.edges}
    return {n.id for n in graph.nodes if n.id not in sources}


_TYPE_MAP = {"string": "string", "number": "number", "boolean": "boolean"}


def _var_type_to_json(var_type: str) -> str:
    return _TYPE_MAP.get(var_type, "string")
