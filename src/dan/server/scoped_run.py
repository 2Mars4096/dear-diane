"""Backend module for building scoped executable graphs from a workflow."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.models.control_flow import (
    CompositeNode,
    ForEachNode,
    InputNode,
    InputVariable,
    WhileLoopNode,
)
from dan.models.edges import DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.ports import OutputPort
from dan.validation.graph import validate_graph

logger = logging.getLogger(__name__)

__all__ = [
    "ScopedRunRequest",
    "ScopedRunResponse",
    "ScopedRunError",
    "ScopedGraphResult",
    "build_scoped_graph",
    "parse_run_command",
    "map_run_event_to_chat_block",
]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ScopedRunRequest(BaseModel):
    workflow_id: str
    scope: Literal["full", "node", "subgraph"]
    target_node_id: str | None = None
    target_subgraph_key: str | None = None
    inputs: dict[str, Any] | None = None
    thread_id: str | None = None
    origin_message_id: str | None = None


class ScopedRunResponse(BaseModel):
    run_id: str
    status: str
    scope: str
    target: str | None = None
    stream_channel_id: str | None = None


class ScopedRunError(BaseModel):
    error_type: Literal[
        "target_not_found", "target_not_runnable", "validation_failed", "input_required",
    ]
    message: str
    required_inputs: list[str] | None = None


# ---------------------------------------------------------------------------
# Core: build_scoped_graph
# ---------------------------------------------------------------------------


class ScopedGraphResult:
    def __init__(
        self,
        graph: Graph | None,
        error: ScopedRunError | None = None,
        scope_metadata: dict | None = None,
    ):
        self.graph = graph
        self.error = error
        self.scope_metadata = scope_metadata or {}


def build_scoped_graph(
    graph: Graph,
    scope: str,
    target_node_id: str | None = None,
    target_subgraph_key: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> ScopedGraphResult:
    if scope == "full":
        return ScopedGraphResult(graph=graph, scope_metadata={"scope": "full"})

    if scope == "node":
        return _build_node_scope(graph, target_node_id, inputs)

    if scope == "subgraph":
        return _build_subgraph_scope(graph, target_node_id, target_subgraph_key)

    return ScopedGraphResult(
        graph=None,
        error=ScopedRunError(
            error_type="validation_failed",
            message=f"Unknown scope '{scope}'",
        ),
    )


def _build_node_scope(
    graph: Graph,
    target_node_id: str | None,
    inputs: dict[str, Any] | None,
) -> ScopedGraphResult:
    if not target_node_id:
        return ScopedGraphResult(
            graph=None,
            error=ScopedRunError(
                error_type="target_not_found",
                message="target_node_id is required for scope='node'",
            ),
        )

    target = graph.node_by_id(target_node_id)
    if target is None:
        return ScopedGraphResult(
            graph=None,
            error=ScopedRunError(
                error_type="target_not_found",
                message=f"Node '{target_node_id}' not found in graph",
            ),
        )

    required_ports = [p.name for p in target.input_ports if p.required]
    if required_ports:
        provided = set((inputs or {}).keys())
        missing = [p for p in required_ports if p not in provided]
        if missing:
            return ScopedGraphResult(
                graph=None,
                error=ScopedRunError(
                    error_type="input_required",
                    message=f"Node '{target_node_id}' requires inputs: {missing}",
                    required_inputs=missing,
                ),
            )

    nodes: list = [target]
    edges: list = []
    entry_points = [target.id]

    if inputs:
        input_node_id = f"_scoped_input_{uuid.uuid4().hex[:8]}"
        variables = [InputVariable(name=k, type="string") for k in inputs]
        output_ports = [OutputPort(name=k, json_schema={"type": "string"}) for k in inputs]
        input_node = InputNode(
            id=input_node_id,
            name="Scoped Input",
            variables=variables,
            output_ports=output_ports,
        )
        nodes.insert(0, input_node)
        entry_points = [input_node_id]

        for port_name in inputs:
            target_port = next(
                (p for p in target.input_ports if p.name == port_name), None,
            )
            if target_port is not None:
                edges.append(
                    DataEdge(
                        id=f"_scoped_edge_{port_name}_{uuid.uuid4().hex[:8]}",
                        source_node_id=input_node_id,
                        source_port=port_name,
                        target_node_id=target.id,
                        target_port=port_name,
                    )
                )

    scoped = Graph(
        metadata=GraphMetadata(name=f"Scoped: {target.name}"),
        nodes=nodes,
        edges=edges,
        entry_points=entry_points,
        exit_points=[target.id],
    )

    errors = validate_graph(scoped)
    if errors:
        return ScopedGraphResult(
            graph=None,
            error=ScopedRunError(
                error_type="validation_failed",
                message=f"Scoped graph validation failed: {'; '.join(errors)}",
            ),
        )

    return ScopedGraphResult(
        graph=scoped,
        scope_metadata={"scope": "node", "target_node_id": target.id},
    )


def _build_subgraph_scope(
    graph: Graph,
    target_node_id: str | None,
    target_subgraph_key: str | None,
) -> ScopedGraphResult:
    sub: Graph | None = None
    resolved_key: str | None = target_subgraph_key

    if target_subgraph_key and target_subgraph_key in graph.sub_graphs:
        sub = graph.sub_graphs[target_subgraph_key]
    elif target_node_id:
        node = graph.node_by_id(target_node_id)
        if node is not None and isinstance(node, (CompositeNode, WhileLoopNode, ForEachNode)):
            resolved_key = node.body_graph
            sub = graph.sub_graphs.get(node.body_graph)

    if sub is None:
        return ScopedGraphResult(
            graph=None,
            error=ScopedRunError(
                error_type="target_not_found",
                message=f"Sub-graph '{resolved_key or target_node_id}' not found",
            ),
        )

    standalone = sub.model_copy(deep=True)

    errors = validate_graph(standalone)
    if errors:
        return ScopedGraphResult(
            graph=None,
            error=ScopedRunError(
                error_type="validation_failed",
                message=f"Sub-graph validation failed: {'; '.join(errors)}",
            ),
        )

    return ScopedGraphResult(
        graph=standalone,
        scope_metadata={"scope": "subgraph", "subgraph_key": resolved_key},
    )


# ---------------------------------------------------------------------------
# Chat command parser
# ---------------------------------------------------------------------------

_MENTION_RE = re.compile(r"@\[([^\]]*)\]\((\w+):([^)]+)\)")


def parse_run_command(text: str) -> dict[str, Any] | None:
    """Parse /run commands from chat text.  Returns None if not a run command.

    Formats:
      /run                          -> {"scope": "full"}
      /run @[Name](node:id)         -> {"scope": "node", "target_node_id": "id"}
      /run-subgraph @[Name](subgraph:key) -> {"scope": "subgraph", "target_subgraph_key": "key"}
      /run-node @[Name](node:id)    -> {"scope": "node", "target_node_id": "id"}
    """
    stripped = text.strip()

    if stripped.startswith("/run-subgraph"):
        body = stripped[len("/run-subgraph"):].strip()
        m = _MENTION_RE.search(body)
        if m:
            mention_type, mention_id = m.group(2), m.group(3)
            if mention_type == "subgraph":
                return {"scope": "subgraph", "target_subgraph_key": mention_id}
            if mention_type == "node":
                return {"scope": "subgraph", "target_node_id": mention_id}
        return {"scope": "subgraph"}

    if stripped.startswith("/run-node"):
        body = stripped[len("/run-node"):].strip()
        m = _MENTION_RE.search(body)
        if m and m.group(2) == "node":
            return {"scope": "node", "target_node_id": m.group(3)}
        return {"scope": "node"}

    if stripped.startswith("/run"):
        body = stripped[len("/run"):].strip()
        if not body:
            return {"scope": "full"}
        m = _MENTION_RE.search(body)
        if m:
            mention_type, mention_id = m.group(2), m.group(3)
            if mention_type == "node":
                return {"scope": "node", "target_node_id": mention_id}
            if mention_type == "subgraph":
                return {"scope": "subgraph", "target_subgraph_key": mention_id}
        return {"scope": "full"}

    return None


# ---------------------------------------------------------------------------
# Run event → chat message mapper
# ---------------------------------------------------------------------------

_CHAT_EVENT_TYPES = frozenset({
    "run_started", "run_completed", "run_failed", "run_cancelled",
    "node_started", "node_completed", "node_failed",
    "node_output", "tool_call_started", "tool_call_result",
    "human_input_needed",
})


def map_run_event_to_chat_block(
    event_dict: dict[str, Any],
    scope: str,
    target: str | None,
) -> dict[str, Any] | None:
    """Convert a run event into a chat-displayable block.

    Returns ``None`` for events that should not appear in the chat timeline.
    For mapped events returns::

        {
            "type": "run_event",
            "event_type": "<original_event_type>",
            "node_id": "<node_id or None>",
            "summary": "<one-line display text>",
            "detail": {<run_id, scope, target, data>},
        }
    """
    event_type = event_dict.get("event_type") or event_dict.get("type", "")
    if event_type not in _CHAT_EVENT_TYPES:
        return None

    run_id = event_dict.get("run_id", "")
    node_id = event_dict.get("node_id")
    data: dict[str, Any] = event_dict.get("data") or {}

    if event_type == "run_started":
        scope_label = f" ({scope})" if scope != "full" else ""
        target_label = f" on {target}" if target else ""
        summary = f"Run started{scope_label}{target_label}"
    elif event_type == "run_completed":
        summary = "Run completed successfully"
    elif event_type == "run_failed":
        error = event_dict.get("error") or data.get("error", "unknown error")
        summary = f"Run failed: {error}"
    elif event_type == "node_started":
        summary = f"Node '{node_id}' started"
    elif event_type == "node_completed":
        summary = f"Node '{node_id}' completed"
    elif event_type == "node_failed":
        error = event_dict.get("error") or data.get("error", "unknown error")
        summary = f"Node '{node_id}' failed: {error}"
    elif event_type == "node_output":
        summary = f"Node '{node_id}' produced output"
    elif event_type == "tool_call_started":
        tool_name = data.get("tool_id") or data.get("tool_name", "unknown")
        summary = f"Tool '{tool_name}' called on node '{node_id}'"
    elif event_type == "tool_call_result":
        tool_name = data.get("tool_id") or data.get("tool_name", "unknown")
        summary = f"Tool '{tool_name}' completed on node '{node_id}'"
    elif event_type == "run_cancelled":
        reason = data.get("reason", "cancelled")
        summary = f"Run cancelled: {reason}"
    elif event_type == "human_input_needed":
        prompt = data.get("prompt", "Input required")
        summary = f"Waiting for input: {prompt}"
    else:
        return None

    return {
        "type": "run_event",
        "event_type": event_type,
        "node_id": node_id,
        "summary": summary,
        "detail": {
            "run_id": run_id,
            "scope": scope,
            "target": target,
            "data": data,
        },
    }
