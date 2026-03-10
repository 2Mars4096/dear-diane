"""Proactive follow-up: DAN-initiated messages on completions, stale tasks, and discoveries.

Implements plan 31-12: priority-queued follow-up triggers with deduplication,
quiet-hours gating, rate limiting, and stale-task scanning.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from .resume import TaskSnapshot

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FollowUpTrigger(BaseModel):
    """A single pending follow-up message."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: Literal[
        "run_completion", "schedule", "memory", "blocker_resolved", "stale_task"
    ]
    priority: Literal["low", "medium", "high"]
    message: str
    context: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    conversation_key: str | None = None
    thread_key: str | None = None
    target_surface: str | None = None
    reply_correlation_id: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime | None = None
    delivered: bool = False
    delivered_at: datetime | None = None


class FollowUpConfig(BaseModel):
    """Configuration for the proactive follow-up system."""

    enabled: bool = False
    quiet_hours: str | None = None
    max_per_hour: int = 3
    stale_task_hours: int = 24


def load_follow_up_config() -> FollowUpConfig:
    """Build config from environment variables."""
    enabled = os.getenv("DAN_PROACTIVE_FOLLOW_UP", "0") not in (
        "0",
        "",
        "false",
        "no",
    )
    quiet_hours = os.getenv("DAN_QUIET_HOURS")
    max_per_hour = int(os.getenv("DAN_FOLLOW_UP_MAX_PER_HOUR", "3"))
    stale_task_hours = int(os.getenv("DAN_STALE_TASK_HOURS", "24"))
    return FollowUpConfig(
        enabled=enabled,
        quiet_hours=quiet_hours,
        max_per_hour=max_per_hour,
        stale_task_hours=stale_task_hours,
    )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _dedup_key(trigger: FollowUpTrigger) -> str:
    """Hash by source + project_id + first 60 chars of message."""
    raw = f"{trigger.source}:{trigger.project_id or ''}:{trigger.message[:60]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# FollowUpQueue
# ---------------------------------------------------------------------------


class FollowUpQueue:
    """Per-user/project scoped priority queue with deduplication and expiry."""

    def __init__(self) -> None:
        self._pending: list[FollowUpTrigger] = []
        self._seen_keys: set[str] = set()

    def enqueue(self, trigger: FollowUpTrigger) -> bool:
        """Add a trigger. Returns False if deduplicated away."""
        key = _dedup_key(trigger)
        if key in self._seen_keys:
            return False
        self._seen_keys.add(key)
        self._pending.append(trigger)
        self._pending.sort(key=lambda t: _PRIORITY_ORDER.get(t.priority, 99))
        return True

    def drain(self, max_count: int = 5) -> list[FollowUpTrigger]:
        """Return up to *max_count* pending triggers, marking them delivered."""
        now = _utc_now()
        self._pending = [
            t
            for t in self._pending
            if t.expires_at is None or t.expires_at > now
        ]

        result: list[FollowUpTrigger] = []
        remaining: list[FollowUpTrigger] = []
        for t in self._pending:
            if len(result) < max_count:
                t.delivered = True
                t.delivered_at = now
                result.append(t)
            else:
                remaining.append(t)
        self._pending = remaining
        return result

    def pending_count(self) -> int:
        now = _utc_now()
        return sum(
            1
            for t in self._pending
            if t.expires_at is None or t.expires_at > now
        )

    def list_pending(self) -> list[FollowUpTrigger]:
        now = _utc_now()
        return [
            t
            for t in self._pending
            if t.expires_at is None or t.expires_at > now
        ]


# ---------------------------------------------------------------------------
# Trigger factories
# ---------------------------------------------------------------------------


def create_run_completion_trigger(
    run_id: str,
    result_summary: str,
    project_id: str | None = None,
) -> FollowUpTrigger:
    """Create a follow-up for a completed workflow/task run."""
    return FollowUpTrigger(
        source="run_completion",
        priority="medium",
        message=f"Run `{run_id}` completed: {result_summary}",
        context={"run_id": run_id},
        project_id=project_id,
    )


def create_stale_task_trigger(snapshot: TaskSnapshot) -> FollowUpTrigger:
    """Create a follow-up for a task that's been paused/blocked too long."""
    hours = 0
    if snapshot.last_activity:
        delta = _utc_now() - snapshot.last_activity
        hours = int(delta.total_seconds() / 3600)

    status = snapshot.status
    ctx_parts: list[str] = []
    if snapshot.blocker:
        ctx_parts.append(f"Blocked on: {snapshot.blocker}")
    if snapshot.pending_count:
        ctx_parts.append(f"{snapshot.pending_count} steps remaining")
    ctx_str = ". ".join(ctx_parts) if ctx_parts else "No additional context"

    return FollowUpTrigger(
        source="stale_task",
        priority="low",
        message=(
            f"Your task '{snapshot.task_name}' has been {status} for {hours}h. "
            f"{ctx_str}. Want me to continue?"
        ),
        context={
            "task_id": snapshot.task_id,
            "project_name": snapshot.project_name,
            "status": status,
            "hours_stale": hours,
        },
        task_id=snapshot.task_id,
    )


