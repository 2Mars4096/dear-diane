"""Server-side graph layout. Topological level placement for DAGs."""

from __future__ import annotations

import copy
import os
from collections import defaultdict, deque
from typing import Any


NODE_WIDTH = 180
_FLATTEN_LOOP_BODIES = os.environ.get("DAN_FLATTEN_LOOP_BODIES", "0").lower() in ("1", "true", "yes")
NODE_HEIGHT = 100
RANK_SEP = 80
NODE_SEP = 50


def _topological_levels(node_ids: set[str], edges: list[dict[str, Any]]) -> dict[str, int]:
    """Assign level (0=roots) to each node by longest path from any root."""
    in_degree: dict[str, int] = defaultdict(int)
    out_adj: dict[str, list[str]] = defaultdict(list)
    for n in node_ids:
        in_degree.setdefault(n, 0)
    for e in edges:
        src = e.get("source_node_id") or e.get("source")
        tgt = e.get("target_node_id") or e.get("target")
        if src and tgt and src in node_ids and tgt in node_ids:
            out_adj[src].append(tgt)
            in_degree[tgt] += 1

    # Kahn: process level by level
    levels: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque()
    for n in node_ids:
        if in_degree[n] == 0:
            queue.append((n, 0))

    while queue:
        n, lev = queue.popleft()
        levels[n] = max(levels.get(n, 0), lev)
        for succ in out_adj[n]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append((succ, lev + 1))

    # Nodes unreachable from roots (e.g. in cycles) get max+1
    max_lev = max(levels.values(), default=0)
    for n in node_ids:
        if n not in levels:
            levels[n] = max_lev + 1
    return levels


