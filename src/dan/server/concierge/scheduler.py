"""Scheduled tasks — cron-style and interval-based task scheduling.

Implements plan 31-7: enables DAN to run workflows, checks, and reports on
a recurring basis.  ``/schedule add "run equity report" every day at 9am``
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import os
import re
import shlex
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduler authority — single-writer policy (Task 2-2)
# ---------------------------------------------------------------------------


class SchedulerAuthority(enum.Enum):
    """Which process owns schedule firing."""

    SERVICE = "service"  # dan-service daemon (primary)
    SERVER = "server"  # dan-serve web server (fallback)


def resolve_scheduler_authority() -> SchedulerAuthority:
    """Determine which process should own schedule firing.

    Checks for a running ``dan-service`` daemon via the lease file.
    Falls back to ``SERVER`` when no daemon holds a valid lease.
    """
    try:
        mgr = ScheduleLeaseManager()
        lease = mgr.read_lease()
        if lease is not None and not lease.is_stale and _is_pid_alive(lease.pid):
            return SchedulerAuthority.SERVICE
    except Exception:
        logger.debug("Lease check failed, defaulting to SERVER", exc_info=True)
    return SchedulerAuthority.SERVER


# ---------------------------------------------------------------------------
# Schedule lease / lock (Task 2-3)
# ---------------------------------------------------------------------------

_DEFAULT_LEASE_PATH = os.path.expanduser("~/.dan/schedule_lease.json")


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with *pid* is running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


class ScheduleLease(BaseModel):
    """Filesystem-based lease preventing double-firing across processes."""

    owner_id: str
    pid: int = 0
    acquired_at: datetime
    expires_at: datetime
    stale_after_seconds: int = 120

    @property
    def is_stale(self) -> bool:
        now = datetime.now(timezone.utc)
        deadline = self.acquired_at + timedelta(seconds=self.stale_after_seconds)
        return now > deadline

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


class ScheduleLeaseManager:
    """Acquire / release / renew a filesystem lock for the scheduler."""

    def __init__(self, path: str | None = None, stale_after_seconds: int = 120) -> None:
        self._path = path or _DEFAULT_LEASE_PATH
        self._stale_after = stale_after_seconds

    @property
    def path(self) -> str:
        return self._path

    def read_lease(self) -> ScheduleLease | None:
        if not os.path.exists(self._path):
            return None
        try:
            with open(self._path) as f:
                data = json.load(f)
            return ScheduleLease.model_validate(data)
        except Exception:
            return None

    def acquire(self, owner_id: str, duration_seconds: int = 300) -> ScheduleLease | None:
        """Try to acquire the lease. Returns the lease on success, None if held by another.

        When a different owner holds a non-stale/non-expired lease, the owner's
        PID is checked.  If the PID is no longer alive the lease is treated as
        stale and the caller takes over automatically.
        """
        existing = self.read_lease()
        if existing is not None and not existing.is_stale and not existing.is_expired:
            if existing.owner_id != owner_id:
                if _is_pid_alive(existing.pid):
                    return None
        now = datetime.now(timezone.utc)
        lease = ScheduleLease(
            owner_id=owner_id,
            pid=os.getpid(),
            acquired_at=now,
            expires_at=now + timedelta(seconds=duration_seconds),
            stale_after_seconds=self._stale_after,
        )
        self._write(lease)
        return lease

    def renew(self, owner_id: str, duration_seconds: int = 300) -> ScheduleLease | None:
        """Renew an existing lease. Returns None if not held by this owner."""
        existing = self.read_lease()
        if existing is None or (existing.owner_id != owner_id and not existing.is_stale):
            return None
        now = datetime.now(timezone.utc)
        lease = ScheduleLease(
            owner_id=owner_id,
            pid=os.getpid(),
            acquired_at=now,
            expires_at=now + timedelta(seconds=duration_seconds),
            stale_after_seconds=self._stale_after,
        )
        self._write(lease)
        return lease

    def release(self, owner_id: str) -> bool:
        """Release the lease if held by this owner."""
        existing = self.read_lease()
        if existing is None:
            return True
        if existing.owner_id != owner_id:
            return False
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass
        return True

    def is_held_by_current_process(self) -> bool:
        """Return True if the current process holds a valid (non-stale) lease."""
        lease = self.read_lease()
        if lease is None:
            return False
        return lease.pid == os.getpid() and not lease.is_stale and not lease.is_expired

    def _write(self, lease: ScheduleLease) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(Path(self._path).parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(lease.model_dump(mode="json"), f, indent=2, default=str)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

# ---------------------------------------------------------------------------
# croniter availability
# ---------------------------------------------------------------------------

try:
    from croniter import croniter as _croniter  # type: ignore[import-untyped]

    HAS_CRONITER = True
except ImportError:
    HAS_CRONITER = False
    _croniter = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TriggerContext(BaseModel):
    """Metadata about the scheduling origin — injected into the concierge
    dispatch so the LLM knows this is an autonomous (scheduled) invocation."""

    source_surface: str = "schedule"
    project_id: str | None = None
    task_id: str | None = None
    user_id: str | None = None
    thread_key: str | None = None


class DeliveryTarget(BaseModel):
    """Where the results of a scheduled run should be sent."""

    surface: str = "cli"
    conversation_key: str | None = None
    user_id: str | None = None
    project_id: str | None = None
    thread_key: str | None = None
    fallback_policy: Literal["store_and_notify", "private_surface", "drop"] = (
        "store_and_notify"
    )


class ScheduleEntry(BaseModel):
    """A single scheduled task."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    trigger: str
    action: str
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    timezone: str = "UTC"
    trigger_context: TriggerContext = Field(default_factory=TriggerContext)
    delivery_target: DeliveryTarget = Field(default_factory=DeliveryTarget)
    on_missed: Literal["run_once", "skip"] = "run_once"
    workflow_id: str | None = None
    workflow_inputs: dict[str, Any] = Field(default_factory=dict)
    workflow_run_policy: dict[str, Any] | None = None


