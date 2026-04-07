from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..models import Project, ResolvedContext, SurfaceMessage, Task
from ..triage import TriageResult

if TYPE_CHECKING:
    from . import Concierge


_CONTEXT_CONTINUATION_RE = re.compile(
    r"\b(?:continue|resume|also|then|next|retry|redo|again|fix|update|edit|patch|apply|run|test|"
    r"check|review|open|show|summarize|that|this|it|them|those|these)\b",
    re.IGNORECASE,
)
_CONTEXT_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CONTEXT_LABEL_STOPWORDS = frozenset(
    {
        "task",
        "project",
        "workflow",
        "report",
        "draft",
        "file",
        "files",
        "readme",
        "notes",
        "plan",
        "chat",
        "current",
        "conversation",
    }
)
_EPHEMERAL_PROJECT_PREFIX = "_ephemeral:"


def _resolve_context(self: Concierge, msg: SurfaceMessage) -> ResolvedContext:
    direct = self._resolve_direct_context_reference(msg)
    if direct is not None:
        return direct

    pending = self._peek_pending_context(msg)
    if pending is not None:
        return pending

    active = self._peek_active_context(msg)
    if active is not None:
        return active

    return self._ephemeral_context(msg)


def _resolve_direct_context_reference(
    self: Concierge,
    msg: SurfaceMessage,
) -> ResolvedContext | None:
    trigger_context = msg.metadata.get("trigger_context")
    if isinstance(trigger_context, dict):
        project_id = str(trigger_context.get("project_id") or "").strip()
        task_id = str(trigger_context.get("task_id") or "").strip()
        if project_id:
            resolved = self._context_from_project_ids(
                project_id,
                task_id=task_id,
                surface_id=msg.external_id,
            )
            if resolved is not None:
                return resolved

    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    resolved_project_id = str(metadata.get("resolved_project_id") or "").strip()
    resolved_task_id = str(metadata.get("resolved_task_id") or "").strip()
    resolved_concierge_task_id = str(metadata.get("concierge_task_id") or "").strip()
    if resolved_project_id:
        resolved = self._context_from_project_ids(
            resolved_project_id,
            task_id=resolved_task_id,
            surface_id=msg.external_id,
        )
        if resolved is not None and resolved_concierge_task_id:
            resolved.concierge_task_id = resolved_concierge_task_id
        return resolved
    return None


def _context_from_project_ids(
    self: Concierge,
    project_id: str,
    *,
    task_id: str = "",
    surface_id: str,
) -> ResolvedContext | None:
    if not project_id:
        return None
    project = self.project_store.get_project(project_id, surface_id)
    if project is None:
        project = self.project_store.get_project_any_surface(project_id)
    if project is None:
        return None

    task = None
    if task_id:
        for candidate in project.tasks:
            if candidate.task_id == task_id:
                task = candidate
                break
    if task is None:
        task = self.project_store.get_current_task(project.project_id, surface_id)
        if task is None:
            task = self.project_store.get_current_task_any_surface(project.project_id)
    if task is None and project.tasks:
        task = project.tasks[-1]
    if task is None:
        return None

    return ResolvedContext(
        project=project,
        task=task,
        is_new_project=False,
        is_new_task=False,
        confidence=1.0,
        domain=project.domain,
    )


def _peek_pending_context(self: Concierge, msg: SurfaceMessage) -> ResolvedContext | None:
    pending_projects = self.project_store.list_pending_projects(msg.external_id)
    if not pending_projects:
        return None
    if len(pending_projects) == 1:
        project = pending_projects[0]
    else:
        lower = str(getattr(msg, "text", "") or "").lower()
        project = next(
            (
                candidate
                for candidate in pending_projects
                if candidate.label and candidate.label.lower() in lower
            ),
            None,
        )
        if project is None:
            return None
    return self._existing_project_context(
        project,
        surface_id=msg.external_id,
        confidence=1.0,
    )


def _peek_active_context(self: Concierge, msg: SurfaceMessage) -> ResolvedContext | None:
    active_projects = self.project_store.list_active(msg.external_id)
    if not active_projects:
        return None

    lower = str(getattr(msg, "text", "") or "").strip().lower()
    if not lower:
        return None

    label_matches: list[tuple[Project, Task]] = []
    continuation_candidates: list[tuple[Project, Task]] = []
    for project in active_projects:
        task = self._current_task_or_placeholder(project, msg.external_id, msg.text)
        if self._message_mentions_context_label(lower, project, task):
            label_matches.append((project, task))
        elif self._message_has_continuation_cue(lower):
            continuation_candidates.append((project, task))

    if label_matches:
        project, task = label_matches[0]
        return ResolvedContext(
            project=project,
            task=task,
            is_new_project=False,
            is_new_task=False,
            confidence=0.95,
            domain=project.domain,
        )

    if len(active_projects) == 1 and len(continuation_candidates) == 1:
        project, task = continuation_candidates[0]
        return ResolvedContext(
            project=project,
            task=task,
            is_new_project=False,
            is_new_task=False,
            confidence=0.85,
            domain=project.domain,
        )

    return None


def _existing_project_context(
    self: Concierge,
    project: Project,
    *,
    surface_id: str,
    confidence: float,
) -> ResolvedContext:
    return ResolvedContext(
        project=project,
        task=self._current_task_or_placeholder(project, surface_id, project.label),
        is_new_project=False,
        is_new_task=False,
        confidence=confidence,
        domain=project.domain,
    )


