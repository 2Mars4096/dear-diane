"""Canonical node-taxonomy helpers derived from the runtime Graph union.

This module centralizes small but important policy sets so downstream code does
not need to hand-maintain parallel node-type lists.
"""

from __future__ import annotations

from typing import Type, get_args

from dan.models.graph import Node
from dan.models.nodes import NodeBase


def _runtime_union_members() -> tuple[type[NodeBase], ...]:
    union_type = get_args(Node)[0]
    return tuple(get_args(union_type))


def runtime_node_type_map() -> dict[str, Type[NodeBase]]:
    """Return the ordered runtime `node_type` -> model-class mapping."""
    return {
        str(cls.model_fields["node_type"].default): cls
        for cls in _runtime_union_members()
    }


RUNTIME_NODE_TYPE_MAP: dict[str, Type[NodeBase]] = runtime_node_type_map()
RUNTIME_NODE_TYPES_IN_ORDER: tuple[str, ...] = tuple(RUNTIME_NODE_TYPE_MAP)
RUNTIME_NODE_TYPES: frozenset[str] = frozenset(RUNTIME_NODE_TYPES_IN_ORDER)

# Runtime aliases that still deserialize but should not be the preferred new
# authoring spelling for canonical graphs.
DEPRECATED_RUNTIME_ALIAS_NODE_TYPES: frozenset[str] = frozenset({
    "if_else",
    "human_in_the_loop",
})

CANONICAL_RUNTIME_NODE_TYPES: tuple[str, ...] = tuple(
    node_type
    for node_type in RUNTIME_NODE_TYPES_IN_ORDER
    if node_type not in DEPRECATED_RUNTIME_ALIAS_NODE_TYPES
)

# Runtime node kinds whose primary semantics depend on owned subgraphs.
_SUBGRAPH_FIELD_NAMES = frozenset({
    "body_graph",
    "branch_graphs",
    "teams",
    "agents",
})

SUBGRAPH_BEARING_RUNTIME_NODE_TYPES: frozenset[str] = frozenset(
    node_type
    for node_type, cls in RUNTIME_NODE_TYPE_MAP.items()
    if any(field_name in cls.model_fields for field_name in _SUBGRAPH_FIELD_NAMES)
)

BODY_GRAPH_RUNTIME_NODE_TYPES: frozenset[str] = frozenset(
    node_type
    for node_type, cls in RUNTIME_NODE_TYPE_MAP.items()
    if "body_graph" in cls.model_fields
)

# Surface-only labels that should never persist as graph `node_type`s.
AUTHORING_PSEUDO_NODE_TYPES: frozenset[str] = frozenset({
    "gate_if_else",
    "gate_while",
    "loopGroup",
})

# Mutation/chat surfaces intentionally omit the deprecated `if_else` alias and
# target canonical runtime shapes instead.
MUTATION_NODE_TYPES: tuple[str, ...] = tuple(
    node_type
    for node_type in RUNTIME_NODE_TYPES_IN_ORDER
    if node_type != "if_else"
)

# The lightweight planner `GENERATE` spec intentionally targets only a reduced
# subset of simple runtime node kinds. Richer runtime primitives are expected to
# arrive through mutation, builder code, or other authoring surfaces.
GENERATE_SPEC_NODE_TYPES: tuple[str, ...] = (
    "llm_operator",
    "tool_operator",
    "code_operator",
    "gate",
)

# Runtime kinds that the markdown loader/decompiler can currently represent
# directly as agent/workflow markdown without dropping to a stub.
MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES: frozenset[str] = frozenset({
    "llm_operator",
    "tool_operator",
    "code_operator",
    "human",
    "human_in_the_loop",
    "router",
    "gate",
    "for_each",
    "composite",
    "reflection",
    "goal_loop",
    "vote",
    "agent_team",
    "input",
})

# Fail fast if policy subsets drift from the runtime union (catches typos early).
assert set(GENERATE_SPEC_NODE_TYPES) <= RUNTIME_NODE_TYPES
assert MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES <= RUNTIME_NODE_TYPES
