"""Compile-time reference types for the workflow builder DSL.

NodeRef and PortRef are lightweight immutable proxies returned by
WorkflowBuilder node-creation methods. They carry no data — they
are resolved by the compiler at build() time.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dan.builder.builder import WorkflowBuilder

# Marker format: <<dan:node_id:port_name>>
# Regex-parseable, unlikely to collide with user content.
MARKER_PATTERN = re.compile(r"<<dan:([^:>]+):([^:>]+)>>")


def _sanitize_alias(raw: str) -> str:
    """Convert a raw node/port ID into a valid ``str.format_map`` key.

    Replaces hyphens, dots, spaces, and other non-identifier chars with
    underscores. Strips leading digits so the result is a valid Python
    identifier usable as ``{alias}`` in prompt templates.
    """
    alias = re.sub(r"[^0-9a-zA-Z_]", "_", raw)
    if alias and alias[0].isdigit():
        alias = f"_{alias}"
    return alias or "_ref"


class PortRef:
    """Immutable reference to a specific port on a specific node.

    Used as a compile-time token — the compiler resolves it into
    actual InputPort/OutputPort definitions and DataEdge objects.
    """

    __slots__ = ("node_id", "port_name", "_builder")

    def __init__(
        self, node_id: str, port_name: str, builder: WorkflowBuilder | None = None
    ) -> None:
        self.node_id = node_id
        self.port_name = port_name
        self._builder = builder

    def __format__(self, format_spec: str) -> str:
        return f"<<dan:{self.node_id}:{self.port_name}>>"

    def __repr__(self) -> str:
        return f"PortRef({self.node_id!r}, {self.port_name!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PortRef):
            return self.node_id == other.node_id and self.port_name == other.port_name
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.node_id, self.port_name))


class NodeRef:
    """Immutable reference to a node registered with a WorkflowBuilder.

    Supports:
      - ``f"{node_ref}"`` — emits a compile-time marker for the default
        output port so the compiler can auto-wire a DataEdge.
      - ``node_ref["port"]`` — returns a PortRef for a named port.
      - ``a >> b`` — registers a DataEdge from *a*'s default output to
        *b*'s default input on the shared builder.
    """

    __slots__ = ("node_id", "node_type", "_builder")

    def __init__(
        self,
        node_id: str,
        node_type: str,
        builder: WorkflowBuilder | None = None,
    ) -> None:
        self.node_id = node_id
        self.node_type = node_type
        self._builder = builder

    @property
    def default_output(self) -> str:
        from dan.builder.compiler import default_output_port
        return default_output_port(self.node_type)

    def __getitem__(self, port_name: str) -> PortRef:
        return PortRef(self.node_id, port_name, self._builder)

    def __format__(self, format_spec: str) -> str:
        return f"<<dan:{self.node_id}:{self.default_output}>>"

    def __rshift__(self, other: NodeRef) -> NodeRef:
        if not isinstance(other, NodeRef):
            return NotImplemented
        if self._builder is not None:
            self._builder._register_chain(self, other)
        elif other._builder is not None:
            other._builder._register_chain(self, other)
        return other

    def __repr__(self) -> str:
        return f"NodeRef({self.node_id!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NodeRef):
            return self.node_id == other.node_id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.node_id)
