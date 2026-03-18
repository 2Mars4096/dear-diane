"""Unified telemetry: single event model, append-only stores, query + export.

Plan: 31-20-unified-telemetry.md
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment knobs
# ---------------------------------------------------------------------------


def _env_retention_days() -> int:
    raw = str(os.environ.get("DAN_TELEMETRY_RETENTION_DAYS", "90") or "").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(
            "Invalid DAN_TELEMETRY_RETENTION_DAYS=%r; falling back to 90 days",
            raw,
        )
        return 90


def _default_telemetry_db_path() -> str:
    configured = str(os.environ.get("DAN_TELEMETRY_DB", "") or "").strip()
    if configured:
        return configured
    return str(Path.home() / ".dan" / "telemetry.db")

EventType = Literal[
    "chat_turn",
    "fast_command",
    "workflow_run",
    "workflow_node",
    "session_complete",
    "tiered_dispatch_complete",
    "guard_check",
    "classification",
    "memory_retrieval",
    "tool_call",
    "intent_extraction",
    "parameter_decision",
]

# Filterable columns used by query/aggregate.
_FILTER_COLUMNS = (
    "project_id", "task_id", "surface", "session_id", "model", "event_type",
    "intent", "tool_name", "run_id", "parent_event_id",
)

# Columns valid for GROUP BY in aggregate().
_GROUPABLE_COLUMNS = {
    "project_id", "task_id", "surface", "model", "event_type",
    "intent", "tool_name", "run_id", "node_id",
}

# Virtual groupable columns (computed, not stored).
_VIRTUAL_GROUP_COLUMNS = {"day"}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


def generate_event_id() -> str:
    return uuid.uuid4().hex[:16]


class TelemetryEvent(BaseModel):
    id: str = Field(default_factory=generate_event_id)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: EventType

    # Scope
    project_id: str | None = None
    task_id: str | None = None
    surface: str | None = None
    session_id: str | None = None

    # Correlation
    parent_event_id: str | None = None
    run_id: str | None = None
    graph_id: str | None = None

    # Identity
    model: str | None = None
    node_id: str | None = None
    intent: str | None = None
    tool_name: str | None = None

    # Metrics
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    duration_ms: float = 0.0

    # Quality
    success: bool = True
    retry_count: int = 0
    guard_action: str | None = None

    # Self-adaptive behavior: parameter-outcome linking (31-22 task 4-1)
    parameter_key: str | None = None
    parameter_value: str | None = None

    # Extensible context
    metadata: dict[str, Any] = Field(default_factory=dict)


class TelemetryQuery(BaseModel):
    project_id: str | None = None
    task_id: str | None = None
    surface: str | None = None
    session_id: str | None = None
    model: str | None = None
    event_type: str | None = None
    intent: str | None = None
    tool_name: str | None = None
    run_id: str | None = None
    parent_event_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 1000


class AggregateRow(BaseModel):
    group_key: dict[str, str] = Field(default_factory=dict)
    count: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    total_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    min_duration_ms: float = 0.0
    max_duration_ms: float = 0.0
    success_rate: float = 0.0


# ---------------------------------------------------------------------------
# Abstract store interface
# ---------------------------------------------------------------------------


class TelemetryStore:
    """Base class / interface for telemetry stores."""

    async def record(self, event: TelemetryEvent) -> None:
        raise NotImplementedError

    async def query(self, filters: TelemetryQuery | None = None) -> list[TelemetryEvent]:
        raise NotImplementedError

    async def aggregate(
        self, filters: TelemetryQuery | None = None, group_by: list[str] | None = None,
    ) -> list[AggregateRow]:
        raise NotImplementedError

    async def export_jsonl(self, filters: TelemetryQuery | None = None, *, path: Path | None = None) -> int:
        raise NotImplementedError

    async def export_csv(self, filters: TelemetryQuery | None = None, *, path: Path | None = None) -> int:
        raise NotImplementedError

    async def prune(self, older_than_days: int | None = None) -> int:
        raise NotImplementedError

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Null store (DAN_TELEMETRY=0)
# ---------------------------------------------------------------------------


class NullTelemetryStore(TelemetryStore):
    """No-op store when telemetry is disabled."""

    async def record(self, event: TelemetryEvent) -> None:
        pass

    async def query(self, filters: TelemetryQuery | None = None) -> list[TelemetryEvent]:
        return []

    async def aggregate(
        self, filters: TelemetryQuery | None = None, group_by: list[str] | None = None,
    ) -> list[AggregateRow]:
        return []

    async def export_jsonl(self, filters: TelemetryQuery | None = None, *, path: Path | None = None) -> int:
        return 0

    async def export_csv(self, filters: TelemetryQuery | None = None, *, path: Path | None = None) -> int:
        return 0

    async def prune(self, older_than_days: int | None = None) -> int:
        return 0


# ---------------------------------------------------------------------------
# In-memory store (for tests)
# ---------------------------------------------------------------------------


class InMemoryTelemetryStore(TelemetryStore):
    """List-backed store for tests and short-lived sessions."""

    def __init__(self) -> None:
        self._events: list[TelemetryEvent] = []

    async def record(self, event: TelemetryEvent) -> None:
        self._events.append(event)

    async def query(self, filters: TelemetryQuery | None = None) -> list[TelemetryEvent]:
        return list(self._apply_filters(filters))

    async def aggregate(
        self, filters: TelemetryQuery | None = None, group_by: list[str] | None = None,
    ) -> list[AggregateRow]:
        group_by = group_by or ["event_type"]
        filtered = list(self._apply_filters(filters))
        buckets: dict[tuple[tuple[str, str], ...], list[TelemetryEvent]] = {}
        for ev in filtered:
            key_parts: list[tuple[str, str]] = []
            for col in group_by:
                if col == "day":
                    key_parts.append(("day", ev.timestamp.date().isoformat()))
                else:
                    key_parts.append((col, getattr(ev, col, None) or ""))
            key = tuple(key_parts)
            buckets.setdefault(key, []).append(ev)
        rows: list[AggregateRow] = []
        for key, events in buckets.items():
            durations = [e.duration_ms for e in events]
            successes = sum(1 for e in events if e.success)
            rows.append(AggregateRow(
                group_key=dict(key),
                count=len(events),
                total_tokens=sum(e.total_tokens for e in events),
                total_cost=sum(e.estimated_cost for e in events),
                total_duration_ms=sum(durations),
                avg_duration_ms=sum(durations) / len(durations) if durations else 0,
                min_duration_ms=min(durations) if durations else 0,
                max_duration_ms=max(durations) if durations else 0,
                success_rate=successes / len(events) if events else 0,
            ))
        return rows

    async def export_jsonl(self, filters: TelemetryQuery | None = None, *, path: Path | None = None) -> int:
        events = list(self._apply_filters(filters))
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                for ev in events:
                    f.write(ev.model_dump_json() + "\n")
        return len(events)

    async def export_csv(self, filters: TelemetryQuery | None = None, *, path: Path | None = None) -> int:
        events = list(self._apply_filters(filters))
        if path is not None and events:
            path.parent.mkdir(parents=True, exist_ok=True)
            fields = list(events[0].model_dump().keys())
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for ev in events:
                    row = ev.model_dump()
                    row["metadata"] = json.dumps(row.get("metadata", {}))
                    row["timestamp"] = str(row["timestamp"])
                    writer.writerow(row)
        return len(events)

    async def prune(self, older_than_days: int | None = None) -> int:
        days = older_than_days if older_than_days is not None else _env_retention_days()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        before = len(self._events)
        self._events = [e for e in self._events if e.timestamp >= cutoff]
        return before - len(self._events)

    def _apply_filters(self, filters: TelemetryQuery | None) -> list[TelemetryEvent]:
        if filters is None:
            return self._events[:]
        result: list[TelemetryEvent] = []
        for ev in self._events:
            if not self._matches(ev, filters):
                continue
            result.append(ev)
            if len(result) >= filters.limit:
                break
        return result

    @staticmethod
    def _matches(ev: TelemetryEvent, q: TelemetryQuery) -> bool:
        for col in _FILTER_COLUMNS:
            val = getattr(q, col, None)
            if val is not None and getattr(ev, col, None) != val:
                return False
        if q.since is not None and ev.timestamp < q.since:
            return False
        if q.until is not None and ev.timestamp >= q.until:
            return False
        return True


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    project_id TEXT,
    task_id TEXT,
    surface TEXT,
    session_id TEXT,
    parent_event_id TEXT,
    run_id TEXT,
    graph_id TEXT,
    model TEXT,
    node_id TEXT,
    intent TEXT,
    tool_name TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost REAL DEFAULT 0.0,
    duration_ms REAL DEFAULT 0.0,
    success INTEGER DEFAULT 1,
    retry_count INTEGER DEFAULT 0,
    guard_action TEXT,
    parameter_key TEXT,
    parameter_value TEXT,
    metadata TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_project_ts ON events(project_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_surface_ts ON events(surface, timestamp);
CREATE INDEX IF NOT EXISTS idx_model_ts ON events(model, timestamp);
CREATE INDEX IF NOT EXISTS idx_event_type_ts ON events(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_run_id ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_parent ON events(parent_event_id);
"""

