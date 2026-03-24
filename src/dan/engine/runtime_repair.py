"""Bounded runtime self-healing models and repair planning."""

from __future__ import annotations

import hashlib
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RuntimeFailureCategory(str, Enum):
    TIMEOUT = "timeout"
    LLM_FAILURE = "llm_failure"
    SCHEMA_MISMATCH = "schema_mismatch"
    TOOL_FAILURE = "tool_failure"
    CONFIG_FAILURE = "config_failure"
    CODE_FAILURE = "code_failure"
    CONDITION_FAILURE = "condition_failure"
    UNKNOWN = "unknown"


class RuntimeRepairKind(str, Enum):
    RETRY = "retry"
    PROMPT_FIX = "prompt_fix"
    PARAMETER_FIX = "parameter_fix"
    CODE_FIX = "code_fix"
    ADVISORY = "advisory"
    NONE = "none"


class RuntimeOverlay(BaseModel):
    """Execution-local overlay applied only while a node is pending."""

    node_id: str
    patch: dict[str, Any] = Field(default_factory=dict)
    source: str = "runtime_repair"
    reason: str = ""
    created_at: float = Field(default_factory=time.time)
    based_on_failure_signature: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)


class RuntimeRepairAttempt(BaseModel):
    """Recorded repair attempt for a node failure."""

    kind: RuntimeRepairKind
    category: RuntimeFailureCategory
    failure_signature: str
    outcome: str
    reason: str = ""
    cause: str = ""
    next_step: str = ""
    user_visible_message: str = ""
    post_run_repair_level: str = ""
    overlay: RuntimeOverlay | None = None
    diagnostic_record: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)


class RuntimeFailureContext(BaseModel):
    """Normalized failure context routed into runtime repair."""

    run_id: str
    workflow_id: str = ""
    node_id: str
    node_type: str = ""
    category: RuntimeFailureCategory
    failure_signature: str
    error_message: str = ""
    retry_budget_remaining: int = 0
    overlay_provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_record: dict[str, Any] = Field(default_factory=dict)


class RuntimeRepairPlan(BaseModel):
    """Bounded repair decision emitted by the runtime repair layer."""

    kind: RuntimeRepairKind = RuntimeRepairKind.NONE
    category: RuntimeFailureCategory = RuntimeFailureCategory.UNKNOWN
    retry_current_node: bool = False
    retry_delay_sec: float = 0.0
    overlay: RuntimeOverlay | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


_REPAIR_LIMITS = {
    RuntimeRepairKind.RETRY.value: 2,
    RuntimeRepairKind.PROMPT_FIX.value: 1,
    RuntimeRepairKind.PARAMETER_FIX.value: 1,
    RuntimeRepairKind.CODE_FIX.value: 1,
}

_SAFE_OVERLAY_FIELDS = frozenset({
    "system_prompt",
    "prompt_template",
    "model",
    "temperature",
    "max_tokens",
    "retry_policy",
    "tool_id",
    "tool_config",
    "sandbox_config",
    "code",
})


def runtime_self_healing_enabled(config: Any) -> bool:
    """Feature flag gate for bounded runtime self-healing."""

    import os

    if getattr(config, "runtime_self_healing_enabled", False):
        return True
    return os.getenv("DAN_RUNTIME_SELF_HEALING", "0").strip() in {"1", "true", "yes"}


def classify_runtime_failure(node: Any, error_message: str, metadata: dict[str, Any] | None = None) -> RuntimeFailureCategory:
    """Map executor failures into stable runtime categories."""

    lower = (error_message or "").lower()
    node_type = str(getattr(node, "node_type", "") or "")

    if "timeout" in lower:
        return RuntimeFailureCategory.TIMEOUT
    if "invalid json" in lower or "schema" in lower or "normalization" in lower:
        return RuntimeFailureCategory.SCHEMA_MISMATCH
    if "condition evaluation" in lower:
        return RuntimeFailureCategory.CONDITION_FAILURE
    if node_type == "tool_operator":
        if "unknown tool" in lower or "not found" in lower or "config" in lower:
            return RuntimeFailureCategory.CONFIG_FAILURE
        return RuntimeFailureCategory.TOOL_FAILURE
    if node_type == "code_operator":
        return RuntimeFailureCategory.CODE_FAILURE
    if node_type == "llm_operator":
        return RuntimeFailureCategory.LLM_FAILURE
    return RuntimeFailureCategory.UNKNOWN


