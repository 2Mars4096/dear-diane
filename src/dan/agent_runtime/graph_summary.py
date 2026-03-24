"""Graph revision hashing and summary serialization for agent runtimes."""

from __future__ import annotations

import hashlib
import json
import logging

from dan.chat_events import EdgeSummary, GraphSummary, NodeSummary
from dan.models.graph import Graph

logger = logging.getLogger(__name__)

__all__ = [
    "build_graph_summary",
    "compute_graph_revision",
    "serialize_for_prompt",
]


def _strip_revision_noise(value: object) -> object:
    """Remove non-semantic graph fields from revision hashing.

    Graph saves always refresh ``metadata.created_at`` / ``metadata.updated_at``.
    Those timestamps should not invalidate mutation previews or concurrency checks
    when the workflow structure itself is unchanged.
    """
    if isinstance(value, list):
        return [_strip_revision_noise(item) for item in value]
    if not isinstance(value, dict):
        return value

    is_graph_like = "nodes" in value and "edges" in value
    cleaned: dict[str, object] = {}
    for key, item in value.items():
        if key == "metadata" and is_graph_like and isinstance(item, dict):
            cleaned[key] = {
                meta_key: _strip_revision_noise(meta_val)
                for meta_key, meta_val in item.items()
                if meta_key not in {"created_at", "updated_at"}
            }
            continue
        cleaned[key] = _strip_revision_noise(item)
    return cleaned


def compute_graph_revision(graph_dict: dict) -> str:
    """Stable SHA-256 hash of the graph for concurrency checks.

    Normalizes through the Pydantic ``Graph`` model so that missing default
    fields (``version``, ``sub_graphs``, ``entry_points``, ...) do not cause
    a revision mismatch between callers that round-trip through Pydantic and
    those that hash the raw dict read from disk. Save-time timestamps are
    stripped so noop autosaves do not spuriously invalidate previews.
    """
    try:
        normalized = json.loads(Graph.model_validate(graph_dict).model_dump_json())
    except Exception:
        normalized = graph_dict
    canonical = json.dumps(
        _strip_revision_noise(normalized),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_graph_summary(graph: Graph, workflow_id: str) -> GraphSummary:
    """Build a graph summary, including the empty-graph case."""
    nodes: list[NodeSummary] = []
    for node in graph.nodes:
        model_val: str | None = getattr(node, "model", None)
        snippet: str | None = None
        prompt_template = getattr(node, "prompt_template", None)
        if prompt_template:
            snippet = prompt_template[:200]

        nodes.append(NodeSummary(
            id=node.id,
            name=node.name,
            node_type=node.node_type,
            description=node.description,
            input_ports=[port.name for port in node.input_ports],
            output_ports=[port.name for port in node.output_ports],
            model=model_val,
            prompt_snippet=snippet,
        ))

    edges = [
        EdgeSummary(
            edge_type=edge.edge_type,
            source_node_id=edge.source_node_id,
            source_port=edge.source_port,
            target_node_id=edge.target_node_id,
            target_port=edge.target_port,
        )
        for edge in graph.edges
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


def _format_node_line(node: NodeSummary) -> str:
    parts = [f"  - {node.id} [{node.node_type}]"]
    if node.model:
        parts.append(f"model={node.model}")
    if node.input_ports:
        parts.append(f"in: {', '.join(node.input_ports)}")
    if node.output_ports:
        parts.append(f"out: {', '.join(node.output_ports)}")
    return " | ".join(parts)


def serialize_for_prompt(summary: GraphSummary, max_tokens: int = 4000) -> str:
    """Serialize a graph summary to a compact prompt-friendly string."""
    max_chars = max_tokens * 4

    header = (
        f'Workflow: "{summary.name}" ({summary.node_count} nodes, '
        f"{summary.edge_count} edges)"
    )
    if summary.description:
        header += f"\nDescription: {summary.description}"

    node_lines = [_format_node_line(node) for node in summary.nodes]
    edge_lines = [
        f"  {edge.source_node_id}.{edge.source_port} -> "
        f"{edge.target_node_id}.{edge.target_port} [{edge.edge_type}]"
        for edge in summary.edges
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
        for node in summary.nodes:
            parts = [f"  - {node.id} [{node.node_type}]"]
            if node.model:
                parts.append(f"model={node.model}")
            if level < 2:
                if node.input_ports:
                    parts.append(f"in: {', '.join(node.input_ports)}")
                if node.output_ports:
                    parts.append(f"out: {', '.join(node.output_ports)}")
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
