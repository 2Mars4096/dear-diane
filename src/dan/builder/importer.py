"""Graph import utilities — convert a standalone Graph into a CompositeNode.

Mirrors the editor's ``graphImporter.ts`` so that a Graph built and
serialised in Python can be embedded as a single composite node inside
another workflow, enabling progressive workflow wrapping.
"""

from __future__ import annotations

from dan.models.graph import Graph
from dan.models.ports import InputPort, OutputPort


def namespace_graph(graph: Graph, prefix: str) -> Graph:
    """Deep-copy *graph* with all internal IDs prefixed to avoid collisions.

    Namespaced items: node IDs, edge IDs, edge source/target refs,
    body_graph keys, sub_graph dict keys (recursively), and
    entry/exit point lists.
    """
    id_map: dict[str, str] = {n.id: f"{prefix}{n.id}" for n in graph.nodes}

    new_nodes = []
    for node in graph.nodes:
        d = node.model_dump()
        d["id"] = id_map[node.id]
        if "body_graph" in d and d["body_graph"]:
            d["body_graph"] = f"{prefix}{d['body_graph']}"
        if "sub_workers" in d and isinstance(d["sub_workers"], dict):
            d["sub_workers"] = {
                name: f"{prefix}{ref}" if ref else ref
                for name, ref in d["sub_workers"].items()
            }
        new_nodes.append(d)

    new_edges = []
    for edge in graph.edges:
        d = edge.model_dump()
        d["id"] = f"{prefix}{d['id']}"
        if d["source_node_id"] in id_map:
            d["source_node_id"] = id_map[d["source_node_id"]]
        if d["target_node_id"] in id_map:
            d["target_node_id"] = id_map[d["target_node_id"]]
        new_edges.append(d)

    new_entry = [id_map.get(ep, ep) for ep in graph.entry_points]
    new_exit = [id_map.get(ep, ep) for ep in graph.exit_points]

    new_sub_graphs: dict[str, Graph] = {}
    for key, sub in graph.sub_graphs.items():
        new_sub_graphs[f"{prefix}{key}"] = namespace_graph(sub, prefix)

    return Graph.model_validate({
        "version": graph.version,
        "metadata": graph.metadata.model_dump(),
        "nodes": new_nodes,
        "edges": new_edges,
        "sub_graphs": {k: v.model_dump() for k, v in new_sub_graphs.items()},
        "entry_points": new_entry,
        "exit_points": new_exit,
        "shared_context": [sc.model_dump() for sc in graph.shared_context],
        "artifact_refs": [ar.model_dump() for ar in graph.artifact_refs],
        "worker_resources": graph.worker_resources,
    })


def derive_ports(
    graph: Graph,
) -> tuple[list[InputPort], list[OutputPort], dict[str, str], dict[str, str]]:
    """Derive composite input/output ports from a graph's entry/exit nodes.

    Returns ``(input_ports, output_ports, input_mappings, output_mappings)``.

    The mapping values use ``"node_id::port_name"`` qualification so the
    engine can resolve which inner node receives each outer port value.
    This matches the convention in ``editor/src/lib/graphImporter.ts``.
    """
    entry_set = set(graph.entry_points)
    exit_set = set(graph.exit_points)
    entry_nodes = [n for n in graph.nodes if n.id in entry_set]
    exit_nodes = [n for n in graph.nodes if n.id in exit_set]

    input_ports: list[InputPort] = []
    output_ports: list[OutputPort] = []
    input_mappings: dict[str, str] = {}
    output_mappings: dict[str, str] = {}
    used_in: set[str] = set()
    used_out: set[str] = set()

    for node in entry_nodes:
        for port in node.input_ports:
            outer = port.name
            if outer in used_in:
                outer = f"{node.id}__{port.name}"
            used_in.add(outer)
            input_ports.append(InputPort(name=outer))
            input_mappings[outer] = f"{node.id}::{port.name}"

    for node in reversed(exit_nodes):
        for port in node.output_ports:
            outer = port.name
            if outer in used_out:
                outer = f"{node.id}__{port.name}"
            used_out.add(outer)
            output_ports.append(OutputPort(name=outer))
            output_mappings[f"{node.id}::{port.name}"] = outer

    return input_ports, output_ports, input_mappings, output_mappings
