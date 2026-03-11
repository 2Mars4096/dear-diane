from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel

from .models import Project, SurfaceMessage, Task
from .project_store import ProjectStore

_CONTINUATION_WORDS = frozenset(
    {
        "yes",
        "do it",
        "just do it",
        "go ahead",
        "ok",
        "okay",
        "sure",
        "send it",
        "yes please",
        "please",
        "summarize it",
        "review it",
        "read it",
        "summarize this",
        "review this",
    }
)

_TASK_FILLER_WORDS = {
    "an",
    "and",
    "for",
    "in",
    "inside",
    "me",
    "my",
    "new",
    "now",
    "of",
    "on",
    "please",
    "project",
    "task",
    "this",
    "to",
    "workflow",
}


class ResolvedContext(BaseModel):
    project: Project
    task: Task
    is_new_project: bool
    is_new_task: bool
    confidence: float
    domain: str | None = None


def _populate_domain(result: ResolvedContext, msg: SurfaceMessage) -> None:
    """Populate domain from project or detect_domain (31-21)."""
    if result.project.domain:
        result.domain = result.project.domain
    else:
        try:
            from .domain_learning import detect_domain
            detected = detect_domain(msg.text, result.project)
            if detected:
                result.domain = detected
        except Exception:
            pass


def _persist_project_domain(
    result: ResolvedContext,
    project_store: ProjectStore,
) -> None:
    """Persist newly detected project domain for later follow-up turns."""
    if not result.domain or result.project.domain:
        return
    try:
        result.project.domain = result.domain
        project_store.save_project(result.project)
    except Exception:
        pass


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _normalize(text: str) -> str:
    return " ".join(_tokenize(text))


def _is_continuation(text: str) -> bool:
    clean = text.lower().strip().rstrip(".!?,")
    return clean.isdigit() or clean in _CONTINUATION_WORDS


def _slugify_tokens(tokens: list[str], fallback: str = "project") -> str:
    return "-".join(tokens[:6]) or fallback


