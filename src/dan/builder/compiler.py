"""Compiler — converts WorkflowBuilder state into a validated Graph model.

Responsibilities:
  1. Resolve f-string markers (<<dan:node_id:port_name>>) in prompt templates.
  2. Auto-generate InputPort / OutputPort definitions using per-node-type
     output contract map.
  3. Auto-generate edges from markers, >> chains, and PortRef connections.
  4. Assemble Graph with sub_graphs, entry_points, exit_points, shared_context.
  5. Validate via validate_graph() and raise BuildError on failures.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from dan.builder.refs import MARKER_PATTERN, PortRef, _sanitize_alias
from dan.models.context import ArtifactRef, ContextMode, SharedContextDeclaration
from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    GateNode,
    HumanInTheLoopNode,
    IfElseNode,
    ReduceNode,
    RouterNode,
    ValidatorNode,
    WhileLoopNode,
)
from dan.models.edges import ContextEdge, ControlEdge, DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import CodeOperator, LLMOperator, NodeBase, RAGOperator, ToolOperator
from dan.models.ports import InputPort, OutputPort
from dan.validation.graph import validate_graph

_VALIDATION_WARNING_PATTERNS = (
    "schema safety bypassed",
    "untyped data edge",
    "deprecated",
)

# ── Node-type output contract map ──────────────────────────────────────
# Maps node_type -> default output port name matching runtime executor
# behaviour. The compiler uses this instead of a universal "result" default.

DEFAULT_OUTPUT_PORTS: dict[str, str] = {
    "llm_operator": "text",
    "tool_operator": "result",
    "code_operator": "result",
    "rag_operator": "chunks",
    "if_else": "branch",
    "gate": "true",
    "while_loop": "result",
    "for_each": "results",
    "reduce": "result",
    "router": "route",
    "human_in_the_loop": "response",
    "validator": "valid",
    "composite": "result",
}

DEFAULT_INPUT_PORT = "input"


def default_output_port(node_type: str) -> str:
    return DEFAULT_OUTPUT_PORTS.get(node_type, "result")


class BuildError(Exception):
    """Raised when graph compilation or validation fails."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Build failed with {len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors))


# ── Internal data structures ───────────────────────────────────────────

@dataclass
class _PendingNode:
    """Builder-side representation of a node before compilation."""
    id: str
    node_type: str
    kwargs: dict[str, Any]
    explicit_input_ports: list[InputPort] = field(default_factory=list)
    explicit_output_ports: list[OutputPort] = field(default_factory=list)


@dataclass
class _PendingEdge:
    """Builder-side representation of an edge before compilation."""
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str
    edge_type: str = "data"
    condition: str | None = None
    context_key: str | None = None
    mode: ContextMode | str | None = None


@dataclass
class _PendingSubGraph:
    """A sub-graph attached to a control-flow node."""
    parent_node_id: str
    sub_graph_key: str
    graph: Graph


@dataclass
class _MarkerHit:
    """A resolved marker found in a prompt template."""
    source_node_id: str
    source_port: str
    target_node_id: str
    alias: str  # sanitized placeholder name used in the final template


# ── Compilation ────────────────────────────────────────────────────────

