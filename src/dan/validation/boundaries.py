"""Boundary auto-insert — generate validator-shaped Workers at composite boundaries.

Inspects ``external_input_schema`` and ``external_output_schema`` on
composite-style nodes and produces validator Workers with appropriate
required_keys + schema_conformance rules, wired into the graph.
"""

from __future__ import annotations

from typing import Any

from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.legacy import ValidationRule
from dan.models.nodes import NodeBase
from dan.models.ports import InputPort, OutputPort
from dan.worker.model import Worker


def _unique_port_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _default_input_ports(composite_node: NodeBase) -> list[str]:
    ports = getattr(composite_node, "input_ports", []) or []
    names = [p.name for p in ports if getattr(p, "name", "")]
    return _unique_port_names(names) or ["input"]


def _default_output_ports(composite_node: NodeBase) -> list[str]:
    ports = getattr(composite_node, "output_ports", []) or []
    names = [p.name for p in ports if getattr(p, "name", "")]
    return _unique_port_names(names) or ["result"]


def _validator_output_ports(boundary_ports: list[str]) -> list[OutputPort]:
    output_ports = [
        OutputPort(name="valid", description="Passthrough when all rules pass"),
        OutputPort(name="invalid", description="Data + errors when any rule fails"),
    ]
    for port in boundary_ports:
        if port not in ("valid", "invalid"):
            output_ports.append(
                OutputPort(name=port, description=f"Passthrough for '{port}'"),
            )
    return output_ports


def generate_entry_validator(
    composite_node: NodeBase,
    port_names: list[str] | None = None,
) -> tuple[Worker, list[DataEdge]]:
    """Generate a validator Worker from a composite node's ``external_input_schema``.

    Returns the validator node and a list of DataEdges that should be
    inserted to wire it into the graph (replacing the composite's
    incoming edges).
    """
    schema = getattr(composite_node, "external_input_schema", None) or {}
    node_id = f"{composite_node.id}__entry_validator"
    boundary_ports = _unique_port_names(port_names or _default_input_ports(composite_node))

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

    validator = Worker(
        id=node_id,
        name=f"Entry validator for {composite_node.id}",
        role="validator",
        validation_rules=rules,
        input_ports=[InputPort(name=port, required=False) for port in boundary_ports],
        output_ports=_validator_output_ports(boundary_ports),
        metadata={
            "generated": True,
            "validator_on_failure": "route",
        },
    )

    edges_to_composite = [
        DataEdge(
            id=f"edge_{node_id}_{port}__to__{composite_node.id}_{port}",
            source_node_id=node_id,
            source_port=port,
            target_node_id=composite_node.id,
            target_port=port,
        )
        for port in boundary_ports
    ]

    return validator, edges_to_composite


def generate_exit_validator(
    composite_node: NodeBase,
    port_names: list[str] | None = None,
) -> tuple[Worker, list[DataEdge]]:
    """Generate a validator Worker from a composite node's ``external_output_schema``.

    Returns the validator node and a list of DataEdges wiring the
    composite's output into the validator.
    """
    schema = getattr(composite_node, "external_output_schema", None) or {}
    node_id = f"{composite_node.id}__exit_validator"
    boundary_ports = _unique_port_names(port_names or _default_output_ports(composite_node))

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

    validator = Worker(
        id=node_id,
        name=f"Exit validator for {composite_node.id}",
        role="validator",
        validation_rules=rules,
        input_ports=[InputPort(name=port, required=False) for port in boundary_ports],
        output_ports=_validator_output_ports(boundary_ports),
        metadata={
            "generated": True,
            "validator_on_failure": "route",
        },
    )

    edges_from_composite = [
        DataEdge(
            id=f"edge_{composite_node.id}_{port}__to__{node_id}_{port}",
            source_node_id=composite_node.id,
            source_port=port,
            target_node_id=node_id,
            target_port=port,
        )
        for port in boundary_ports
    ]

    return validator, edges_from_composite


def insert_boundary_validators(
    graph: Graph,
    composite_node_id: str,
) -> Graph:
    """Return a new Graph with validators inserted at both boundaries of a composite node.

    Rewires incoming edges to flow through the entry validator and
    outgoing edges to flow through the exit validator.

    **Idempotent**: if validators with the expected IDs already exist in
    the graph, the call returns the graph unchanged.
    """
    composite_node = graph.node_by_id(composite_node_id)
    if composite_node is None:
        raise ValueError(f"Node '{composite_node_id}' not found in graph")

    entry_id = f"{composite_node_id}__entry_validator"
    exit_id = f"{composite_node_id}__exit_validator"
    if graph.node_by_id(entry_id) is not None or graph.node_by_id(exit_id) is not None:
        return graph

    new_graph = graph.model_copy(deep=True)
    composite_node = new_graph.node_by_id(composite_node_id)
    if composite_node is None:
        raise ValueError(f"Node '{composite_node_id}' not found in copied graph")

    new_nodes: list[Any] = list(new_graph.nodes)
    new_edges: list[Any] = list(new_graph.edges)

    entry_schema = getattr(composite_node, "external_input_schema", None)
    exit_schema = getattr(composite_node, "external_output_schema", None)

    if entry_schema:
        entry_ports = _unique_port_names(
            [
                edge.target_port
                for edge in new_edges
                if isinstance(edge, DataEdge) and edge.target_node_id == composite_node_id
            ],
        ) or _default_input_ports(composite_node)
        entry_validator, entry_wiring_edges = generate_entry_validator(
            composite_node, entry_ports,
        )
        new_nodes.append(entry_validator)

        rewired_edges = []
        for edge in new_edges:
            if isinstance(edge, DataEdge) and edge.target_node_id == composite_node_id:
                rewired = edge.model_copy(update={
                    "target_node_id": entry_validator.id,
                    "id": f"{edge.id}__rewired",
                })
                rewired_edges.append(rewired)
            else:
                rewired_edges.append(edge)
        new_edges = rewired_edges
        new_edges.extend(entry_wiring_edges)

    if exit_schema:
        exit_ports = _unique_port_names(
            [
                edge.source_port
                for edge in new_edges
                if isinstance(edge, DataEdge) and edge.source_node_id == composite_node_id
            ],
        ) or _default_output_ports(composite_node)
        exit_validator, exit_wiring_edges = generate_exit_validator(
            composite_node, exit_ports,
        )
        new_nodes.append(exit_validator)

        rewired_edges = []
        for edge in new_edges:
            if isinstance(edge, DataEdge) and edge.source_node_id == composite_node_id:
                rewired = edge.model_copy(update={
                    "source_node_id": exit_validator.id,
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
