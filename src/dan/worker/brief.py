"""Typed brief contracts rendered into universal-cell execution requests."""

from __future__ import annotations

import json
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, Field

from dan.worker.contracts.prompt_context import PromptContext
from dan.worker.contracts.sampling import SamplingPolicy, resolve_sampling_policy
from dan.worker.core.contracts import (
    EvidenceBlock,
    ExecutionRequest,
    OutputContract,
    ToolUseContract,
)


class RoleSpec(BaseModel):
    """Orchestrator-discovered role data, not infrastructure role code."""

    role_label: str
    responsibility: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    artifact_targets: list[str] = Field(default_factory=list)
    collaboration_contract: str = ""
    trace_role: str = ""


class HeartbeatPolicy(BaseModel):
    runtime_cadence_seconds: float | None = Field(default=None, gt=0)
    semantic_cadence_seconds: float | None = Field(default=None, gt=0)
    stale_timeout_seconds: float | None = Field(default=None, gt=0)
    material_event_triggers: list[str] = Field(default_factory=list)


class SemanticStatusContract(BaseModel):
    fields: list[str] = Field(
        default_factory=lambda: [
            "current_focus",
            "last_material_change",
            "next_intended_action",
            "blocker",
            "confidence",
            "risk_flags",
            "artifact_refs",
        ]
    )
    max_chars: int = Field(default=800, ge=1)


class DecisionTriggerPolicy(BaseModel):
    trigger_events: list[str] = Field(default_factory=list)
    observer_cadence_seconds: float | None = Field(default=None, gt=0)
    min_material_delta: int | None = Field(default=None, ge=0)
    require_admission_gate: bool = True


class AgentLifecyclePolicy(BaseModel):
    max_spawned_cells: int | None = Field(default=None, ge=0)
    max_depth: int | None = Field(default=None, ge=0)
    idle_timeout_seconds: float | None = Field(default=None, gt=0)
    stale_heartbeat_timeout_seconds: float | None = Field(default=None, gt=0)
    close_behavior: Literal["close", "keep-idle"] = "close"
    resume_behavior: Literal["allowed", "disallowed"] = "allowed"
    cancel_propagation: Literal["descendants", "self-only"] = "descendants"


class MailboxPolicy(BaseModel):
    passive_notes_do_not_wake: bool = True
    wake_on_followup_commands: bool = True
    note_vs_followup_semantics: str = (
        "Passive notes are recorded as context; admitted follow-up commands wake execution."
    )
    max_pending_messages: int | None = Field(default=None, ge=0)


class WorkerBrief(BaseModel):
    """The sole specialization channel from orchestrator to universal cell."""

    role: RoleSpec
    task: str
    scope: str | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)
    tool_policy: ToolUseContract = Field(default_factory=ToolUseContract)
    runtime_policy: dict[str, Any] = Field(default_factory=dict)
    validation_policy: dict[str, Any] = Field(default_factory=dict)
    lifecycle_policy: AgentLifecyclePolicy = Field(default_factory=AgentLifecyclePolicy)
    mailbox_policy: MailboxPolicy = Field(default_factory=MailboxPolicy)
    prompt_context: PromptContext = Field(default_factory=PromptContext)
    prompt_slots: dict[str, Any] = Field(default_factory=dict)
    contract_snippets: list[str] = Field(default_factory=list)
    fail_predicates: list[str] = Field(default_factory=list)
    recovery_hints: list[str] = Field(default_factory=list)
    output_contract: OutputContract = Field(default_factory=OutputContract)
    sampling_policy: SamplingPolicy = Field(default_factory=resolve_sampling_policy)
    heartbeat_policy: HeartbeatPolicy = Field(default_factory=HeartbeatPolicy)
    semantic_status_contract: SemanticStatusContract = Field(default_factory=SemanticStatusContract)
    decision_trigger_policy: DecisionTriggerPolicy = Field(default_factory=DecisionTriggerPolicy)
    evidence: list[EvidenceBlock] = Field(default_factory=list)
    context_packet: dict[str, Any] = Field(default_factory=dict)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _list_section(items: Sequence[Any]) -> str:
    return "\n".join(f"- {_stringify(item)}" for item in items if _stringify(item).strip())


def _section(title: str, body: Any) -> str:
    text = _stringify(body).strip()
    if not text:
        return ""
    return f"{title}:\n{text}"


def _evidence_section(evidence: Sequence[EvidenceBlock]) -> str:
    blocks = []
    for block in evidence:
        blocks.append(
            "\n".join(
                part
                for part in [
                    f"- {block.label}",
                    f"  ref: {block.ref_id}" if block.ref_id else "",
                    f"  source: {block.source}" if block.source else "",
                    "  content: " + _stringify(block.content).replace("\n", "\n  "),
                ]
                if part
            )
        )
    return "\n".join(blocks)


