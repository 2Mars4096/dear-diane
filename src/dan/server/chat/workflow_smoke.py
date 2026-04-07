"""Bounded workflow smoke helpers used by chat-side generation paths."""

from __future__ import annotations

import asyncio
from typing import Any

from dan.models.graph import Graph


async def run_candidate_execution_smoke(
    *,
    capability_context: Any,
    graph_dict: dict[str, Any],
    workflow_id: str,
    inputs: dict[str, Any],
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Run a bounded one-shot smoke on a structured candidate when runtime support exists."""

    run_manager = getattr(capability_context, "run_manager", None)
    if run_manager is None:
        return {
            "success": False,
            "skipped": True,
            "reason": "RunManager is unavailable for structured execution smoke.",
        }

    try:
        graph = Graph.model_validate(graph_dict)
    except Exception as exc:
        return {
            "success": False,
            "errors": [f"Structured execution smoke could not load the candidate graph: {exc}"],
        }

    structured_generation = dict(
        ((graph_dict.get("metadata") or {}).get("structured_generation") or {})
    )
    candidate_workspace_id = str(
        structured_generation.get("candidate_workspace_id") or ""
    ).strip()
    smoke_graph_id = (
        f"{workflow_id}::candidate-smoke::{candidate_workspace_id}"
        if candidate_workspace_id
        else f"{workflow_id}::candidate-smoke"
    )
    record = await run_manager.start_run(
        graph,
        graph_id=smoke_graph_id,
        inputs=dict(inputs or {}),
        goal_context={
            "source": "structured_execution_smoke",
            "workflow_id": workflow_id,
            "candidate_workspace_id": candidate_workspace_id or None,
        },
        run_policy={
            "profile": "structured_smoke",
            "max_duration": max(float(timeout_seconds), 1.0),
        },
    )
    task = getattr(run_manager, "_tasks", {}).get(record.run_id)
    if task is None:
        return {
            "success": False,
            "run_id": record.run_id,
            "errors": ["Structured execution smoke started without a tracked task."],
        }

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max(float(timeout_seconds), 1.0))
    except asyncio.TimeoutError:
        task.cancel()
        return {
            "success": False,
            "run_id": record.run_id,
            "errors": [
                f"Structured execution smoke timed out after {timeout_seconds:.0f}s."
            ],
        }

    status = getattr(record.status, "value", str(record.status))
    result = getattr(record, "result", None)
    if status != "completed" or result is None or not getattr(result, "success", False):
        snapshot = record.snapshot()
        errors = [
            str(item).strip()
            for item in (
                list((snapshot.get("errors") or {}).values())
                if isinstance(snapshot.get("errors"), dict)
                else [snapshot.get("error") or ""]
            )
            if str(item).strip()
        ] or [
            f"Structured execution smoke ended with status {status}."
        ]
        return {
            "success": False,
            "run_id": record.run_id,
            "status": status,
            "errors": errors,
        }

    return {
        "success": True,
        "run_id": record.run_id,
        "status": status,
        "outputs": dict(getattr(result, "outputs", {}) or {}),
    }