def compile_graph(
    name: str,
    description: str,
    tags: list[str],
    nodes: list[_PendingNode],
    edges: list[_PendingEdge],
    sub_graphs: list[_PendingSubGraph],
    shared_context: list[SharedContextDeclaration],
    port_ref_connections: list[tuple[PortRef, str, str]],
    artifact_refs: list[ArtifactRef] | None = None,
    *,
    validate: bool = True,
) -> Graph:
    """Compile builder state into a validated Graph model."""

    # Make local working copies so repeated build() calls are idempotent.
    working_nodes: list[_PendingNode] = copy.deepcopy(nodes)
    working_edges: list[_PendingEdge] = copy.deepcopy(edges)
    working_port_ref_connections = list(port_ref_connections)

    # ── Step 1: Resolve markers in prompt templates ────────────────
    marker_hits: list[_MarkerHit] = []
    for pn in working_nodes:
        if pn.node_type == "llm_operator" and "prompt_template" in pn.kwargs:
            pn.kwargs["prompt_template"], hits = _resolve_markers(
                pn.kwargs["prompt_template"], pn.id
            )
            marker_hits.extend(hits)

    # ── Step 2: Collect all edges ──────────────────────────────────
    all_edges: list[_PendingEdge] = list(working_edges)

    for hit in marker_hits:
        all_edges.append(_PendingEdge(
            source_node_id=hit.source_node_id,
            source_port=hit.source_port,
            target_node_id=hit.target_node_id,
            target_port=hit.alias,
            edge_type="data",
        ))

    for port_ref, target_node_id, target_port in working_port_ref_connections:
        all_edges.append(_PendingEdge(
            source_node_id=port_ref.node_id,
            source_port=port_ref.port_name,
            target_node_id=target_node_id,
            target_port=target_port,
            edge_type="data",
        ))

    # Deduplicate edges
    seen: set[tuple[str, str, str, str, str, str | None, str | None, str | None]] = set()
    deduped: list[_PendingEdge] = []
    for e in all_edges:
        mode = e.mode.value if isinstance(e.mode, ContextMode) else e.mode
        key = (
            e.source_node_id,
            e.source_port,
            e.target_node_id,
            e.target_port,
            e.edge_type,
            e.condition,
            e.context_key,
            mode,
        )
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    all_edges = deduped

    # ── Step 3: Auto-generate ports on nodes ───────────────────────
    _auto_generate_ports(working_nodes, all_edges)

    # ── Step 4: Build Pydantic node objects ────────────────────────
    graph_nodes: list[NodeBase] = []
    for pn in working_nodes:
        model_node = _build_node(pn)
        graph_nodes.append(model_node)

    # Ignore virtual entry references in the materialized graph.
    materialized_edges = [
        e for e in all_edges
        if e.source_node_id != "__entry__" and e.target_node_id != "__entry__"
    ]

    # ── Step 5: Build typed edge objects ───────────────────────────
    graph_edges: list[DataEdge | ControlEdge | ContextEdge] = []
    for i, e in enumerate(materialized_edges):
        graph_edges.append(_build_edge(e, i))

    # ── Step 6: Assemble sub-graphs ────────────────────────────────
    sub_graph_map: dict[str, Graph] = {}
    for sg in sub_graphs:
        sub_graph_map[sg.sub_graph_key] = sg.graph

    # ── Step 7: Determine entry/exit points ────────────────────────
    entry_points = _find_entry_points(working_nodes, materialized_edges)
    exit_points = _find_exit_points(working_nodes, materialized_edges)

    # ── Step 8: Assemble Graph ─────────────────────────────────────
    graph = Graph(
        version="dan_graph_v1",
        metadata=GraphMetadata(
            name=name,
            description=description,
            tags=tags,
        ),
        nodes=graph_nodes,
        edges=graph_edges,
        sub_graphs=sub_graph_map,
        entry_points=entry_points,
        exit_points=exit_points,
        shared_context=shared_context,
        artifact_refs=artifact_refs or [],
    )

    # ── Step 9: Validate ───────────────────────────────────────────
    if validate:
        errors = validate_graph(graph)
        fatal = [e for e in errors if not _is_warning(e)]
        if fatal:
            raise BuildError(fatal)

    return graph


# ── Helpers ────────────────────────────────────────────────────────────

def _is_warning(msg: str) -> bool:
    lower = msg.lower()
    return any(p in lower for p in _VALIDATION_WARNING_PATTERNS)


def _resolve_markers(
    template: str, target_node_id: str
) -> tuple[str, list[_MarkerHit]]:
    """Replace <<dan:node_id:port>> markers with sanitized aliases."""
    hits: list[_MarkerHit] = []
    used_aliases: dict[str, int] = {}

    def replacer(m: re.Match) -> str:
        src_node = m.group(1)
        src_port = m.group(2)
        # Virtual __entry__ markers use source-port aliases (input/item/index),
        # while normal references use source-node aliases.
        alias_seed = src_port if src_node == "__entry__" else src_node
        base_alias = _sanitize_alias(alias_seed)
        if base_alias in used_aliases:
            used_aliases[base_alias] += 1
            alias = f"{base_alias}_{used_aliases[base_alias]}"
        else:
            used_aliases[base_alias] = 0
            alias = base_alias
        hits.append(_MarkerHit(
            source_node_id=src_node,
            source_port=src_port,
            target_node_id=target_node_id,
            alias=alias,
        ))
        return "{" + alias + "}"

    resolved = MARKER_PATTERN.sub(replacer, template)
    return resolved, hits


