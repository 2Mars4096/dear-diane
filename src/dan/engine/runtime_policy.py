"""Run-scoped execution policies, presets, and progress helpers."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from dan.models.nodes import RetryPolicy


class RunPhase(str, Enum):
    """Explicit run lifecycle phases for long-running/reporting surfaces."""

    ACTIVE = "active"
    STOPPING_ON_LIMIT = "stopping_on_limit"
    PARTIAL = "partial"
    RESUMABLE = "resumable"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    BACKGROUNDED = "backgrounded"


class StopReason(str, Enum):
    """Deterministic stop reasons surfaced in results and checkpoints."""

    NONE = ""
    DURATION_LIMIT = "duration_limit"
    COST_LIMIT = "cost_limit"
    HALT = "halt"
    FAILURE = "failure"


@dataclass
class RunPolicy:
    """User-facing per-run execution policy overlay."""

    profile: str | None = None
    max_duration: float | None = None
    max_cost: float | None = None
    checkpoint_batch_size: int | None = None
    checkpoint_interval_sec: float | None = None
    checkpoint_on_critical_nodes: bool | None = None
    critical_node_tags: list[str] | None = None
    partial_results: bool | None = None
    progress_enabled: bool | None = None
    progress_emit_events: bool | None = None
    progress_eta_enabled: bool | None = None
    progress_stage_labels: bool | None = None
    default_retry_policy: RetryPolicy | dict[str, Any] | None = None
    default_failure_policy: Any | None = None
    default_model_policy: Any | None = None
    fallback_model: str | None = None

    @classmethod
    def from_input(cls, value: "RunPolicy | dict[str, Any] | None") -> "RunPolicy | None":
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        data = dict(value)
        retry = data.get("default_retry_policy")
        if isinstance(retry, dict):
            data["default_retry_policy"] = RetryPolicy(**retry)
        return cls(**data)


@dataclass
class EffectiveRunPolicy:
    """Resolved per-run policy after config + preset + override precedence."""

    profile: str = "default"
    max_duration: float | None = None
    max_cost: float | None = None
    checkpoint_batch_size: int = 5
    checkpoint_interval_sec: float = 10.0
    checkpoint_on_critical_nodes: bool = False
    critical_node_tags: list[str] = field(default_factory=list)
    partial_results: bool = False
    progress_enabled: bool = True
    progress_emit_events: bool = True
    progress_eta_enabled: bool = True
    progress_stage_labels: bool = True
    default_retry_policy: RetryPolicy | None = None
    default_failure_policy: Any | None = None
    default_model_policy: Any | None = None
    fallback_model: str | None = None

    @classmethod
    def from_snapshot(cls, data: dict[str, Any] | None) -> "EffectiveRunPolicy":
        if not data:
            return cls()
        payload = dict(data)
        retry = payload.get("default_retry_policy")
        if isinstance(retry, dict):
            payload["default_retry_policy"] = RetryPolicy(**retry)
        return cls(**payload)

    def snapshot(self) -> dict[str, Any]:
        data = {
            "profile": self.profile,
            "max_duration": self.max_duration,
            "max_cost": self.max_cost,
            "checkpoint_batch_size": self.checkpoint_batch_size,
            "checkpoint_interval_sec": self.checkpoint_interval_sec,
            "checkpoint_on_critical_nodes": self.checkpoint_on_critical_nodes,
            "critical_node_tags": list(self.critical_node_tags),
            "partial_results": self.partial_results,
            "progress_enabled": self.progress_enabled,
            "progress_emit_events": self.progress_emit_events,
            "progress_eta_enabled": self.progress_eta_enabled,
            "progress_stage_labels": self.progress_stage_labels,
            "default_failure_policy": _deepcopy_if_needed(self.default_failure_policy),
            "default_model_policy": _deepcopy_if_needed(self.default_model_policy),
            "fallback_model": self.fallback_model,
        }
        if self.default_retry_policy is not None:
            data["default_retry_policy"] = self.default_retry_policy.model_dump()
        else:
            data["default_retry_policy"] = None
        return data


def _deepcopy_if_needed(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:
        return value


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _long_running_preset(config: Any, policy: RunPolicy | None) -> dict[str, Any]:
    fallback_model = None
    if policy is not None and policy.fallback_model:
        fallback_model = policy.fallback_model
    elif getattr(config, "llm_default_model", None):
        fallback_model = getattr(config, "llm_default_model")

    return {
        "max_duration": None,
        "max_cost": None,
        "checkpoint_batch_size": 1,
        "checkpoint_interval_sec": min(
            5.0,
            max(0.5, float(getattr(config, "checkpoint_interval_sec", 10.0))),
        ),
        "checkpoint_on_critical_nodes": True,
        "critical_node_tags": ["critical", "checkpoint", "human"],
        "partial_results": True,
        "progress_enabled": True,
        "progress_emit_events": True,
        "progress_eta_enabled": True,
        "progress_stage_labels": True,
        "default_retry_policy": RetryPolicy(
            max_retries=2,
            backoff=1.0,
            backoff_max=10.0,
            fallback_model=fallback_model,
            on_failure="skip",
        ),
        "default_failure_policy": None,
        "default_model_policy": getattr(config, "default_model_policy", None),
        "fallback_model": fallback_model,
    }


def resolve_effective_run_policy(
    config: Any,
    requested: RunPolicy | dict[str, Any] | None = None,
    *,
    persisted: EffectiveRunPolicy | dict[str, Any] | None = None,
) -> EffectiveRunPolicy:
    """Resolve run policy precedence.

    Precedence: explicit per-run overrides > profile defaults > EngineConfig.
    When a persisted policy exists (resume path), it becomes the default base
    unless an explicit override is provided.
    """

    requested_policy = RunPolicy.from_input(requested)
    if persisted is not None:
        base = (
            persisted
            if isinstance(persisted, EffectiveRunPolicy)
            else EffectiveRunPolicy.from_snapshot(persisted)
        )
    else:
        profile = requested_policy.profile if requested_policy is not None and requested_policy.profile else "default"
        preset = _long_running_preset(config, requested_policy) if profile == "long_running" else {}
        base = EffectiveRunPolicy(
            profile=profile,
            max_duration=preset.get("max_duration"),
            max_cost=preset.get("max_cost"),
            checkpoint_batch_size=int(
                preset.get("checkpoint_batch_size", getattr(config, "checkpoint_batch_size", 5))
            ),
            checkpoint_interval_sec=float(
                preset.get(
                    "checkpoint_interval_sec",
                    getattr(config, "checkpoint_interval_sec", 10.0),
                )
            ),
            checkpoint_on_critical_nodes=bool(
                preset.get("checkpoint_on_critical_nodes", False)
            ),
            critical_node_tags=list(preset.get("critical_node_tags", [])),
            partial_results=bool(preset.get("partial_results", False)),
            progress_enabled=bool(preset.get("progress_enabled", True)),
            progress_emit_events=bool(preset.get("progress_emit_events", True)),
            progress_eta_enabled=bool(preset.get("progress_eta_enabled", True)),
            progress_stage_labels=bool(preset.get("progress_stage_labels", True)),
            default_retry_policy=_deepcopy_if_needed(preset.get("default_retry_policy")),
            default_failure_policy=_deepcopy_if_needed(preset.get("default_failure_policy")),
            default_model_policy=_deepcopy_if_needed(
                preset.get("default_model_policy", getattr(config, "default_model_policy", None))
            ),
            fallback_model=preset.get("fallback_model"),
        )

    if requested_policy is None:
        return base

    if requested_policy.profile is not None:
        base.profile = requested_policy.profile
    base.max_duration = _coalesce(requested_policy.max_duration, base.max_duration)
    base.max_cost = _coalesce(requested_policy.max_cost, base.max_cost)
    base.checkpoint_batch_size = int(
        _coalesce(requested_policy.checkpoint_batch_size, base.checkpoint_batch_size)
    )
    base.checkpoint_interval_sec = float(
        _coalesce(requested_policy.checkpoint_interval_sec, base.checkpoint_interval_sec)
    )
    base.checkpoint_on_critical_nodes = bool(
        _coalesce(requested_policy.checkpoint_on_critical_nodes, base.checkpoint_on_critical_nodes)
    )
    base.critical_node_tags = list(
        _coalesce(requested_policy.critical_node_tags, base.critical_node_tags)
    )
    base.partial_results = bool(
        _coalesce(requested_policy.partial_results, base.partial_results)
    )
    base.progress_enabled = bool(
        _coalesce(requested_policy.progress_enabled, base.progress_enabled)
    )
    base.progress_emit_events = bool(
        _coalesce(requested_policy.progress_emit_events, base.progress_emit_events)
    )
    base.progress_eta_enabled = bool(
        _coalesce(requested_policy.progress_eta_enabled, base.progress_eta_enabled)
    )
    base.progress_stage_labels = bool(
        _coalesce(requested_policy.progress_stage_labels, base.progress_stage_labels)
    )

    if requested_policy.default_retry_policy is not None:
        retry = requested_policy.default_retry_policy
        if isinstance(retry, dict):
            retry = RetryPolicy(**retry)
        base.default_retry_policy = _deepcopy_if_needed(retry)
    if requested_policy.default_failure_policy is not None:
        base.default_failure_policy = _deepcopy_if_needed(requested_policy.default_failure_policy)
    if requested_policy.default_model_policy is not None:
        base.default_model_policy = _deepcopy_if_needed(requested_policy.default_model_policy)
    if requested_policy.fallback_model is not None:
        base.fallback_model = requested_policy.fallback_model
        if base.default_retry_policy is not None and not base.default_retry_policy.fallback_model:
            base.default_retry_policy = base.default_retry_policy.model_copy(
                update={"fallback_model": requested_policy.fallback_model}
            )
    return base


def is_critical_checkpoint_node(node: Any, policy: EffectiveRunPolicy) -> bool:
    """True when the node should force a stronger checkpoint cadence."""

    if not policy.checkpoint_on_critical_nodes:
        return False
    metadata = getattr(node, "metadata", {}) or {}
    if bool(metadata.get("critical_checkpoint")):
        return True
    tags = set(getattr(node, "tags", []) or [])
    return bool(tags & set(policy.critical_node_tags))


def progress_snapshot(
    graph: Any,
    state: Any,
    *,
    elapsed_seconds: float,
    ready_node_ids: list[str] | None = None,
    active_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic progress snapshot from scheduler state."""

    from dan.engine.state import NodeStatus

    ready = sorted(set(ready_node_ids or []))
    active = sorted(set(active_node_ids or []))
    completed = sorted(
        node_id
        for node_id, status in state.node_statuses.items()
        if status == NodeStatus.COMPLETED
    )
    failed = sorted(
        node_id
        for node_id, status in state.node_statuses.items()
        if status == NodeStatus.FAILED
    )
    pending = sorted(
        node_id
        for node_id, status in state.node_statuses.items()
        if status in (NodeStatus.PENDING, NodeStatus.WAITING)
    )
    total_nodes = len(state.node_statuses)
    completed_count = len(completed)
    remaining_count = len(pending) + len(active)
    progress_fraction = (completed_count / total_nodes) if total_nodes else 1.0

    average_node_seconds = None
    if completed_count > 0:
        durations = [
            float((state.node_metadata.get(node_id, {}) or {}).get("elapsed_seconds", 0.0) or 0.0)
            for node_id in completed
        ]
        durations = [value for value in durations if value > 0]
        if durations:
            average_node_seconds = sum(durations) / len(durations)

    eta_seconds = None
    if average_node_seconds is not None and remaining_count > 0:
        eta_seconds = round(average_node_seconds * remaining_count, 3)

    stage_nodes = active or ready or pending
    stage_label = ""
    if stage_nodes:
        names: list[str] = []
        for node_id in stage_nodes[:3]:
            node = graph.node_by_id(node_id) if hasattr(graph, "node_by_id") else None
            names.append(getattr(node, "name", node_id) if node is not None else node_id)
        stage_label = " -> ".join(names)

    return {
        "elapsed_seconds": round(elapsed_seconds, 3),
        "completed_nodes": completed_count,
        "total_nodes": total_nodes,
        "remaining_nodes": remaining_count,
        "progress_fraction": round(progress_fraction, 4),
        "completed_node_ids": completed,
        "pending_node_ids": pending,
        "active_node_ids": active,
        "ready_node_ids": ready,
        "failed_node_ids": failed,
        "eta_seconds": eta_seconds,
        "stage_label": stage_label,
    }


def stable_invocation_key(*parts: Any) -> str:
    """Stable hash helper reused by composition/repair subsystems."""

    try:
        payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        payload = repr(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
