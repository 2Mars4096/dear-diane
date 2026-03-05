"""Node definitions — atomic operator types and the shared base."""

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
# Operator nodes (atomic)
# ---------------------------------------------------------------------------


class LLMOperator(NodeBase):
    """Single LLM call with structured output and optional tool-calling loop."""

    node_type: Literal["llm_operator"] = "llm_operator"
    model: str
    prompt_template: str
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int | None = None
    output_json_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema the LLM response must conform to",
    )
    model_policy: Any | None = Field(
        default=None,
        description="Policy-driven model selection (ModelPolicy from dan.providers.model_policy)",
    )
    tools: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Tool schemas (OpenAI function-calling format) the LLM may invoke",
    )
    max_tool_rounds: int = Field(
        default=10,
        description="Safety bound on tool-calling loop iterations",
    )
    # -- 18-1: Smart context assembly ------------------------------------------
    target_input_tokens: int | None = Field(
        default=None,
        description="Advisory token budget — guides context assembly, never enforced as hard cap",
    )
    summarize_inputs: bool | dict[str, Any] | None = Field(
        default=None,
        description="When true or SummarizationConfig dict, long free-text inputs are summarized before injection",
    )
    jit_tool_loading: bool = Field(
        default=False,
        description="When true, inject only tool catalog (name+description); full schemas loaded on demand",
    )
    agent_context_tools: bool = Field(
        default=False,
        description="When true, inject context tools (search_context, read_context, read_state, list_available_context)",
    )
    prune_fields: list[str] = Field(
        default_factory=list,
        description="Glob patterns for JSON fields to strip from structured inputs (e.g. '*.created_at')",
    )
    input_format: str = Field(
        default="json",
        description="Serialization format for structured inputs: 'json', 'yaml', or 'compact'",
    )
    semantic_cache: bool = Field(
        default=False,
        description="Enable semantic response cache for deterministic prompts (temperature=0)",
    )
    history_policy: HistoryPolicy | None = Field(
        default=None,
        description="Policy for conversation-style message history assembly",
    )
    # -- 18-5: Task-level model tiering ----------------------------------------
    task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = Field(
        default=None,
        description="Explicit task tier override. Bypasses automatic scoring.",
    )


class ToolOperator(NodeBase):
    """Invokes a registered tool (API, database, external service).

    ``tool_id`` is a registry key resolved at runtime — not a Python
    callable — so the model serialises cleanly to JSON.
    """

    node_type: Literal["tool_operator"] = "tool_operator"
    tool_id: str
    tool_config: dict[str, Any] = Field(default_factory=dict)


class CodeOperator(NodeBase):
    """Executes user-supplied code in a sandboxed environment."""

    node_type: Literal["code_operator"] = "code_operator"
    code: str
    language: str = "python"
    sandbox_config: dict[str, Any] = Field(default_factory=dict)


class RAGOperator(NodeBase):
    """Retrieves relevant chunks from a vector store given a query.

    Backed by a pluggable vector store and embedding pipeline.
    The query is embedded, searched against the collection, and
    matching chunks are returned on the ``chunks`` output port.
    """

    node_type: Literal["rag_operator"] = "rag_operator"
    collection: str
    top_k: int = 5
    similarity_threshold: float | None = None
    embedding_model: str = ""
    vector_store_config: dict[str, Any] = Field(default_factory=dict)
    query_template: str = "{query}"
    include_metadata: bool = True
    rerank: bool = False


class ReflectionNode(NodeBase):
    """Post-run analysis node that distills errors into causal principles."""

    node_type: Literal["reflection"] = "reflection"
    reflection_prompt: str = ""
    reflection_model: str | None = None
    source: Literal["last_run", "last_n_runs", "error_index"] = "last_run"
    source_config: dict[str, Any] = Field(default_factory=dict)
    output_format: Literal["principles", "rules", "summary"] = "principles"
    max_principles: int = 10
    min_confidence: float = 0.3
    dedup_strategy: Literal["embedding_similarity", "exact_key", "none"] = "embedding_similarity"
    # -- 18-5: Task-level model tiering ----------------------------------------
    task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = Field(
        default=None,
        description="Explicit task tier override. Bypasses automatic scoring.",
    )
