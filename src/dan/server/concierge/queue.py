from __future__ import annotations

from enum import Enum

from .classifier import ClassificationResult, IntentCategory
from .context_resolver import ResolvedContext
from .models import SurfaceMessage

_OVERFLOW_KEY_PROJECT = "overflow"
_OVERFLOW_KEY_TASK = ""


class QueueDecision(str, Enum):
    IMMEDIATE = "immediate"
    QUEUED = "queued"
    PARALLEL = "parallel"


class ProjectMessageQueue:
    def __init__(self, *, max_active_projects_per_surface: int = 3) -> None:
        self.max_active_projects_per_surface = max_active_projects_per_surface
        self._queued: dict[tuple[str, str, str], list[SurfaceMessage]] = {}
        self._active_projects: dict[str, set[str]] = {}

    def mark_active(self, surface_id: str, project_id: str) -> None:
        self._active_projects.setdefault(surface_id, set()).add(project_id)

    def mark_completed(self, surface_id: str, project_id: str) -> None:
        active = self._active_projects.get(surface_id)
        if active is not None:
            active.discard(project_id)

    def enqueue(
        self,
        msg: SurfaceMessage,
        context: ResolvedContext,
        classification: ClassificationResult,
    ) -> QueueDecision:
        if classification.intent == IntentCategory.STATUS_CHECK:
            return QueueDecision.IMMEDIATE
        if context.is_new_project:
            active = self._active_projects.get(msg.external_id, set())
            if len(active) >= self.max_active_projects_per_surface:
                self._queued.setdefault(
                    (msg.external_id, _OVERFLOW_KEY_PROJECT, _OVERFLOW_KEY_TASK),
                    [],
                ).append(msg)
                return QueueDecision.QUEUED
            return QueueDecision.PARALLEL
        if context.task.status == "active":
            self._queued.setdefault(
                (msg.external_id, context.project.project_id, context.task.task_id),
                [],
            ).append(msg)
            return QueueDecision.QUEUED
        return QueueDecision.IMMEDIATE

    def drain(self, project_id: str, task_id: str, surface_id: str) -> list[SurfaceMessage]:
        return self._queued.pop((surface_id, project_id, task_id), [])

    def drain_overflow(self, surface_id: str) -> list[SurfaceMessage]:
        return self._queued.pop(
            (surface_id, _OVERFLOW_KEY_PROJECT, _OVERFLOW_KEY_TASK), []
        )

    def peek(self, surface_id: str) -> list[SurfaceMessage]:
        items: list[SurfaceMessage] = []
        for (sid, _project_id, _task_id), queued in self._queued.items():
            if sid == surface_id:
                items.extend(queued)
        return items
