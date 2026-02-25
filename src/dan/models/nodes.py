"""Node definitions — atomic operator types and the shared base."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

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
    retry_policy: RetryPolicy | None = None


# ---------------------------------------------------------------------------
# Operator nodes (atomic)
# ---------------------------------------------------------------------------


class LLMOperator(NodeBase):
    """Single LLM call with structured output."""

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
