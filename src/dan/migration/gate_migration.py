"""Helpers to migrate legacy IfElse/WhileLoop graphs to GateNode patterns.

All functions operate on raw ``dict`` (graph JSON), not Pydantic models,
so they can be applied before deserialization.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)


def migrate_if_else_to_gate(graph_dict: dict[str, Any]) -> dict[str, Any]:
    """Convert IfElseNode entries to GateNode(gate_mode='if_else').

    Transforms:
    - node_type "if_else" -> "gate" with gate_mode="if_else"
    - Preserves condition field
    - Adds output_ports ["true", "false"] if not present
    - Remaps edges with source_port "branch" to "true" as a best-effort
      default. Edges intended for the false branch must be fixed manually
      after migration; a warning is logged for each remapped edge.
    """
    nodes = graph_dict.get("nodes", [])
    migrated_ids: set[str] = set()

    for node in nodes:
        if node.get("node_type") != "if_else":
            continue

        node["node_type"] = "gate"
        node["gate_mode"] = "if_else"
        node.setdefault("max_iterations", 10)

        existing_port_names = {
            p.get("name") for p in node.get("output_ports", [])
        }
        if "true" not in existing_port_names or "false" not in existing_port_names:
            node["output_ports"] = [
                {"name": "true", "description": "Active when condition is true"},
                {"name": "false", "description": "Active when condition is false"},
            ]

        migrated_ids.add(node.get("id", ""))

    for edge in graph_dict.get("edges", []):
        if (
            edge.get("source_node_id") in migrated_ids
            and edge.get("source_port") == "branch"
        ):
            logger.warning(
                "Migrated edge %s: remapped source_port 'branch' -> 'true'; "
                "false-branch edges must be corrected manually",
                edge.get("id", "?"),
            )
            edge["source_port"] = "true"

    return graph_dict


def migrate_while_loop_to_flat_gate(graph_dict: dict[str, Any]) -> dict[str, Any]:
    """Convert WhileLoopNode containers to flat gate-controlled cycles.

    1. Extract body_graph nodes from sub_graphs into the main graph
    2. Create a GateNode(gate_mode='while') at the end of the body
    3. Wire gate's 'continue' port back to the body entry (back-edge)
    4. Wire gate's 'done' port to whatever was downstream of the while_loop
    5. Remove the while_loop container node and its sub_graphs entry
    6. Namespace body node IDs to avoid collisions (prefix with "{while_loop_id}__")
    """
    nodes = graph_dict.get("nodes", [])
    edges = graph_dict.get("edges", [])
    sub_graphs = graph_dict.get("sub_graphs", {})

    while_nodes = [n for n in nodes if n.get("node_type") == "while_loop"]
    if not while_nodes:
        return graph_dict

    for wl_node in while_nodes:
        wl_id = wl_node.get("id", "")
        body_key = wl_node.get("body_graph", "")
        condition = wl_node.get("condition", "true")
        max_iterations = wl_node.get("max_iterations", 10)

        body_graph = sub_graphs.get(body_key)
        if not body_graph:
            continue

        prefix = f"{wl_id}__"
        body_nodes = body_graph.get("nodes", [])
        body_edges = body_graph.get("edges", [])
        body_entry_points = body_graph.get("entry_points", [])
        body_exit_points = body_graph.get("exit_points", [])

        id_map: dict[str, str] = {}
        for bn in body_nodes:
            old_id = bn.get("id", "")
            new_id = f"{prefix}{old_id}"
            id_map[old_id] = new_id
            bn["id"] = new_id
            if bn.get("name") == old_id:
                bn["name"] = new_id

        for be in body_edges:
            be["id"] = f"{prefix}{be.get('id', '')}"
            if be.get("source_node_id") in id_map:
                be["source_node_id"] = id_map[be["source_node_id"]]
            if be.get("target_node_id") in id_map:
                be["target_node_id"] = id_map[be["target_node_id"]]

        gate_id = f"{wl_id}__gate"
        gate_node: dict[str, Any] = {
            "id": gate_id,
            "name": f"{wl_id} gate",
            "node_type": "gate",
            "condition": condition,
            "gate_mode": "while",
            "max_iterations": max_iterations,
            "input_ports": [{"name": "input", "schema": {}}],
            "output_ports": [
                {"name": "continue", "description": "Loop back — condition still true"},
                {"name": "done", "description": "Exit loop — condition is false"},
            ],
            "position": wl_node.get("position", {"x": 0.0, "y": 0.0}),
        }

        body_entry_ids = [id_map.get(ep, ep) for ep in body_entry_points]
        body_exit_ids = [id_map.get(ep, ep) for ep in body_exit_points]

        if not body_entry_ids or not body_exit_ids:
            logger.warning(
                "Skipping while_loop '%s' migration: empty entry or exit points",
                wl_id,
            )
            continue

        body_node_map = {n["id"]: n for n in body_nodes}

        new_body_edges: list[dict[str, Any]] = []
        exit_id = body_exit_ids[0]
        exit_port = "result"
        exit_node = body_node_map.get(exit_id)
        if exit_node:
            ports = exit_node.get("output_ports", [])
            if ports:
                exit_port = ports[0].get("name", "result")
        new_body_edges.append({
            "id": f"{prefix}exit_to_gate",
            "edge_type": "data",
            "source_node_id": exit_id,
            "source_port": exit_port,
            "target_node_id": gate_id,
            "target_port": "input",
        })

        entry_id = body_entry_ids[0]
        entry_port = "input"
        entry_node = body_node_map.get(entry_id)
        if entry_node:
            ports = entry_node.get("input_ports", [])
            if ports:
                entry_port = ports[0].get("name", "input")
        new_body_edges.append({
            "id": f"{prefix}gate_continue",
            "edge_type": "data",
            "source_node_id": gate_id,
            "source_port": "continue",
            "target_node_id": entry_id,
            "target_port": entry_port,
        })

        downstream_edges = [
            e for e in edges
            if e.get("source_node_id") == wl_id
        ]
        upstream_edges = [
            e for e in edges
            if e.get("target_node_id") == wl_id
        ]

        for de in downstream_edges:
            de["source_node_id"] = gate_id
            de["source_port"] = "done"

        for ue in upstream_edges:
            ue["target_node_id"] = entry_id

        nodes[:] = [n for n in nodes if n.get("id") != wl_id]
        nodes.extend(body_nodes)
        nodes.append(gate_node)

        edges.extend(body_edges)
        edges.extend(new_body_edges)

        sub_graphs.pop(body_key, None)

        entry_points = graph_dict.get("entry_points", [])
        exit_points = graph_dict.get("exit_points", [])
        if wl_id in entry_points:
            idx = entry_points.index(wl_id)
            if body_entry_ids:
                entry_points[idx] = body_entry_ids[0]
            else:
                entry_points.pop(idx)
        if wl_id in exit_points:
            idx = exit_points.index(wl_id)
            exit_points[idx] = gate_id

    return graph_dict


def migrate_graph(graph_dict: dict[str, Any]) -> dict[str, Any]:
    """Apply all available migrations to a graph dict.

    Non-destructive — returns a new dict.
    """
    result = copy.deepcopy(graph_dict)
    result = migrate_if_else_to_gate(result)
    result = migrate_while_loop_to_flat_gate(result)
    return result
