"""Tests for unified telemetry store (31-20 task 1)."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dan.server.telemetry import (
    AggregateRow,
    InMemoryTelemetryStore,
    NullTelemetryStore,
    SQLiteTelemetryStore,
    TelemetryEvent,
    TelemetryQuery,
    generate_event_id,
    get_telemetry_store,
    summarize_telemetry,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(**kw) -> TelemetryEvent:
    defaults: dict = {"event_type": "chat_turn"}
    defaults.update(kw)
    return TelemetryEvent(**defaults)


# ---------------------------------------------------------------------------
# Null store
# ---------------------------------------------------------------------------


class TestNullStore:
    @pytest.mark.asyncio
    async def test_record_noop(self):
        store = NullTelemetryStore()
        await store.record(_event())
        assert await store.query() == []

    @pytest.mark.asyncio
    async def test_aggregate_empty(self):
        store = NullTelemetryStore()
        assert await store.aggregate() == []

    @pytest.mark.asyncio
    async def test_export_zero(self):
        store = NullTelemetryStore()
        assert await store.export_jsonl() == 0
        assert await store.export_csv() == 0

    @pytest.mark.asyncio
    async def test_prune_zero(self):
        store = NullTelemetryStore()
        assert await store.prune() == 0


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


class TestInMemoryStore:
    @pytest.mark.asyncio
    async def test_record_and_query(self):
        store = InMemoryTelemetryStore()
        ev = _event(project_id="p1", surface="telegram")
        await store.record(ev)
        results = await store.query()
        assert len(results) == 1
        assert results[0].id == ev.id

    @pytest.mark.asyncio
    async def test_query_filter_project(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(project_id="p1"))
        await store.record(_event(project_id="p2"))
        results = await store.query(TelemetryQuery(project_id="p1"))
        assert len(results) == 1
        assert results[0].project_id == "p1"

    @pytest.mark.asyncio
    async def test_query_filter_event_type(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(event_type="chat_turn"))
        await store.record(_event(event_type="guard_check"))
        await store.record(_event(event_type="fast_command"))
        results = await store.query(TelemetryQuery(event_type="guard_check"))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_filter_run_id(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(event_type="workflow_node", run_id="r1"))
        await store.record(_event(event_type="workflow_node", run_id="r2"))
        results = await store.query(TelemetryQuery(run_id="r1"))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_filter_parent_event_id(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(parent_event_id="turn1"))
        await store.record(_event(parent_event_id="turn2"))
        results = await store.query(TelemetryQuery(parent_event_id="turn1"))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_filter_task_id(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(task_id="t1"))
        await store.record(_event(task_id="t2"))
        results = await store.query(TelemetryQuery(task_id="t1"))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_filter_since_until(self):
        store = InMemoryTelemetryStore()
        t1 = _now() - timedelta(days=5)
        t2 = _now() - timedelta(days=1)
        await store.record(_event(timestamp=t1))
        await store.record(_event(timestamp=t2))
        results = await store.query(TelemetryQuery(since=_now() - timedelta(days=3)))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_limit(self):
        store = InMemoryTelemetryStore()
        for _ in range(10):
            await store.record(_event())
        results = await store.query(TelemetryQuery(limit=3))
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_aggregate_by_model(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(model="gpt-4o", total_tokens=100, estimated_cost=0.01, duration_ms=500))
        await store.record(_event(model="gpt-4o", total_tokens=200, estimated_cost=0.02, duration_ms=300))
        await store.record(_event(model="claude", total_tokens=150, estimated_cost=0.03, duration_ms=400))
        rows = await store.aggregate(group_by=["model"])
        assert len(rows) == 2
        gpt_row = next(r for r in rows if r.group_key["model"] == "gpt-4o")
        assert gpt_row.count == 2
        assert gpt_row.total_tokens == 300
        assert abs(gpt_row.total_cost - 0.03) < 1e-9
        assert gpt_row.min_duration_ms == 300
        assert gpt_row.max_duration_ms == 500

    @pytest.mark.asyncio
    async def test_aggregate_success_rate(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(success=True))
        await store.record(_event(success=True))
        await store.record(_event(success=False))
        rows = await store.aggregate(group_by=["event_type"])
        assert len(rows) == 1
        assert abs(rows[0].success_rate - 2 / 3) < 1e-9

    @pytest.mark.asyncio
    async def test_export_jsonl(self, tmp_path):
        store = InMemoryTelemetryStore()
        await store.record(_event(project_id="p1"))
        await store.record(_event(project_id="p2"))
        path = tmp_path / "out.jsonl"
        count = await store.export_jsonl(path=path)
        assert count == 2
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["project_id"] == "p1"

    @pytest.mark.asyncio
    async def test_export_csv(self, tmp_path):
        store = InMemoryTelemetryStore()
        await store.record(_event(model="gpt-4o"))
        path = tmp_path / "out.csv"
        count = await store.export_csv(path=path)
        assert count == 1
        content = path.read_text()
        assert "gpt-4o" in content
        assert "model" in content.split("\n")[0]

    @pytest.mark.asyncio
    async def test_prune(self):
        store = InMemoryTelemetryStore()
        old = _now() - timedelta(days=100)
        recent = _now() - timedelta(days=10)
        await store.record(_event(timestamp=old))
        await store.record(_event(timestamp=recent))
        pruned = await store.prune(older_than_days=90)
        assert pruned == 1
        assert len(await store.query()) == 1

    @pytest.mark.asyncio
    async def test_prune_uses_env_retention_dynamically(self, monkeypatch):
        store = InMemoryTelemetryStore()
        old = _now() - timedelta(days=40)
        recent = _now() - timedelta(days=10)
        await store.record(_event(timestamp=old))
        await store.record(_event(timestamp=recent))
        monkeypatch.setenv("DAN_TELEMETRY_RETENTION_DAYS", "30")
        pruned = await store.prune()
        assert pruned == 1
        assert len(await store.query()) == 1

    @pytest.mark.asyncio
    async def test_aggregate_with_filter(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(project_id="p1", model="gpt-4o", total_tokens=100))
        await store.record(_event(project_id="p2", model="gpt-4o", total_tokens=200))
        rows = await store.aggregate(
            filters=TelemetryQuery(project_id="p1"),
            group_by=["model"],
        )
        assert len(rows) == 1
        assert rows[0].total_tokens == 100

    @pytest.mark.asyncio
    async def test_aggregate_by_day(self):
        store = InMemoryTelemetryStore()
        day1 = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
        day2 = datetime(2025, 6, 2, 14, 0, tzinfo=timezone.utc)
        await store.record(_event(timestamp=day1, total_tokens=100))
        await store.record(_event(timestamp=day1, total_tokens=200))
        await store.record(_event(timestamp=day2, total_tokens=50))
        rows = await store.aggregate(group_by=["day"])
        assert len(rows) == 2
        row_map = {r.group_key["day"]: r for r in rows}
        assert row_map["2025-06-01"].total_tokens == 300
        assert row_map["2025-06-01"].count == 2
        assert row_map["2025-06-02"].total_tokens == 50

    @pytest.mark.asyncio
    async def test_aggregate_by_concierge_metadata(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(
            event_type="chat_turn",
            total_tokens=100,
            estimated_cost=0.01,
            metadata={
                "concierge_stage": "workflow_build",
                "session_tier": 2,
                "route_source": "fast_lexical",
            },
        ))
        await store.record(_event(
            event_type="chat_turn",
            total_tokens=200,
            estimated_cost=0.02,
            metadata={
                "concierge_stage": "workflow_build",
                "session_tier": 2,
                "route_source": "fast_lexical",
            },
        ))
        await store.record(_event(
            event_type="chat_turn",
            total_tokens=50,
            estimated_cost=0.005,
            metadata={
                "concierge_stage": "conversation",
                "session_tier": 1,
                "route_source": "llm",
            },
        ))
        rows = await store.aggregate(
            group_by=["concierge_stage", "session_tier", "route_source"],
        )
        assert len(rows) == 2
        workflow_row = next(
            row for row in rows if row.group_key["concierge_stage"] == "workflow_build"
        )
        assert workflow_row.group_key["session_tier"] == "2"
        assert workflow_row.group_key["route_source"] == "fast_lexical"
        assert workflow_row.total_tokens == 300
        assert abs(workflow_row.total_cost - 0.03) < 1e-9

    @pytest.mark.asyncio
    async def test_aggregate_by_lint_metadata(self):
        store = InMemoryTelemetryStore()
        await store.record(_event(
            event_type="workflow_run",
            total_tokens=100,
            metadata={
                "had_lint_activity": True,
                "had_lint_blocks": True,
                "had_lint_autofix": False,
                "lint_state": "blocked",
            },
        ))
        await store.record(_event(
            event_type="workflow_run",
            total_tokens=60,
            metadata={
                "had_lint_activity": True,
                "had_lint_blocks": False,
                "had_lint_autofix": True,
                "lint_state": "auto_fixed",
            },
        ))
        rows = await store.aggregate(group_by=["lint_state", "had_lint_blocks"])
        assert len(rows) == 2
        blocked_row = next(row for row in rows if row.group_key["lint_state"] == "blocked")
        assert blocked_row.group_key["had_lint_blocks"] == "True"
        assert blocked_row.total_tokens == 100


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class TestSQLiteStore:
    @pytest.fixture
    def store(self, tmp_path) -> SQLiteTelemetryStore:
        return SQLiteTelemetryStore(db_path=tmp_path / "test.db")

    @pytest.mark.asyncio
    async def test_record_and_query(self, store):
        ev = _event(project_id="p1", surface="cli")
        await store.record(ev)
        results = await store.query()
        assert len(results) == 1
        assert results[0].id == ev.id
        assert results[0].project_id == "p1"
        assert results[0].surface == "cli"

    @pytest.mark.asyncio
    async def test_query_filters(self, store):
        await store.record(_event(project_id="p1", model="gpt-4o"))
        await store.record(_event(project_id="p2", model="claude"))
        assert len(await store.query(TelemetryQuery(project_id="p1"))) == 1
        assert len(await store.query(TelemetryQuery(model="claude"))) == 1
        assert len(await store.query(TelemetryQuery(project_id="p3"))) == 0

    @pytest.mark.asyncio
    async def test_query_run_id(self, store):
        await store.record(_event(event_type="workflow_node", run_id="r1"))
        await store.record(_event(event_type="workflow_node", run_id="r2"))
        results = await store.query(TelemetryQuery(run_id="r1"))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_parent_event_id(self, store):
        await store.record(_event(parent_event_id="turn1"))
        await store.record(_event(parent_event_id="turn2"))
        results = await store.query(TelemetryQuery(parent_event_id="turn1"))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_task_id(self, store):
        await store.record(_event(task_id="t1"))
        await store.record(_event(task_id="t2"))
        results = await store.query(TelemetryQuery(task_id="t1"))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_time_range(self, store):
        old = _now() - timedelta(days=10)
        recent = _now() - timedelta(hours=1)
        await store.record(_event(timestamp=old))
        await store.record(_event(timestamp=recent))
        results = await store.query(TelemetryQuery(since=_now() - timedelta(days=2)))
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_limit(self, store):
        for _ in range(10):
            await store.record(_event())
        results = await store.query(TelemetryQuery(limit=5))
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_aggregate_by_model(self, store):
        await store.record(_event(model="gpt-4o", total_tokens=100, estimated_cost=0.01, duration_ms=500))
        await store.record(_event(model="gpt-4o", total_tokens=200, estimated_cost=0.02, duration_ms=300))
        await store.record(_event(model="claude", total_tokens=150, estimated_cost=0.03, duration_ms=400))
        rows = await store.aggregate(group_by=["model"])
        assert len(rows) == 2
        gpt_row = next(r for r in rows if r.group_key["model"] == "gpt-4o")
        assert gpt_row.count == 2
        assert gpt_row.total_tokens == 300
        assert abs(gpt_row.total_cost - 0.03) < 1e-9
        assert gpt_row.min_duration_ms == 300
        assert gpt_row.max_duration_ms == 500

    @pytest.mark.asyncio
    async def test_aggregate_by_surface_day(self, store):
        await store.record(_event(surface="telegram", total_tokens=100))
        await store.record(_event(surface="cli", total_tokens=200))
        rows = await store.aggregate(group_by=["surface"])
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_aggregate_success_rate(self, store):
        await store.record(_event(success=True))
        await store.record(_event(success=True))
        await store.record(_event(success=False))
        rows = await store.aggregate(group_by=["event_type"])
        assert len(rows) == 1
        assert abs(rows[0].success_rate - 2 / 3) < 1e-9

    @pytest.mark.asyncio
    async def test_aggregate_with_filter(self, store):
        await store.record(_event(project_id="p1", model="gpt-4o", total_tokens=100))
        await store.record(_event(project_id="p2", model="gpt-4o", total_tokens=200))
        rows = await store.aggregate(
            filters=TelemetryQuery(project_id="p1"),
            group_by=["model"],
        )
        assert len(rows) == 1
        assert rows[0].total_tokens == 100

    @pytest.mark.asyncio
    async def test_aggregate_by_day(self, store):
        day1 = datetime(2025, 6, 1, 10, 0, tzinfo=timezone.utc)
        day2 = datetime(2025, 6, 2, 14, 0, tzinfo=timezone.utc)
        await store.record(_event(timestamp=day1, total_tokens=100))
        await store.record(_event(timestamp=day1, total_tokens=200))
        await store.record(_event(timestamp=day2, total_tokens=50))
        rows = await store.aggregate(group_by=["day"])
        assert len(rows) == 2
        row_map = {r.group_key["day"]: r for r in rows}
        assert row_map["2025-06-01"].total_tokens == 300
        assert row_map["2025-06-01"].count == 2
        assert row_map["2025-06-02"].total_tokens == 50

    @pytest.mark.asyncio
    async def test_aggregate_by_concierge_metadata(self, store):
        await store.record(_event(
            event_type="chat_turn",
            total_tokens=100,
            estimated_cost=0.01,
            metadata={
                "concierge_stage": "workflow_build",
                "session_tier": 2,
                "route_source": "fast_lexical",
            },
        ))
        await store.record(_event(
            event_type="chat_turn",
            total_tokens=200,
            estimated_cost=0.02,
            metadata={
                "concierge_stage": "workflow_build",
                "session_tier": 2,
                "route_source": "fast_lexical",
            },
        ))
        await store.record(_event(
            event_type="chat_turn",
            total_tokens=50,
            estimated_cost=0.005,
            metadata={
                "concierge_stage": "conversation",
                "session_tier": 1,
                "route_source": "llm",
            },
        ))
        rows = await store.aggregate(
            group_by=["concierge_stage", "session_tier", "route_source"],
        )
        assert len(rows) == 2
        workflow_row = next(
            row for row in rows if row.group_key["concierge_stage"] == "workflow_build"
        )
        assert workflow_row.group_key["session_tier"] == "2"
        assert workflow_row.group_key["route_source"] == "fast_lexical"
        assert workflow_row.total_tokens == 300
        assert abs(workflow_row.total_cost - 0.03) < 1e-9

    @pytest.mark.asyncio
    async def test_aggregate_by_lint_metadata(self, store):
        await store.record(_event(
            event_type="workflow_run",
            total_tokens=100,
            metadata={
                "had_lint_activity": True,
                "had_lint_blocks": True,
                "had_lint_autofix": False,
                "lint_state": "blocked",
            },
        ))
        await store.record(_event(
            event_type="workflow_run",
            total_tokens=60,
            metadata={
                "had_lint_activity": True,
                "had_lint_blocks": False,
                "had_lint_autofix": True,
                "lint_state": "auto_fixed",
            },
        ))
        rows = await store.aggregate(group_by=["lint_state", "had_lint_blocks"])
        assert len(rows) == 2
        blocked_row = next(row for row in rows if row.group_key["lint_state"] == "blocked")
        assert blocked_row.group_key["had_lint_blocks"] == "True"
        assert blocked_row.total_tokens == 100

    @pytest.mark.asyncio
    async def test_export_jsonl(self, store, tmp_path):
        await store.record(_event(project_id="p1"))
        path = tmp_path / "exports" / "out.jsonl"
        count = await store.export_jsonl(path=path)
        assert count == 1
        data = json.loads(path.read_text().strip())
        assert data["project_id"] == "p1"

    @pytest.mark.asyncio
    async def test_export_csv(self, store, tmp_path):
        await store.record(_event(model="gpt-4o", estimated_cost=0.05))
        path = tmp_path / "exports" / "out.csv"
        count = await store.export_csv(path=path)
        assert count == 1
        content = path.read_text()
        assert "gpt-4o" in content

    @pytest.mark.asyncio
    async def test_prune(self, store):
        old = _now() - timedelta(days=100)
        recent = _now()
        await store.record(_event(timestamp=old))
        await store.record(_event(timestamp=recent))
        pruned = await store.prune(older_than_days=90)
        assert pruned == 1
        assert len(await store.query()) == 1

    @pytest.mark.asyncio
    async def test_metadata_round_trip(self, store):
        ev = _event(metadata={"guard_notes": ["mismatch"], "source": "scheduled"})
        await store.record(ev)
        results = await store.query()
        assert results[0].metadata == {"guard_notes": ["mismatch"], "source": "scheduled"}

    @pytest.mark.asyncio
    async def test_duplicate_id_ignored(self, store):
        ev = _event()
        await store.record(ev)
        await store.record(ev)
        assert len(await store.query()) == 1

    @pytest.mark.asyncio
    async def test_close_and_reopen(self, tmp_path):
        db_path = tmp_path / "reopen.db"
        s1 = SQLiteTelemetryStore(db_path=db_path)
        await s1.record(_event(project_id="p1"))
        await s1.close()
        s2 = SQLiteTelemetryStore(db_path=db_path)
        results = await s2.query()
        assert len(results) == 1
        await s2.close()

    @pytest.mark.asyncio
    async def test_persists_chat_mode_model_used_and_hour_rollups(self, store):
        await store.record(_event(
            event_type="chat_turn",
            session_id="session-1",
            model="gpt-4.1",
            model_used="gpt-4.1",
            chat_mode="plan",
            total_tokens=120,
            estimated_cost=0.0012,
            timestamp=datetime(2026, 3, 25, 1, 15, tzinfo=timezone.utc),
        ))
        await store.record(_event(
            event_type="fast_command",
            session_id="session-1",
            model="gpt-4o-mini",
            chat_mode="auto",
            total_tokens=12,
            estimated_cost=0.0,
            timestamp=datetime(2026, 3, 25, 2, 5, tzinfo=timezone.utc),
        ))

        events = await store.query(TelemetryQuery(session_id="session-1", limit=10))
        model_used_values = {event.model_used for event in events}
        chat_modes = {event.chat_mode for event in events}
        assert model_used_values == {"gpt-4.1", "gpt-4o-mini"}
        assert chat_modes == {"plan", "auto"}

        hour_rows = await store.aggregate(
            TelemetryQuery(session_id="session-1", limit=10),
            group_by=["hour"],
        )
        assert sorted(row.group_key["hour"] for row in hour_rows) == [
            "2026-03-25T01:00:00Z",
            "2026-03-25T02:00:00Z",
        ]

        model_rows = await store.aggregate(
            TelemetryQuery(session_id="session-1", limit=10),
            group_by=["model_used"],
        )
        assert {row.group_key["model_used"] for row in model_rows} == {
            "gpt-4.1",
            "gpt-4o-mini",
        }


class TestTelemetrySummaries:
    @pytest.mark.asyncio
    async def test_summarize_telemetry_uses_turn_totals_without_double_counting_gateway_calls(self):
        store = InMemoryTelemetryStore()
        timestamp = datetime(2026, 3, 25, 10, 30, tzinfo=timezone.utc)
        await store.record(_event(
            event_type="chat_turn",
            session_id="session-1",
            model="gpt-4.1",
            model_used="gpt-4.1",
            chat_mode="plan",
            total_tokens=120,
            estimated_cost=0.0012,
            timestamp=timestamp,
        ))
        await store.record(_event(
            event_type="fast_command",
            session_id="session-1",
            chat_mode="auto",
            total_tokens=0,
            estimated_cost=0.0,
            timestamp=timestamp,
        ))
        await store.record(_event(
            event_type="gateway_call",
            session_id="session-1",
            model="gpt-4.1",
            model_used="gpt-4.1",
            chat_mode="plan",
            total_tokens=120,
            estimated_cost=0.0012,
            timestamp=timestamp,
        ))

        summary = await summarize_telemetry(
            store,
            TelemetryQuery(session_id="session-1", limit=10),
        )

        assert summary["totals"] == {
            "events": 3,
            "chat_turns": 1,
            "fast_commands": 1,
            "gateway_calls": 1,
            "total_tokens": 120,
            "total_cost": 0.0012,
        }
        assert [row["group_key"]["hour"] for row in summary["activity_by_hour"]] == [
            "2026-03-25T10:00:00Z",
        ]
        assert {row["group_key"]["chat_mode"] for row in summary["modes"]} == {
            "plan",
            "auto",
        }
        assert [row["group_key"]["model_used"] for row in summary["models"]] == [
            "gpt-4.1",
        ]
        assert summary["lint"] == {
            "workflow_runs": 0,
            "with_activity": 0,
            "blocked_runs": 0,
            "autofixed_runs": 0,
            "warning_runs": 0,
            "passed_runs": 0,
            "states": [],
        }

    @pytest.mark.asyncio
    async def test_summarize_telemetry_includes_lint_rollups(self):
        store = InMemoryTelemetryStore()
        timestamp = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)
        await store.record(_event(
            event_type="workflow_run",
            run_id="run-1",
            total_tokens=50,
            metadata={
                "had_lint_activity": True,
                "had_lint_blocks": True,
                "had_lint_autofix": True,
                "lint_state": "blocked+auto_fixed",
            },
            timestamp=timestamp,
        ))
        await store.record(_event(
            event_type="workflow_run",
            run_id="run-2",
            total_tokens=40,
            metadata={
                "had_lint_activity": True,
                "had_lint_blocks": False,
                "had_lint_autofix": False,
                "lint_state": "warning",
            },
            timestamp=timestamp,
        ))
        await store.record(_event(
            event_type="workflow_run",
            run_id="run-3",
            total_tokens=30,
            metadata={
                "had_lint_activity": True,
                "had_lint_blocks": False,
                "had_lint_autofix": False,
                "lint_state": "passed",
            },
            timestamp=timestamp,
        ))

        summary = await summarize_telemetry(store, TelemetryQuery(limit=10))

        assert summary["lint"]["workflow_runs"] == 3
        assert summary["lint"]["with_activity"] == 3
        assert summary["lint"]["blocked_runs"] == 1
        assert summary["lint"]["autofixed_runs"] == 1
        assert summary["lint"]["warning_runs"] == 1
        assert summary["lint"]["passed_runs"] == 1
        assert {row["group_key"]["lint_state"] for row in summary["lint"]["states"]} == {
            "blocked+auto_fixed",
            "warning",
            "passed",
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("DAN_TELEMETRY", "0")
        store = get_telemetry_store()
        assert isinstance(store, NullTelemetryStore)

    def test_enabled_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DAN_TELEMETRY", "1")
        monkeypatch.setenv("DAN_TELEMETRY_DB", str(tmp_path / "t.db"))
        store = get_telemetry_store()
        assert isinstance(store, SQLiteTelemetryStore)
        assert store._db_path == tmp_path / "t.db"

    def test_explicit_params(self, tmp_path):
        store = get_telemetry_store(enabled=True, db_path=tmp_path / "custom.db")
        assert isinstance(store, SQLiteTelemetryStore)
        assert store._db_path == tmp_path / "custom.db"

    def test_explicit_disabled(self):
        store = get_telemetry_store(enabled=False)
        assert isinstance(store, NullTelemetryStore)


# ---------------------------------------------------------------------------
# TelemetryEvent model
# ---------------------------------------------------------------------------


class TestEventModel:
    def test_generate_event_id(self):
        id1 = generate_event_id()
        id2 = generate_event_id()
        assert len(id1) == 16
        assert id1 != id2

    def test_defaults(self):
        ev = TelemetryEvent(event_type="chat_turn")
        assert ev.id
        assert ev.timestamp
        assert ev.prompt_tokens == 0
        assert ev.success is True
        assert ev.metadata == {}

    def test_all_fields(self):
        ev = TelemetryEvent(
            event_type="workflow_node",
            project_id="p1", task_id="t1", surface="telegram", session_id="s1",
            parent_event_id="pe1", run_id="r1", graph_id="g1",
            model="gpt-4o", node_id="n1", intent="direct_task", tool_name=None,
            prompt_tokens=100, completion_tokens=50, total_tokens=150,
            estimated_cost=0.01, duration_ms=1234.5,
            success=False, retry_count=2, guard_action="clarify",
            metadata={"key": "val"},
        )
        assert ev.event_type == "workflow_node"
        assert ev.run_id == "r1"
        assert ev.retry_count == 2