def _current_task_or_placeholder(
    self: Concierge,
    project: Project,
    surface_id: str,
    fallback_text: str,
) -> Task:
    task = self.project_store.get_current_task(project.project_id, surface_id)
    if task is None:
        task = self.project_store.get_current_task_any_surface(project.project_id)
    if task is None and project.tasks:
        task = project.tasks[-1]
    if task is not None:
        return task
    label = self._context_label(fallback_text, default="task")
    return Task(task_id=f"{_EPHEMERAL_PROJECT_PREFIX}task:{project.project_id}", label=label)


def _ephemeral_context(self: Concierge, msg: SurfaceMessage) -> ResolvedContext:
    project = Project(
        project_id=f"{_EPHEMERAL_PROJECT_PREFIX}{msg.surface}:{msg.external_id}",
        surface_id=msg.external_id,
        label="Current conversation",
    )
    task = Task(
        task_id=f"{_EPHEMERAL_PROJECT_PREFIX}task:{msg.surface}:{msg.external_id}",
        label=self._context_label(msg.text, default="chat"),
    )
    return ResolvedContext(
        project=project,
        task=task,
        is_new_project=True,
        is_new_task=True,
        confidence=0.0,
    )


def _message_has_continuation_cue(self: Concierge, lower_text: str) -> bool:
    return bool(_CONTEXT_CONTINUATION_RE.search(lower_text))


def _message_mentions_context_label(
    self: Concierge,
    lower_text: str,
    project: Project,
    task: Task | None,
) -> bool:
    message_tokens = self._context_label_tokens(lower_text)
    for label in (
        getattr(project, "label", ""),
        getattr(task, "label", "") if task is not None else "",
    ):
        label_text = str(label or "").strip().lower()
        if not label_text:
            continue
        if label_text in lower_text:
            return True
        label_tokens = self._context_label_tokens(label_text)
        if label_tokens and message_tokens.intersection(label_tokens):
            return True
    return False


def _context_label_tokens(self: Concierge, text: str) -> set[str]:
    return {
        token
        for token in _CONTEXT_TOKEN_RE.findall(str(text or "").lower())
        if len(token) >= 4 and token not in _CONTEXT_LABEL_STOPWORDS
    }


def _context_label(self: Concierge, text: str, *, default: str) -> str:
    return str(text or "").strip()[:60].replace("\n", " ") or default


def _is_ephemeral_context(
    self: Concierge,
    context: ResolvedContext | None,
) -> bool:
    if context is None:
        return True
    project_id = str(getattr(getattr(context, "project", None), "project_id", "") or "")
    return project_id.startswith(_EPHEMERAL_PROJECT_PREFIX)


def _should_persist_context(
    self: Concierge,
    context: ResolvedContext | None,
) -> bool:
    return not self._is_ephemeral_context(context)


def _should_materialize_project_turn(
    self: Concierge,
    msg: SurfaceMessage,
    triage: TriageResult | None,
) -> bool:
    metadata = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
    if metadata.get("resolved_project_id") or metadata.get("trigger_context"):
        return True
    if triage is None:
        return False
    if bool(getattr(triage, "is_social", False)):
        return False
    if bool(getattr(triage, "is_resume", False)):
        return True
    intent = str(getattr(triage, "intent", "") or "").strip().lower()
    if intent in {"agent", "plan"}:
        return True
    route = getattr(triage, "route", None)
    target = str(getattr(route, "target", "") or "").strip().lower()
    if target in {"file", "workflow", "run", "memory"}:
        return True
    hints = {
        str(item or "").strip()
        for item in list(getattr(route, "action_hints", None) or [])
        if str(item or "").strip()
    }
    return bool(
        hints.intersection(
            {"read_file", "write_file", "workflow_edit", "workflow_run", "run_control"}
        )
    )


def _materialize_context(
    self: Concierge,
    msg: SurfaceMessage,
    *,
    triage: TriageResult | None = None,
    base_context: ResolvedContext | None = None,
) -> ResolvedContext:
    context = base_context or self._resolve_context(msg)
    if self._should_persist_context(context):
        materialized = self._context_from_project_ids(
            context.project.project_id,
            task_id=context.task.task_id,
            surface_id=msg.external_id,
        )
        if materialized is not None:
            return materialized
        task = self.project_store.add_task(
            context.project.project_id,
            self._context_label(context.task.label or msg.text, default="task"),
            msg.external_id,
        )
        project = self.project_store.get_project(context.project.project_id, msg.external_id) or context.project
        return ResolvedContext(
            project=project,
            task=task,
            is_new_project=False,
            is_new_task=True,
            confidence=max(0.8, float(getattr(context, "confidence", 0.0) or 0.0)),
            domain=project.domain,
        )

    if not self._should_materialize_project_turn(msg, triage):
        return context

    label = self._context_label(msg.text, default="project")
    project = self.project_store.create_project(label, msg.external_id)
    task = self.project_store.add_task(project.project_id, label, msg.external_id)
    project = self.project_store.get_project(project.project_id, msg.external_id) or project
    return ResolvedContext(
        project=project,
        task=task,
        is_new_project=True,
        is_new_task=True,
        confidence=1.0,
        domain=project.domain,
    )
