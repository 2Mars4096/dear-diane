"""Shared node base/support models plus compatibility re-exports."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.models.context import ContextDeclaration
from dan.models.ports import InputPort, OutputPort


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


class RetryPolicy(BaseModel):
    """Configurable retry/backoff/fallback for any node executor."""

    max_retries: int = 0
    backoff: float = 1.0
    backoff_max: float = 60.0
    fallback_model: str | None = None
    on_failure: Literal["error", "skip", "halt"] = "error"


class HistoryPolicy(BaseModel):
    """Policy for conversation-history assembly in LLMOperator prompts."""

    inline_recent: int | None = Field(
        default=None,
        description="Number of recent messages to keep inline (advisory)",
    )
    summarize_older: bool = Field(
        default=True,
        description="Summarize older messages instead of dropping them",
    )
    summary_model: str | None = Field(
        default=None,
        description="Model for history summarization (defaults to low-cost/default)",
    )
    keep_system: bool = Field(
        default=True,
        description="Always keep system messages inline",
    )


# ---------------------------------------------------------------------------
# UI metadata
# ---------------------------------------------------------------------------


class Position(BaseModel):
    """Canvas position for the visual editor."""

    x: float = 0.0
    y: float = 0.0


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class NodeBase(BaseModel):
    """Fields shared by every node type.

    Subclasses add a ``node_type`` literal used as the Pydantic
    discriminator for polymorphic JSON round-trips.
    """

    id: str
    name: str
    description: str = ""
    input_ports: list[InputPort] = Field(default_factory=list)
    output_ports: list[OutputPort] = Field(default_factory=list)
    position: Position = Field(default_factory=Position)
    ui: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary UI hints (color, icon, collapsed state, …)",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(
        default_factory=list,
        description="User-defined labels for hyperedge attachment and categorization",
    )
    retry_policy: RetryPolicy | None = None
    read_set: list[ContextDeclaration] = Field(
        default_factory=list,
        description="Context keys this node reads (for READ-mode context edges targeting this node)",
    )
    write_set: list[ContextDeclaration] = Field(
        default_factory=list,
        description="Context keys this node writes (for WRITE/APPEND-mode context edges from this node)",
    )
    # -- 18-2: Caching layer ----------------------------------------------------
    memoize: bool = Field(
        default=False,
        description="Cache result by content-hash of effective inputs",
    )
    cache_ttl: int | None = Field(
        default=None,
        description="Cache TTL in seconds (None = no expiry)",
    )


# ---------------------------------------------------------------------------
# Legacy compute compatibility re-exports
# ---------------------------------------------------------------------------


_LEGACY_EXPORTS = {
    "LLMOperator",
    "ToolOperator",
    "CodeOperator",
    "RAGOperator",
    "ReflectionNode",
}


def __getattr__(name: str) -> Any:
    if name in _LEGACY_EXPORTS:
        from dan.models import legacy as legacy_models

        return getattr(legacy_models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