class ScheduleRunRecord(BaseModel):
    """Result of a single schedule execution."""

    schedule_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["success", "error", "running"] = "running"
    result_summary: str = ""
    error: str | None = None


def _dispatch_result_is_inflight_workflow(result: str) -> bool:
    """Return True when a schedule dispatch only started a workflow run.

    The scheduler should not record these as completed successes because the
    underlying workflow may still fail or be cancelled later.
    """
    text = (result or "").strip()
    return text.startswith("Started workflow `") and " as run `" in text


# ---------------------------------------------------------------------------
# Cron / interval parsing
# ---------------------------------------------------------------------------

_INTERVAL_RE = re.compile(
    r"^every\s+(\d+)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|s|sec|secs|seconds?)$",
    re.IGNORECASE,
)

_DAILY_AT_RE = re.compile(
    r"^daily\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$",
    re.IGNORECASE,
)

_WEEKDAYS_AT_RE = re.compile(
    r"^weekdays?\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$",
    re.IGNORECASE,
)

_EVERY_DAY_AT_RE = re.compile(
    r"^every\s+day\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$",
    re.IGNORECASE,
)

_CRON_RE = re.compile(
    r"^[*\d/,\-]+\s+[*\d/,\-]+\s+[*\d/,\-]+\s+[*\d/,\-]+\s+[*\d/,\-]+$"
)


def _parse_time_fields(
    hour_s: str,
    minute_s: str | None,
    ampm: str | None,
) -> tuple[int, int]:
    """Return (hour_24, minute) from parsed regex groups."""
    hour = int(hour_s)
    minute = int(minute_s) if minute_s else 0
    if ampm:
        if ampm.lower() == "pm" and hour != 12:
            hour += 12
        elif ampm.lower() == "am" and hour == 12:
            hour = 0
    return hour, minute


def parse_trigger(trigger: str) -> str:
    """Convert a human-readable trigger to a standard 5-field cron expression.

    Supports:
      - ``every 6h`` → ``0 */6 * * *``
      - ``daily at 9am`` → ``0 9 * * *``
      - ``every day at 9am`` → ``0 9 * * *``
      - ``weekdays at 8:30am`` → ``30 8 * * 1-5``
      - ``every 30m`` → ``*/30 * * * *``
      - Standard 5-field cron expressions pass through unchanged.
    """
    cleaned = trigger.strip()

    if _CRON_RE.match(cleaned):
        return cleaned

    m = _INTERVAL_RE.match(cleaned)
    if m:
        value = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("m"):
            return f"*/{value} * * * *"
        if unit.startswith("h"):
            return f"0 */{value} * * *"
        if unit.startswith("d"):
            return f"0 0 */{value} * *"
        if unit.startswith("s"):
            return f"*/{max(1, value // 60)} * * * *"

    m = _DAILY_AT_RE.match(cleaned)
    if m:
        hour, minute = _parse_time_fields(m.group(1), m.group(2), m.group(3))
        return f"{minute} {hour} * * *"

    m = _EVERY_DAY_AT_RE.match(cleaned)
    if m:
        hour, minute = _parse_time_fields(m.group(1), m.group(2), m.group(3))
        return f"{minute} {hour} * * *"

    m = _WEEKDAYS_AT_RE.match(cleaned)
    if m:
        hour, minute = _parse_time_fields(m.group(1), m.group(2), m.group(3))
        return f"{minute} {hour} * * 1-5"

    return cleaned


def compute_next_run(cron_expr: str, after: datetime) -> datetime:
    """Compute the next run time after *after* using *cron_expr*.

    Uses ``croniter`` if available; otherwise provides a simple interval-only
    fallback for ``*/N`` minute/hour patterns.
    """
    if HAS_CRONITER:
        utc_after = after.astimezone(timezone.utc) if after.tzinfo else after.replace(tzinfo=timezone.utc)
        cron = _croniter(cron_expr, utc_after)
        next_dt: datetime = cron.get_next(datetime)
        if next_dt.tzinfo is None:
            next_dt = next_dt.replace(tzinfo=timezone.utc)
        return next_dt

    return _fallback_next_run(cron_expr, after)


