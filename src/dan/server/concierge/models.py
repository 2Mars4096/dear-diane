from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SurfaceMessage(BaseModel):
    surface: str
    external_id: str
    text: str
    surface_type: str = ""
    surface_id: str = ""
    session_id: str = ""
    attachments: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=_utc_now)
    intent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    label: str
    status: Literal["active", "paused", "blocked", "completed"] = "active"
    turns: list[TaskTurn] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    completed_steps: list[str] = Field(default_factory=list)
    pending_steps: list[str] = Field(default_factory=list)
    current_blocker: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    last_activity: datetime | None = None
    goal_id: str | None = None
    progress_updated_at: float | None = None


class PendingAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"pending_{uuid.uuid4().hex[:12]}")
    kind: Literal["confirm", "clarify"]
    intent: str
    original_text: str
    options: list[str] = Field(default_factory=list)
    task_id: str | None = None
    attempt_session_id: str | None = None
    replay_context: list[TaskTurn] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class Project(BaseModel):
    project_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    surface_id: str
    label: str
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    status: Literal["active", "paused", "completed"] = "active"
    linked_workflow_ids: list[str] = Field(default_factory=list)
    linked_run_ids: list[str] = Field(default_factory=list)
    linked_meta_session_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    domain: str | None = None
    autonomy_preference: str = "auto"
    tasks: list[Task] = Field(default_factory=list)
    current_task_id: str | None = None
    pending_action: PendingAction | None = None


# ---------------------------------------------------------------------------
# Concierge orchestrator state (plan 29-2)
# ---------------------------------------------------------------------------


class GoalProgressEntry(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    source: Literal["goal_command", "user_turn", "assistant_turn", "task_finalize", "system"] = "system"
    task_id: str | None = None
    task_label: str = ""
    note: str = ""
    completed_steps: list[str] = Field(default_factory=list)
    pending_steps: list[str] = Field(default_factory=list)
    current_blocker: str | None = None
    clear_blocker: bool = False
    artifacts: dict[str, str] = Field(default_factory=dict)


class GoalProgressSnapshot(BaseModel):
    project_id: str | None = None
    task_id: str | None = None
    task_label: str = ""
    completed_steps: list[str] = Field(default_factory=list)
    pending_steps: list[str] = Field(default_factory=list)
    current_blocker: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    last_note: str = ""
    updated_at: float = Field(default_factory=time.time)


class ConciergeGoal(BaseModel):
    """Single goal tracked by the concierge orchestrator."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    description: str = ""
    status: Literal["active", "paused", "completed", "failed"] = "active"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    plan_steps: list[dict[str, Any]] = Field(default_factory=list)
    current_step_index: int = 0
    context: dict[str, Any] = Field(default_factory=dict)
    workflow_id: str | None = None
    run_history: list[str] = Field(default_factory=list)
    error_history: list[str] = Field(default_factory=list)
    user_interventions: list[dict[str, Any]] = Field(default_factory=list)
    plan_result: dict[str, Any] | None = None
    iteration: int = 0
    max_iterations: int = 5
    paused_at_stage: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    progress: GoalProgressSnapshot = Field(default_factory=GoalProgressSnapshot)
    progress_log: list[GoalProgressEntry] = Field(default_factory=list)


class ConciergeState(BaseModel):
    """Working state for the concierge (persisted as WORKING_STATE in memory kernel)."""

    active_goals: list[ConciergeGoal] = Field(default_factory=list)
    pending_clarifications: list[dict[str, Any]] = Field(default_factory=list)
    autonomy_preference: str | None = None
    last_autonomy_level: str | None = None
    last_interaction_at: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Routing and context types for the rebuilt concierge runtime.
# ---------------------------------------------------------------------------


class IntentCategory(str, Enum):
    ASK = "ask"
    AGENT = "agent"
    PLAN = "plan"


class RouteMode(str, Enum):
    ASK = "ask"
    AGENT = "agent"
    PLAN = "plan"


class RouteDecision(BaseModel):
    mode: RouteMode
    target: str = "general"
    action_hints: list[str] = []
    rationale: str = ""


class ResolvedContext(BaseModel):
    project: Project
    task: Task
    is_new_project: bool
    is_new_task: bool
    confidence: float
    domain: str | None = None
    concierge_task_id: str | None = None
    dispatch_mode: str | None = None
    follow_up_type: str | None = None
    follow_up_prompt: str | None = None
    follow_up_candidates: list[str] = Field(default_factory=list)
