from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SurfaceMessage(BaseModel):
    surface: str
    external_id: str
    text: str
    attachments: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=_utc_now)
    intent: str | None = None


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    label: str
    status: Literal["active", "paused", "completed"] = "active"
    turns: list[TaskTurn] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class PendingAction(BaseModel):
    kind: Literal["confirm", "clarify"]
    intent: str
    original_text: str
    options: list[str] = Field(default_factory=list)
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
    tasks: list[Task] = Field(default_factory=list)
    current_task_id: str | None = None
    pending_action: PendingAction | None = None