def build_runtime_error_record(
    failure_context: RuntimeFailureContext,
    *,
    input_snapshot: dict[str, Any] | None = None,
    upstream_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Project runtime failures onto the shared ErrorRecord vocabulary."""

    from dan.engine.error_memory import ErrorCategory, ErrorRecord

    category_map = {
        RuntimeFailureCategory.TIMEOUT: ErrorCategory.TIMEOUT,
        RuntimeFailureCategory.LLM_FAILURE: ErrorCategory.LLM_FAILURE,
        RuntimeFailureCategory.SCHEMA_MISMATCH: ErrorCategory.SCHEMA_MISMATCH,
        RuntimeFailureCategory.TOOL_FAILURE: ErrorCategory.TOOL_FAILURE,
        RuntimeFailureCategory.CONFIG_FAILURE: ErrorCategory.TOOL_FAILURE,
        RuntimeFailureCategory.CONDITION_FAILURE: ErrorCategory.CONDITION_FAILURE,
    }
    record = ErrorRecord(
        run_id=failure_context.run_id,
        workflow_id=failure_context.workflow_id,
        node_id=failure_context.node_id,
        node_type=failure_context.node_type,
        error_message=failure_context.error_message,
        error_category=category_map.get(failure_context.category, ErrorCategory.UNKNOWN),
        input_snapshot=input_snapshot,
        upstream_node_ids=list(upstream_node_ids or []),
    )
    return record.model_dump()


def failure_signature(node: Any, category: RuntimeFailureCategory, error_message: str) -> str:
    """Compute a stable per-node failure signature for dedupe/circuit breaking."""

    payload = f"{getattr(node, 'id', '')}|{getattr(node, 'node_type', '')}|{category.value}|{error_message or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def advisory_repair_level(kind: RuntimeRepairKind, category: RuntimeFailureCategory, next_step: str) -> str:
    """Normalize runtime repair outcomes onto the shared RepairClassifier scale."""

    from dan.repair_classification import RepairClassifier

    if kind == RuntimeRepairKind.PARAMETER_FIX:
        repair_level = "parameter_fix"
    elif kind == RuntimeRepairKind.PROMPT_FIX:
        repair_level = "prompt_fix"
    elif kind == RuntimeRepairKind.RETRY:
        repair_level = "retry"
    elif kind == RuntimeRepairKind.CODE_FIX:
        repair_level = "parameter_fix"
    else:
        repair_level = (
            "parameter_fix"
            if category in {
                RuntimeFailureCategory.TOOL_FAILURE,
                RuntimeFailureCategory.CONFIG_FAILURE,
                RuntimeFailureCategory.CODE_FAILURE,
            }
            else "prompt_fix"
        )

    synthetic_principle = type(
        "_RuntimePrinciple",
        (),
        {"repair_level": repair_level, "action": next_step or repair_level},
    )()
    level = RepairClassifier().classify(synthetic_principle)
    return level.name.lower()


def get_repair_lineage(state: Any, node_id: str) -> dict[str, Any]:
    """Return or initialize per-node repair lineage in execution state."""

    store = state.run_state.setdefault("repair_lineage", {})
    return store.setdefault(
        node_id,
        {
            "attempts": [],
            "remaining_budgets": dict(_REPAIR_LIMITS),
            "attempted_signatures": {},
            "active_overlays": [],
            "last_outcome": "",
            "last_summary": {},
        },
    )


def active_overlay_for_node(state: Any, node_id: str) -> list[dict[str, Any]]:
    """Return pending overlays for a node."""

    overlays = state.run_state.setdefault("pending_overlays", {})
    return list(overlays.get(node_id, []))


def apply_pending_overlay(state: Any, overlay: RuntimeOverlay) -> None:
    """Persist an overlay so the scheduler can apply it when the node is pending."""

    overlays = state.run_state.setdefault("pending_overlays", {})
    overlays.setdefault(overlay.node_id, []).append(overlay.model_dump())
    lineage = get_repair_lineage(state, overlay.node_id)
    lineage["active_overlays"] = list(overlays.get(overlay.node_id, []))


def clear_pending_overlays(state: Any, node_id: str) -> None:
    """Remove active overlays once a node leaves the pending repair path."""

    overlays = state.run_state.setdefault("pending_overlays", {})
    overlays.pop(node_id, None)
    lineage = get_repair_lineage(state, node_id)
    lineage["active_overlays"] = []


def record_repair_attempt(state: Any, node_id: str, attempt: RuntimeRepairAttempt) -> None:
    """Append a repair attempt into checkpointed lineage."""

    lineage = get_repair_lineage(state, node_id)
    lineage["attempts"].append(attempt.model_dump())
    lineage["last_outcome"] = attempt.outcome
    lineage["last_summary"] = {
        "cause": attempt.cause,
        "repair_attempted": attempt.kind.value,
        "next_step": attempt.next_step,
        "user_visible_message": attempt.user_visible_message,
        "failure_signature": attempt.failure_signature,
        "post_run_repair_level": attempt.post_run_repair_level,
    }
    if attempt.kind != RuntimeRepairKind.NONE:
        remaining = lineage["remaining_budgets"].get(attempt.kind.value)
        if remaining is not None:
            lineage["remaining_budgets"][attempt.kind.value] = max(0, int(remaining) - 1)
    attempted = lineage["attempted_signatures"].setdefault(attempt.kind.value, [])
    if attempt.failure_signature not in attempted:
        attempted.append(attempt.failure_signature)


def repair_summary_for_node(state: Any, node_id: str) -> dict[str, Any]:
    """Convenience accessor for final repair guidance."""

    return dict(get_repair_lineage(state, node_id).get("last_summary", {}))


def overlay_patch_for_node(state: Any, node_id: str) -> dict[str, Any]:
    """Merge active overlay patches for a node."""

    merged: dict[str, Any] = {}
    for overlay in active_overlay_for_node(state, node_id):
        patch = overlay.get("patch", {})
        for key, value in patch.items():
            if key in _SAFE_OVERLAY_FIELDS:
                merged[key] = value
    return merged


def _remaining_budget(state: Any, node_id: str, kind: RuntimeRepairKind) -> int:
    lineage = get_repair_lineage(state, node_id)
    return int(lineage["remaining_budgets"].get(kind.value, 0))


def _already_failed_signature(state: Any, node_id: str, kind: RuntimeRepairKind, signature: str) -> bool:
    lineage = get_repair_lineage(state, node_id)
    attempted = lineage["attempted_signatures"].get(kind.value, [])
    return signature in attempted


def _repair_overlay_from_metadata(
    node: Any,
    *,
    node_id: str,
    signature: str,
    source: str,
    reason: str,
) -> RuntimeOverlay | None:
    metadata = getattr(node, "metadata", {}) or {}
    patch = metadata.get("runtime_repair_overlay") or metadata.get("repair_overlay")
    if not isinstance(patch, dict):
        return None
    allowed = {key: value for key, value in patch.items() if key in _SAFE_OVERLAY_FIELDS}
    if not allowed:
        return None
    return RuntimeOverlay(
        node_id=node_id,
        patch=allowed,
        source=source,
        reason=reason,
        based_on_failure_signature=signature,
        provenance={"metadata_overlay": True},
    )


def plan_runtime_repair(
    state: Any,
    node: Any,
    failure_context: RuntimeFailureContext,
) -> RuntimeRepairPlan:
    """Choose a bounded repair action for a failed node."""

    node_id = failure_context.node_id
    signature = failure_context.failure_signature
    category = failure_context.category

    if category in {RuntimeFailureCategory.TIMEOUT, RuntimeFailureCategory.LLM_FAILURE}:
        if _remaining_budget(state, node_id, RuntimeRepairKind.RETRY) > 0 and not _already_failed_signature(
            state, node_id, RuntimeRepairKind.RETRY, signature
        ):
            return RuntimeRepairPlan(
                kind=RuntimeRepairKind.RETRY,
                category=category,
                retry_current_node=True,
                retry_delay_sec=0.0,
                summary={
                    "cause": category.value,
                    "repair_attempted": RuntimeRepairKind.RETRY.value,
                    "next_step": "Retry the node once more at the scheduler boundary.",
                    "user_visible_message": "Transient model failure detected. Retrying the node.",
                    "post_run_repair_level": advisory_repair_level(
                        RuntimeRepairKind.RETRY,
                        category,
                        "Retry the node once more at the scheduler boundary.",
                    ),
                },
            )

    if category == RuntimeFailureCategory.SCHEMA_MISMATCH:
        overlay = _repair_overlay_from_metadata(
            node,
            node_id=node_id,
            signature=signature,
            source="runtime_repair",
            reason="schema_mismatch",
        )
        if overlay is not None and _remaining_budget(state, node_id, RuntimeRepairKind.PROMPT_FIX) > 0:
            return RuntimeRepairPlan(
                kind=RuntimeRepairKind.PROMPT_FIX,
                category=category,
                retry_current_node=True,
                overlay=overlay,
                summary={
                    "cause": category.value,
                    "repair_attempted": RuntimeRepairKind.PROMPT_FIX.value,
                    "next_step": "Retry with a stricter prompt/model overlay.",
                    "user_visible_message": "Structured output was invalid. Applying a prompt fix and retrying.",
                    "post_run_repair_level": advisory_repair_level(
                        RuntimeRepairKind.PROMPT_FIX,
                        category,
                        "Retry with a stricter prompt/model overlay.",
                    ),
                },
            )
        if _remaining_budget(state, node_id, RuntimeRepairKind.RETRY) > 0 and not _already_failed_signature(
            state, node_id, RuntimeRepairKind.RETRY, signature
        ):
            return RuntimeRepairPlan(
                kind=RuntimeRepairKind.RETRY,
                category=category,
                retry_current_node=True,
                summary={
                    "cause": category.value,
                    "repair_attempted": RuntimeRepairKind.RETRY.value,
                    "next_step": "Retry the node once after schema/JSON normalization failure.",
                    "user_visible_message": "Structured output validation failed. Retrying once.",
                    "post_run_repair_level": advisory_repair_level(
                        RuntimeRepairKind.RETRY,
                        category,
                        "Retry the node once after schema/JSON normalization failure.",
                    ),
                },
            )

    if category in {RuntimeFailureCategory.TOOL_FAILURE, RuntimeFailureCategory.CONFIG_FAILURE}:
        overlay = _repair_overlay_from_metadata(
            node,
            node_id=node_id,
            signature=signature,
            source="runtime_repair",
            reason=category.value,
        )
        if overlay is not None and _remaining_budget(state, node_id, RuntimeRepairKind.PARAMETER_FIX) > 0:
            return RuntimeRepairPlan(
                kind=RuntimeRepairKind.PARAMETER_FIX,
                category=category,
                retry_current_node=True,
                overlay=overlay,
                summary={
                    "cause": category.value,
                    "repair_attempted": RuntimeRepairKind.PARAMETER_FIX.value,
                    "next_step": "Retry with a whitelisted tool/config overlay.",
                    "user_visible_message": "Tool configuration looked repairable. Applying a bounded overlay.",
                    "post_run_repair_level": advisory_repair_level(
                        RuntimeRepairKind.PARAMETER_FIX,
                        category,
                        "Retry with a whitelisted tool/config overlay.",
                    ),
                },
            )
        if _remaining_budget(state, node_id, RuntimeRepairKind.RETRY) > 0 and not _already_failed_signature(
            state, node_id, RuntimeRepairKind.RETRY, signature
        ):
            return RuntimeRepairPlan(
                kind=RuntimeRepairKind.RETRY,
                category=category,
                retry_current_node=True,
                summary={
                    "cause": category.value,
                    "repair_attempted": RuntimeRepairKind.RETRY.value,
                    "next_step": "Retry once before surfacing the tool failure.",
                    "user_visible_message": "Tool failure looked transient. Retrying once.",
                    "post_run_repair_level": advisory_repair_level(
                        RuntimeRepairKind.RETRY,
                        category,
                        "Retry once before surfacing the tool failure.",
                    ),
                },
            )

    if category == RuntimeFailureCategory.CODE_FAILURE:
        metadata = getattr(node, "metadata", {}) or {}
        if bool(metadata.get("repairable_code")):
            overlay = _repair_overlay_from_metadata(
                node,
                node_id=node_id,
                signature=signature,
                source="runtime_repair",
                reason="code_failure",
            )
            if overlay is not None and _remaining_budget(state, node_id, RuntimeRepairKind.CODE_FIX) > 0:
                return RuntimeRepairPlan(
                    kind=RuntimeRepairKind.CODE_FIX,
                    category=category,
                    retry_current_node=True,
                    overlay=overlay,
                    summary={
                        "cause": category.value,
                        "repair_attempted": RuntimeRepairKind.CODE_FIX.value,
                        "next_step": "Retry with the explicit repairable-code overlay.",
                        "user_visible_message": "Repairable generated code failed. Applying a bounded code overlay.",
                        "post_run_repair_level": advisory_repair_level(
                            RuntimeRepairKind.CODE_FIX,
                            category,
                            "Retry with the explicit repairable-code overlay.",
                        ),
                    },
                )
        return RuntimeRepairPlan(
            kind=RuntimeRepairKind.ADVISORY,
            category=category,
            retry_current_node=False,
            summary={
                "cause": category.value,
                "repair_attempted": RuntimeRepairKind.ADVISORY.value,
                "next_step": "Inspect or replace the failing code explicitly; runtime self-healing will not edit static code.",
                "user_visible_message": "Code execution failed. Runtime self-healing captured lineage but will not retry or edit non-repairable code automatically.",
                "post_run_repair_level": advisory_repair_level(
                    RuntimeRepairKind.ADVISORY,
                    category,
                    "Inspect or replace the failing code explicitly; runtime self-healing will not edit static code.",
                ),
            },
        )

    if _remaining_budget(state, node_id, RuntimeRepairKind.RETRY) > 0 and not _already_failed_signature(
        state, node_id, RuntimeRepairKind.RETRY, signature
    ):
        return RuntimeRepairPlan(
            kind=RuntimeRepairKind.RETRY,
            category=category,
            retry_current_node=True,
            summary={
                "cause": category.value,
                "repair_attempted": RuntimeRepairKind.RETRY.value,
                "next_step": "One conservative retry before surfacing failure.",
                "user_visible_message": "Retrying once before surfacing the failure.",
                "post_run_repair_level": advisory_repair_level(
                    RuntimeRepairKind.RETRY,
                    category,
                    "One conservative retry before surfacing failure.",
                ),
            },
        )

    return RuntimeRepairPlan(
        kind=RuntimeRepairKind.ADVISORY,
        category=category,
        retry_current_node=False,
        summary={
            "cause": category.value,
            "repair_attempted": RuntimeRepairKind.ADVISORY.value,
            "next_step": "No bounded repair remained; inspect the node or resume with explicit changes.",
            "user_visible_message": "Runtime self-healing exhausted its bounded repair budget.",
            "post_run_repair_level": advisory_repair_level(
                RuntimeRepairKind.ADVISORY,
                category,
                "No bounded repair remained; inspect the node or resume with explicit changes.",
            ),
        },
    )