class ProjectContextResolver:
    def __init__(
        self,
        project_store: ProjectStore,
        activity_tracker: Any = None,
        conversation_memory: Any = None,
    ) -> None:
        self.project_store = project_store
        self.activity_tracker = activity_tracker
        self.conversation_memory = conversation_memory

    def resolve(self, msg: SurfaceMessage) -> ResolvedContext:
        trigger_context = msg.metadata.get("trigger_context")
        if isinstance(trigger_context, dict):
            project_id = str(trigger_context.get("project_id") or "").strip()
            task_id = str(trigger_context.get("task_id") or "").strip()
            if project_id:
                project = self.project_store.get_project(project_id, msg.external_id)
                if project is None:
                    project = self.project_store.get_project_any_surface(project_id)
                task = (
                    self.project_store.get_current_task(project_id, msg.external_id)
                    if not task_id
                    else None
                )
                if task is None and not task_id:
                    task = self.project_store.get_current_task_any_surface(project_id)
                if project is not None and task_id:
                    for candidate in project.tasks:
                        if candidate.task_id == task_id:
                            task = candidate
                            break
                if project is not None and task is not None:
                    result = ResolvedContext(
                        project=project,
                        task=task,
                        is_new_project=False,
                        is_new_task=False,
                        confidence=1.0,
                    )
                    _populate_domain(result, msg)
                    return result

        active_projects = self.project_store.list_active(msg.external_id)
        text = msg.text.strip()

        explicit = self._extract_explicit_project(text)
        if explicit:
            matched = self._match_by_label(explicit, active_projects)
            if matched is not None:
                task, is_new_task = self._resolve_task(matched, text, msg.external_id)
                result = ResolvedContext(
                    project=matched,
                    task=task,
                    is_new_project=False,
                    is_new_task=is_new_task,
                    confidence=1.0,
                )
                _populate_domain(result, msg)
                _persist_project_domain(result, self.project_store)
                return result

        if _is_continuation(text) and active_projects:
            project = active_projects[0]
            task = self.project_store.get_current_task(project.project_id, msg.external_id)
            if task is None:
                task = self.project_store.add_task(project.project_id, self._generate_label(text), msg.external_id)
                project = self.project_store.get_project(project.project_id, msg.external_id) or project
            result = ResolvedContext(
                project=project,
                task=task,
                is_new_project=False,
                is_new_task=False,
                confidence=0.95,
            )
            _populate_domain(result, msg)
            _persist_project_domain(result, self.project_store)
            return result

        workflow_match = self._match_by_link(text, active_projects)
        if workflow_match is not None:
            task = self.project_store.get_current_task(workflow_match.project_id, msg.external_id)
            if task is None:
                task = self.project_store.add_task(
                    workflow_match.project_id,
                    self._generate_label(text, workflow_match.label),
                    msg.external_id,
                )
            result = ResolvedContext(
                project=workflow_match,
                task=task,
                is_new_project=False,
                is_new_task=False,
                confidence=0.9,
            )
            _populate_domain(result, msg)
            _persist_project_domain(result, self.project_store)
            return result

        best_project, best_score = self._best_project_match(text, active_projects)
        if best_project is None or best_score < 0.45:
            project_label = self._generate_label(text)
            project = self.project_store.create_project(project_label, msg.external_id)
            task = self.project_store.add_task(project.project_id, project_label, msg.external_id)
            project = self.project_store.get_project(project.project_id, msg.external_id) or project
            result = ResolvedContext(
                project=project,
                task=task,
                is_new_project=True,
                is_new_task=False,
                confidence=0.0,
            )
            _populate_domain(result, msg)
            _persist_project_domain(result, self.project_store)
            return result

        task, is_new_task = self._resolve_task(best_project, text, msg.external_id)
        result = ResolvedContext(
            project=best_project,
            task=task,
            is_new_project=False,
            is_new_task=is_new_task,
            confidence=best_score,
        )
        _populate_domain(result, msg)
        _persist_project_domain(result, self.project_store)
        return result

    def _extract_explicit_project(self, text: str) -> str | None:
        lower = text.lower().strip()
        match = re.search(r"/project\s+([a-z0-9._-]+)", lower)
        return match.group(1) if match else None

    def _match_by_label(self, label: str, projects: list[Project]) -> Project | None:
        for project in projects:
            if _normalize(project.label) == _normalize(label):
                return project
        for project in projects:
            if _normalize(label) in _normalize(project.label):
                return project
        return None

    def _match_by_link(self, text: str, projects: list[Project]) -> Project | None:
        lower = text.lower()
        for project in projects:
            if any(workflow_id.lower() in lower for workflow_id in project.linked_workflow_ids):
                return project
            if any(run_id.lower() in lower for run_id in project.linked_run_ids):
                return project
        return None

    def _best_project_match(self, text: str, projects: list[Project]) -> tuple[Project | None, float]:
        best_project = None
        best_score = -1.0
        for project in projects:
            score = self._score_project_match(text, project)
            if score > best_score:
                best_project = project
                best_score = score
        return best_project, best_score

    def _score_project_match(self, text: str, project: Project) -> float:
        query_norm = _normalize(text)
        haystack = _normalize(
            " ".join(
                [
                    project.label,
                    project.summary,
                    " ".join(task.label for task in project.tasks),
                    " ".join(project.linked_workflow_ids),
                    " ".join(project.linked_run_ids),
                ]
            )
        )
        if not query_norm or not haystack:
            return 0.0
        query_tokens = query_norm.split()
        coverage = sum(1 for token in query_tokens if token in haystack)
        ratio = SequenceMatcher(None, query_norm, haystack).ratio()
        return (coverage / max(len(query_tokens), 1)) + ratio

    def _resolve_task(self, project: Project, text: str, surface_id: str) -> tuple[Task, bool]:
        current = self.project_store.get_current_task(project.project_id, surface_id)
        if current is None:
            task = self.project_store.add_task(project.project_id, self._generate_label(text, project.label), surface_id)
            project = self.project_store.get_project(project.project_id, surface_id) or project
            return task, False

        if _is_continuation(text):
            return current, False

        best_task = current
        best_score = self._score_task_match(text, current)
        for task in project.tasks:
            score = self._score_task_match(text, task)
            if score > best_score:
                best_score = score
                best_task = task

        if best_score >= 0.55:
            return best_task, False

        task_label = self._generate_label(text, project.label)
        new_task = self.project_store.add_task(project.project_id, task_label, surface_id)
        return new_task, True

    def _score_task_match(self, text: str, task: Task) -> float:
        query_norm = _normalize(text)
        haystack = _normalize(" ".join([task.label] + [turn.content for turn in task.turns[-5:]]))
        if not query_norm or not haystack:
            return 0.0
        query_tokens = query_norm.split()
        coverage = sum(1 for token in query_tokens if token in haystack)
        ratio = SequenceMatcher(None, query_norm, haystack).ratio()
        return (coverage / max(len(query_tokens), 1)) + ratio

    def _generate_label(self, text: str, project_label: str | None = None) -> str:
        tokens = _tokenize(text)
        exclude = set(_tokenize(project_label or ""))
        filtered = [token for token in tokens if token not in exclude and token not in _TASK_FILLER_WORDS]
        return _slugify_tokens(filtered or tokens)