_INSERT_SQL = """
INSERT OR IGNORE INTO events (
    id, timestamp, event_type,
    project_id, task_id, surface, session_id,
    parent_event_id, run_id, graph_id,
    model, node_id, intent, tool_name,
    prompt_tokens, completion_tokens, total_tokens,
    estimated_cost, duration_ms,
    success, retry_count, guard_action,
    parameter_key, parameter_value,
    metadata
) VALUES (
    :id, :timestamp, :event_type,
    :project_id, :task_id, :surface, :session_id,
    :parent_event_id, :run_id, :graph_id,
    :model, :node_id, :intent, :tool_name,
    :prompt_tokens, :completion_tokens, :total_tokens,
    :estimated_cost, :duration_ms,
    :success, :retry_count, :guard_action,
    :parameter_key, :parameter_value,
    :metadata
)
"""


def _event_to_row(ev: TelemetryEvent) -> dict[str, Any]:
    d = ev.model_dump()
    d["timestamp"] = ev.timestamp.isoformat()
    d["success"] = 1 if ev.success else 0
    d["metadata"] = json.dumps(d.get("metadata", {}))
    return d


def _row_to_event(row: dict[str, Any]) -> TelemetryEvent:
    row = dict(row)
    row["success"] = bool(row.get("success", 1))
    meta = row.get("metadata", "{}")
    row["metadata"] = json.loads(meta) if isinstance(meta, str) else meta
    ts = row.get("timestamp", "")
    if isinstance(ts, str):
        row["timestamp"] = datetime.fromisoformat(ts)
    return TelemetryEvent(**row)


