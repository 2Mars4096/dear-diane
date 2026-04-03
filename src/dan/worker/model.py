"""Worker models — lightweight compute/contract primitives with shared refs."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.models.context import (
    BoundaryContract,
    CompactionRule,
    ContextProjection,
    FailurePolicy,
    FeedbackSelector,
    MergeStrategy,
    NodeLocalState,
)
from dan.models.control_flow import SpawnPolicy
from dan.models.legacy import ValidationRule
from dan.models.nodes import HistoryPolicy, NodeBase
from dan.models.ports import InputPort, OutputPort

TaskTier = Literal["micro", "routine", "reasoning", "critical"]


class WorkerAuthority(str, Enum):
    """High-level authority label for governance and delegation."""

    LEAF = "leaf"
    DELEGATE = "delegate"
    LEAD = "lead"
    DIRECTOR = "director"


class ContextBindings(BaseModel):
    """Explicit references to shared worker resources owned by the graph."""

    instruction_profile_ref: str | None = None
    memory_policy_ref: str | None = None
    context_bundle_refs: list[str] = Field(default_factory=list)
    provider_policy_ref: str | None = None
    retry_policy_ref: str | None = None
    toolset_refs: list[str] = Field(default_factory=list)
    authority_policy_ref: str | None = None
    inherit_defaults: bool = True


class AuthorityPolicy(BaseModel):
    """Inspectable limits on delegation, tier usage, and privileged access."""

    max_spawned_workers: int | None = Field(default=None, ge=0)
    task_tier_cap: TaskTier | None = None
    allow_delegate: bool = False
    allow_memory_write_scopes: list[str] = Field(default_factory=list)
    allowed_toolset_refs: list[str] = Field(default_factory=list)


class ExecutionSemantics(BaseModel):
    """Runtime hints for locking and sub-worker coordination."""

    resource_locks: list[str] = Field(default_factory=list)
    blocking_mode: Literal["auto", "exclusive", "shared"] = "auto"
    await_subworkers: bool = True
    parallelism_override: int | None = Field(default=None, ge=1)


class ControlFlowConfig(BaseModel):
    """Optional gate-style routing config referenced by a Worker contract."""

    condition: str
    gate_mode: Literal["if_else", "while"] = "if_else"
    max_iterations: int = Field(default=10, ge=1)
    state_schema: dict[str, Any] | None = None
    state_defaults: dict[str, Any] | None = None
    feedback_selector: FeedbackSelector | None = None
    artifact_ports: list[str] | None = None


class LLMHints(BaseModel):
    """Optional LLM-specific tuning kept off the flat Worker model."""

    prompt_template: str = ""
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int | None = None
    output_json_schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    max_tool_rounds: int = 10
    task_tier: TaskTier | None = None
    history_policy: HistoryPolicy | None = None


def llm_hints_configured(hints: LLMHints | None) -> bool:
    """Return whether an `LLMHints` payload implies LLM execution."""

    if hints is None:
        return False
    return any([
        bool(hints.prompt_template),
        bool(hints.system_prompt),
        hints.max_tokens is not None,
        hints.output_json_schema is not None,
        bool(hints.tools),
        hints.task_tier is not None,
        hints.history_policy is not None,
    ])


class WorkerConfig(BaseModel):
    """Reusable Worker configuration payload for roles and presets."""

    role: str = ""
    instruction: str = ""
    persona: str = ""
    authority: WorkerAuthority = WorkerAuthority.LEAF
    model: str | None = None
    tool_ids: list[str] = Field(default_factory=list)
    code: str = ""
    language: str = "python"
    llm_hints: LLMHints | None = None
    context: ContextBindings | None = None
    authority_policy: AuthorityPolicy | None = None
    execution: ExecutionSemantics | None = None
    control_flow: ControlFlowConfig | None = None
    body_graph: str | None = None
    sub_workers: dict[str, str] = Field(default_factory=dict)
    input_mappings: dict[str, str] = Field(default_factory=dict)
    output_mappings: dict[str, str] = Field(default_factory=dict)
    parallelism: int = Field(default=1, ge=1)
    merge_strategy: MergeStrategy = MergeStrategy.APPEND
    spawn_policy: SpawnPolicy | None = None
    external_input_schema: dict[str, Any] | None = None
    external_output_schema: dict[str, Any] | None = None
    control_state_schema: dict[str, Any] = Field(default_factory=dict)
    local_state: NodeLocalState = Field(default_factory=NodeLocalState)
    compaction_rule: CompactionRule | None = None
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    projections: list[ContextProjection] = Field(default_factory=list)
    boundary_contract: BoundaryContract | None = None
    validation_rules: list[ValidationRule] = Field(default_factory=list)


class Worker(NodeBase):
    """Universal compute/contract node with reference-first configuration."""

    node_type: Literal["worker"] = "worker"

    role: str = ""
    instruction: str = ""
    persona: str = ""
    authority: WorkerAuthority = WorkerAuthority.LEAF

    model: str | None = None
    tool_ids: list[str] = Field(default_factory=list)
    code: str = ""
    language: str = "python"
    llm_hints: LLMHints | None = None
    context: ContextBindings | None = None
    authority_policy: AuthorityPolicy | None = None
    execution: ExecutionSemantics | None = None
    control_flow: ControlFlowConfig | None = None

    body_graph: str | None = None
    sub_workers: dict[str, str] = Field(default_factory=dict)
    input_mappings: dict[str, str] = Field(default_factory=dict)
    output_mappings: dict[str, str] = Field(default_factory=dict)
    parallelism: int = Field(default=1, ge=1)
    merge_strategy: MergeStrategy = MergeStrategy.APPEND
    spawn_policy: SpawnPolicy | None = None
    external_input_schema: dict[str, Any] | None = None
    external_output_schema: dict[str, Any] | None = None
    control_state_schema: dict[str, Any] = Field(default_factory=dict)
    local_state: NodeLocalState = Field(default_factory=NodeLocalState)
    compaction_rule: CompactionRule | None = None
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    projections: list[ContextProjection] = Field(default_factory=list)
    boundary_contract: BoundaryContract | None = None
    validation_rules: list[ValidationRule] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if not self.input_ports:
            self.input_ports = [InputPort(name="input", required=False)]
        if not self.output_ports:
            if self.control_flow is not None:
                if self.control_flow.gate_mode == "while":
                    self.output_ports = [
                        OutputPort(name="continue", description="Loop back while condition stays true"),
                        OutputPort(name="done", description="Exit branch when condition becomes false"),
                    ]
                else:
                    self.output_ports = [
                        OutputPort(name="true", description="Active when condition is true"),
                        OutputPort(name="false", description="Active when condition is false"),
                    ]
                return
            default_output = "result"
            if self.model or llm_hints_configured(self.llm_hints):
                default_output = "text"
            elif self.code or self.tool_ids or self.body_graph or self.sub_workers:
                default_output = "result"
            self.output_ports = [OutputPort(name=default_output)]
