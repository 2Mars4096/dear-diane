"""Canonical node-taxonomy helpers derived from the runtime Graph union.

This module centralizes small but important policy sets so downstream code does
not need to hand-maintain parallel node-type lists.
"""

from __future__ import annotations

import os
from typing import Literal, Type, get_args

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

# New compute authoring should target `worker`; the remaining explicit
# primitives here are retained because they carry real scheduler/protocol
# semantics rather than being redundant compute wrappers.
CANONICAL_COMPUTE_NODE_TYPES: frozenset[str] = frozenset({"worker"})
RETAINED_RUNTIME_NODE_TYPES: frozenset[str] = frozenset({
    "gate",
    "while_loop",
    "for_each",
    "parallel_subagents",
    "orchestrator",
    "composite",
    "agent_team",
    "goal_loop",
})
LEGACY_COMPATIBILITY_NODE_TYPES: frozenset[str] = frozenset(
    RUNTIME_NODE_TYPES
    - CANONICAL_COMPUTE_NODE_TYPES
    - RETAINED_RUNTIME_NODE_TYPES
)
CANONICAL_AUTHORING_NODE_TYPES: tuple[str, ...] = tuple(
    ["worker"]
    + [
        node_type
        for node_type in CANONICAL_RUNTIME_NODE_TYPES
        if node_type != "worker" and node_type in RETAINED_RUNTIME_NODE_TYPES
    ]
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

WorkerGenerationMode = Literal["disabled", "canary", "enabled"]
WorkerBuilderMode = Literal["disabled", "canary", "enabled"]

# The lightweight planner `GENERATE` spec intentionally targets only a reduced
# subset of simple runtime node kinds. Richer runtime primitives are expected to
# arrive through mutation, builder code, or other authoring surfaces.
GENERATE_SPEC_LEGACY_NODE_TYPES: tuple[str, ...] = (
    "llm_operator",
    "tool_operator",
    "code_operator",
    "gate",
)
GENERATE_SPEC_NODE_TYPES: tuple[str, ...] = ("worker", *GENERATE_SPEC_LEGACY_NODE_TYPES)


def resolve_worker_generation_mode(raw: str | None = None) -> WorkerGenerationMode:
    """Resolve the additive Worker-generation rollout mode.

    `disabled`: generation continues preferring legacy compute node types.
    `canary` / `enabled`: generation prefers Worker for simple compute stages
    while retaining specialized control/runtime primitives such as `gate`.
    """

    mode = str(raw if raw is not None else os.environ.get("DAN_WORKER_GENERATION", "disabled")).strip().lower()
    if mode in {"canary", "enabled"}:
        return mode
    return "disabled"


def preferred_generate_spec_node_types(
    *,
    mode: str | None = None,
) -> tuple[str, ...]:
    """Return the preferred lightweight `GENERATE` contract for the active mode."""

    resolved = resolve_worker_generation_mode(mode)
    if resolved == "disabled":
        return GENERATE_SPEC_LEGACY_NODE_TYPES
    return ("worker", "gate")


def worker_generation_uses_workers(*, mode: str | None = None) -> bool:
    """Whether generation should prefer Worker-native compute nodes."""

    return resolve_worker_generation_mode(mode) != "disabled"


def resolve_worker_builder_mode(raw: str | None = None) -> WorkerBuilderMode:
    """Resolve the additive Worker-builder rollout mode.

    `disabled`: builder aliases continue emitting legacy compute node types.
    `canary` / `enabled`: simple compute aliases may emit Worker-native nodes
    while retained control/runtime primitives stay explicit.
    """

    mode = str(raw if raw is not None else os.environ.get("DAN_WORKER_BUILDER", "disabled")).strip().lower()
    if mode in {"canary", "enabled"}:
        return mode
    return "disabled"


def worker_builder_uses_workers(*, mode: str | None = None) -> bool:
    """Whether public Python builder aliases should emit Worker nodes."""

    return resolve_worker_builder_mode(mode) != "disabled"

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
assert set(GENERATE_SPEC_LEGACY_NODE_TYPES) <= RUNTIME_NODE_TYPES
assert MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES <= RUNTIME_NODE_TYPES
