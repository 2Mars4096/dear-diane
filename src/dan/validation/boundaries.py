"""Boundary auto-insert — generate ValidatorNodes at composite boundaries.

Inspects ``external_input_schema`` and ``external_output_schema`` on
composite-style nodes and produces ValidatorNodes with appropriate
required_keys + schema_conformance rules, wired into the graph.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from dan.models.control_flow import CompositeNode, ValidatorNode, ValidationRule
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import NodeBase
from dan.models.ports import InputPort, OutputPort


def generate_entry_validator(
    composite_node: NodeBase,
) -> tuple[ValidatorNode, list[DataEdge]]:
    """Generate a ValidatorNode from a composite node's ``external_input_schema``.

    Returns the validator node and a list of DataEdges that should be
    inserted to wire it into the graph (replacing the composite's
    incoming edges).
    """
    schema = getattr(composite_node, "external_input_schema", None) or {}
    node_id = f"{composite_node.id}__entry_validator"

    rules: list[ValidationRule] = []

    if isinstance(schema, dict) and schema:
        required_keys = schema.get("required", [])
        if required_keys:
            rules.append(ValidationRule(
                rule_type="required_keys",
                config={"keys": required_keys},
            ))
        rules.append(ValidationRule(
            rule_type="schema_conformance",
            config={"schema": schema},
        ))

    validator = ValidatorNode(
        id=node_id,
        name=f"Entry validator for {composite_node.id}",
        validation_rules=rules,
        on_failure="route",
        input_ports=[InputPort(name="input", required=True)],
        output_ports=[
            OutputPort(name="valid", description="Passthrough when all rules pass"),
            OutputPort(name="invalid", description="Data + errors when any rule fails"),
        ],
    )

    edge_to_composite = DataEdge(
        id=f"edge_{node_id}__to__{composite_node.id}",
        source_node_id=node_id,
        source_port="valid",
        target_node_id=composite_node.id,
        target_port="input",
    )

    return validator, [edge_to_composite]


def generate_exit_validator(
    composite_node: NodeBase,
) -> tuple[ValidatorNode, list[DataEdge]]:
    """Generate a ValidatorNode from a composite node's ``external_output_schema``.

    Returns the validator node and a list of DataEdges wiring the
    composite's output into the validator.
    """
    schema = getattr(composite_node, "external_output_schema", None) or {}
    node_id = f"{composite_node.id}__exit_validator"

    rules: list[ValidationRule] = []

    if isinstance(schema, dict) and schema:
        required_keys = schema.get("required", [])
        if required_keys:
            rules.append(ValidationRule(
                rule_type="required_keys",
                config={"keys": required_keys},
            ))
        rules.append(ValidationRule(
            rule_type="schema_conformance",
            config={"schema": schema},
        ))

    validator = ValidatorNode(
        id=node_id,
        name=f"Exit validator for {composite_node.id}",
        validation_rules=rules,
        on_failure="route",
        input_ports=[InputPort(name="input", required=True)],
        output_ports=[
            OutputPort(name="valid", description="Passthrough when all rules pass"),
            OutputPort(name="invalid", description="Data + errors when any rule fails"),
        ],
    )

    edge_from_composite = DataEdge(
        id=f"edge_{composite_node.id}__to__{node_id}",
        source_node_id=composite_node.id,
        source_port="result",
        target_node_id=node_id,
        target_port="input",
    )

    return validator, [edge_from_composite]


def insert_boundary_validators(
    graph: Graph,
    composite_node_id: str,
) -> Graph:
    """Return a new Graph with validators inserted at both boundaries of a composite node.

    Rewires incoming edges to flow through the entry validator and
    outgoing edges to flow through the exit validator.
    """
    composite_node = graph.node_by_id(composite_node_id)
    if composite_node is None:
        raise ValueError(f"Node '{composite_node_id}' not found in graph")

    new_graph = graph.model_copy(deep=True)
    new_nodes: list[Any] = list(new_graph.nodes)
    new_edges: list[Any] = list(new_graph.edges)

    entry_schema = getattr(composite_node, "external_input_schema", None)
    exit_schema = getattr(composite_node, "external_output_schema", None)

    if entry_schema:
        entry_validator, entry_wiring_edges = generate_entry_validator(composite_node)
        new_nodes.append(entry_validator)

        rewired_edges = []
        for edge in new_edges:
            if isinstance(edge, DataEdge) and edge.target_node_id == composite_node_id:
                rewired = edge.model_copy(update={
                    "target_node_id": entry_validator.id,
                    "target_port": "input",
                    "id": f"{edge.id}__rewired",
                })
                rewired_edges.append(rewired)
            else:
                rewired_edges.append(edge)
        new_edges = rewired_edges
        new_edges.extend(entry_wiring_edges)

    if exit_schema:
        exit_validator, exit_wiring_edges = generate_exit_validator(composite_node)
        new_nodes.append(exit_validator)

        rewired_edges = []
        for edge in new_edges:
            if isinstance(edge, DataEdge) and edge.source_node_id == composite_node_id:
                rewired = edge.model_copy(update={
                    "source_node_id": exit_validator.id,
                    "source_port": "valid",
                    "id": f"{edge.id}__rewired",
                })
                rewired_edges.append(rewired)
            else:
                rewired_edges.append(edge)
        new_edges = rewired_edges
        new_edges.extend(exit_wiring_edges)

    new_graph.nodes = new_nodes
    new_graph.edges = new_edges
    return new_graph
