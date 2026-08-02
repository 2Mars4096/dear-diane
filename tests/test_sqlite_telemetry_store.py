from __future__ import annotations

import sqlite3

import pytest

from dan.server.telemetry import SQLiteTelemetryStore, TelemetryEvent, TelemetryQuery


LEGACY_EVENTS_SCHEMA = """
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
    metadata TEXT DEFAULT '{}'
);
"""


@pytest.mark.asyncio
async def test_sqlite_store_migrates_and_round_trips_parameter_decisions(tmp_path) -> None:
    db_path = tmp_path / "telemetry.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(LEGACY_EVENTS_SCHEMA)
    conn.commit()
    conn.close()

    store = SQLiteTelemetryStore(db_path=db_path)

    await store.record(
        TelemetryEvent(
            event_type="parameter_decision",
            parameter_key="temperature",
            parameter_value="0.2",
            metadata={"decision": "raised"},
        )
    )

    events = await store.query(TelemetryQuery(event_type="parameter_decision"))

    assert len(events) == 1
    assert events[0].parameter_key == "temperature"
    assert events[0].parameter_value == "0.2"

    with sqlite3.connect(db_path) as verify_conn:
        columns = {
            row[1]
            for row in verify_conn.execute("PRAGMA table_info(events)").fetchall()
        }

    assert "parameter_key" in columns
    assert "parameter_value" in columns

    await store.close()