def flatten_loop_bodies(graph: dict[str, Any]) -> None:
    """Inline while-loop body composites into the main graph so department nodes appear at top level.

    Finds gate(while) nodes whose 'continue' port targets a composite. Inlines that composite's
    subgraph into the parent, replaces gate→composite and composite→gate edges with
    gate→body_entry and body_exit→gate, then removes the composite.
    Mutates graph in place.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    sub_graphs = graph.get("sub_graphs") or {}
    node_ids = {n["id"] for n in nodes}
    if not node_ids:
        return

    # Find gate(while) + composite loop body pairs
    gate_to_body: dict[str, tuple[str, str]] = {}  # gate_id -> (composite_id, body_graph_key)
    for e in edges:
        src = e.get("source_node_id") or e.get("source")
        tgt = e.get("target_node_id") or e.get("target")
        src_port = e.get("source_port", "")
        if not src or not tgt:
            continue
        src_node = next((n for n in nodes if n.get("id") == src), None)
        tgt_node = next((n for n in nodes if n.get("id") == tgt), None)
        if not src_node or not tgt_node:
            continue
        if (
            src_node.get("node_type") == "gate"
            and src_node.get("gate_mode") == "while"
            and src_port == "continue"
            and tgt_node.get("node_type") == "composite"
        ):
            body_key = tgt_node.get("body_graph")
            if body_key and body_key in sub_graphs:
                gate_to_body[src] = (tgt, body_key)

    for gate_id, (composite_id, body_key) in gate_to_body.items():
        body = sub_graphs.get(body_key)
        if not body or not isinstance(body, dict):
            continue
        body_nodes = body.get("nodes") or []
        body_edges = body.get("edges") or []
        body_entry = (body.get("entry_points") or [None])[0]
        body_exit = (body.get("exit_points") or [None])[0]
        if not body_entry or not body_exit:
            continue

        prefix = f"{composite_id}__"
        id_map: dict[str, str] = {}
        for n in body_nodes:
            old_id = n.get("id")
            if not old_id:
                continue
            new_id = f"{prefix}{old_id}"
            id_map[old_id] = new_id

        new_nodes = []
        for n in body_nodes:
            ncopy = copy.deepcopy(n)
            old_id = ncopy.get("id")
            if old_id and old_id in id_map:
                ncopy["id"] = id_map[old_id]
                if ncopy.get("node_type") == "input" and not (ncopy.get("input_ports") or []):
                    ncopy.setdefault("input_ports", []).append({
                        "name": "input",
                        "json_schema": {"type": "object"},
                        "required": True,
                        "description": "",
                    })
            new_nodes.append(ncopy)

        new_edges = []
        for i, e in enumerate(body_edges):
            ecopy = copy.deepcopy(e)
            src = ecopy.get("source_node_id") or ecopy.get("source")
            tgt = ecopy.get("target_node_id") or ecopy.get("target")
            if src in id_map:
                ecopy["source_node_id"] = id_map[src]
                ecopy["source"] = id_map[src]
            if tgt in id_map:
                ecopy["target_node_id"] = id_map[tgt]
                ecopy["target"] = id_map[tgt]
            ecopy["id"] = f"{prefix}edge_{i}"
            new_edges.append(ecopy)

        entry_id = id_map.get(body_entry, body_entry)
        exit_node = next((n for n in body_nodes if n.get("id") == body_exit), None)
        exit_port = "result"
        if exit_node and (exit_node.get("output_ports") or []):
            exit_port = exit_node["output_ports"][0].get("name", "result")

        nodes.extend(new_nodes)
        edge_counter = len(edges) + len(new_edges)
        edges.extend(new_edges)
        edges.append({
            "id": f"edge_{gate_id}_to_{entry_id}_{edge_counter}",
            "source_node_id": gate_id,
            "source_port": "continue",
            "target_node_id": entry_id,
            "target_port": "input",
            "ui": {},
            "metadata": {},
            "edge_type": "data",
        })
        edge_counter += 1
        edges.append({
            "id": f"edge_{id_map.get(body_exit, body_exit)}_to_{gate_id}_{edge_counter}",
            "source_node_id": id_map.get(body_exit, body_exit),
            "source_port": exit_port,
            "target_node_id": gate_id,
            "target_port": "input",
            "ui": {},
            "metadata": {},
            "edge_type": "data",
        })

        edges[:] = [
            e for e in edges
            if (e.get("source_node_id") or e.get("source")) != gate_id
            or (e.get("target_node_id") or e.get("target")) != composite_id
        ]
        edges[:] = [
            e for e in edges
            if (e.get("source_node_id") or e.get("source")) != composite_id
            or (e.get("target_node_id") or e.get("target")) != gate_id
        ]
        nodes[:] = [n for n in nodes if n.get("id") != composite_id]
        if body_key in sub_graphs:
            del sub_graphs[body_key]

        node_ids.update(n["id"] for n in new_nodes)


def apply_layout(graph: dict[str, Any]) -> dict[str, Any]:
    """Apply topological layout to graph nodes. Mutates graph in place, returns it."""
    if _FLATTEN_LOOP_BODIES:
        flatten_loop_bodies(graph)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    node_ids = {n["id"] for n in nodes}
    if not node_ids:
        return graph

    levels = _topological_levels(node_ids, edges)
    # Group by level, sort for determinism
    by_level: dict[int, list[str]] = defaultdict(list)
    for nid, lev in levels.items():
        by_level[lev].append(nid)
    for lev in by_level:
        by_level[lev].sort()

    # Position: x = level * (NODE_WIDTH + RANK_SEP), y = index * (NODE_HEIGHT + NODE_SEP)
    positions: dict[str, tuple[float, float]] = {}
    for lev in sorted(by_level.keys()):
        for i, nid in enumerate(by_level[lev]):
            x = lev * (NODE_WIDTH + RANK_SEP)
            y = i * (NODE_HEIGHT + NODE_SEP)
            positions[nid] = (x, y)

    # Center y per level
    for lev in sorted(by_level.keys()):
        nids = by_level[lev]
        if len(nids) > 1:
            total_h = (len(nids) - 1) * (NODE_HEIGHT + NODE_SEP)
            offset = -total_h / 2
            for i, nid in enumerate(nids):
                x, _ = positions[nid]
                y = offset + i * (NODE_HEIGHT + NODE_SEP)
                positions[nid] = (x, y)

    for node in nodes:
        nid = node.get("id")
        if nid and nid in positions:
            x, y = positions[nid]
            if "position" not in node:
                node["position"] = {}
            node["position"]["x"] = x
            node["position"]["y"] = y

    # Recurse into sub_graphs
    for key, sub in (graph.get("sub_graphs") or {}).items():
        if isinstance(sub, dict) and "nodes" in sub:
            apply_layout(sub)

    return graph
