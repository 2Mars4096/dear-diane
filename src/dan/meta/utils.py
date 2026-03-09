"""Shared utility functions extracted from MetaController (29-2 §5-1).

These helpers are reusable by the concierge, MetaController, and other
orchestration layers without going through the full MetaController class.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plan deserialization
# ---------------------------------------------------------------------------

def plan_from_dict(plan_dict: dict[str, Any] | None) -> Any | None:
    """Deserialize a plan dict into the appropriate Plan model.

    Handles REUSE, ADAPT, and GENERATE plan types. Returns None if
    the dict is invalid or unparseable.
    """
    if not isinstance(plan_dict, dict):
        return None
    try:
        from dan.meta.planner import AdaptPlan, GeneratePlan, ReusePlan

        action = str(plan_dict.get("action", "")).upper()
        if action == "REUSE":
            return ReusePlan.model_validate(plan_dict)
        if action == "ADAPT":
            return AdaptPlan.model_validate(plan_dict)
        if action == "GENERATE":
            return GeneratePlan.model_validate(plan_dict)
    except Exception:
        logger.debug("Failed to parse plan from dict", exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------

def topo_sort_workflows(workflows: list[Any]) -> list[Any]:
    """Topological sort of workflow specs by depends_on field.

    Specs without dependencies come first. Cycles are handled gracefully
    by appending remaining items at the end.
    """
    name_to_spec = {w.name: w for w in workflows}
    in_degree = {w.name: 0 for w in workflows}
    for w in workflows:
        for dep in w.depends_on:
            if dep in in_degree:
                in_degree[w.name] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    result: list[Any] = []
    while queue:
        name = queue.pop(0)
        result.append(name_to_spec[name])
        for w in workflows:
            if name in w.depends_on:
                in_degree[w.name] -= 1
                if in_degree[w.name] == 0:
                    queue.append(w.name)

    for w in workflows:
        if w.name not in {r.name for r in result}:
            result.append(w)

    return result


# ---------------------------------------------------------------------------
# Session creation helpers
# ---------------------------------------------------------------------------

def create_meta_session(
    goal: str,
    max_iterations: int = 5,
    goal_context: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> Any:
    """Create a MetaSession with sensible defaults.

    Avoids importing MetaSession at module level so this module stays
    lightweight.
    """
    from dan.meta.controller import MetaSession, MetaSessionStatus

    return MetaSession(
        session_id=session_id or str(uuid.uuid4()),
        goal=goal,
        status=MetaSessionStatus.PLANNING,
        max_iterations=max_iterations,
        goal_context=dict(goal_context or {}),
    )


def goal_to_session_fields(goal: Any) -> dict[str, Any]:
    """Extract MetaSession-compatible fields from a ConciergeGoal.

    Returns a dict suitable for MetaSession construction or update.
    """
    return {
        "session_id": goal.id,
        "goal": goal.description,
        "max_iterations": getattr(goal, "max_iterations", 5),
        "plan_result": getattr(goal, "plan_result", None),
        "workflow_id": getattr(goal, "workflow_id", None),
        "run_history": list(getattr(goal, "run_history", [])),
        "iteration": getattr(goal, "iteration", 0),
        "goal_context": dict(getattr(goal, "context", {}) or {}),
    }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_session_resumable(session: Any) -> tuple[bool, str]:
    """Check whether a MetaSession can be resumed.

    Returns (is_valid, reason). If is_valid is False, reason explains why.
    """
    from dan.meta.controller import MetaSessionStatus

    if session is None:
        return False, "Session not found"
    if session.status != MetaSessionStatus.PAUSED:
        return False, f"Session is not paused (status={session.status})"
    return True, ""


def session_is_terminal(session: Any) -> bool:
    """Return True if the session is in a terminal state (completed/failed)."""
    from dan.meta.controller import MetaSessionStatus

    return session.status in (MetaSessionStatus.COMPLETED, MetaSessionStatus.FAILED)
