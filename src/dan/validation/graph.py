"""Graph well-formedness validation.

Checks:
  1. Entry / exit points reference existing nodes.
  2. No orphan nodes (every node reachable from an entry point).
  3. All required input ports are connected.
  4. Sub-graph references resolve.
  5. All edges reference existing nodes and ports.
  6. Data-edge schema compatibility (delegates to schema.py).
  7. Empty-schema data edges produce warnings.
  8. Shared-context keys are declared at graph level.
  9. ContextEdge modes match node read_set / write_set declarations.
  10. Data-edge cycles only allowed through control-flow nodes.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from dan.models.context import ContextMode
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    WhileLoopNode,
)
from dan.models.edges import ContextEdge, ControlEdge, DataEdge
from dan.validation.schema import check_schema_compatible

try:
    from dan.models.control_flow import GateNode  # noqa: F401
    _HAS_GATE_NODE = True
except ImportError:
    _HAS_GATE_NODE = False

if TYPE_CHECKING:
    from dan.models.graph import Graph

_LOOP_NODE_TYPES = frozenset({"while_loop", "for_each"})
_GATE_LOOP_TYPES = frozenset({"gate"})


def validate_graph(graph: "Graph") -> list[str]:
    """Run all well-formedness checks and return a list of errors."""
    errors: list[str] = []
    errors.extend(_check_entry_exit(graph))
    errors.extend(_check_reachability(graph))
    errors.extend(_check_required_ports(graph))
    errors.extend(_check_sub_graph_refs(graph))
    errors.extend(_check_edge_endpoints(graph))
    errors.extend(_check_data_edge_schemas(graph))
    errors.extend(_check_context_declarations(graph))
    errors.extend(_check_context_edge_permissions(graph))
    errors.extend(_check_data_cycles(graph))
    errors.extend(_validate_gate_cycles(graph))
    errors.extend(_check_deprecated_edge_conditions(graph))
    return errors


# ------------------------------------------------------------------
# Individual checks
# ------------------------------------------------------------------


def _check_entry_exit(graph: "Graph") -> list[str]:
    errors: list[str] = []
    node_ids = {n.id for n in graph.nodes}

    for ep in graph.entry_points:
        if ep not in node_ids:
            errors.append(f"Entry point '{ep}' does not match any node ID")
    for xp in graph.exit_points:
        if xp not in node_ids:
            errors.append(f"Exit point '{xp}' does not match any node ID")

    if graph.nodes and not graph.entry_points:
        errors.append("Graph has nodes but no entry points")

    return errors


def _check_reachability(graph: "Graph") -> list[str]:
    if not graph.entry_points or not graph.nodes:
        return []

    adjacency: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for e in graph.edges:
        if e.source_node_id in adjacency:
            adjacency[e.source_node_id].add(e.target_node_id)

    visited: set[str] = set()
    queue: deque[str] = deque(graph.entry_points)
    while queue:
        nid = queue.popleft()
        if nid in visited:
            continue
        visited.add(nid)
        for neighbor in adjacency.get(nid, set()):
            if neighbor not in visited:
                queue.append(neighbor)

    unreachable = {n.id for n in graph.nodes} - visited
    return [f"Node '{nid}' is unreachable from any entry point" for nid in sorted(unreachable)]


def _check_required_ports(graph: "Graph") -> list[str]:
    errors: list[str] = []
    entry_set = set(graph.entry_points)
    connected_inputs: dict[str, set[str]] = {n.id: set() for n in graph.nodes}
    for e in graph.edges:
        if e.target_node_id in connected_inputs:
            connected_inputs[e.target_node_id].add(e.target_port)

    for node in graph.nodes:
        if node.id in entry_set:
            continue
        for port in node.input_ports:
            if port.required and port.name not in connected_inputs.get(node.id, set()):
                errors.append(
                    f"Node '{node.id}' has required input port '{port.name}' that is not connected"
                )
    return errors


def _check_sub_graph_refs(graph: "Graph") -> list[str]:
    errors: list[str] = []
    for node in graph.nodes:
        ref: str | None = None
        if isinstance(node, (WhileLoopNode, ForEachNode, CompositeNode)):
            ref = node.body_graph
        if ref is not None and ref not in graph.sub_graphs:
            errors.append(
                f"Node '{node.id}' references sub-graph '{ref}' which is not in Graph.sub_graphs"
            )
    return errors


def _check_edge_endpoints(graph: "Graph") -> list[str]:
    """Verify every edge references existing nodes and ports."""
    errors: list[str] = []
    node_map = {n.id: n for n in graph.nodes}

    for edge in graph.edges:
        src = node_map.get(edge.source_node_id)
        tgt = node_map.get(edge.target_node_id)
        if src is None:
            errors.append(f"Edge '{edge.id}': source node '{edge.source_node_id}' not found")
        if tgt is None:
            errors.append(f"Edge '{edge.id}': target node '{edge.target_node_id}' not found")
        if src is None or tgt is None:
            continue

        src_port = next((p for p in src.output_ports if p.name == edge.source_port), None)
        tgt_port = next((p for p in tgt.input_ports if p.name == edge.target_port), None)
        if src_port is None:
            available = sorted(p.name for p in src.output_ports)
            errors.append(
                f"Edge '{edge.id}': source node '{src.id}' has no output port '{edge.source_port}'"
                f" (available: {available})"
            )
        if tgt_port is None:
            available = sorted(p.name for p in tgt.input_ports)
            errors.append(
                f"Edge '{edge.id}': target node '{tgt.id}' has no input port '{edge.target_port}'"
                f" (available: {available})"
            )

    return errors


def _check_data_edge_schemas(graph: "Graph") -> list[str]:
    """Check schema compatibility on data edges, warn on empty schemas."""
    errors: list[str] = []
    node_map = {n.id: n for n in graph.nodes}

    for edge in graph.edges:
        if not isinstance(edge, DataEdge):
            continue
        src = node_map.get(edge.source_node_id)
        tgt = node_map.get(edge.target_node_id)
        if src is None or tgt is None:
            continue

        src_port = next((p for p in src.output_ports if p.name == edge.source_port), None)
        tgt_port = next((p for p in tgt.input_ports if p.name == edge.target_port), None)
        if src_port is None or tgt_port is None:
            continue

        if getattr(edge, 'spread', False):
            src_type = (src_port.json_schema or {}).get("type") if src_port.json_schema else None
            tgt_type = (tgt_port.json_schema or {}).get("type") if tgt_port.json_schema else None
            if (src_type is not None and src_type != "object") or (tgt_type is not None and tgt_type != "object"):
                errors.append(
                    f"Edge '{edge.id}': spread edge requires object-type ports "
                    f"(source '{edge.source_port}' type={src_type!r}, "
                    f"target '{edge.target_port}' type={tgt_type!r})"
                )
            continue

        src_empty = not src_port.json_schema
        tgt_empty = not tgt_port.json_schema
        if src_empty or tgt_empty:
            errors.append(
                f"Edge '{edge.id}': untyped data edge "
                f"(source port schema empty: {src_empty}, target port schema empty: {tgt_empty}) "
                f"— schema safety bypassed"
            )
            continue

        schema_errors = check_schema_compatible(src_port.json_schema, tgt_port.json_schema)
        for se in schema_errors:
            errors.append(f"Edge '{edge.id}': schema incompatibility — {se}")

    return errors


def _check_context_declarations(graph: "Graph") -> list[str]:
    """Verify every ContextEdge references a graph-level declared key."""
    errors: list[str] = []
    declared_keys = {d.key for d in graph.shared_context}

    for edge in graph.edges:
        if not isinstance(edge, ContextEdge):
            continue
        if edge.context_key not in declared_keys:
            errors.append(
                f"ContextEdge '{edge.id}' references undeclared context key '{edge.context_key}'"
            )
    return errors


def _check_context_edge_permissions(graph: "Graph") -> list[str]:
    """Verify ContextEdge modes match the source node's declared read_set / write_set."""
    errors: list[str] = []
    node_map = {n.id: n for n in graph.nodes}

    for edge in graph.edges:
        if not isinstance(edge, ContextEdge):
            continue
        src_node = node_map.get(edge.source_node_id)
        if src_node is None:
            continue

        read_keys: set[str] = set()
        write_keys: set[str] = set()
        if hasattr(src_node, "read_set"):
            read_keys = {d.key for d in src_node.read_set}
        if hasattr(src_node, "write_set"):
            write_keys = {d.key for d in src_node.write_set}

        key = edge.context_key
        if edge.mode == ContextMode.READ and key not in read_keys:
            errors.append(
                f"ContextEdge '{edge.id}': node '{src_node.id}' reads context key "
                f"'{key}' but does not declare it in read_set"
            )
        if edge.mode in (ContextMode.WRITE, ContextMode.APPEND) and key not in write_keys:
            errors.append(
                f"ContextEdge '{edge.id}': node '{src_node.id}' writes context key "
                f"'{key}' but does not declare it in write_set"
            )

    return errors