def _auto_generate_ports(
    nodes: list[_PendingNode],
    edges: list[_PendingEdge],
) -> None:
    """Ensure every node has the ports referenced by edges."""
    for pn in nodes:
        existing_out = {p.name for p in pn.explicit_output_ports}
        existing_in = {p.name for p in pn.explicit_input_ports}

        needed_out: set[str] = set()
        needed_in: set[str] = set()

        for e in edges:
            if e.source_node_id == pn.id and e.source_port not in existing_out:
                needed_out.add(e.source_port)
            if e.target_node_id == pn.id and e.target_port not in existing_in:
                needed_in.add(e.target_port)

        for port_name in sorted(needed_out):
            pn.explicit_output_ports.append(OutputPort(name=port_name))
        for port_name in sorted(needed_in):
            pn.explicit_input_ports.append(InputPort(name=port_name, required=True))


def _build_node(pn: _PendingNode) -> NodeBase:
    """Instantiate the correct Pydantic node model from pending state."""
    kwargs = dict(pn.kwargs)
    common = {
        "id": pn.id,
        "name": kwargs.pop("name", pn.id),
        "description": kwargs.pop("description", ""),
        "input_ports": list(pn.explicit_input_ports),
        "output_ports": list(pn.explicit_output_ports),
    }

    if pn.node_type == "llm_operator":
        return LLMOperator(**common, **kwargs)
    elif pn.node_type == "tool_operator":
        return ToolOperator(**common, **kwargs)
    elif pn.node_type == "code_operator":
        return CodeOperator(**common, **kwargs)
    elif pn.node_type == "if_else":
        return IfElseNode(**common, **kwargs)
    elif pn.node_type == "gate":
        return GateNode(**common, **kwargs)
    elif pn.node_type == "while_loop":
        return WhileLoopNode(**common, **kwargs)
    elif pn.node_type == "for_each":
        return ForEachNode(**common, **kwargs)
    elif pn.node_type == "reduce":
        return ReduceNode(**common, **kwargs)
    elif pn.node_type == "router":
        return RouterNode(**common, **kwargs)
    elif pn.node_type == "human_in_the_loop":
        return HumanInTheLoopNode(**common, **kwargs)
    elif pn.node_type == "rag_operator":
        return RAGOperator(**common, **kwargs)
    elif pn.node_type == "validator":
        return ValidatorNode(**common, **kwargs)
    elif pn.node_type == "composite":
        return CompositeNode(**common, **kwargs)
    else:
        raise BuildError([f"Unknown node_type: {pn.node_type!r}"])


def _find_entry_points(
    nodes: list[_PendingNode], edges: list[_PendingEdge]
) -> list[str]:
    """Nodes with no incoming data edges are entry points."""
    has_incoming = {e.target_node_id for e in edges if e.edge_type == "data"}
    return [n.id for n in nodes if n.id not in has_incoming]


def _find_exit_points(
    nodes: list[_PendingNode], edges: list[_PendingEdge]
) -> list[str]:
    """Nodes with no outgoing data edges are exit points."""
    has_outgoing = {e.source_node_id for e in edges if e.edge_type == "data"}
    return [n.id for n in nodes if n.id not in has_outgoing]


def _build_edge(edge: _PendingEdge, index: int) -> DataEdge | ControlEdge | ContextEdge:
    edge_id = f"edge_{edge.source_node_id}__{edge.target_node_id}_{index}"
    if edge.edge_type == "data":
        return DataEdge(
            id=edge_id,
            source_node_id=edge.source_node_id,
            source_port=edge.source_port,
            target_node_id=edge.target_node_id,
            target_port=edge.target_port,
        )
    if edge.edge_type == "control":
        return ControlEdge(
            id=edge_id,
            source_node_id=edge.source_node_id,
            source_port=edge.source_port,
            target_node_id=edge.target_node_id,
            target_port=edge.target_port,
            condition=edge.condition,
        )
    if edge.edge_type == "context":
        if edge.context_key is None:
            raise BuildError([f"Context edge '{edge_id}' missing context_key"])
        if edge.mode is None:
            raise BuildError([f"Context edge '{edge_id}' missing mode"])
        mode = ContextMode(edge.mode) if isinstance(edge.mode, str) else edge.mode
        return ContextEdge(
            id=edge_id,
            source_node_id=edge.source_node_id,
            source_port=edge.source_port,
            target_node_id=edge.target_node_id,
            target_port=edge.target_port,
            context_key=edge.context_key,
            mode=mode,
        )
    raise BuildError([f"Unknown edge_type: {edge.edge_type!r}"])