class SQLiteTelemetryStore(TelemetryStore):
    """SQLite-backed append-only telemetry store."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = _default_telemetry_db_path()
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._record_fail_warned = False
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        try:
            conn.executescript(_SCHEMA_SQL)
            existing_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()
            }
            for column_name in ("parameter_key", "parameter_value"):
                if column_name not in existing_columns:
                    conn.execute(f"ALTER TABLE events ADD COLUMN {column_name} TEXT")
            conn.commit()
        finally:
            conn.close()

    # -- Sync helpers (run in thread) ----------------------------------------

    def _record_sync(self, event: TelemetryEvent) -> None:
        conn = self._get_conn()
        try:
            with self._lock:
                conn.execute(_INSERT_SQL, _event_to_row(event))
                conn.commit()
        finally:
            conn.close()

    def _query_sync(self, filters: TelemetryQuery | None) -> list[TelemetryEvent]:
        sql, params = self._build_select(filters)
        conn = self._get_conn()
        try:
            with self._lock:
                rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [_row_to_event(dict(r)) for r in rows]

    def _aggregate_sync(
        self, filters: TelemetryQuery | None, group_by: list[str] | None,
    ) -> list[AggregateRow]:
        group_by = group_by or ["event_type"]
        all_valid = _GROUPABLE_COLUMNS | _VIRTUAL_GROUP_COLUMNS
        valid = [c for c in group_by if c in all_valid]
        if not valid:
            valid = ["event_type"]
        select_exprs: list[str] = []
        group_exprs: list[str] = []
        for c in valid:
            if c == "day":
                select_exprs.append("DATE(timestamp) AS day")
                group_exprs.append("DATE(timestamp)")
            else:
                select_exprs.append(c)
                group_exprs.append(c)
        select_cols = ", ".join(select_exprs)
        group_cols = ", ".join(group_exprs)

        where, params = self._build_where(filters)
        sql = f"""
            SELECT {select_cols},
                   COUNT(*) as cnt,
                   SUM(total_tokens) as sum_tokens,
                   SUM(estimated_cost) as sum_cost,
                   SUM(duration_ms) as sum_dur,
                   AVG(duration_ms) as avg_dur,
                   MIN(duration_ms) as min_dur,
                   MAX(duration_ms) as max_dur,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes
            FROM events
            {where}
            GROUP BY {group_cols}
            ORDER BY sum_cost DESC
        """
        conn = self._get_conn()
        try:
            with self._lock:
                rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        result: list[AggregateRow] = []
        for r in rows:
            rd = dict(r)
            cnt = rd["cnt"] or 0
            result.append(AggregateRow(
                group_key={c: str(rd.get(c, "") or "") for c in valid},
                count=cnt,
                total_tokens=rd.get("sum_tokens", 0) or 0,
                total_cost=rd.get("sum_cost", 0.0) or 0.0,
                total_duration_ms=rd.get("sum_dur", 0.0) or 0.0,
                avg_duration_ms=rd.get("avg_dur", 0.0) or 0.0,
                min_duration_ms=rd.get("min_dur", 0.0) or 0.0,
                max_duration_ms=rd.get("max_dur", 0.0) or 0.0,
                success_rate=(rd.get("successes", 0) or 0) / cnt if cnt else 0.0,
            ))
        return result

    def _export_jsonl_sync(self, filters: TelemetryQuery | None, path: Path) -> int:
        events = self._query_sync(filters)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for ev in events:
                f.write(ev.model_dump_json() + "\n")
        return len(events)

    def _export_csv_sync(self, filters: TelemetryQuery | None, path: Path) -> int:
        events = self._query_sync(filters)
        if not events:
            return 0
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = list(events[0].model_dump().keys())
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for ev in events:
                row = ev.model_dump()
                row["metadata"] = json.dumps(row.get("metadata", {}))
                row["timestamp"] = str(row["timestamp"])
                writer.writerow(row)
        return len(events)

    def _prune_sync(self, older_than_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        conn = self._get_conn()
        try:
            with self._lock:
                cur = conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
                conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # -- Async public API ----------------------------------------------------

    async def record(self, event: TelemetryEvent) -> None:
        try:
            await asyncio.to_thread(self._record_sync, event)
        except Exception:
            if not self._record_fail_warned:
                logger.warning("Telemetry record failed (first occurrence)", exc_info=True)
                self._record_fail_warned = True
            else:
                logger.debug("Telemetry record failed", exc_info=True)

    async def query(self, filters: TelemetryQuery | None = None) -> list[TelemetryEvent]:
        return await asyncio.to_thread(self._query_sync, filters)

    async def aggregate(
        self, filters: TelemetryQuery | None = None, group_by: list[str] | None = None,
    ) -> list[AggregateRow]:
        return await asyncio.to_thread(self._aggregate_sync, filters, group_by)

    async def export_jsonl(self, filters: TelemetryQuery | None = None, *, path: Path | None = None) -> int:
        if path is None:
            path = Path.home() / ".dan" / "exports" / "telemetry.jsonl"
        return await asyncio.to_thread(self._export_jsonl_sync, filters, path)

    async def export_csv(self, filters: TelemetryQuery | None = None, *, path: Path | None = None) -> int:
        if path is None:
            path = Path.home() / ".dan" / "exports" / "telemetry.csv"
        return await asyncio.to_thread(self._export_csv_sync, filters, path)

    async def prune(self, older_than_days: int | None = None) -> int:
        days = older_than_days if older_than_days is not None else _env_retention_days()
        return await asyncio.to_thread(self._prune_sync, days)

    async def close(self) -> None:
        return None

    # -- SQL helpers ---------------------------------------------------------

    @staticmethod
    def _build_where(filters: TelemetryQuery | None) -> tuple[str, list[Any]]:
        if filters is None:
            return "", []
        clauses: list[str] = []
        params: list[Any] = []
        for col in _FILTER_COLUMNS:
            val = getattr(filters, col, None)
            if val is not None:
                clauses.append(f"{col} = ?")
                params.append(val)
        if filters.since is not None:
            clauses.append("timestamp >= ?")
            params.append(filters.since.isoformat())
        if filters.until is not None:
            clauses.append("timestamp < ?")
            params.append(filters.until.isoformat())
        if not clauses:
            return "", []
        return "WHERE " + " AND ".join(clauses), params

    @staticmethod
    def _build_select(filters: TelemetryQuery | None) -> tuple[str, list[Any]]:
        where, params = SQLiteTelemetryStore._build_where(filters)
        limit = filters.limit if filters else 1000
        sql = f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        return sql, params


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_telemetry_store(
    *, enabled: bool | None = None, db_path: str | Path | None = None,
) -> TelemetryStore:
    """Create the appropriate telemetry store based on configuration."""
    if enabled is None:
        enabled = os.environ.get("DAN_TELEMETRY", "1") == "1"
    if not enabled:
        return NullTelemetryStore()
    return SQLiteTelemetryStore(db_path=db_path)
