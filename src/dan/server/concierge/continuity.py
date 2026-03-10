"""Multi-surface continuity: cross-surface context sharing, handoff detection,
presence tracking, and surface routing.

Enables seamless task continuity across surfaces — start a task on CLI, continue
on Telegram, come back and see the full thread.  The Project scope is the
continuity anchor, not the surface.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .models import Project, Task
from .project_store import ProjectStore
from .resume import TaskSnapshot, snapshot_from_task


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SurfaceRoutingPolicy(BaseModel):
    """Per-project policy controlling cross-surface context sharing."""

    visibility: Literal["private", "shared"] = "private"
    allow_project_context: bool = True
    allow_follow_ups: bool = False


class SurfacePresence(BaseModel):
    """Records user activity on a specific surface."""

    surface_id: str
    surface_type: str
    last_active: datetime
    project_id: str | None = None


class ConversationTurn(BaseModel):
    """A single turn in a cross-surface conversation."""

    surface_id: str
    surface_type: str
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime
    project_id: str | None = None
    task_id: str | None = None


class CrossSurfaceContext(BaseModel):
    """Handoff payload delivered when a user switches surfaces mid-task."""

    project_id: str
    recent_turns: list[ConversationTurn]
    task_snapshot: TaskSnapshot | None = None
    project_summary: str


# ---------------------------------------------------------------------------
# ProjectConversationStore — view over existing ProjectStore
# ---------------------------------------------------------------------------


class ProjectConversationStore:
    """Aggregate conversation turns across surfaces for a project.

    This is a *view* over ``ProjectStore``, not an independent data store.
    It reads task turns from persisted projects and presents them in a unified
    timeline.
    """

    def __init__(self, project_store: ProjectStore) -> None:
        self._store = project_store

    def _find_project_across_surfaces(self, project_id: str) -> list[tuple[str, Project]]:
        """Return ``(surface_id, Project)`` pairs for *project_id* across all surfaces."""
        results: list[tuple[str, Project]] = []
        if not self._store.base_dir.exists():
            return results
        for surface_dir in sorted(self._store.base_dir.iterdir()):
            if not surface_dir.is_dir():
                continue
            surface_id = surface_dir.name
            project = self._store.get_project(project_id, surface_id)
            if project is not None:
                results.append((surface_id, project))
        return results

    def _extract_turns(
        self,
        surface_id: str,
        project: Project,
    ) -> list[ConversationTurn]:
        """Extract ``ConversationTurn`` objects from every task in *project*."""
        turns: list[ConversationTurn] = []
        for task in project.tasks:
            for tt in task.turns:
                if tt.role not in ("user", "assistant"):
                    continue
                turns.append(
                    ConversationTurn(
                        surface_id=surface_id,
                        surface_type=_guess_surface_type(surface_id),
                        role=tt.role,
                        content=tt.content,
                        timestamp=tt.timestamp,
                        project_id=project.project_id,
                        task_id=task.task_id,
                    )
                )
        return turns

    def get_unified_history(
        self,
        project_id: str,
        limit: int = 20,
    ) -> list[ConversationTurn]:
        """Aggregate conversation turns from all surfaces, ordered by timestamp."""
        all_turns: list[ConversationTurn] = []
        for surface_id, project in self._find_project_across_surfaces(project_id):
            all_turns.extend(self._extract_turns(surface_id, project))
        all_turns.sort(key=lambda t: t.timestamp)
        return all_turns[-limit:] if limit else all_turns

    def get_recent_turns(
        self,
        project_id: str,
        surface_id: str | None = None,
        limit: int = 5,
    ) -> list[ConversationTurn]:
        """Last *limit* turns, optionally filtered to a single surface."""
        all_turns = self.get_unified_history(project_id, limit=0)
        if surface_id is not None:
            all_turns = [t for t in all_turns if t.surface_id == surface_id]
        return all_turns[-limit:] if limit else all_turns


# ---------------------------------------------------------------------------
# Surface-type heuristic
# ---------------------------------------------------------------------------

_SURFACE_TYPE_PATTERNS: list[tuple[str, str]] = [
    ("telegram", "telegram"),
    ("tg", "telegram"),
    ("whatsapp", "whatsapp"),
    ("wa", "whatsapp"),
    ("cli", "cli"),
    ("editor", "editor"),
    ("email", "email"),
]


def _guess_surface_type(surface_id: str) -> str:
    lower = surface_id.lower()
    for pattern, stype in _SURFACE_TYPE_PATTERNS:
        if pattern in lower:
            return stype
    return "unknown"


# ---------------------------------------------------------------------------
# Surface handoff detection
# ---------------------------------------------------------------------------

_HANDOFF_THRESHOLD = 0.3


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def detect_surface_switch(
    current_surface: str,
    message: str,
    project_store: ProjectStore,
) -> str | None:
    """Check if *message* on *current_surface* relates to work on another surface.

    Returns the ``project_id`` of the matching project if a handoff is detected,
    ``None`` otherwise.  Uses keyword/topic overlap with active projects on
    *other* surfaces — reuses the ``ProjectContextResolver.infer_project()``
    matching pattern.
    """
    msg_tokens = _tokenize(message)
    if len(msg_tokens) < 2:
        return None

    best_score = 0.0
    best_project_id: str | None = None

    if not project_store.base_dir.exists():
        return None

    for surface_dir in sorted(project_store.base_dir.iterdir()):
        if not surface_dir.is_dir():
            continue
        surface_id = surface_dir.name
        if surface_id == current_surface:
            continue
        for project in project_store.list_active(surface_id):
            project_tokens = _tokenize(
                " ".join(
                    [
                        project.label,
                        project.summary,
                        " ".join(t.label for t in project.tasks),
                    ]
                )
            )
            if not project_tokens:
                continue
            overlap = len(msg_tokens & project_tokens)
            score = overlap / max(len(project_tokens), 1)
            if score > best_score:
                best_score = score
                best_project_id = project.project_id

    if best_score >= _HANDOFF_THRESHOLD:
        return best_project_id
    return None


# ---------------------------------------------------------------------------
# Handoff context generator
# ---------------------------------------------------------------------------


def generate_handoff_context(
    project_id: str,
    project_store: ProjectStore,
) -> CrossSurfaceContext:
    """Build a ``CrossSurfaceContext`` payload for a surface handoff.

    Includes task snapshot (if an active task exists), recent turns from any
    surface, and the project summary.
    """
    conv_store = ProjectConversationStore(project_store)
    recent = conv_store.get_recent_turns(project_id, limit=5)

    snapshot: TaskSnapshot | None = None
    summary = ""

    for surface_dir in sorted(project_store.base_dir.iterdir()):
        if not surface_dir.is_dir():
            continue
        surface_id = surface_dir.name
        project = project_store.get_project(project_id, surface_id)
        if project is None:
            continue
        summary = summary or project.summary
        if project.current_task_id:
            for task in project.tasks:
                if task.task_id == project.current_task_id:
                    snapshot = snapshot_from_task(task, project)
                    break
        if snapshot:
            break

    return CrossSurfaceContext(
        project_id=project_id,
        recent_turns=recent,
        task_snapshot=snapshot,
        project_summary=summary,
    )


# ---------------------------------------------------------------------------
# Presence tracker
# ---------------------------------------------------------------------------


class PresenceTracker:
    """In-memory tracker of user activity across surfaces."""

    def __init__(self) -> None:
        self._presences: dict[str, SurfacePresence] = {}

    def update(
        self,
        surface_id: str,
        surface_type: str,
        project_id: str | None = None,
    ) -> None:
        """Record user activity on *surface_id*."""
        self._presences[surface_id] = SurfacePresence(
            surface_id=surface_id,
            surface_type=surface_type,
            last_active=_utc_now(),
            project_id=project_id,
        )

    def get_active_surface(self) -> SurfacePresence | None:
        """Return the most recently active surface."""
        if not self._presences:
            return None
        return max(self._presences.values(), key=lambda p: p.last_active)

    def get_surfaces_for_project(self, project_id: str) -> list[SurfacePresence]:
        """Return surfaces where *project_id* was discussed."""
        return [
            p for p in self._presences.values() if p.project_id == project_id
        ]

    def get_preferred_surface(self, project_id: str) -> SurfacePresence | None:
        """Return the surface where *project_id* was most recently discussed."""
        candidates = self.get_surfaces_for_project(project_id)
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.last_active)


# ---------------------------------------------------------------------------
# Surface routing
# ---------------------------------------------------------------------------


def route_message_to_surface(
    project_id: str,
    tracker: PresenceTracker,
    policy: SurfaceRoutingPolicy,
) -> str | None:
    """Determine which surface to deliver a DAN-initiated message to.

    - Default: most recently active **private** surface for the project.
    - Group/shared chats require explicit opt-in via *policy*.
    - Returns ``None`` if no suitable surface is found.
    """
    preferred = tracker.get_preferred_surface(project_id)

    if preferred is not None:
        if policy.visibility == "shared" and not policy.allow_follow_ups:
            return None
        return preferred.surface_id

    active = tracker.get_active_surface()
    if active is None:
        return None

    if policy.visibility == "shared" and not policy.allow_follow_ups:
        return None

    return active.surface_id


# ---------------------------------------------------------------------------
# /sync command handler
# ---------------------------------------------------------------------------


def handle_sync_command(
    text: str,
    project_store: ProjectStore,
    tracker: PresenceTracker,
) -> str:
    """Handle ``/sync [--allow-group]``.

    Pulls and displays latest context from all surfaces for a project inferred
    from presence state.
    """
    args = text.strip()
    if args.lower().startswith("/sync"):
        args = args[len("/sync"):].strip()

    allow_group = "--allow-group" in args.lower()

    preferred = tracker.get_active_surface()
    if preferred is None or preferred.project_id is None:
        return "No active project to sync. Start or resume a project first."

    project_id = preferred.project_id

    if allow_group:
        for surface_dir in sorted(project_store.base_dir.iterdir()):
            if not surface_dir.is_dir():
                continue
            project = project_store.get_project(project_id, surface_dir.name)
            if project is not None:
                break

    ctx = generate_handoff_context(project_id, project_store)

    lines: list[str] = [f"Sync for project '{project_id}':"]

    surfaces = tracker.get_surfaces_for_project(project_id)
    if surfaces:
        surface_names = ", ".join(
            f"{s.surface_type}({s.surface_id})" for s in surfaces
        )
        lines.append(f"Surfaces: {surface_names}")

    if ctx.task_snapshot:
        snap = ctx.task_snapshot
        lines.append(
            f"Task: {snap.task_name} [{snap.status}] — "
            f"{snap.completed_count} done, {snap.pending_count} pending"
        )
        if snap.blocker:
            lines.append(f"Blocked on: {snap.blocker}")

    if ctx.project_summary:
        lines.append(f"Summary: {ctx.project_summary}")

    if ctx.recent_turns:
        lines.append(f"Recent activity: {len(ctx.recent_turns)} turns")
        for turn in ctx.recent_turns[-3:]:
            prefix = "You" if turn.role == "user" else "DAN"
            snippet = turn.content[:80]
            if len(turn.content) > 80:
                snippet += "..."
            lines.append(f"  [{turn.surface_type}] {prefix}: {snippet}")

    if allow_group:
        lines.append("Group chat context sharing: enabled")

    return "\n".join(lines)
