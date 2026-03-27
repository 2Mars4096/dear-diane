from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        default_base_dir = os.environ.get("DAN_PROJECT_STORE_DIR")
        self.base_dir = Path(
            base_dir
            or default_base_dir
            or (Path.home() / ".dan" / "projects")
        )
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._journal_compact_every = max(
            0,
            int(os.environ.get("DAN_PROJECT_STORE_COMPACT_EVERY", "100")),
        )
        self._journal_counts: dict[Path, int] = {}

    def _projects_dir(self, surface_id: str) -> Path:
        d = self.base_dir / _safe_segment(surface_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _project_path(self, project_id: str, surface_id: str) -> Path:
        return self._projects_dir(surface_id) / f"{_safe_segment(project_id)}.json"

    def _journal_path(self, project_id: str, surface_id: str) -> Path:
        return self._projects_dir(surface_id) / f"{_safe_segment(project_id)}.journal.jsonl"

    def _resolve_project_snapshot_path(self, project_id: str, surface_id: str) -> Path | None:
        path = self._project_path(project_id, surface_id)
        if path.exists():
            return path
        safe_id = _safe_segment(project_id)
        for candidate in sorted(self.base_dir.glob(f"*/{safe_id}.json")):
            return candidate
        return None

    def _load_project_snapshot_payload(
        self,
        project_id: str,
        surface_id: str,
    ) -> tuple[Path, dict[str, Any]] | None:
        path = self._resolve_project_snapshot_path(project_id, surface_id)
        if path is None:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load project snapshot from %s", path, exc_info=True)
            return None
        if not isinstance(payload, dict):
            logger.warning("Project snapshot at %s is not a JSON object", path)
            return None
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            logger.warning("Project snapshot at %s has invalid tasks payload", path)
            return None
        return path, payload

    @staticmethod
    def _snapshot_has_task(payload: dict[str, Any], task_id: str) -> bool:
        for task in payload.get("tasks", []):
            if isinstance(task, dict) and str(task.get("task_id") or "").strip() == task_id:
                return True
        return False

    def _resolve_project_fields(
        self,
        project_id: str,
        surface_id: str,
        *field_names: str,
    ) -> tuple[Path, dict[str, Any]] | None:
        snapshot = self._load_project_snapshot_payload(project_id, surface_id)
        if snapshot is None:
            return None
        path, payload = snapshot
        resolved = {field_name: payload.get(field_name) for field_name in field_names}
        journal_path = path.with_suffix(".journal.jsonl")
        if not journal_path.exists():
            return path, resolved
        try:
            with journal_path.open("r", encoding="utf-8") as handle:
                line_count = 0
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    line_count += 1
                    entry = json.loads(raw)
                    if entry.get("op") != "update_project":
                        continue
                    fields = entry.get("fields")
                    if not isinstance(fields, dict):
                        continue
                    for field_name in field_names:
                        if field_name in fields:
                            resolved[field_name] = fields[field_name]
                self._journal_counts[journal_path] = line_count
        except Exception:
            logger.warning("Failed to inspect project journal %s", journal_path, exc_info=True)
        return path, resolved

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        return _utc_now()

    def _write_snapshot(self, project: Project) -> None:
        path = self._project_path(project.project_id, project.surface_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        journal_path = self._journal_path(project.project_id, project.surface_id)
        if journal_path.exists():
            journal_path.unlink()
        self._journal_counts[journal_path] = 0

    def _append_journal_entry(
        self,
        project_id: str,
        surface_id: str,
        entry: dict[str, Any],
    ) -> None:
        journal_path = self._journal_path(project_id, surface_id)
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        current = self._journal_counts.get(journal_path)
        if current is None:
            try:
                with journal_path.open("r", encoding="utf-8") as handle:
                    current = sum(1 for _ in handle)
            except OSError:
                current = 1
        else:
            current += 1
        self._journal_counts[journal_path] = current
        self._maybe_compact_journal(project_id, surface_id, current)

    def _maybe_compact_journal(
        self,
        project_id: str,
        surface_id: str,
        journal_count: int | None = None,
    ) -> None:
        if self._journal_compact_every <= 0:
            return
        if journal_count is None:
            journal_path = self._journal_path(project_id, surface_id)
            journal_count = self._journal_counts.get(journal_path, 0)
        if journal_count < self._journal_compact_every:
            return
        project = self.get_project(project_id, surface_id)
        if project is None:
            return
        self._write_snapshot(project)

    def _apply_journal_entry(self, project: Project, entry: dict[str, Any]) -> None:
        op = str(entry.get("op") or "").strip()
        if not op:
            return
        if op == "append_turn":
            task_id = str(entry.get("task_id") or "").strip()
            turn_payload = entry.get("turn")
            if not task_id or not isinstance(turn_payload, dict):
                return
            for task in project.tasks:
                if task.task_id != task_id:
                    continue
                turn = TaskTurn.model_validate(turn_payload)
                task.turns.append(turn)
                task.updated_at = self._parse_datetime(entry.get("task_updated_at"))
                if task.status != "completed":
                    task.status = "active"
                project.current_task_id = task.task_id
                project.updated_at = self._parse_datetime(entry.get("project_updated_at"))
                return
            return

        if op == "update_project":
            fields = entry.get("fields")
            if not isinstance(fields, dict):
                return
            for key, value in fields.items():
                if key == "pending_action":
                    project.pending_action = (
                        PendingAction.model_validate(value)
                        if isinstance(value, dict)
                        else None
                    )
                    continue
                if key in {"created_at", "updated_at"}:
                    setattr(project, key, self._parse_datetime(value))
                    continue
                setattr(project, key, value)
            return

        if op == "update_task":
            task_id = str(entry.get("task_id") or "").strip()
            fields = entry.get("fields")
            if not task_id or not isinstance(fields, dict):
                return
            for task in project.tasks:
                if task.task_id != task_id:
                    continue
                for key, value in fields.items():
                    if key in {"created_at", "updated_at", "last_activity"} and value is not None:
                        setattr(task, key, self._parse_datetime(value))
                    else:
                        setattr(task, key, value)
                project.updated_at = self._parse_datetime(entry.get("project_updated_at"))
                return

    def _load_project_from_path(self, path: Path) -> Project | None:
        try:
            project = Project.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to load project from %s", path, exc_info=True)
            return None
        journal_path = path.with_suffix(".journal.jsonl")
        if journal_path.exists():
            try:
                with journal_path.open("r", encoding="utf-8") as handle:
                    line_count = 0
                    for raw in handle:
                        raw = raw.strip()
                        if not raw:
                            continue
                        line_count += 1
                        self._apply_journal_entry(project, json.loads(raw))
                    self._journal_counts[journal_path] = line_count
            except Exception:
                logger.warning("Failed to replay project journal %s", journal_path, exc_info=True)
        return project

    def save_project(self, project: Project) -> None:
        self._write_snapshot(project)

    def create_project(self, label: str, surface_id: str) -> Project:
        project = Project(label=label, surface_id=surface_id)
        self.save_project(project)
        return project

    def get_project(self, project_id: str, surface_id: str) -> Project | None:
        path = self._project_path(project_id, surface_id)
        if path.exists():
            return self._load_project_from_path(path)
        return self.get_project_any_surface(project_id)

    def get_project_any_surface(self, project_id: str) -> Project | None:
        safe_id = _safe_segment(project_id)
        for path in sorted(self.base_dir.glob(f"*/{safe_id}.json")):
            project = self._load_project_from_path(path)
            if project is not None:
                return project
        return None

    def list_projects(self, surface_id: str) -> list[Project]:
        projects: list[Project] = []
        for path in sorted(self._projects_dir(surface_id).glob("*.json")):
            project = self._load_project_from_path(path)
            if project is not None:
                projects.append(project)
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

    def get_current_task_any_surface(self, project_id: str) -> Task | None:
        project = self.get_project_any_surface(project_id)
        if project is None or not project.tasks:
            return None
        if project.current_task_id:
            for task in project.tasks:
                if task.task_id == project.current_task_id:
                    return task
        return max(project.tasks, key=lambda task: task.updated_at)

    def append_turn(self, project_id: str, task_id: str, turn: TaskTurn, surface_id: str) -> None:
        snapshot = self._load_project_snapshot_payload(project_id, surface_id)
        if snapshot is None:
            raise KeyError(f"Project '{project_id}' not found")
        _path, payload = snapshot
        if not self._snapshot_has_task(payload, task_id):
            raise KeyError(f"Task '{task_id}' not found")
        task_updated_at = _utc_now()
        project_updated_at = _utc_now()
        self._append_journal_entry(
            project_id,
            surface_id,
            {
                "op": "append_turn",
                "task_id": task_id,
                "turn": turn.model_dump(mode="json"),
                "task_updated_at": task_updated_at.isoformat(),
                "project_updated_at": project_updated_at.isoformat(),
            },
        )

    def update_project_status(self, project_id: str, status: str, surface_id: str) -> None:
        snapshot = self._load_project_snapshot_payload(project_id, surface_id)
        if snapshot is None:
            raise KeyError(f"Project '{project_id}' not found")
        self._append_journal_entry(
            project_id,
            surface_id,
            {
                "op": "update_project",
                "fields": {
                    "status": status,
                    "updated_at": _utc_now().isoformat(),
                },
            },
        )

    def update_task_status(self, project_id: str, task_id: str, status: str, surface_id: str) -> None:
        snapshot = self._load_project_snapshot_payload(project_id, surface_id)
        if snapshot is None:
            raise KeyError(f"Project '{project_id}' not found")
        _path, payload = snapshot
        if not self._snapshot_has_task(payload, task_id):
            raise KeyError(f"Task '{task_id}' not found")
        task_updated_at = _utc_now()
        project_updated_at = _utc_now()
        self._append_journal_entry(
            project_id,
            surface_id,
            {
                "op": "update_task",
                "task_id": task_id,
                "fields": {
                    "status": status,
                    "updated_at": task_updated_at.isoformat(),
                },
                "project_updated_at": project_updated_at.isoformat(),
            },
        )

    def update_project_summary(self, project_id: str, summary: str, surface_id: str) -> None:
        snapshot = self._load_project_snapshot_payload(project_id, surface_id)
        if snapshot is None:
            raise KeyError(f"Project '{project_id}' not found")
        self._append_journal_entry(
            project_id,
            surface_id,
            {
                "op": "update_project",
                "fields": {
                    "summary": summary,
                    "updated_at": _utc_now().isoformat(),
                },
            },
        )

    def get_pending_project(self, surface_id: str) -> Project | None:
        pending = self.list_pending_projects(surface_id)
        return pending[0] if pending else None

    def list_pending_projects(self, surface_id: str) -> list[Project]:
        return [project for project in self.list_projects(surface_id) if project.pending_action is not None]

    def set_pending_action(self, project_id: str, pending_action: PendingAction, surface_id: str) -> None:
        snapshot = self._load_project_snapshot_payload(project_id, surface_id)
        if snapshot is None:
            raise KeyError(f"Project '{project_id}' not found")
        self._append_journal_entry(
            project_id,
            surface_id,
            {
                "op": "update_project",
                "fields": {
                    "pending_action": pending_action.model_dump(mode="json"),
                    "updated_at": _utc_now().isoformat(),
                },
            },
        )

    def clear_pending_action(self, project_id: str, surface_id: str) -> None:
        snapshot = self._load_project_snapshot_payload(project_id, surface_id)
        if snapshot is None:
            raise KeyError(f"Project '{project_id}' not found")
        self._append_journal_entry(
            project_id,
            surface_id,
            {
                "op": "update_project",
                "fields": {
                    "pending_action": None,
                    "updated_at": _utc_now().isoformat(),
                },
            },
        )

    def link_workflow(self, project_id: str, workflow_id: str, surface_id: str) -> None:
        resolved = self._resolve_project_fields(project_id, surface_id, "linked_workflow_ids")
        if resolved is None:
            raise KeyError(f"Project '{project_id}' not found")
        _path, fields = resolved
        linked_workflow_ids = fields.get("linked_workflow_ids")
        if not isinstance(linked_workflow_ids, list):
            linked_workflow_ids = []
        if workflow_id not in linked_workflow_ids:
            self._append_journal_entry(
                project_id,
                surface_id,
                {
                    "op": "update_project",
                    "fields": {
                        "linked_workflow_ids": [*linked_workflow_ids, workflow_id],
                        "updated_at": _utc_now().isoformat(),
                    },
                },
            )

    def link_run(self, project_id: str, run_id: str, surface_id: str) -> None:
        resolved = self._resolve_project_fields(project_id, surface_id, "linked_run_ids")
        if resolved is None:
            raise KeyError(f"Project '{project_id}' not found")
        _path, fields = resolved
        linked_run_ids = fields.get("linked_run_ids")
        if not isinstance(linked_run_ids, list):
            linked_run_ids = []
        if run_id not in linked_run_ids:
            self._append_journal_entry(
                project_id,
                surface_id,
                {
                    "op": "update_project",
                    "fields": {
                        "linked_run_ids": [*linked_run_ids, run_id],
                        "updated_at": _utc_now().isoformat(),
                    },
                },
            )

    def link_meta_session(self, project_id: str, session_id: str, surface_id: str) -> None:
        resolved = self._resolve_project_fields(project_id, surface_id, "linked_meta_session_ids")
        if resolved is None:
            raise KeyError(f"Project '{project_id}' not found")
        _path, fields = resolved
        linked_meta_session_ids = fields.get("linked_meta_session_ids")
        if not isinstance(linked_meta_session_ids, list):
            linked_meta_session_ids = []
        if session_id not in linked_meta_session_ids:
            self._append_journal_entry(
                project_id,
                surface_id,
                {
                    "op": "update_project",
                    "fields": {
                        "linked_meta_session_ids": [*linked_meta_session_ids, session_id],
                        "updated_at": _utc_now().isoformat(),
                    },
                },
            )

    def update_task_progress(
        self,
        project_id: str,
        task_id: str,
        surface_id: str,
        *,
        completed_steps: list[str] | None = None,
        pending_steps: list[str] | None = None,
        current_blocker: str | None = None,
        artifacts: dict[str, str] | None = None,
        goal_id: str | None = None,
        progress_updated_at: float | None = None,
        clear_blocker: bool = False,
    ) -> None:
        snapshot = self._load_project_snapshot_payload(project_id, surface_id)
        if snapshot is None:
            raise KeyError(f"Project '{project_id}' not found")
        _path, payload = snapshot
        if not self._snapshot_has_task(payload, task_id):
            raise KeyError(f"Task '{task_id}' not found")
        task_updated_at = _utc_now()
        project_updated_at = _utc_now()
        fields: dict[str, Any] = {
            "updated_at": task_updated_at.isoformat(),
            "last_activity": task_updated_at.isoformat(),
        }
        if completed_steps is not None:
            fields["completed_steps"] = list(completed_steps)
        if pending_steps is not None:
            fields["pending_steps"] = list(pending_steps)
        if artifacts is not None:
            fields["artifacts"] = dict(artifacts)
        if goal_id is not None:
            fields["goal_id"] = goal_id
        if progress_updated_at is not None:
            fields["progress_updated_at"] = progress_updated_at
        if clear_blocker:
            fields["current_blocker"] = None
        elif current_blocker is not None:
            fields["current_blocker"] = current_blocker
        self._append_journal_entry(
            project_id,
            surface_id,
            {
                "op": "update_task",
                "task_id": task_id,
                "fields": fields,
                "project_updated_at": project_updated_at.isoformat(),
            },
        )

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