def _check_data_cycles(graph: "Graph") -> list[str]:
    """Detect data-edge cycles that do NOT pass through a loop or gate node."""
    data_adj: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    exempt_nodes = set()
    for node in graph.nodes:
        if node.node_type in _LOOP_NODE_TYPES or node.node_type in _GATE_LOOP_TYPES:
            exempt_nodes.add(node.id)

    for edge in graph.edges:
        if isinstance(edge, DataEdge):
            if edge.source_node_id in data_adj:
                data_adj[edge.source_node_id].append(edge.target_node_id)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in data_adj}
    errors: list[str] = []

    def dfs(nid: str) -> bool:
        color[nid] = GRAY
        for neighbor in data_adj.get(nid, []):
            if neighbor in exempt_nodes:
                continue
            if color.get(neighbor) == GRAY:
                errors.append(
                    f"Data-edge cycle detected through non-loop node '{neighbor}'"
                )
                return True
            if color.get(neighbor) == WHITE and dfs(neighbor):
                return True
        color[nid] = BLACK
        return False

    for nid in list(data_adj):
        if color[nid] == WHITE:
            dfs(nid)

    return errors


def _validate_gate_cycles(graph: "Graph") -> list[str]:
    """Validate that any cycles involving gate nodes are well-formed.

    For each gate node, check if there is a data-edge path from any of its
    successors back to itself.  If a cycle exists:
      - The gate must be in ``while`` mode.
      - ``max_iterations`` must be >= 1.
      - No other while-gate may share the same cycle region.
    """
    errors: list[str] = []
    node_ids = {n.id for n in graph.nodes}
    node_map = {n.id: n for n in graph.nodes}

    adj: dict[str, list[str]] = defaultdict(list)
    rev_adj: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if isinstance(edge, DataEdge) and edge.target_node_id in node_ids:
            adj[edge.source_node_id].append(edge.target_node_id)
            rev_adj[edge.target_node_id].append(edge.source_node_id)

    cycle_regions: dict[str, set[str]] = {}

    for node in graph.nodes:
        if node.node_type != "gate":
            continue

        visited: set[str] = set()
        queue: deque[str] = deque(adj.get(node.id, []))
        forms_cycle = False
        while queue:
            n = queue.popleft()
            if n == node.id:
                forms_cycle = True
                break
            if n in visited:
                continue
            visited.add(n)
            for succ in adj.get(n, []):
                if succ not in visited:
                    queue.append(succ)

        if not forms_cycle:
            continue

        # Only flag as cycle-creator if this gate receives a back-edge (i.e. a node in
        # its forward closure has an edge TO this gate). if_else inside a while body
        # is reachable in a cycle but doesn't receive the back-edge; the while gate does.
        fwd_from_gate: set[str] = set()
        q_fwd: deque[str] = deque(adj.get(node.id, []))
        while q_fwd:
            n = q_fwd.popleft()
            if n in fwd_from_gate:
                continue
            fwd_from_gate.add(n)
            for succ in adj.get(n, []):
                if succ not in fwd_from_gate:
                    q_fwd.append(succ)
        has_back_edge = any(
            node.id in adj.get(n, []) for n in fwd_from_gate
        )
        if not has_back_edge:
            continue

        gmode = getattr(node, "gate_mode", None)
        if gmode != "while":
            errors.append(
                f"Gate '{node.id}' creates a cycle but is in '{gmode}' mode — "
                f"must be 'while' mode for loop-back"
            )
            continue

        max_iter = getattr(node, "max_iterations", 0)
        if max_iter < 1:
            errors.append(
                f"Gate '{node.id}' has max_iterations={max_iter} — must be >= 1"
            )

        fwd: set[str] = set()
        q: deque[str] = deque(adj.get(node.id, []))
        while q:
            n = q.popleft()
            if n in fwd:
                continue
            fwd.add(n)
            for succ in adj.get(n, []):
                if succ not in fwd:
                    q.append(succ)

        rev: set[str] = set()
        q = deque(rev_adj.get(node.id, []))
        while q:
            n = q.popleft()
            if n in rev:
                continue
            rev.add(n)
            for pred in rev_adj.get(n, []):
                if pred not in rev:
                    q.append(pred)

        cycle_regions[node.id] = (fwd & rev) | {node.id}

    checked_pairs: set[tuple[str, str]] = set()
    for g1, region1 in cycle_regions.items():
        for g2, region2 in cycle_regions.items():
            if g1 >= g2:
                continue
            pair = (g1, g2)
            if pair in checked_pairs:
                continue
            checked_pairs.add(pair)
            if region1 & region2:
                errors.append(
                    f"Ambiguous multi-gate cycle: gates '{g1}' and '{g2}' share "
                    f"overlapping cycle regions — only one while-gate per cycle is allowed"
                )

    return errors


def _check_deprecated_edge_conditions(graph: "Graph") -> list[str]:
    """Emit deprecation warnings for ControlEdge.condition usage."""
    warnings: list[str] = []
    for edge in graph.edges:
        if isinstance(edge, ControlEdge) and edge.condition:
            warnings.append(
                f"Edge {edge.source_node_id}->{edge.target_node_id} uses "
                f"deprecated ControlEdge.condition; prefer GateNode branch "
                f"ports for conditional routing"
            )
    return warnings
