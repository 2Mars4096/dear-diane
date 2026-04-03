"""Canonical compatibility surface for legacy runtime node models.

These classes remain part of the runtime union for deserialization and
backward compatibility, but they are no longer the preferred surface for new
compute authoring. New imports should prefer this module when they mean
"legacy compute/compatibility node" rather than "specialized runtime
primitive".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.models.nodes import HistoryPolicy, NodeBase
from dan.models.ports import InputPort, OutputPort


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
    task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = Field(
        default=None,
        description="Explicit task tier override. Bypasses automatic scoring.",
    )


class ToolOperator(NodeBase):
    """Invokes a registered tool (API, database, external service)."""

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
    """Retrieves relevant chunks from a vector store given a query."""

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
    task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = Field(
        default=None,
        description="Explicit task tier override. Bypasses automatic scoring.",
    )


class InputVariable(BaseModel):
    """A single typed variable declared on an InputNode."""

    name: str
    type: Literal["string", "number", "boolean"] = "string"
    default: Any = None
    description: str = ""


class InputNode(NodeBase):
    """Visual entry point for workflow inputs."""

    node_type: Literal["input"] = "input"
    variables: list[InputVariable] = Field(default_factory=list)


class ReduceNode(NodeBase):
    """Aggregates results from parallel fan-out branches."""

    node_type: Literal["reduce"] = "reduce"
    reducer: str = Field(
        description="Expression or function name that combines branch outputs",
    )


class RouterNode(NodeBase):
    """LLM-powered dynamic routing."""

    node_type: Literal["router"] = "router"
    model: str
    route_descriptions: dict[str, str] = Field(
        default_factory=dict,
        description="route_name → natural-language description",
    )
    model_policy: Any | None = Field(
        default=None,
        description="Policy-driven model selection (ModelPolicy from dan.providers.model_policy)",
    )
    task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = Field(
        default=None,
        description="Explicit task tier override. Bypasses automatic scoring.",
    )


class HumanNode(NodeBase):
    """First-class human interaction node with typed I/O and render modes."""

    node_type: Literal["human"] = "human"
    prompt: str = ""
    timeout_seconds: float | None = None
    default_action: str | None = None
    input_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for what the human receives (presentation hint)",
    )
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for what the human must provide (validated on submit)",
    )
    render_mode: Literal["text", "approval", "form", "selection", "file_upload", "rich"] = Field(
        default="text",
        description="Hint to the rendering surface for how to present the interaction",
    )
    options: list[str] | None = Field(
        default=None,
        description="Choices for 'selection' render mode",
    )
    instructions: str = Field(
        default="",
        description="Guidance text shown above the input area (supports markdown)",
    )
    render_target: Literal["dialog", "chat", "both"] = Field(
        default="dialog",
        description="Where the interaction appears: popup dialog, chat panel, or both",
    )


class HumanInTheLoopNode(HumanNode):
    """Backward-compatible alias — deserializes ``human_in_the_loop`` JSON."""

    node_type: Literal["human_in_the_loop"] = "human_in_the_loop"  # type: ignore[assignment]


class ValidationRule(BaseModel):
    """A single validation rule applied by a ValidatorNode."""

    rule_type: Literal[
        "required_keys",
        "non_empty",
        "schema_conformance",
        "type_check",
        "custom_expression",
    ]
    config: dict[str, Any] = Field(default_factory=dict)


class ValidatorNode(NodeBase):
    """Checks data at agent boundaries and routes to valid/invalid ports."""

    node_type: Literal["validator"] = "validator"
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    on_failure: Literal["route", "warn", "halt"] = "route"
    strict_mode: bool = False

    def model_post_init(self, __context: Any) -> None:
        if not self.input_ports:
            self.input_ports = [
                InputPort(name="data", description="Payload to validate"),
            ]
        if not self.output_ports:
            self.output_ports = [
                OutputPort(name="valid", description="Passthrough when all rules pass"),
                OutputPort(name="invalid", description="Data + errors when any rule fails"),
            ]


class VoteConfig(BaseModel):
    """Strategy-specific configuration for VoteNode."""

    judge_model: str | None = Field(
        default=None, description="Model used for 'judge'/'best_of_n' strategies",
    )
    judge_prompt: str | None = Field(
        default=None, description="Custom prompt for the judge model",
    )
    quality_metric: str | None = Field(
        default=None,
        description="Expression evaluated on each vote output for 'weighted' strategy",
    )
    unanimity_threshold: float = Field(
        default=1.0,
        description="Fraction of agreement required for 'unanimous' strategy",
    )
    consensus_mode: Literal["whole", "field"] = Field(
        default="whole",
        description="Compare entire output ('whole') or per-field ('field') for structured outputs",
    )


class VoteNode(NodeBase):
    """Runs the same task through multiple model instances and selects the best."""

    node_type: Literal["vote"] = "vote"

    candidates: list[str] = Field(
        description="Model names to vote across; single entry = same-model voting",
    )
    num_votes: int = Field(default=3, ge=1, description="How many times to run the task")
    prompt_template: str = Field(
        default="", description="Prompt with {input_port} placeholders",
    )
    system_prompt: str = ""
    temperature: float = Field(
        default=0.7,
        description="Temperature for all candidates; higher = more diverse votes",
    )
    output_json_schema: dict[str, Any] | None = Field(
        default=None,
        description="If set, all votes must conform; enables structured comparison",
    )
    vote_strategy: Literal["majority", "weighted", "best_of_n", "judge", "unanimous"] = Field(
        default="majority",
        description="How to select the winner from collected votes",
    )
    vote_config: VoteConfig | None = None
    task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = Field(
        default=None,
        description="Explicit task tier override. Bypasses automatic scoring.",
    )
    parallelism: int = Field(default=3, ge=1, description="Max concurrent LLM calls")
    timeout_seconds: float | None = None


__all__ = [
    "LLMOperator",
    "ToolOperator",
    "CodeOperator",
    "RAGOperator",
    "ReflectionNode",
    "InputVariable",
    "InputNode",
    "ReduceNode",
    "RouterNode",
    "HumanNode",
    "HumanInTheLoopNode",
    "ValidationRule",
    "ValidatorNode",
    "VoteConfig",
    "VoteNode",
]
