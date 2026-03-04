"""Variable inspector — compute upstream inputs for a given node.

Walks incoming edges (data + context) to collect source node/port names,
infer types from output_schema / port definitions, and detect likely-missing
inputs.  Used by the ``/api/graphs/{graph_id}/nodes/{node_id}/inputs``
REST endpoint and the frontend "Inputs" section in ConfigPanel.
"""

from __future__ import annotations

from typing import Any

from dan.models.graph import Graph


def _type_hint_from_schema(schema: dict[str, Any]) -> str:
    """Derive a human-readable type hint from a JSON Schema dict."""
    if not schema:
        return "any"
    t = schema.get("type", "")
    if isinstance(t, list):
        return " | ".join(t)
    if t == "array":
        items = schema.get("items", {})
        inner = _type_hint_from_schema(items) if items else "any"
        return f"array<{inner}>"
    if t == "object":
        return "object"
    return t or "any"


def compute_upstream_variables(
    node_id: str,
    graph: Graph,
) -> list[dict[str, Any]]:
    """Return upstream variable descriptors for *node_id*.

    Each item has:
    - ``variable_name``: the target port name on the inspected node
    - ``source_node``: id (and name) of the node supplying data
    - ``source_port``: the output port on the source node
    - ``type_hint``: inferred type from the source port's ``json_schema``
    - ``required``: whether the target input port is marked required
    - ``edge_type``: "data" or "context"
    - ``context_key``: for context edges, the shared-context key
    - ``connected``: True (always true for wired entries)

    After listing wired inputs, we append entries for *unconnected* required
    input ports (marked ``connected: False``) as "likely missing" diagnostics.
    """
    target_node = graph.node_by_id(node_id)
    if target_node is None:
        return []

    incoming_edges = graph.edges_to(node_id)

    # Build lookup maps
    source_nodes: dict[str, Any] = {n.id: n for n in graph.nodes}
    target_input_ports = {p.name: p for p in target_node.input_ports}
    connected_ports: set[str] = set()

    variables: list[dict[str, Any]] = []

    for edge in incoming_edges:
        edge_dict = edge.model_dump()
        edge_type = edge_dict.get("edge_type", "data")

        src = source_nodes.get(edge.source_node_id)
        src_name = src.name if src else edge.source_node_id

        # Resolve type hint from source node's output port schema
        type_hint = "any"
        if src is not None:
            for op in src.output_ports:
                if op.name == edge.source_port:
                    type_hint = _type_hint_from_schema(op.json_schema)
                    break

        # Check if target port is required
        target_port_obj = target_input_ports.get(edge.target_port)
        required = target_port_obj.required if target_port_obj else True

        entry: dict[str, Any] = {
            "variable_name": edge.target_port,
            "source_node": src_name,
            "source_node_id": edge.source_node_id,
            "source_port": edge.source_port,
            "type_hint": type_hint,
            "required": required,
            "edge_type": edge_type,
            "connected": True,
        }

        if edge_type == "context":
            entry["context_key"] = edge_dict.get("context_key", "")

        variables.append(entry)
        connected_ports.add(edge.target_port)

    # Append "likely missing" diagnostics for unconnected required input ports
    for port in target_node.input_ports:
        if port.name not in connected_ports:
            variables.append({
                "variable_name": port.name,
                "source_node": None,
                "source_node_id": None,
                "source_port": None,
                "type_hint": _type_hint_from_schema(port.json_schema),
                "required": port.required,
                "edge_type": None,
                "connected": False,
            })

    return variables
