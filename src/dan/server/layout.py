"""Server-side graph layout. Topological level placement for DAGs."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


NODE_WIDTH = 180
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


def apply_layout(graph: dict[str, Any]) -> dict[str, Any]:
    """Apply topological layout to graph nodes. Mutates graph in place, returns it."""
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
            _inject_orchestrator_department_groups(sub)

    return graph


def _inject_orchestrator_department_groups(graph: dict[str, Any]) -> None:
    """Add loop_groups for orchestrator and departments when both exist (multi-dept workflow)."""
    nodes = graph.get("nodes") or []
    node_ids = {n.get("id") for n in nodes}
    if "orchestrator" in node_ids and "department_strategy" in node_ids:
        meta = graph.setdefault("metadata", {})
        if not isinstance(meta, dict):
            return
        meta["loop_groups"] = [
            {
                "id": "lg-orchestrator",
                "label": "Orchestrator",
                "gateNodeId": "orchestrator",
                "memberNodeIds": ["orchestrator"],
                "collapsed": False,
            },
            {
                "id": "lg-departments",
                "label": "Departments",
                "gateNodeId": "department_strategy",
                "memberNodeIds": ["department_strategy"],
                "collapsed": False,
            },
        ]
