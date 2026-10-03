"""Soft-budget helpers for Chat V2 scheduler continuation caps."""

from __future__ import annotations

import os
from typing import Any, Mapping

from diane.server.chat_v2 import AgentRunEvent
from diane.server.chat_v2_store import AgentRunRecord


_DEFAULT_MAX_LEASES = 2
_MAX_LEASES_HARD_CAP = 6
_DEFAULT_EXTRA_CONTINUATIONS = 2
_MAX_EXTRA_CONTINUATIONS_HARD_CAP = 4
_TERMINAL_RUN_STATUSES = {"completed", "failed", "blocked", "stopped"}
_DISABLE_TOKENS = {"0", "false", "no", "off", "disabled"}
_ENABLE_TOKENS = {"1", "true", "yes", "on", "enabled"}


def scheduler_continuation_budget_decision(
    run: AgentRunRecord,
    *,
    cap_name: str,
    reason: str,
    remaining_continuations: int,
    candidate_count: int = 0,
    next_run_id: str = "",
    requested_extra: int = _DEFAULT_EXTRA_CONTINUATIONS,
) -> dict[str, Any]:
    """Decide whether a scheduler continuation cap gets a small lease.

    This intentionally mirrors the worker soft-budget shape but stays
    deterministic: scheduler caps are about whether bounded orchestration may
    continue, not about giving a worker more local tool turns.
    """

    sources = _scheduler_budget_sources(run)
    existing_extensions = _existing_extensions(sources)
    audit = {
        "cap_name": str(cap_name or "continuation"),
        "reason": _compact(reason, limit=300),
        "run_id": run.run_id,
        "task_id": run.task_id,
        "run_status": run.status,
        "remaining_continuations": max(0, int(remaining_continuations or 0)),
        "candidate_count": max(0, int(candidate_count or 0)),
        "next_run_id": str(next_run_id or ""),
        "existing_lease_count": len(existing_extensions),
    }

    if not _scheduler_budget_enabled(sources):
        return _denied(audit, existing_extensions, "Scheduler soft budget is disabled.")
    if run.metadata.get("stop_requested") or run.metadata.get("pause_requested"):
        return _denied(audit, existing_extensions, "Run has an operator stop/pause request.")
    if run.status not in _TERMINAL_RUN_STATUSES:
        return _denied(audit, existing_extensions, "Run is not at a terminal scheduler checkpoint.")
    if not audit["next_run_id"] and audit["candidate_count"] <= 0:
        return _denied(audit, existing_extensions, "No queued continuation/dependency candidate is available.")

    max_leases = _scheduler_budget_max_leases(sources)
    audit["max_leases"] = max_leases
    if len(existing_extensions) >= max_leases:
        return _denied(
            audit,
            existing_extensions,
            f"Scheduler soft-budget lease limit reached ({max_leases}).",
        )

    extra = min(
        _scheduler_budget_max_extra_continuations(sources),
        max(1, int(requested_extra or _DEFAULT_EXTRA_CONTINUATIONS)),
    )
    lease = {
        **audit,
        "lease_index": len(existing_extensions) + 1,
        "extra_continuations": extra,
        "decision": "approved",
    }
    extensions = [*existing_extensions, lease]
    return {
        "approved": True,
        "extra_continuations": extra,
        "reason": _grant_reason(cap_name=cap_name, extra=extra, reason=reason),
        "audit": audit,
        "lease": lease,
        "scheduler_budget_extensions": extensions,
    }


