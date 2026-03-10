"""Cross-session resume: structured task snapshots, resume protocol, and /resume command."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .models import Project, Task
from .project_store import ProjectStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# TaskSnapshot
# ---------------------------------------------------------------------------

class TaskSnapshot(BaseModel):
    """Lightweight serializable summary of a task for quick-resume prompts."""

    task_id: str
    task_name: str
    project_name: str
    status: str
    completed_count: int
    pending_count: int
    blocker: str | None = None
    summary: str
    last_activity: datetime | None = None
    artifact_count: int = 0


def snapshot_from_task(task: Task, project: Project) -> TaskSnapshot:
    """Build a ``TaskSnapshot`` from a live ``Task`` and its parent ``Project``."""
    parts: list[str] = []
    if task.completed_steps:
        recent = task.completed_steps[-3:]
        parts.append(f"Completed: {', '.join(recent)}")
    if task.pending_steps:
        parts.append(f"Next: {task.pending_steps[0]}")
    if task.current_blocker:
        parts.append(f"Blocked on: {task.current_blocker}")

    return TaskSnapshot(
        task_id=task.task_id,
        task_name=task.label,
        project_name=project.label,
        status=task.status,
        completed_count=len(task.completed_steps),
        pending_count=len(task.pending_steps),
        blocker=task.current_blocker,
        summary="; ".join(parts) if parts else "No progress recorded yet.",
        last_activity=task.last_activity,
        artifact_count=len(task.artifacts),
    )


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def _format_time_ago(dt: datetime | None) -> str:
    if dt is None:
        return "unknown time ago"
    delta = _utc_now() - dt
    if delta.days > 0:
        return f"{delta.days} day{'s' if delta.days != 1 else ''} ago"
    hours = delta.seconds // 3600
    if hours > 0:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    minutes = delta.seconds // 60
    if minutes > 0:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    return "just now"


# ---------------------------------------------------------------------------
# ResumeProtocol
# ---------------------------------------------------------------------------

_RESUME_CUES = [
    "continue",
    "pick up",
    "where were we",
    "resume",
    "keep going",
    "left off",
    "pick back up",
]


class ResumeProtocol:
    """Detect, rank, and generate prompts for resumable tasks."""

    def check_resumable_tasks(
        self,
        project_store: ProjectStore,
        *,
        max_age_days: int = 7,
    ) -> list[TaskSnapshot]:
        """Find tasks with an active/paused/blocked status and recent activity."""
        cutoff = _utc_now() - timedelta(days=max_age_days)
        snapshots: list[TaskSnapshot] = []

        for surface_dir in sorted(project_store.base_dir.iterdir()):
            if not surface_dir.is_dir():
                continue
            for project_path in surface_dir.glob("*.json"):
                try:
                    project = Project.model_validate_json(
                        project_path.read_text(encoding="utf-8"),
                    )
                except Exception:
                    continue
                for task in project.tasks:
                    if task.status not in ("active", "paused", "blocked"):
                        continue
                    activity = task.last_activity or task.updated_at
                    if activity >= cutoff:
                        snapshots.append(snapshot_from_task(task, project))

        snapshots.sort(
            key=lambda s: s.last_activity or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return snapshots

    def generate_resume_prompt(self, snapshot: TaskSnapshot) -> str:
        """Human-readable resume prompt for a single task snapshot."""
        time_ago = _format_time_ago(snapshot.last_activity)
        return (
            f"You have an active task: '{snapshot.task_name}' (started {time_ago}). "
            f"{snapshot.completed_count} steps done, {snapshot.pending_count} remaining. "
            f"Current state: {snapshot.summary}. Continue?"
        )

    def auto_resume_match(
        self,
        message: str,
        candidates: list[TaskSnapshot],
        threshold: float = 0.5,
    ) -> TaskSnapshot | None:
        """Return the best candidate if the message clearly indicates resume intent
        for exactly one task.  Returns ``None`` when ambiguous or unrelated."""
        if not candidates:
            return None

        lower = message.lower()
        if not any(cue in lower for cue in _RESUME_CUES):
            return None

        if len(candidates) == 1:
            return candidates[0]

        msg_tokens = set(re.findall(r"[a-z0-9]+", lower))
        scored: list[tuple[float, TaskSnapshot]] = []
        for candidate in candidates:
            name_tokens = set(re.findall(r"[a-z0-9]+", candidate.task_name.lower()))
            if not name_tokens:
                continue
            overlap = len(msg_tokens & name_tokens)
            score = overlap / len(name_tokens)
            scored.append((score, candidate))

        above = [(s, c) for s, c in scored if s >= threshold]

        if len(above) == 1:
            return above[0][1]
        if len(above) > 1:
            return None

        return None

    def update_task_state(
        self,
        task: Task,
        completed_steps: list[str] | None = None,
        pending_steps: list[str] | None = None,
        blocker: str | None = None,
        artifacts: dict[str, str] | None = None,
    ) -> None:
        """Mutate *task* in place with the provided state updates."""
        if completed_steps is not None:
            task.completed_steps = completed_steps
        if pending_steps is not None:
            task.pending_steps = pending_steps
        if blocker is not None:
            task.current_blocker = blocker
        if artifacts is not None:
            task.artifacts.update(artifacts)
        task.last_activity = _utc_now()
        task.updated_at = _utc_now()


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def persist_task_state(
    project_store: ProjectStore,
    project_id: str,
    task: Task,
) -> None:
    """Save *task* back into its project across any surface."""
    for surface_dir in sorted(project_store.base_dir.iterdir()):
        if not surface_dir.is_dir():
            continue
        surface_id = surface_dir.name
        project = project_store.get_project(project_id, surface_id)
        if project is None:
            continue
        for i, t in enumerate(project.tasks):
            if t.task_id == task.task_id:
                project.tasks[i] = task
                project.updated_at = _utc_now()
                project_store.save_project(project)
                return
    raise KeyError(f"Project '{project_id}' or task '{task.task_id}' not found")


def extract_structured_state(conversation_text: str) -> dict:
    """Heuristic extraction of completed/pending steps from conversation text."""
    completed: list[str] = []
    pending: list[str] = []

    for line in conversation_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if re.match(r"[-*]?\s*\[x\]", stripped, re.IGNORECASE):
            text = re.sub(r"^[-*]?\s*\[x\]\s*", "", stripped, flags=re.IGNORECASE).strip()
            if text:
                completed.append(text)
            continue

        if re.match(r"[-*]?\s*\[ ?\]", stripped):
            text = re.sub(r"^[-*]?\s*\[ ?\]\s*", "", stripped).strip()
            if text:
                pending.append(text)
            continue

        lower = stripped.lower()
        if re.search(r"\b(done|completed|finished)\b", lower):
            text = re.sub(r"^[-*]\s*", "", stripped).strip()
            text = re.sub(r"^\d+[.)]\s*", "", text).strip()
            if text:
                completed.append(text)
            continue

        if re.search(r"\b(next|remaining|todo|to do|pending)\b", lower):
            text = re.sub(r"^[-*]\s*", "", stripped).strip()
            text = re.sub(r"^\d+[.)]\s*", "", text).strip()
            if text:
                pending.append(text)
            continue

    return {"completed_steps": completed, "pending_steps": pending}


# ---------------------------------------------------------------------------
# /resume command handler
# ---------------------------------------------------------------------------

def handle_resume_command(text: str, project_store: ProjectStore) -> str:
    """Handle ``/resume [task_name]``."""
    args = text.strip()
    if args.lower().startswith("/resume"):
        args = args[len("/resume"):].strip()

    protocol = ResumeProtocol()
    snapshots = protocol.check_resumable_tasks(project_store)

    if not snapshots:
        return "No resumable tasks found. Start a new task with a message or /run."

    if args:
        matches = [s for s in snapshots if args.lower() in s.task_name.lower()]
        if not matches:
            task_names = ", ".join(f"'{s.task_name}'" for s in snapshots[:5])
            return f"No task matching '{args}' found. Available tasks: {task_names}"
        snapshot = matches[0]
    else:
        snapshot = snapshots[0]

    return protocol.generate_resume_prompt(snapshot)