def _fallback_next_run(cron_expr: str, after: datetime) -> datetime:
    """Best-effort next-run for common cron patterns without croniter."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"Cannot parse cron expression '{cron_expr}' without croniter. "
            "Install croniter: pip install croniter"
        )

    minute_f, hour_f, dom_f, _mon_f, _dow_f = parts

    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)

    # */N minute intervals
    m = re.match(r"^\*/(\d+)$", minute_f)
    if m and hour_f == "*" and dom_f == "*":
        interval = int(m.group(1))
        delta = timedelta(minutes=interval)
        candidate = after + delta
        candidate = candidate.replace(second=0, microsecond=0)
        return candidate

    # 0 */N hour intervals
    m_h = re.match(r"^\*/(\d+)$", hour_f)
    if minute_f == "0" and m_h and dom_f == "*":
        interval = int(m_h.group(1))
        delta = timedelta(hours=interval)
        candidate = after + delta
        candidate = candidate.replace(minute=0, second=0, microsecond=0)
        return candidate

    # Fixed time daily: M H * * *
    if minute_f.isdigit() and hour_f.isdigit() and dom_f == "*":
        target_min = int(minute_f)
        target_hour = int(hour_f)
        candidate = after.replace(
            hour=target_hour, minute=target_min, second=0, microsecond=0
        )
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate

    raise ValueError(
        f"Cannot parse cron expression '{cron_expr}' without croniter. "
        "Install croniter: pip install croniter"
    )


# ---------------------------------------------------------------------------
# Schedule Store
# ---------------------------------------------------------------------------

_DEFAULT_SCHEDULES_PATH = os.path.expanduser("~/.dan/schedules.json")


class ScheduleStore:
    """Filesystem-backed CRUD for ``ScheduleEntry`` objects."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_SCHEDULES_PATH
        self._entries: dict[str, ScheduleEntry] = {}
        self._loaded = False

    @property
    def path(self) -> str:
        return self._path

    def load(self) -> None:
        self._entries = {}
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    data = json.load(f)
                for item in data:
                    entry = ScheduleEntry.model_validate(item)
                    self._entries[entry.id] = entry
            except Exception:
                logger.warning("Failed to load schedules from %s", self._path)
        self._loaded = True

    def save(self) -> None:
        """Atomic save: write to temp file then rename."""
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        data = [e.model_dump(mode="json") for e in self._entries.values()]
        fd, tmp_path = tempfile.mkstemp(
            dir=str(Path(self._path).parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def add(self, entry: ScheduleEntry) -> None:
        self._ensure_loaded()
        self._entries[entry.id] = entry
        self.save()

    def remove(self, id_or_name: str) -> ScheduleEntry | None:
        self._ensure_loaded()
        entry = self.get(id_or_name)
        if entry:
            del self._entries[entry.id]
            self.save()
        return entry

    def get(self, id_or_name: str) -> ScheduleEntry | None:
        self._ensure_loaded()
        if id_or_name in self._entries:
            return self._entries[id_or_name]
        for e in self._entries.values():
            if e.name.lower() == id_or_name.lower():
                return e
        return None

    def list_all(self) -> list[ScheduleEntry]:
        self._ensure_loaded()
        return list(self._entries.values())

    def update(self, entry: ScheduleEntry) -> None:
        self._ensure_loaded()
        self._entries[entry.id] = entry
        self.save()


# ---------------------------------------------------------------------------
# Schedule History Store
# ---------------------------------------------------------------------------

_DEFAULT_HISTORY_PATH = os.path.expanduser("~/.dan/schedule_history.json")
_MAX_RECORDS_PER_SCHEDULE = 20


class ScheduleHistoryStore:
    """Filesystem-backed run history, keeping the last N records per schedule."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_HISTORY_PATH
        self._records: dict[str, list[dict[str, Any]]] = {}
        self._loaded = False

    def load(self) -> None:
        self._records = {}
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._records = json.load(f)
            except Exception:
                logger.warning("Failed to load schedule history from %s", self._path)
        self._loaded = True

    def save(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(Path(self._path).parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._records, f, indent=2, default=str)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def add_record(self, record: ScheduleRunRecord) -> None:
        self._ensure_loaded()
        key = record.schedule_id
        if key not in self._records:
            self._records[key] = []
        payload = record.model_dump(mode="json")
        if (
            self._records[key]
            and self._records[key][-1].get("started_at") == payload.get("started_at")
        ):
            self._records[key][-1] = payload
        else:
            self._records[key].append(payload)
        self._records[key] = self._records[key][-_MAX_RECORDS_PER_SCHEDULE:]
        self.save()

    def get_history(
        self, schedule_id: str, limit: int = 10
    ) -> list[ScheduleRunRecord]:
        self._ensure_loaded()
        raw = self._records.get(schedule_id, [])
        records = [ScheduleRunRecord.model_validate(r) for r in raw[-limit:]]
        return records


# ---------------------------------------------------------------------------
# TaskScheduler runtime
# ---------------------------------------------------------------------------


class TaskScheduler:
    """Asyncio background task that fires scheduled actions.

    Parameters
    ----------
    store : ScheduleStore
        The schedule store to poll.
    dispatch_fn : Callable
        ``async dispatch_fn(action, trigger_context, delivery_target)``
        or ``async dispatch_fn(action, trigger_context, delivery_target, *, entry=...)``
        — dispatches the scheduled action through the concierge pipeline.
    history_store : ScheduleHistoryStore | None
        Optional history store for recording run results.
    poll_interval : float
        Seconds between schedule checks (default 30).
    lease_manager : ScheduleLeaseManager | None
        If provided, only fire schedules when this process holds the lease.
    authority : SchedulerAuthority
        Whether this is the primary (SERVICE) or fallback (SERVER) scheduler.
    event_bus : Any
        Optional ``GlobalEventBus`` for broadcasting delivery events.
    """

    def __init__(
        self,
        store: ScheduleStore,
        dispatch_fn: Callable[
            [str, TriggerContext, DeliveryTarget], Awaitable[str]
        ],
        history_store: ScheduleHistoryStore | None = None,
        poll_interval: float = 30.0,
        lease_manager: ScheduleLeaseManager | None = None,
        authority: SchedulerAuthority = SchedulerAuthority.SERVER,
        event_bus: Any = None,
    ) -> None:
        self._store = store
        self._dispatch_fn = dispatch_fn
        self._history = history_store
        self._poll_interval = poll_interval
        self._lease_manager = lease_manager
        self._authority = authority
        self._event_bus = event_bus
        self._owner_id = f"{authority.value}-{uuid.uuid4().hex[:8]}"
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._inflight_schedule_ids: set[str] = set()
        self._inflight_tasks: set[asyncio.Task[None]] = set()

    @property
    def authority(self) -> SchedulerAuthority:
        return self._authority

    @property
    def owner_id(self) -> str:
        return self._owner_id

    async def _dispatch_entry(
        self,
        entry: ScheduleEntry,
        trigger_context: TriggerContext,
        delivery_target: DeliveryTarget,
    ) -> str:
        try:
            return await self._dispatch_fn(
                entry.action,
                trigger_context,
                delivery_target,
                entry=entry,
            )
        except TypeError as exc:
            if "unexpected keyword argument 'entry'" not in str(exc):
                raise
        return await self._dispatch_fn(
            entry.action,
            trigger_context,
            delivery_target,
        )

    def _holds_lease(self) -> bool:
        """Return True if this scheduler holds the lease (or no lease is required)."""
        if self._lease_manager is None:
            return True
        lease = self._lease_manager.read_lease()
        if lease is None:
            return False
        return (
            lease.owner_id == self._owner_id
            and lease.pid == os.getpid()
            and not lease.is_stale
        )

    async def start(self) -> None:
        """Start the scheduler background loop."""
        if self._lease_manager is not None:
            lease = self._lease_manager.acquire(self._owner_id)
            if lease is None:
                logger.info(
                    "Scheduler (%s) could not acquire lease — another process holds it",
                    self._authority.value,
                )
                return
            logger.info(
                "Scheduler (%s) acquired lease as %s",
                self._authority.value,
                self._owner_id,
            )
        self._stop_event.clear()
        self._store.load()
        await self._handle_missed_runs()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Signal the loop to stop and wait for it and inflight tasks to finish."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._inflight_tasks:
            await asyncio.gather(*self._inflight_tasks, return_exceptions=True)
            self._inflight_tasks.clear()
        if self._lease_manager is not None:
            self._lease_manager.release(self._owner_id)

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._lease_manager is not None:
                    if not self._holds_lease():
                        self._lease_manager.renew(self._owner_id)
                        if not self._holds_lease():
                            logger.warning("Lost schedule lease, stopping scheduler")
                            break
                await self._check_schedules()
            except Exception:
                logger.exception("Scheduler tick error")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
                break
            except asyncio.TimeoutError:
                pass

    async def _check_schedules(self) -> None:
        now = datetime.now(timezone.utc)
        for entry in self._store.list_all():
            if not entry.enabled:
                continue
            if entry.next_run is None:
                try:
                    cron_expr = parse_trigger(entry.trigger)
                    entry.next_run = compute_next_run(cron_expr, now)
                    self._store.update(entry)
                except Exception:
                    logger.warning("Cannot compute next_run for %s", entry.name)
                continue
            if entry.next_run <= now and entry.id not in self._inflight_schedule_ids:
                self._inflight_schedule_ids.add(entry.id)
                task = asyncio.create_task(self._fire(entry))
                self._inflight_tasks.add(task)
                task.add_done_callback(self._inflight_tasks.discard)

    async def _fire(self, entry: ScheduleEntry) -> None:
        """Fire a single schedule entry."""
        now = datetime.now(timezone.utc)
        record = ScheduleRunRecord(
            schedule_id=entry.id,
            started_at=now,
        )
        if self._history:
            self._history.add_record(record)

        try:
            dispatch_context = entry.trigger_context
            result = await self._dispatch_entry(
                entry,
                dispatch_context,
                entry.delivery_target,
            )
            record.result_summary = str(result)[:500] if result else ""
            record.status = (
                "running"
                if _dispatch_result_is_inflight_workflow(record.result_summary)
                else "success"
            )
            await deliver_result(
                entry, record.result_summary, event_bus=self._event_bus
            )
        except Exception as exc:
            record.status = "error"
            record.error = str(exc)[:500]
            logger.exception("Schedule %s (%s) failed", entry.name, entry.id)
            await apply_fallback_policy(
                entry,
                record.error or "Unknown error",
                event_bus=self._event_bus,
            )
        finally:
            if record.status != "running":
                record.completed_at = datetime.now(timezone.utc)
            if self._history:
                self._history.add_record(record)

            entry.last_run = now
            try:
                cron_expr = parse_trigger(entry.trigger)
                entry.next_run = compute_next_run(cron_expr, now)
            except Exception:
                entry.next_run = None
            self._store.update(entry)
            self._inflight_schedule_ids.discard(entry.id)

    async def _handle_missed_runs(self) -> None:
        """On startup, fire once for any overdue schedules with on_missed='run_once'."""
        now = datetime.now(timezone.utc)
        for entry in self._store.list_all():
            if not entry.enabled:
                continue
            if entry.next_run is not None and entry.next_run < now:
                if entry.on_missed == "run_once":
                    if entry.id in self._inflight_schedule_ids:
                        continue
                    self._inflight_schedule_ids.add(entry.id)
                    logger.info(
                        "Missed run for schedule %s (due %s), firing once",
                        entry.name,
                        entry.next_run.isoformat(),
                    )
                    task = asyncio.create_task(self._fire(entry))
                    self._inflight_tasks.add(task)
                    task.add_done_callback(self._inflight_tasks.discard)
                else:
                    try:
                        cron_expr = parse_trigger(entry.trigger)
                        entry.next_run = compute_next_run(cron_expr, now)
                        self._store.update(entry)
                    except Exception:
                        pass


# ---------------------------------------------------------------------------
# Natural language schedule creation (Task 3-5)
# ---------------------------------------------------------------------------

_NL_REMIND_RE = re.compile(
    r"^remind\s+me\s+to\s+(.+?)\s+(every\s+.+|daily|weekly|monthly)$",
    re.IGNORECASE,
)
_NL_DO_FREQ_RE = re.compile(
    r"^(?:do|run|check|send|generate|update)\s+(.+?)\s+(daily|weekly|monthly|every\s+.+)$",
    re.IGNORECASE,
)
_NL_RUN_AT_RE = re.compile(
    r"^(?:run|do|check|send)\s+(.+?)\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)$",
    re.IGNORECASE,
)
_NL_EVERY_N_RE = re.compile(
    r"^(.+?)\s+(every\s+\d+\s+(?:hours?|minutes?|days?|h|m|d))$",
    re.IGNORECASE,
)

_FREQ_MAP = {
    "daily": "every day at 9am",
    "weekly": "every 7d",
    "monthly": "every 30d",
}


def parse_nl_schedule(text: str) -> tuple[str, str] | None:
    """Extract ``(action, trigger)`` from natural language scheduling intent.

    Returns *None* if the text does not match any known NL pattern.
    Designed to be called from the concierge when a scheduling intent is detected.
    """
    text = text.strip()

    m = _NL_REMIND_RE.match(text)
    if m:
        action = m.group(1).strip()
        trigger = _FREQ_MAP.get(m.group(2).lower(), m.group(2).strip())
        return action, trigger

    m = _NL_EVERY_N_RE.match(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    m = _NL_RUN_AT_RE.match(text)
    if m:
        action = m.group(1).strip()
        trigger = f"daily at {m.group(2).strip()}"
        return action, trigger

    m = _NL_DO_FREQ_RE.match(text)
    if m:
        action = m.group(1).strip()
        trigger = _FREQ_MAP.get(m.group(2).lower(), m.group(2).strip())
        return action, trigger

    return None


# ---------------------------------------------------------------------------
# Delivery routing (Task 4-1) + fallback policy (Task 4-2)
# ---------------------------------------------------------------------------


async def deliver_result(
    entry: ScheduleEntry,
    result: str,
    *,
    event_bus: Any = None,
) -> None:
    """Route a schedule result to the target surface via the event bus.

    Emits a ``schedule_result_ready`` event that adapters (Telegram, CLI, etc.)
    can subscribe to.
    """
    now_ts = datetime.now(timezone.utc).timestamp()
    result_text = result[:2000] if result else ""
    event = {
        "event_type": "schedule_result_ready",
        "schedule_id": entry.id,
        "schedule_name": entry.name,
        "surface": entry.delivery_target.surface,
        "surface_id": entry.delivery_target.conversation_key,
        "conversation_key": entry.delivery_target.conversation_key,
        "user_id": entry.delivery_target.user_id,
        "project_id": entry.delivery_target.project_id,
        "thread_key": entry.delivery_target.thread_key,
        "result": result_text,
        "timestamp": now_ts,
        "data": {
            "status": "ready",
            "result": result_text,
            "fallback": False,
        },
    }
    if event_bus is not None:
        try:
            event_bus.broadcast(event)
        except Exception:
            logger.warning(
                "Failed to broadcast schedule_result_ready for %s", entry.name
            )
            await apply_fallback_policy(
                entry,
                f"Delivery failed: {result[:200]}",
                event_bus=event_bus,
            )
            return
    logger.debug(
        "Delivered result for schedule %s to surface=%s",
        entry.name,
        entry.delivery_target.surface,
    )


async def apply_fallback_policy(
    entry: ScheduleEntry,
    error_message: str,
    *,
    event_bus: Any = None,
) -> None:
    """Apply ``delivery_target.fallback_policy`` when the target is unreachable.

    Policies:
    - ``store_and_notify``: persist result and emit a notification event.
    - ``private_surface``: route to the user's private/default surface.
    - ``drop``: discard silently (log only).
    """
    policy = entry.delivery_target.fallback_policy
    now_ts = datetime.now(timezone.utc).timestamp()

    if policy == "drop":
        logger.info(
            "Dropping failed result for schedule %s (policy=drop): %s",
            entry.name,
            error_message[:100],
        )
        return

    if policy == "store_and_notify":
        result_text = f"[fallback] Schedule '{entry.name}' failed: {error_message[:500]}"
        event = {
            "event_type": "schedule_result_ready",
            "schedule_id": entry.id,
            "schedule_name": entry.name,
            "surface": "notification",
            "surface_id": entry.delivery_target.conversation_key,
            "conversation_key": entry.delivery_target.conversation_key,
            "user_id": entry.delivery_target.user_id,
            "project_id": entry.delivery_target.project_id,
            "thread_key": entry.delivery_target.thread_key,
            "result": result_text,
            "fallback": True,
            "timestamp": now_ts,
            "data": {
                "status": "error",
                "result": result_text,
                "error": error_message[:500],
                "fallback": True,
            },
        }
        if event_bus is not None:
            try:
                event_bus.broadcast(event)
            except Exception:
                logger.warning("Fallback notification broadcast failed for %s", entry.name)
        logger.info(
            "Stored fallback notification for schedule %s: %s",
            entry.name,
            error_message[:100],
        )
        return

    if policy == "private_surface":
        result_text = f"[private fallback] Schedule '{entry.name}' failed: {error_message[:500]}"
        event = {
            "event_type": "schedule_result_ready",
            "schedule_id": entry.id,
            "schedule_name": entry.name,
            "surface": "private",
            "surface_id": entry.delivery_target.conversation_key,
            "conversation_key": entry.delivery_target.conversation_key,
            "user_id": entry.delivery_target.user_id,
            "project_id": entry.delivery_target.project_id,
            "thread_key": entry.delivery_target.thread_key,
            "result": result_text,
            "fallback": True,
            "timestamp": now_ts,
            "data": {
                "status": "error",
                "result": result_text,
                "error": error_message[:500],
                "fallback": True,
            },
        }
        if event_bus is not None:
            try:
                event_bus.broadcast(event)
            except Exception:
                logger.warning("Private surface fallback failed for %s", entry.name)
        logger.info(
            "Routed to private surface for schedule %s: %s",
            entry.name,
            error_message[:100],
        )
        return


# ---------------------------------------------------------------------------
# Service wiring hook (Task 2-4)
# ---------------------------------------------------------------------------


def create_service_scheduler(
    store: ScheduleStore,
    dispatch_fn: Callable[[str, TriggerContext, DeliveryTarget], Awaitable[str]],
    history_store: ScheduleHistoryStore | None = None,
    lease_path: str | None = None,
    event_bus: Any = None,
) -> TaskScheduler:
    """Factory for the dan-service daemon to create a scheduler with SERVICE authority.

    Usage in a future ``src/dan/service/`` entry point::

        scheduler = create_service_scheduler(store, dispatch_fn, history_store)
        await scheduler.start()
    """
    lease_manager = ScheduleLeaseManager(path=lease_path)
    return TaskScheduler(
        store=store,
        dispatch_fn=dispatch_fn,
        history_store=history_store,
        lease_manager=lease_manager,
        authority=SchedulerAuthority.SERVICE,
        event_bus=event_bus,
    )


def create_server_scheduler(
    store: ScheduleStore,
    dispatch_fn: Callable[[str, TriggerContext, DeliveryTarget], Awaitable[str]],
    history_store: ScheduleHistoryStore | None = None,
    lease_path: str | None = None,
    event_bus: Any = None,
) -> TaskScheduler:
    """Factory for dan-serve to create a scheduler with SERVER (fallback) authority.

    Only starts if no service daemon holds the lease.
    """
    lease_manager = ScheduleLeaseManager(path=lease_path)
    return TaskScheduler(
        store=store,
        dispatch_fn=dispatch_fn,
        history_store=history_store,
        lease_manager=lease_manager,
        authority=SchedulerAuthority.SERVER,
        event_bus=event_bus,
    )


# ---------------------------------------------------------------------------
# Chat command handler
# ---------------------------------------------------------------------------


def parse_schedule_add(text: str) -> tuple[str, str]:
    """Extract ``(action, trigger)`` from an add command.

    Expects: ``"<action>" <trigger>`` — action in quotes, trigger is the rest.
    Falls back to splitting on ``every`` / ``daily`` / ``weekdays`` / cron.
    """
    text = text.strip()

    # Quoted action
    m = re.match(r'^"(.+?)"\s+(.+)$', text)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    m = re.match(r"^'(.+?)'\s+(.+)$", text)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # Unquoted: split on known trigger keywords
    for keyword in ("every ", "daily ", "weekday"):
        idx = text.lower().find(keyword)
        if idx > 0:
            return text[:idx].strip(), text[idx:].strip()

    parts = text.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]

    return text, ""


def handle_schedule_command(
    text: str,
    store: ScheduleStore,
    history_store: ScheduleHistoryStore | None = None,
    *,
    default_trigger_context: TriggerContext | None = None,
    default_delivery_target: DeliveryTarget | None = None,
    default_workflow_id: str | None = None,
) -> str:
    """Dispatch ``/schedule`` subcommands.

    Returns a human-readable response string.
    """
    text = text.strip()
    if text.lower().startswith("/schedule"):
        text = text[len("/schedule"):].strip()

    parts = text.split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "add":
        return _cmd_add(
            rest,
            store,
            trigger_context=default_trigger_context,
            delivery_target=default_delivery_target,
        )
    if sub == "workflow":
        return _cmd_workflow(
            rest,
            store,
            trigger_context=default_trigger_context,
            delivery_target=default_delivery_target,
            default_workflow_id=default_workflow_id,
        )
    if sub == "list":
        return _cmd_list(store)
    if sub == "remove":
        return _cmd_remove(rest, store)
    if sub == "pause":
        return _cmd_pause(rest, store)
    if sub == "resume":
        return _cmd_resume(rest, store)
    if sub == "history":
        return _cmd_history(rest, store, history_store)

    nl_result = parse_nl_schedule(text)
    if nl_result is not None:
        action, trigger = nl_result
        return _cmd_add(
            f'"{action}" {trigger}',
            store,
            trigger_context=default_trigger_context,
            delivery_target=default_delivery_target,
        )

    return (
        "Usage: /schedule <add|workflow|list|remove|pause|resume|history> [args]\n"
        "  add \"<action>\" <trigger>\n"
        "  workflow <workflow_id|current> <trigger> [--input key=value ...] [--profile <name>]\n"
        "  list\n"
        "  remove <id|name>\n"
        "  pause <id|name>\n"
        "  resume <id|name>\n"
        "  history <id|name>"
    )


def _cmd_add(
    text: str,
    store: ScheduleStore,
    *,
    trigger_context: TriggerContext | None = None,
    delivery_target: DeliveryTarget | None = None,
) -> str:
    if not text:
        return 'Usage: /schedule add "<action>" <trigger>'

    action, trigger = parse_schedule_add(text)
    if not action or not trigger:
        return 'Usage: /schedule add "<action>" <trigger>'

    try:
        cron_expr = parse_trigger(trigger)
    except Exception as exc:
        return f"Invalid trigger: {exc}"

    now = datetime.now(timezone.utc)
    try:
        next_run = compute_next_run(cron_expr, now)
    except Exception:
        next_run = None

    entry = ScheduleEntry(
        name=action,
        trigger=trigger,
        action=action,
        next_run=next_run,
        trigger_context=trigger_context or TriggerContext(),
        delivery_target=delivery_target or DeliveryTarget(),
    )
    store.add(entry)
    next_str = next_run.strftime("%Y-%m-%d %H:%M UTC") if next_run else "unknown"
    return (
        f"Scheduled: **{action}**\n"
        f"Trigger: `{trigger}` → `{cron_expr}`\n"
        f"Next run: {next_str}\n"
        f"ID: `{entry.id}`"
    )


def _cmd_workflow(
    text: str,
    store: ScheduleStore,
    *,
    trigger_context: TriggerContext | None = None,
    delivery_target: DeliveryTarget | None = None,
    default_workflow_id: str | None = None,
) -> str:
    raw = str(text or "").strip()
    if not raw:
        return (
            "Usage: /schedule workflow <workflow_id|current> <trigger> "
            "[--input key=value ...] [--profile <name>]"
        )

    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        return f"Invalid workflow schedule command: {exc}"
    if len(tokens) < 2:
        return (
            "Usage: /schedule workflow <workflow_id|current> <trigger> "
            "[--input key=value ...] [--profile <name>]"
        )

    workflow_ref = tokens[0].strip()
    trigger_tokens: list[str] = []
    workflow_inputs: dict[str, Any] = {}
    workflow_run_policy: dict[str, Any] | None = None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"--input", "--profile"} or token.startswith("--input=") or token.startswith("--profile="):
            break
        trigger_tokens.append(token)
        index += 1

    trigger = " ".join(trigger_tokens).strip()
    if not trigger:
        return (
            "Usage: /schedule workflow <workflow_id|current> <trigger> "
            "[--input key=value ...] [--profile <name>]"
        )

    while index < len(tokens):
        token = tokens[index]
        if token == "--input":
            index += 1
            if index >= len(tokens):
                return "Missing value after `--input`; expected `key=value`."
            assignment = tokens[index]
            index += 1
            error = _apply_workflow_input_assignment(workflow_inputs, assignment)
            if error:
                return error
            continue
        if token.startswith("--input="):
            index += 1
            error = _apply_workflow_input_assignment(
                workflow_inputs,
                token.split("=", 1)[1],
            )
            if error:
                return error
            continue
        if token == "--profile":
            index += 1
            if index >= len(tokens):
                return "Missing value after `--profile`."
            profile = tokens[index].strip()
            index += 1
        elif token.startswith("--profile="):
            profile = token.split("=", 1)[1].strip()
            index += 1
        else:
            return f"Unknown workflow schedule option: `{token}`"
        if not profile:
            return "Run profile cannot be empty."
        workflow_run_policy = {"profile": profile}

    workflow_id = workflow_ref
    if workflow_ref.lower() in {"current", "this"}:
        workflow_id = str(default_workflow_id or "").strip()
        if not workflow_id:
            return "No current workflow is available. Use `/schedule workflow <workflow_id> <trigger>`."

    if not workflow_id:
        return "Workflow ID is required."

    try:
        cron_expr = parse_trigger(trigger)
    except Exception as exc:
        return f"Invalid trigger: {exc}"

    now = datetime.now(timezone.utc)
    try:
        next_run = compute_next_run(cron_expr, now)
    except Exception:
        next_run = None

    entry = ScheduleEntry(
        name=f"Run workflow {workflow_id}",
        trigger=trigger,
        action=f"run workflow {workflow_id}",
        next_run=next_run,
        trigger_context=trigger_context or TriggerContext(),
        delivery_target=delivery_target or DeliveryTarget(),
        workflow_id=workflow_id,
        workflow_inputs=workflow_inputs,
        workflow_run_policy=workflow_run_policy,
    )
    store.add(entry)
    next_str = next_run.strftime("%Y-%m-%d %H:%M UTC") if next_run else "unknown"
    summary = (
        f"Scheduled workflow: **{workflow_id}**\n"
        f"Trigger: `{trigger}` → `{cron_expr}`\n"
        f"Next run: {next_str}\n"
        f"ID: `{entry.id}`"
    )
    if workflow_inputs:
        summary += f"\nInputs: {', '.join(sorted(workflow_inputs))}"
    if workflow_run_policy and workflow_run_policy.get("profile"):
        summary += f"\nRun profile: `{workflow_run_policy['profile']}`"
    return summary


def _coerce_schedule_input_value(raw_value: str) -> Any:
    value = raw_value.strip()
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value
    if re.fullmatch(r"-?\d+\.\d+", value):
        try:
            return float(value)
        except ValueError:
            return value
    if value[:1] in {"{", "["}:
        try:
            return json.loads(value)
        except Exception:
            return raw_value
    return raw_value


def _apply_workflow_input_assignment(target: dict[str, Any], assignment: str) -> str | None:
    key, sep, raw_value = assignment.partition("=")
    key = key.strip()
    if not sep or not key:
        return "Workflow inputs must use `key=value` syntax."
    target[key] = _coerce_schedule_input_value(raw_value)
    return None


def _cmd_list(store: ScheduleStore) -> str:
    entries = store.list_all()
    if not entries:
        return "No scheduled tasks."

    lines = ["**Scheduled Tasks**\n"]
    lines.append(f"{'Name':<25} {'Trigger':<20} {'Next Run':<22} {'Status'}")
    lines.append("-" * 80)
    for e in entries:
        next_str = e.next_run.strftime("%Y-%m-%d %H:%M UTC") if e.next_run else "—"
        status = "enabled" if e.enabled else "paused"
        lines.append(f"{e.name:<25} {e.trigger:<20} {next_str:<22} {status}")
    return "\n".join(lines)


def _cmd_remove(id_or_name: str, store: ScheduleStore) -> str:
    if not id_or_name:
        return "Usage: /schedule remove <id|name>"
    entry = store.remove(id_or_name.strip())
    if entry:
        return f"Removed schedule: **{entry.name}** (`{entry.id}`)"
    return f"Schedule not found: `{id_or_name}`"


def _cmd_pause(id_or_name: str, store: ScheduleStore) -> str:
    if not id_or_name:
        return "Usage: /schedule pause <id|name>"
    entry = store.get(id_or_name.strip())
    if not entry:
        return f"Schedule not found: `{id_or_name}`"
    entry.enabled = False
    store.update(entry)
    return f"Paused: **{entry.name}**"


def _cmd_resume(id_or_name: str, store: ScheduleStore) -> str:
    if not id_or_name:
        return "Usage: /schedule resume <id|name>"
    entry = store.get(id_or_name.strip())
    if not entry:
        return f"Schedule not found: `{id_or_name}`"
    entry.enabled = True
    now = datetime.now(timezone.utc)
    try:
        cron_expr = parse_trigger(entry.trigger)
        entry.next_run = compute_next_run(cron_expr, now)
    except Exception:
        pass
    store.update(entry)
    next_str = entry.next_run.strftime("%Y-%m-%d %H:%M UTC") if entry.next_run else "unknown"
    return f"Resumed: **{entry.name}** — next run: {next_str}"


def _cmd_history(
    id_or_name: str,
    store: ScheduleStore,
    history_store: ScheduleHistoryStore | None,
) -> str:
    if not id_or_name:
        return "Usage: /schedule history <id|name>"
    entry = store.get(id_or_name.strip())
    if not entry:
        return f"Schedule not found: `{id_or_name}`"
    if not history_store:
        return "No history store configured."

    records = history_store.get_history(entry.id)
    if not records:
        return f"No run history for **{entry.name}**."

    lines = [f"**Run History: {entry.name}**\n"]
    for r in reversed(records):
        started = r.started_at.strftime("%Y-%m-%d %H:%M")
        dur = ""
        if r.completed_at:
            elapsed = (r.completed_at - r.started_at).total_seconds()
            dur = f" ({elapsed:.1f}s)"
        status_icon = {"success": "OK", "error": "ERR", "running": "..."}
        lines.append(
            f"  {started} [{status_icon.get(r.status, r.status)}]{dur}"
            f" {r.result_summary[:60] if r.result_summary else ''}"
        )
        if r.error:
            lines.append(f"    Error: {r.error[:80]}")
    return "\n".join(lines)
