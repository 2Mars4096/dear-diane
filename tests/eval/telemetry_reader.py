"""Synchronous SQLite reader for the unified telemetry store (31-20).

Part of the workflow generation quality evaluation system (Phase 33).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


class TelemetryReader:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".dan" / "telemetry.db"
        self._path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection | None:
        if not self._path.exists():
            return None
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(
                    f"file:{self._path}?mode=ro", uri=True
                )
                self._conn.row_factory = sqlite3.Row
            except sqlite3.OperationalError:
                return None
        return self._conn

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        meta = d.get("metadata", "{}")
        d["metadata"] = json.loads(meta) if isinstance(meta, str) else meta
        d["success"] = bool(d.get("success", 1))
        return d

    def query_events(
        self,
        *,
        event_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        graph_id: str | None = None,
        session_id: str | None = None,
        parent_event_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        conn = self._get_conn()
        if conn is None:
            return []
        clauses: list[str] = []
        params: list[object] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("timestamp < ?")
            params.append(until.isoformat())
        if graph_id is not None:
            clauses.append("graph_id = ?")
            params.append(graph_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if parent_event_id is not None:
            clauses.append("parent_event_id = ?")
            params.append(parent_event_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)
        sql = f"SELECT * FROM events WHERE {where} ORDER BY timestamp DESC LIMIT ?"
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._row_to_dict(r) for r in rows]

    def get_chat_turn(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        graph_id: str | None = None,
    ) -> dict | None:
        events = self.query_events(
            event_type="chat_turn",
            since=since,
            until=until,
            graph_id=graph_id,
            limit=1,
        )
        return events[0] if events else None

    def get_child_events(self, parent_event_id: str) -> list[dict]:
        return self.query_events(
            parent_event_id=parent_event_id,
            limit=1000,
        )

    def get_run_telemetry(self, run_id: str) -> list[dict]:
        conn = self._get_conn()
        if conn is None:
            return []
        sql = """
            SELECT * FROM events
            WHERE run_id = ? AND event_type IN ('workflow_run', 'workflow_node')
            ORDER BY timestamp ASC
        """
        try:
            rows = conn.execute(sql, (run_id,)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [self._row_to_dict(r) for r in rows]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
