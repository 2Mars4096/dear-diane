"""CLI helpers for consuming universal-organism event stream deltas."""

from __future__ import annotations

from typing import Any, Mapping


def universal_progress_delta(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a compact progress delta for terminal/editor renderers."""

    name = str(event.get("event") or "")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        payload = {}
    if name == "organism.run_state.delta":
        run_state = payload.get("run_state")
        if isinstance(run_state, Mapping):
            return {
                "kind": "run_state",
                "run_id": event.get("run_id", ""),
                "plan_id": event.get("plan_id", ""),
                "state_version": event.get("state_version", 0),
                "run_state": dict(run_state),
            }
    if name == "organism.task.status_changed":
        return {
            "kind": "task_status",
            "run_id": event.get("run_id", ""),
            "plan_id": event.get("plan_id", ""),
            "task_id": event.get("task_id", ""),
            "state_version": event.get("state_version", 0),
            "status": payload.get("status", ""),
            "previous_status": payload.get("previous_status", ""),
        }
    if name == "organism.status.runtime_heartbeat":
        return {
            "kind": "runtime_heartbeat",
            "run_id": event.get("run_id", ""),
            "plan_id": event.get("plan_id", ""),
            "state_version": event.get("state_version", 0),
            "pending_task_ids": list(payload.get("pending_task_ids") or []),
            "running_task_ids": list(payload.get("running_task_ids") or []),
            "capacity_available": payload.get("capacity_available", 0),
        }
    if name == "organism.status.semantic":
        return {
            "kind": "semantic_status",
            "run_id": event.get("run_id", ""),
            "plan_id": event.get("plan_id", ""),
            "task_id": event.get("task_id", ""),
            "state_version": event.get("state_version", 0),
            "current_focus": payload.get("current_focus") or payload.get("focus") or "",
            "blocker": payload.get("blocker", ""),
            "risk_flags": list(payload.get("risk_flags") or []),
            "artifact_refs": list(payload.get("artifact_refs") or []),
        }
    return None


__all__ = ["universal_progress_delta"]
