"""Context management models — the four-layer state system.

Layer 1 (edge data) is handled by port schemas on DataEdge.
Layers 2-4 are defined here:
  Layer 2: NodeLocalState   — private working memory scoped to a composite/loop
  Layer 3: SharedContextDeclaration — namespaced key-value blackboard (graph-wide)
  Layer 4: ArtifactRef      — URI-based references to large immutable objects
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ContextMode(str, Enum):
    """How a node interacts with a shared-context key."""

    READ = "read"
    WRITE = "write"
    APPEND = "append"


class CompactionStrategy(str, Enum):
    """Strategies for summarising local history between loop iterations."""

    SLIDING_WINDOW = "sliding_window"
    KEEP_LAST = "keep_last"
    SUMMARIZE = "summarize"
    DIFF_BASED = "diff_based"
    NONE = "none"


class MergeStrategy(str, Enum):
    """How parallel fan-out branches reconcile writes to the same key."""

    APPEND = "append"
    LAST_WRITE_WINS = "last_write_wins"
    REDUCER = "reducer"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class CompactionRule(BaseModel):
    """Configures how a composite/loop node compresses accumulated state."""

    strategy: CompactionStrategy = CompactionStrategy.NONE
    window_size: int | None = Field(
        default=None, description="Number of recent items to keep (sliding_window)"
    )
    max_tokens: int | None = Field(
        default=None, description="Token budget for summarize strategy"
    )


class FailurePolicy(BaseModel):
    """Exit conditions that terminate a loop or composite node."""

    max_iterations: int | None = None
    timeout_seconds: float | None = None
    stagnation_threshold: int | None = Field(
        default=None,
        description="Consecutive iterations without improvement before stopping",
    )


class ContextDeclaration(BaseModel):
    """A single read or write claim on a shared-context key."""

    key: str
    mode: ContextMode
    json_schema: dict[str, Any] | None = Field(
        default=None, description="Optional JSON Schema constraining the value"
    )


# ---------------------------------------------------------------------------
# Layer 2 — node-local state
# ---------------------------------------------------------------------------


class NodeLocalState(BaseModel):
    """Private working memory for a composite or loop node.

    Mutable within scope, invisible to the parent graph.
    """

    json_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema defining the local state shape",
    )
    description: str = ""


# ---------------------------------------------------------------------------
# Layer 3 — shared context store declarations
# ---------------------------------------------------------------------------


class SharedContextDeclaration(BaseModel):
    """Graph-level declaration of a named shared-context key."""

    key: str
    json_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for the context value",
    )
    description: str = ""


# ---------------------------------------------------------------------------
# Layer 4 — artifact store
# ---------------------------------------------------------------------------


class ArtifactRef(BaseModel):
    """URI-based reference to a large object in the artifact store.

    Artifacts are immutable — each revision creates a new version.
    """

    uri: str
    content_hash: str | None = None
    media_type: str = "application/octet-stream"
    description: str = ""


# ---------------------------------------------------------------------------
# Context projection
# ---------------------------------------------------------------------------


class ContextProjection(BaseModel):
    """Defines the minimal state view a consumer sees at a scope boundary.

    At every scope transition (entering a sub-graph, starting a loop
    iteration) a projection extracts only the keys the consumer needs.
    """

    name: str = Field(description="Identifier for this projection (e.g. 'loop_controller', 'reviser')")
    context_keys: list[str] = Field(
        default_factory=list, description="Keys from shared context to expose"
    )
    local_state_keys: list[str] = Field(
        default_factory=list, description="Keys from node-local state to expose"
    )
    artifact_uris: list[str] = Field(
        default_factory=list, description="Artifact URIs to expose"
    )
    description: str = ""
