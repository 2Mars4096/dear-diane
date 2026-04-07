"""Worker models — lightweight compute/contract primitives with shared refs."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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
_LEGACY_LLM_HINT_FIELDS = (
    "prompt_template",
    "system_prompt",
    "temperature",
    "max_tokens",
    "output_json_schema",
    "tools",
    "max_tool_rounds",
    "task_tier",
    "history_policy",
)


def _coerce_legacy_llm_fields(data: Any) -> Any:
    """Fold legacy flat Worker fields into canonical Worker storage."""

    if not isinstance(data, dict):
        return data
    payload = dict(data)
    llm_payload = payload.get("llm_hints")
    if isinstance(llm_payload, LLMHints):
        hints = llm_payload.model_dump(exclude_none=False)
    elif isinstance(llm_payload, dict):
        hints = dict(llm_payload)
    elif llm_payload is None:
        hints = {}
    else:
        return payload

    merged = False
    for field in _LEGACY_LLM_HINT_FIELDS:
        if field in payload:
            hints[field] = payload.pop(field)
            merged = True
    if merged:
        payload["llm_hints"] = hints
    if "tool_id" in payload:
        tool_id = str(payload.pop("tool_id") or "").strip()
        payload["tool_ids"] = [tool_id] if tool_id else []
    if "tool_config" in payload:
        metadata = dict(payload.get("metadata") or {})
        metadata["tool_config"] = dict(payload.pop("tool_config") or {})
        payload["metadata"] = metadata
    return payload


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

    @model_validator(mode="before")
    @classmethod
    def _merge_legacy_llm_fields(cls, data: Any) -> Any:
        return _coerce_legacy_llm_fields(data)


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

    @model_validator(mode="before")
    @classmethod
    def _merge_legacy_llm_fields(cls, data: Any) -> Any:
        return _coerce_legacy_llm_fields(data)

    def _ensure_llm_hints(self) -> LLMHints:
        if self.llm_hints is None:
            self.llm_hints = LLMHints()
        return self.llm_hints

    @property
    def prompt_template(self) -> str:
        return self.llm_hints.prompt_template if self.llm_hints is not None else ""

    @prompt_template.setter
    def prompt_template(self, value: str) -> None:
        self._ensure_llm_hints().prompt_template = value

    @property
    def system_prompt(self) -> str:
        return self.llm_hints.system_prompt if self.llm_hints is not None else ""

    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        self._ensure_llm_hints().system_prompt = value

    @property
    def temperature(self) -> float:
        return self.llm_hints.temperature if self.llm_hints is not None else 0.7

    @temperature.setter
    def temperature(self, value: float) -> None:
        self._ensure_llm_hints().temperature = value

    @property
    def max_tokens(self) -> int | None:
        return self.llm_hints.max_tokens if self.llm_hints is not None else None

    @max_tokens.setter
    def max_tokens(self, value: int | None) -> None:
        self._ensure_llm_hints().max_tokens = value

    @property
    def output_json_schema(self) -> dict[str, Any] | None:
        return self.llm_hints.output_json_schema if self.llm_hints is not None else None

    @output_json_schema.setter
    def output_json_schema(self, value: dict[str, Any] | None) -> None:
        self._ensure_llm_hints().output_json_schema = value

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self.llm_hints.tools if self.llm_hints is not None else []

    @tools.setter
    def tools(self, value: list[dict[str, Any]]) -> None:
        self._ensure_llm_hints().tools = value

    @property
    def max_tool_rounds(self) -> int:
        return self.llm_hints.max_tool_rounds if self.llm_hints is not None else 10

    @max_tool_rounds.setter
    def max_tool_rounds(self, value: int) -> None:
        self._ensure_llm_hints().max_tool_rounds = value

    @property
    def task_tier(self) -> TaskTier | None:
        return self.llm_hints.task_tier if self.llm_hints is not None else None

    @task_tier.setter
    def task_tier(self, value: TaskTier | None) -> None:
        self._ensure_llm_hints().task_tier = value

    @property
    def history_policy(self) -> HistoryPolicy | None:
        return self.llm_hints.history_policy if self.llm_hints is not None else None

    @history_policy.setter
    def history_policy(self, value: HistoryPolicy | None) -> None:
        self._ensure_llm_hints().history_policy = value

    @property
    def tool_id(self) -> str:
        return self.tool_ids[0] if self.tool_ids else ""

    @tool_id.setter
    def tool_id(self, value: str) -> None:
        tool_id = str(value or "").strip()
        self.tool_ids = [tool_id] if tool_id else []

    @property
    def tool_config(self) -> dict[str, Any]:
        config = self.metadata.get("tool_config")
        if not isinstance(config, dict):
            config = {}
            self.metadata["tool_config"] = config
        return config

    @tool_config.setter
    def tool_config(self, value: dict[str, Any] | None) -> None:
        self.metadata["tool_config"] = dict(value or {})

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
