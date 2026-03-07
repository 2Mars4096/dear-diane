from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import PendingAction, Project, Task, TaskTurn

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned or "default"


def _normalize_search_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


class ProjectStore:
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or (Path.home() / ".dan" / "projects"))
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _projects_dir(self, surface_id: str) -> Path:
        d = self.base_dir / _safe_segment(surface_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _project_path(self, project_id: str, surface_id: str) -> Path:
        return self._projects_dir(surface_id) / f"{_safe_segment(project_id)}.json"

    def save_project(self, project: Project) -> None:
        path = self._project_path(project.project_id, project.surface_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def create_project(self, label: str, surface_id: str) -> Project:
        project = Project(label=label, surface_id=surface_id)
        self.save_project(project)
        return project

    def get_project(self, project_id: str, surface_id: str) -> Project | None:
        path = self._project_path(project_id, surface_id)
        if not path.exists():
            return None
        try:
            return Project.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load project %s", project_id, exc_info=True)
            return None

    def list_projects(self, surface_id: str) -> list[Project]:
        projects: list[Project] = []
        for path in sorted(self._projects_dir(surface_id).glob("*.json")):
            try:
                projects.append(Project.model_validate_json(path.read_text(encoding="utf-8")))
            except Exception:
                logger.warning("Failed to load project from %s", path, exc_info=True)
        projects.sort(key=lambda p: p.updated_at, reverse=True)
        return projects

    def list_active(self, surface_id: str) -> list[Project]:
        return [project for project in self.list_projects(surface_id) if project.status == "active"]

    def add_task(self, project_id: str, task_label: str, surface_id: str) -> Task:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        for existing in project.tasks:
            if existing.status == "active":
                existing.status = "paused"
                existing.updated_at = _utc_now()
        task = Task(label=task_label)
        project.tasks.append(task)
        project.current_task_id = task.task_id
        project.updated_at = _utc_now()
        self.save_project(project)
        return task

    def get_current_task(self, project_id: str, surface_id: str) -> Task | None:
        project = self.get_project(project_id, surface_id)
        if project is None or not project.tasks:
            return None
        if project.current_task_id:
            for task in project.tasks:
                if task.task_id == project.current_task_id:
                    return task
        return max(project.tasks, key=lambda task: task.updated_at)

    def append_turn(self, project_id: str, task_id: str, turn: TaskTurn, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        for task in project.tasks:
            if task.task_id == task_id:
                task.turns.append(turn)
                task.updated_at = _utc_now()
                if task.status != "completed":
                    task.status = "active"
                project.current_task_id = task.task_id
                project.updated_at = _utc_now()
                self.save_project(project)
                return
        raise KeyError(f"Task '{task_id}' not found")

    def update_project_status(self, project_id: str, status: str, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        project.status = status  # type: ignore[assignment]
        project.updated_at = _utc_now()
        self.save_project(project)

    def update_task_status(self, project_id: str, task_id: str, status: str, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        for task in project.tasks:
            if task.task_id == task_id:
                task.status = status  # type: ignore[assignment]
                task.updated_at = _utc_now()
                project.updated_at = _utc_now()
                self.save_project(project)
                return
        raise KeyError(f"Task '{task_id}' not found")

    def update_project_summary(self, project_id: str, summary: str, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        project.summary = summary
        project.updated_at = _utc_now()
        self.save_project(project)

    def get_pending_project(self, surface_id: str) -> Project | None:
        pending = self.list_pending_projects(surface_id)
        return pending[0] if pending else None

    def list_pending_projects(self, surface_id: str) -> list[Project]:
        return [project for project in self.list_projects(surface_id) if project.pending_action is not None]

    def set_pending_action(self, project_id: str, pending_action: PendingAction, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        project.pending_action = pending_action
        project.updated_at = _utc_now()
        self.save_project(project)

    def clear_pending_action(self, project_id: str, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        project.pending_action = None
        project.updated_at = _utc_now()
        self.save_project(project)

    def link_workflow(self, project_id: str, workflow_id: str, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        if workflow_id not in project.linked_workflow_ids:
            project.linked_workflow_ids.append(workflow_id)
            project.updated_at = _utc_now()
            self.save_project(project)

    def link_run(self, project_id: str, run_id: str, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        if run_id not in project.linked_run_ids:
            project.linked_run_ids.append(run_id)
            project.updated_at = _utc_now()
            self.save_project(project)

    def link_meta_session(self, project_id: str, session_id: str, surface_id: str) -> None:
        project = self.get_project(project_id, surface_id)
        if project is None:
            raise KeyError(f"Project '{project_id}' not found")
        if session_id not in project.linked_meta_session_ids:
            project.linked_meta_session_ids.append(session_id)
            project.updated_at = _utc_now()
            self.save_project(project)

    def search_projects(self, query: str, surface_id: str, limit: int = 10) -> list[Project]:
        query_norm = _normalize_search_text(query)
        if not query_norm:
            return []
        query_tokens = query_norm.split()
        scored: list[tuple[int, Project]] = []
        for project in self.list_projects(surface_id):
            text = " ".join(
                part
                for part in [
                    project.label,
                    project.summary,
                    " ".join(task.label for task in project.tasks),
                ]
                if part
            )
            haystack = _normalize_search_text(text)
            score = sum(1 for token in query_tokens if token in haystack)
            if score > 0:
                scored.append((score, project))
        scored.sort(key=lambda item: (-item[0], item[1].updated_at), reverse=False)
        return [project for _score, project in scored[:limit]]
