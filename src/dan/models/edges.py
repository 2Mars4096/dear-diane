"""Edge definitions — typed channels between nodes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.models.context import ContextMode


class EdgeBase(BaseModel):
    """Fields shared by every edge type.

    An edge connects an output port on a source node to an input port
    on a target node.  ``source_port`` / ``target_port`` correspond to
    ``OutputPort.name`` / ``InputPort.name`` on the respective nodes.
    """

    id: str
    source_node_id: str
    source_port: str
    target_node_id: str
    target_port: str
    ui: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary UI hints (label, color, waypoints, …)",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class DataEdge(EdgeBase):
    """Carries structured data between two ports.

    Schema compatibility between the source output port and the target
    input port is validated at graph-construction time.
    """

    edge_type: Literal["data"] = "data"


class ControlEdge(EdgeBase):
    """Encodes routing / flow-control signals (branch selection, loop-back).

    ``condition`` is an optional expression for conditional routing
    (e.g. the true/false branch of an IfElseNode).
    """

    edge_type: Literal["control"] = "control"
    condition: str | None = None


class ContextEdge(EdgeBase):
    """Connects a node to a shared-context key.

    ``mode`` must be one of read / write / append and must match the
    node's declared ``read_set`` or ``write_set``.
    """

    edge_type: Literal["context"] = "context"
    context_key: str = Field(description="Shared-context key this edge references")
    mode: ContextMode
