"""Graph revision hashing and summary serialization."""

from __future__ import annotations

import hashlib
import json
import logging

from dan.models.graph import Graph
from dan.server.chat.events import EdgeSummary, GraphSummary, NodeSummary

logger = logging.getLogger(__name__)


def compute_graph_revision(graph_dict: dict) -> str:
    """Stable SHA-256 hash of the graph for concurrency checks.

    Normalizes through the Pydantic ``Graph`` model so that missing default
    fields (``version``, ``sub_graphs``, ``entry_points``, …) do not cause
    a revision mismatch between callers that round-trip through Pydantic and
    those that hash the raw dict read from disk.
    """
    try:
        normalized = json.loads(Graph.model_validate(graph_dict).model_dump_json())
    except Exception:
        normalized = graph_dict
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_graph_summary(graph: Graph, workflow_id: str) -> GraphSummary:
    """Build a GraphSummary from a Graph. Handles empty graph (nodes=[], edges=[]).

    For empty graph, returns valid GraphSummary with node_count=0, edge_count=0,
    and revision from hash of the canonical empty structure.
    """
    nodes: list[NodeSummary] = []
    for n in graph.nodes:
        model_val: str | None = getattr(n, "model", None)
        snippet: str | None = None
        prompt_template = getattr(n, "prompt_template", None)
        if prompt_template:
            snippet = prompt_template[:200]

        nodes.append(NodeSummary(
            id=n.id,
            name=n.name,
            node_type=n.node_type,
            description=n.description,
            input_ports=[p.name for p in n.input_ports],
            output_ports=[p.name for p in n.output_ports],
            model=model_val,
            prompt_snippet=snippet,
        ))

    edges = [
        EdgeSummary(
            edge_type=e.edge_type,
            source_node_id=e.source_node_id,
            source_port=e.source_port,
            target_node_id=e.target_node_id,
            target_port=e.target_port,
        )
        for e in graph.edges
    ]

    graph_dict = json.loads(graph.model_dump_json())
    revision = compute_graph_revision(graph_dict)

    return GraphSummary(
        workflow_id=workflow_id,
        name=graph.metadata.name,
        description=graph.metadata.description,
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
        nodes=nodes,
        edges=edges,
        entry_points=graph.entry_points,
        exit_points=graph.exit_points,
        revision=revision,
    )


def _format_node_line(n: NodeSummary) -> str:
    parts = [f"  - {n.id} [{n.node_type}]"]
    if n.model:
        parts.append(f"model={n.model}")
    if n.input_ports:
        parts.append(f"in: {', '.join(n.input_ports)}")
    if n.output_ports:
        parts.append(f"out: {', '.join(n.output_ports)}")
    return " | ".join(parts)


def serialize_for_prompt(summary: GraphSummary, max_tokens: int = 4000) -> str:
    max_chars = max_tokens * 4

    header = (
        f'Workflow: "{summary.name}" ({summary.node_count} nodes, '
        f"{summary.edge_count} edges)"
    )
    if summary.description:
        header += f"\nDescription: {summary.description}"

    node_lines = [_format_node_line(n) for n in summary.nodes]
    edge_lines = [
        f"  {e.source_node_id}.{e.source_port} -> "
        f"{e.target_node_id}.{e.target_port} [{e.edge_type}]"
        for e in summary.edges
    ]

    entry_str = ", ".join(summary.entry_points) if summary.entry_points else "(none)"
    exit_str = ", ".join(summary.exit_points) if summary.exit_points else "(none)"
    footer = f"Entry: {entry_str} | Exit: {exit_str}"

    sections = [
        header,
        "Nodes:\n" + "\n".join(node_lines) if node_lines else "Nodes: (none)",
        "Edges:\n" + "\n".join(edge_lines) if edge_lines else "Edges: (none)",
        footer,
    ]
    text = "\n".join(sections)

    if len(text) <= max_chars:
        return text

    for level in range(3):
        truncated_lines: list[str] = []
        for n in summary.nodes:
            parts = [f"  - {n.id} [{n.node_type}]"]
            if n.model:
                parts.append(f"model={n.model}")
            if level < 2:
                if n.input_ports:
                    parts.append(f"in: {', '.join(n.input_ports)}")
                if n.output_ports:
                    parts.append(f"out: {', '.join(n.output_ports)}")
            truncated_lines.append(" | ".join(parts))

        sections = [
            header,
            "Nodes:\n" + "\n".join(truncated_lines),
            "Edges:\n" + "\n".join(edge_lines) if edge_lines else "Edges: (none)",
            footer,
        ]
        text = "\n".join(sections)
        if len(text) <= max_chars:
            return text

    while len(text) > max_chars and edge_lines:
        edge_lines.pop()
        sections[-2] = "Edges:\n" + "\n".join(edge_lines) + "\n  ... (truncated)"
        text = "\n".join(sections)

    return text[:max_chars]