def render_brief_prompt(role: RoleSpec | WorkerBrief, brief: WorkerBrief | None = None) -> str:
    """Render a brief through the fixed universal prompt architecture."""

    resolved_brief = role if isinstance(role, WorkerBrief) else brief
    if resolved_brief is None:
        raise ValueError("render_brief_prompt requires a WorkerBrief")
    resolved_role = resolved_brief.role if isinstance(role, WorkerBrief) else role
    sections = [
        _section("Role", resolved_role.model_dump(mode="json", exclude_none=True)),
        _section("Task", resolved_brief.task),
        _section("Scope", resolved_brief.scope),
        _section("Hard constraints", _list_section(resolved_brief.hard_constraints)),
        _section("Soft constraints", _list_section(resolved_brief.soft_constraints)),
        _section("Evidence", _evidence_section(resolved_brief.evidence)),
        _section("Tool policy", resolved_brief.tool_policy.model_dump(mode="json", exclude_none=True)),
        _section("Runtime policy", resolved_brief.runtime_policy),
        _section("Validation policy", resolved_brief.validation_policy),
        _section(
            "Output contract",
            resolved_brief.output_contract.model_dump(mode="json", exclude_none=True),
        ),
        _section("Prompt slots", resolved_brief.prompt_slots),
        _section("Contract snippets", _list_section(resolved_brief.contract_snippets)),
        _section("Failure criteria", _list_section(resolved_brief.fail_predicates)),
        _section("Recovery hints", _list_section(resolved_brief.recovery_hints)),
        _section("Input payload", resolved_brief.input_payload),
        _section("Context packet", resolved_brief.context_packet),
        _section(
            "Lifecycle policy",
            resolved_brief.lifecycle_policy.model_dump(mode="json", exclude_none=True),
        ),
        _section("Mailbox policy", resolved_brief.mailbox_policy.model_dump(mode="json", exclude_none=True)),
        _section(
            "Status contract",
            {
                "heartbeat_policy": resolved_brief.heartbeat_policy.model_dump(mode="json", exclude_none=True),
                "semantic_status_contract": resolved_brief.semantic_status_contract.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
                "decision_trigger_policy": resolved_brief.decision_trigger_policy.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
            },
        ),
        _section("Metadata", resolved_brief.metadata),
    ]
    return "\n\n".join(part for part in sections if part)


def request_from_brief(brief: WorkerBrief | Mapping[str, Any]) -> ExecutionRequest:
    """Convert a universal worker brief into the existing worker-core request."""

    resolved = brief if isinstance(brief, WorkerBrief) else WorkerBrief.model_validate(dict(brief))
    rendered_prompt = render_brief_prompt(resolved)
    prompt_context = resolved.prompt_context.model_copy(
        update={
            "slot_values": {
                **dict(resolved.prompt_context.slot_values),
                **dict(resolved.prompt_slots),
            }
        }
    )
    metadata = {
        **dict(resolved.metadata),
        "role_spec": resolved.role.model_dump(mode="json", exclude_none=True),
        "trace_role": resolved.role.trace_role or resolved.role.role_label,
        "brief_rendered_user_prompt": rendered_prompt,
        "prompt_context": prompt_context.model_dump(mode="json", exclude_none=True),
        "prompt_stable_fingerprint": prompt_context.stable_fingerprint(),
        "prompt_slot_fingerprint": prompt_context.slot_fingerprint(),
        "sampling_policy": resolved.sampling_policy.model_dump(mode="json", exclude_none=True),
        "runtime_policy": dict(resolved.runtime_policy),
        "validation_policy": dict(resolved.validation_policy),
        "lifecycle_policy": resolved.lifecycle_policy.model_dump(mode="json", exclude_none=True),
        "mailbox_policy": resolved.mailbox_policy.model_dump(mode="json", exclude_none=True),
    }
    return ExecutionRequest.from_handoff(
        task=resolved.task,
        scope=resolved.scope,
        hard_constraints=resolved.hard_constraints,
        soft_constraints=resolved.soft_constraints,
        evidence_blocks=resolved.evidence,
        tooling=resolved.tool_policy,
        definition_of_done=resolved.output_contract.definition_of_done,
        expected_return_shape=resolved.output_contract.expected_return_shape,
        output_schema=resolved.output_contract.output_schema,
        input_payload=resolved.input_payload,
        metadata=metadata,
    )