def create_schedule_result_trigger(
    schedule_name: str,
    result: str,
) -> FollowUpTrigger:
    """Create a follow-up for a completed scheduled task."""
    return FollowUpTrigger(
        source="schedule",
        priority="medium",
        message=f"Scheduled task '{schedule_name}' completed: {result}",
        context={"schedule_name": schedule_name},
    )


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------


def _parse_quiet_hours(spec: str | None) -> tuple[int, int, int, int] | None:
    """Parse ``"HH:MM-HH:MM"`` into (start_h, start_m, end_h, end_m)."""
    if not spec or "-" not in spec:
        return None
    parts = spec.split("-", 1)
    if len(parts) != 2:
        return None
    try:
        start_parts = parts[0].strip().split(":")
        end_parts = parts[1].strip().split(":")
        sh = int(start_parts[0])
        sm = int(start_parts[1]) if len(start_parts) > 1 else 0
        eh = int(end_parts[0])
        em = int(end_parts[1]) if len(end_parts) > 1 else 0
        return sh, sm, eh, em
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Delivery Engine
# ---------------------------------------------------------------------------


class FollowUpDeliveryEngine:
    """Delivers pending follow-ups respecting quiet hours and rate limits."""

    def __init__(
        self,
        config: FollowUpConfig,
        queue: FollowUpQueue,
        dispatch_fn: Callable[[FollowUpTrigger], Awaitable[None]],
    ) -> None:
        self._config = config
        self._queue = queue
        self._dispatch_fn = dispatch_fn
        self._delivery_timestamps: list[datetime] = []
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def config(self) -> FollowUpConfig:
        return self._config

    @config.setter
    def config(self, value: FollowUpConfig) -> None:
        self._config = value

    def is_quiet_hours(self, now: datetime | None = None) -> bool:
        """Check whether *now* falls within the configured quiet window."""
        if not self._config.quiet_hours:
            return False
        parsed = _parse_quiet_hours(self._config.quiet_hours)
        if parsed is None:
            return False
        sh, sm, eh, em = parsed
        if now is None:
            now = _utc_now()
        current_minutes = now.hour * 60 + now.minute
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em

        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes < end_minutes
        # Wraps midnight
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def check_rate_limit(self) -> bool:
        """Return True if delivery count is under the hourly rate limit."""
        now = _utc_now()
        cutoff = now - timedelta(hours=1)
        self._delivery_timestamps = [
            ts for ts in self._delivery_timestamps if ts > cutoff
        ]
        return len(self._delivery_timestamps) < self._config.max_per_hour

    async def deliver_pending(self) -> list[FollowUpTrigger]:
        """Deliver pending follow-ups respecting config constraints."""
        if not self._config.enabled:
            return []
        if self.is_quiet_hours():
            return []

        now = _utc_now()
        cutoff = now - timedelta(hours=1)
        recent_count = sum(1 for ts in self._delivery_timestamps if ts > cutoff)
        available = self._config.max_per_hour - recent_count
        if available <= 0:
            return []

        triggers = self._queue.drain(max_count=available)
        delivered: list[FollowUpTrigger] = []
        for trigger in triggers:
            try:
                await self._dispatch_fn(trigger)
                self._delivery_timestamps.append(_utc_now())
                delivered.append(trigger)
            except Exception:
                logger.exception("Failed to deliver follow-up %s", trigger.id)
        return delivered

    async def start(self) -> None:
        """Start the background delivery loop (checks every 60s)."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the background loop."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.deliver_pending()
            except Exception:
                logger.exception("Follow-up delivery tick error")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=60.0
                )
                break
            except asyncio.TimeoutError:
                pass


# ---------------------------------------------------------------------------
# Stale task scanner
# ---------------------------------------------------------------------------


def scan_stale_tasks(
    project_store: Any,
    stale_hours: int = 24,
) -> list[FollowUpTrigger]:
    """Find tasks paused/blocked beyond *stale_hours* and create triggers."""
    from .models import Project
    from .resume import snapshot_from_task

    cutoff = _utc_now() - timedelta(hours=stale_hours)
    triggers: list[FollowUpTrigger] = []

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
                if task.status not in ("paused", "blocked"):
                    continue
                activity = task.last_activity or task.updated_at
                if activity < cutoff:
                    snapshot = snapshot_from_task(task, project)
                    triggers.append(create_stale_task_trigger(snapshot))

    return triggers


# ---------------------------------------------------------------------------
# Chat command handler
# ---------------------------------------------------------------------------


def handle_follow_ups_command(
    text: str,
    config: FollowUpConfig,
    queue: FollowUpQueue,
) -> str:
    """Dispatch ``/follow-ups`` subcommands."""
    args = text.strip()
    if args.lower().startswith("/follow-ups"):
        args = args[len("/follow-ups") :].strip()

    lower = args.lower()

    if lower == "on":
        config.enabled = True
        return "Proactive follow-ups **enabled**."

    if lower == "off":
        config.enabled = False
        return "Proactive follow-ups **disabled**."

    pending = queue.list_pending()
    if not pending:
        status = "enabled" if config.enabled else "disabled"
        return f"No pending follow-ups. (Follow-ups are currently {status}.)"

    lines = [f"**Pending Follow-Ups** ({len(pending)})\n"]
    for t in pending:
        lines.append(f"  [{t.priority}] {t.message[:80]}")
    status = "enabled" if config.enabled else "disabled"
    lines.append(f"\nFollow-ups are currently {status}.")
    return "\n".join(lines)