def scheduler_budget_metadata_update(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Return metadata to persist a scheduler budget decision on a run."""

    payload = {
        "scheduler_budget_last_decision": {
            key: value
            for key, value in dict(decision).items()
            if key not in {"scheduler_budget_extensions"}
        }
    }
    extensions = decision.get("scheduler_budget_extensions")
    if isinstance(extensions, list):
        payload["scheduler_budget_extensions"] = [
            dict(item) for item in extensions if isinstance(item, Mapping)
        ]
    return payload


def scheduler_budget_event(
    run: AgentRunRecord,
    decision: Mapping[str, Any],
) -> AgentRunEvent:
    """Project a scheduler budget decision as a non-mutating status event."""

    approved = bool(decision.get("approved"))
    extra = int(decision.get("extra_continuations") or 0)
    if approved:
        label = "continuation" if extra == 1 else "continuations"
        summary = f"Scheduler lease granted: +{extra} {label}."
    else:
        summary = "Scheduler lease denied."
    reason = _compact(decision.get("reason"), limit=240)
    if reason:
        summary = f"{summary} Reason: {reason}"
    return AgentRunEvent(
        type="status_reported",
        run_id=run.run_id,
        task_id=run.task_id,
        summary=summary,
        source_event_type=(
            "chat_v2.scheduler.soft_budget.approved"
            if approved
            else "chat_v2.scheduler.soft_budget.denied"
        ),
        payload=dict(decision),
    )


def _scheduler_budget_sources(run: AgentRunRecord) -> list[Mapping[str, Any]]:
    payload = dict(getattr(run.command, "payload", {}) or {})
    sources: list[Mapping[str, Any]] = [
        payload,
        dict(payload.get("profile_policy") or {}),
        dict(run.metadata or {}),
        dict((run.metadata or {}).get("profile_policy") or {}),
    ]
    return sources


def _scheduler_budget_enabled(sources: list[Mapping[str, Any]]) -> bool:
    for key in (
        "scheduler_soft_budget",
        "scheduler_budget_enabled",
        "soft_scheduler_budget",
    ):
        value = _first_value(sources, key)
        if _is_explicit_false(value):
            return False
        if _is_explicit_true(value):
            return True
    raw = os.environ.get("DAN_CHAT_V2_SCHEDULER_SOFT_BUDGET", "")
    if raw.strip().lower() in _DISABLE_TOKENS:
        return False
    return True


def _scheduler_budget_max_leases(sources: list[Mapping[str, Any]]) -> int:
    value = _first_value(
        sources,
        "max_scheduler_budget_leases",
        "scheduler_budget_max_leases",
    )
    if value is None:
        value = os.environ.get("DAN_CHAT_V2_SCHEDULER_BUDGET_MAX_LEASES")
    return _clamp_int(
        value,
        default=_DEFAULT_MAX_LEASES,
        lower=0,
        upper=_MAX_LEASES_HARD_CAP,
    )


def _scheduler_budget_max_extra_continuations(sources: list[Mapping[str, Any]]) -> int:
    value = _first_value(
        sources,
        "max_scheduler_extra_continuations",
        "scheduler_budget_max_extra_continuations",
    )
    if value is None:
        value = os.environ.get("DAN_CHAT_V2_SCHEDULER_BUDGET_MAX_EXTRA_CONTINUATIONS")
    return _clamp_int(
        value,
        default=_DEFAULT_EXTRA_CONTINUATIONS,
        lower=1,
        upper=_MAX_EXTRA_CONTINUATIONS_HARD_CAP,
    )


def _existing_extensions(sources: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] = []
    for source in sources:
        raw = source.get("scheduler_budget_extensions")
        if isinstance(raw, list):
            candidate = [dict(item) for item in raw if isinstance(item, Mapping)]
            if len(candidate) > len(best):
                best = candidate
    return best


def _first_value(sources: list[Mapping[str, Any]], *keys: str) -> Any:
    for source in sources:
        for key in keys:
            if key in source:
                return source.get(key)
    return None


def _is_explicit_false(value: Any) -> bool:
    return (isinstance(value, bool) and not value) or (
        isinstance(value, str) and value.strip().lower() in _DISABLE_TOKENS
    )


def _is_explicit_true(value: Any) -> bool:
    return value is True or (
        isinstance(value, str) and value.strip().lower() in _ENABLE_TOKENS
    )


def _clamp_int(value: Any, *, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(parsed, upper))


def _denied(
    audit: dict[str, Any],
    existing_extensions: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    return {
        "approved": False,
        "extra_continuations": 0,
        "reason": reason,
        "audit": audit,
        "scheduler_budget_extensions": list(existing_extensions),
    }


def _grant_reason(*, cap_name: str, extra: int, reason: str) -> str:
    cap = str(cap_name or "continuation").replace("_", " ")
    base = f"{cap} cap reached, but queued work can plausibly close the current plan."
    if reason:
        return f"{base} {reason}"
    return base


def _compact(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
