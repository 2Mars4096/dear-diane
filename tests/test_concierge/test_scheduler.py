"""Tests for the scheduled tasks module (plan 31-7)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from dan.engine.executor import EngineConfig
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import CodeOperator
from dan.models.ports import OutputPort
from dan.server.concierge.scheduler import (
    DeliveryTarget,
    ScheduleEntry,
    ScheduleHistoryStore,
    ScheduleLease,
    ScheduleLeaseManager,
    ScheduleRunRecord,
    ScheduleStore,
    SchedulerAuthority,
    TaskScheduler,
    TriggerContext,
    apply_fallback_policy,
    compute_next_run,
    create_server_scheduler,
    create_service_scheduler,
    deliver_result,
    handle_schedule_command,
    parse_nl_schedule,
    parse_schedule_add,
    parse_trigger,
    resolve_scheduler_authority,
)
from dan.server.run_manager import RunManager


# =========================================================================
# Cron / interval parsing
# =========================================================================


class TestParseTrigger:
    def test_every_6h(self):
        assert parse_trigger("every 6h") == "0 */6 * * *"

    def test_every_6_hours(self):
        assert parse_trigger("every 6 hours") == "0 */6 * * *"

    def test_every_30m(self):
        assert parse_trigger("every 30m") == "*/30 * * * *"

    def test_every_30_minutes(self):
        assert parse_trigger("every 30 minutes") == "*/30 * * * *"

    def test_every_2d(self):
        assert parse_trigger("every 2d") == "0 0 */2 * *"

    def test_every_1_day(self):
        assert parse_trigger("every 1 day") == "0 0 */1 * *"

    def test_daily_at_9am(self):
        assert parse_trigger("daily at 9am") == "0 9 * * *"

    def test_daily_at_9pm(self):
        assert parse_trigger("daily at 9pm") == "0 21 * * *"

    def test_daily_at_830am(self):
        assert parse_trigger("daily at 8:30am") == "30 8 * * *"

    def test_daily_at_12am(self):
        assert parse_trigger("daily at 12am") == "0 0 * * *"

    def test_daily_at_12pm(self):
        assert parse_trigger("daily at 12pm") == "0 12 * * *"

    def test_every_day_at_9am(self):
        assert parse_trigger("every day at 9am") == "0 9 * * *"

    def test_every_day_at_3_30pm(self):
        assert parse_trigger("every day at 3:30pm") == "30 15 * * *"

    def test_weekdays_at_830am(self):
        assert parse_trigger("weekdays at 8:30am") == "30 8 * * 1-5"

    def test_weekday_at_9am(self):
        assert parse_trigger("weekday at 9am") == "0 9 * * 1-5"

    def test_passthrough_cron(self):
        assert parse_trigger("*/5 * * * *") == "*/5 * * * *"

    def test_passthrough_complex_cron(self):
        assert parse_trigger("0 9 1 * *") == "0 9 1 * *"

    def test_passthrough_cron_ranges(self):
        assert parse_trigger("30 8 * * 1-5") == "30 8 * * 1-5"

    def test_case_insensitive(self):
        assert parse_trigger("Every 6H") == "0 */6 * * *"
        assert parse_trigger("Daily At 9AM") == "0 9 * * *"

    def test_whitespace_tolerance(self):
        assert parse_trigger("  every 30m  ") == "*/30 * * * *"


# =========================================================================
# Next-run computation
# =========================================================================


class TestComputeNextRun:
    def test_interval_minutes(self):
        base = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
        result = compute_next_run("*/30 * * * *", base)
        assert result > base
        assert result.tzinfo is not None

    def test_interval_hours(self):
        base = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
        result = compute_next_run("0 */6 * * *", base)
        assert result > base

    def test_daily_fixed(self):
        base = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc)
        result = compute_next_run("0 9 * * *", base)
        assert result > base
        assert result.hour == 9
        assert result.minute == 0

    def test_daily_fixed_past(self):
        base = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
        result = compute_next_run("0 9 * * *", base)
        assert result > base
        assert result.day == 11

    def test_returns_tz_aware(self):
        base = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
        result = compute_next_run("*/5 * * * *", base)
        assert result.tzinfo is not None

    def test_naive_input_treated_as_utc(self):
        base = datetime(2026, 3, 10, 12, 0)
        result = compute_next_run("*/5 * * * *", base)
        assert result.tzinfo is not None


# =========================================================================
# Models
# =========================================================================


class TestModels:
    def test_trigger_context_defaults(self):
        ctx = TriggerContext()
        assert ctx.source_surface == "schedule"
        assert ctx.project_id is None

    def test_trigger_context_fields(self):
        ctx = TriggerContext(project_id="proj-1", user_id="user-1")
        assert ctx.project_id == "proj-1"
        assert ctx.user_id == "user-1"

    def test_trigger_context_accepts_non_schedule_surface(self):
        ctx = TriggerContext(source_surface="cli", project_id="proj-1")
        assert ctx.source_surface == "cli"
        assert ctx.project_id == "proj-1"

    def test_delivery_target_defaults(self):
        dt = DeliveryTarget()
        assert dt.surface == "cli"
        assert dt.fallback_policy == "store_and_notify"

    def test_delivery_target_custom(self):
        dt = DeliveryTarget(
            surface="telegram",
            conversation_key="chat-123",
            fallback_policy="drop",
        )
        assert dt.surface == "telegram"
        assert dt.fallback_policy == "drop"

    def test_schedule_entry_auto_id(self):
        e = ScheduleEntry(name="test", trigger="every 1h", action="do stuff")
        assert e.id
        assert len(e.id) == 12

    def test_schedule_entry_defaults(self):
        e = ScheduleEntry(name="test", trigger="every 1h", action="do stuff")
        assert e.enabled is True
        assert e.on_missed == "run_once"
        assert e.timezone == "UTC"
        assert e.trigger_context.source_surface == "schedule"
        assert e.delivery_target.surface == "cli"

    def test_schedule_entry_unique_ids(self):
        e1 = ScheduleEntry(name="a", trigger="every 1h", action="a")
        e2 = ScheduleEntry(name="b", trigger="every 1h", action="b")
        assert e1.id != e2.id

    def test_schedule_entry_serialization(self):
        e = ScheduleEntry(name="test", trigger="every 1h", action="do stuff")
        data = e.model_dump(mode="json")
        restored = ScheduleEntry.model_validate(data)
        assert restored.name == e.name
        assert restored.id == e.id

    def test_run_record_defaults(self):
        r = ScheduleRunRecord(
            schedule_id="abc",
            started_at=datetime.now(timezone.utc),
        )
        assert r.status == "running"
        assert r.error is None

    def test_run_record_serialization(self):
        r = ScheduleRunRecord(
            schedule_id="abc",
            started_at=datetime.now(timezone.utc),
            status="success",
            result_summary="done",
        )
        data = r.model_dump(mode="json")
        restored = ScheduleRunRecord.model_validate(data)
        assert restored.status == "success"


# =========================================================================
# Schedule Store CRUD
# =========================================================================


class TestScheduleStore:
    @pytest.fixture
    def store(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        s = ScheduleStore(path=path)
        s.load()
        return s

    def test_add_and_get_by_id(self, store):
        entry = ScheduleEntry(name="report", trigger="every 6h", action="run report")
        store.add(entry)
        found = store.get(entry.id)
        assert found is not None
        assert found.name == "report"

    def test_get_by_name(self, store):
        entry = ScheduleEntry(name="Report", trigger="every 6h", action="run report")
        store.add(entry)
        found = store.get("report")
        assert found is not None
        assert found.id == entry.id

    def test_get_by_name_case_insensitive(self, store):
        entry = ScheduleEntry(name="My Task", trigger="every 1h", action="test")
        store.add(entry)
        assert store.get("my task") is not None
        assert store.get("MY TASK") is not None

    def test_get_not_found(self, store):
        assert store.get("nonexistent") is None

    def test_list_all_empty(self, store):
        assert store.list_all() == []

    def test_list_all(self, store):
        store.add(ScheduleEntry(name="a", trigger="every 1h", action="a"))
        store.add(ScheduleEntry(name="b", trigger="every 2h", action="b"))
        assert len(store.list_all()) == 2

    def test_remove_by_id(self, store):
        entry = ScheduleEntry(name="remove-me", trigger="every 1h", action="x")
        store.add(entry)
        removed = store.remove(entry.id)
        assert removed is not None
        assert removed.id == entry.id
        assert store.get(entry.id) is None

    def test_remove_by_name(self, store):
        entry = ScheduleEntry(name="remove-me", trigger="every 1h", action="x")
        store.add(entry)
        removed = store.remove("remove-me")
        assert removed is not None
        assert store.list_all() == []

    def test_remove_not_found(self, store):
        assert store.remove("ghost") is None

    def test_update(self, store):
        entry = ScheduleEntry(name="updatable", trigger="every 1h", action="x")
        store.add(entry)
        entry.enabled = False
        store.update(entry)
        found = store.get(entry.id)
        assert found is not None
        assert found.enabled is False

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        s1 = ScheduleStore(path=path)
        s1.load()
        entry = ScheduleEntry(name="persist", trigger="every 1h", action="x")
        s1.add(entry)

        s2 = ScheduleStore(path=path)
        s2.load()
        assert len(s2.list_all()) == 1
        assert s2.list_all()[0].name == "persist"

    def test_atomic_save_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "deep" / "nested" / "schedules.json")
        s = ScheduleStore(path=path)
        s.load()
        s.add(ScheduleEntry(name="deep", trigger="every 1h", action="x"))
        assert os.path.exists(path)

    def test_load_corrupt_file(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        with open(path, "w") as f:
            f.write("not json")
        s = ScheduleStore(path=path)
        s.load()
        assert s.list_all() == []


# =========================================================================
# Schedule History Store
# =========================================================================


class TestScheduleHistoryStore:
    @pytest.fixture
    def history(self, tmp_path):
        path = str(tmp_path / "history.json")
        h = ScheduleHistoryStore(path=path)
        h.load()
        return h

    def test_add_and_retrieve(self, history):
        record = ScheduleRunRecord(
            schedule_id="s1",
            started_at=datetime.now(timezone.utc),
            status="success",
            result_summary="done",
        )
        history.add_record(record)
        results = history.get_history("s1")
        assert len(results) == 1
        assert results[0].status == "success"

    def test_limit(self, history):
        for i in range(5):
            history.add_record(ScheduleRunRecord(
                schedule_id="s1",
                started_at=datetime.now(timezone.utc),
                status="success",
                result_summary=f"run-{i}",
            ))
        results = history.get_history("s1", limit=3)
        assert len(results) == 3

    def test_max_records_per_schedule(self, history):
        for i in range(25):
            history.add_record(ScheduleRunRecord(
                schedule_id="s1",
                started_at=datetime.now(timezone.utc),
                status="success",
                result_summary=f"run-{i}",
            ))
        results = history.get_history("s1", limit=100)
        assert len(results) == 20

    def test_separate_schedule_ids(self, history):
        history.add_record(ScheduleRunRecord(
            schedule_id="s1", started_at=datetime.now(timezone.utc), status="success",
        ))
        history.add_record(ScheduleRunRecord(
            schedule_id="s2", started_at=datetime.now(timezone.utc), status="error",
        ))
        assert len(history.get_history("s1")) == 1
        assert len(history.get_history("s2")) == 1

    def test_empty_history(self, history):
        assert history.get_history("nonexistent") == []

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "history.json")
        h1 = ScheduleHistoryStore(path=path)
        h1.load()
        h1.add_record(ScheduleRunRecord(
            schedule_id="s1", started_at=datetime.now(timezone.utc), status="success",
        ))

        h2 = ScheduleHistoryStore(path=path)
        h2.load()
        assert len(h2.get_history("s1")) == 1


# =========================================================================
# TaskScheduler runtime
# =========================================================================


class TestTaskScheduler:
    @pytest.fixture
    def store(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        s = ScheduleStore(path=path)
        s.load()
        return s

    @pytest.fixture
    def history(self, tmp_path):
        path = str(tmp_path / "history.json")
        h = ScheduleHistoryStore(path=path)
        h.load()
        return h

    @pytest.mark.asyncio
    async def test_start_stop(self, store):
        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(store, dispatch, poll_interval=0.1)
        await scheduler.start()
        await asyncio.sleep(0.15)
        await scheduler.stop()
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_fires_due_schedule(self, store):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="fire-me",
            trigger="every 30m",
            action="run report",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05)
        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop()

        assert dispatch.call_count >= 1
        call_args = dispatch.call_args
        assert call_args[0][0] == "run report"
        assert isinstance(call_args[0][1], TriggerContext)
        assert isinstance(call_args[0][2], DeliveryTarget)
        assert call_args[0][1].task_id is None

    @pytest.mark.asyncio
    async def test_fire_preserves_missing_task_id_in_dispatch_context(self, store):
        entry = ScheduleEntry(
            name="inject-id",
            trigger="every 30m",
            action="run report",
        )
        store.add(entry)

        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05)
        await scheduler._fire(entry)

        assert dispatch.await_count == 1
        dispatch_context = dispatch.call_args[0][1]
        assert isinstance(dispatch_context, TriggerContext)
        assert dispatch_context.task_id is None

    @pytest.mark.asyncio
    async def test_fire_passes_entry_when_dispatch_accepts_it(self, store):
        entry = ScheduleEntry(
            name="run-workflow",
            trigger="every 30m",
            action="run workflow wf-1",
            workflow_id="wf-1",
        )
        store.add(entry)

        captured: dict[str, object] = {}

        async def dispatch(action, trigger_context, delivery_target, *, entry=None):
            captured["action"] = action
            captured["entry"] = entry
            return "ok"

        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05)
        await scheduler._fire(entry)

        assert captured["action"] == "run workflow wf-1"
        assert captured["entry"] == entry

    @pytest.mark.asyncio
    async def test_fire_marks_started_workflow_as_running(self, store):
        entry = ScheduleEntry(
            name="run-workflow",
            trigger="every 30m",
            action="run workflow wf-1",
            workflow_id="wf-1",
        )
        store.add(entry)
        history = ScheduleHistoryStore(path=str(Path(store.path).with_name("history.json")))
        dispatch = AsyncMock(return_value="Started workflow `wf-1` as run `run-123`.")
        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05, history_store=history)

        await scheduler._fire(entry)

        records = history.get_history(entry.id)
        assert records
        latest = records[-1]
        assert latest.status == "running"
        assert latest.completed_at is None
        assert "Started workflow" in latest.result_summary

    @pytest.mark.asyncio
    async def test_fire_starts_real_workflow_run_via_existing_scheduler_path(
        self,
        store,
        history,
    ):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="run-workflow",
            trigger="every 30m",
            action="run workflow wf-scheduled",
            workflow_id="wf-scheduled",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        graph = Graph(
            metadata=GraphMetadata(name="scheduled-smoke"),
            nodes=[
                CodeOperator(
                    id="emit",
                    name="emit",
                    code="result = {'report': 'ok'}",
                    output_ports=[OutputPort(name="report"), OutputPort(name="result")],
                )
            ],
            edges=[],
            entry_points=["emit"],
            exit_points=["emit"],
        )
        run_manager = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
        started: dict[str, object] = {}

        async def dispatch(action, trigger_context, delivery_target, *, entry=None):
            assert action == "run workflow wf-scheduled"
            assert entry is not None
            assert entry.workflow_id == "wf-scheduled"
            record = await run_manager.start_run(
                graph,
                graph_id=entry.workflow_id,
                inputs=entry.workflow_inputs or None,
            )
            started["record"] = record
            return f"Started workflow `{entry.workflow_id}` as run `{record.run_id}`."

        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05, history_store=history)
        await scheduler._fire(entry)

        record = started["record"]
        assert isinstance(record, object)
        await asyncio.wait_for(run_manager._tasks[record.run_id], timeout=5.0)

        assert record.status.value == "completed"
        assert record.result is not None
        assert record.result.success is True
        assert record.result.outputs.get("report") == "ok"

        records = history.get_history(entry.id)
        assert records
        assert records[-1].status == "running"
        assert "Started workflow `wf-scheduled`" in records[-1].result_summary

    @pytest.mark.asyncio
    async def test_does_not_refire_while_schedule_is_inflight(self, store):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="slow-task",
            trigger="every 30m",
            action="long running",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        async def slow_dispatch(*args, **kwargs):
            await asyncio.sleep(0.2)
            return "ok"

        dispatch = AsyncMock(side_effect=slow_dispatch)
        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05)
        await scheduler.start()
        await asyncio.sleep(0.12)
        await scheduler.stop()

        assert dispatch.await_count == 1

    @pytest.mark.asyncio
    async def test_updates_last_and_next_run(self, store):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="track-runs",
            trigger="every 30m",
            action="test",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05)
        await scheduler.start()
        await asyncio.sleep(0.3)
        await scheduler.stop()

        updated = store.get(entry.id)
        assert updated is not None
        assert updated.last_run is not None
        assert updated.next_run is not None
        assert updated.next_run > now

    @pytest.mark.asyncio
    async def test_skips_disabled_schedule(self, store):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="disabled",
            trigger="every 30m",
            action="skip me",
            enabled=False,
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05)
        await scheduler.start()
        await asyncio.sleep(0.15)
        await scheduler.stop()
        dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_missed_run_on_startup_run_once(self, store):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="missed",
            trigger="every 1h",
            action="catch up",
            next_run=now - timedelta(hours=2),
            on_missed="run_once",
        )
        store.add(entry)

        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(store, dispatch, poll_interval=60)
        await scheduler.start()
        await asyncio.sleep(0.3)
        await scheduler.stop()

        assert dispatch.call_count >= 1

    @pytest.mark.asyncio
    async def test_missed_run_on_startup_skip(self, store):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="skip-missed",
            trigger="every 1h",
            action="skip me",
            next_run=now - timedelta(hours=2),
            on_missed="skip",
        )
        store.add(entry)

        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(store, dispatch, poll_interval=60)
        await scheduler.start()
        await asyncio.sleep(0.15)
        await scheduler.stop()

        dispatch.assert_not_called()
        updated = store.get(entry.id)
        assert updated is not None
        assert updated.next_run is not None
        assert updated.next_run > now

    @pytest.mark.asyncio
    async def test_dispatch_error_recorded(self, store, history):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="error-task",
            trigger="every 30m",
            action="fail",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        dispatch = AsyncMock(side_effect=RuntimeError("boom"))
        scheduler = TaskScheduler(
            store, dispatch, history_store=history, poll_interval=0.05
        )
        await scheduler.start()
        await asyncio.sleep(0.3)
        await scheduler.stop()

        records = history.get_history(entry.id)
        error_records = [r for r in records if r.status == "error"]
        assert len(error_records) >= 1
        assert "boom" in (error_records[0].error or "")

    @pytest.mark.asyncio
    async def test_records_history(self, store, history):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="history-task",
            trigger="every 30m",
            action="track me",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        dispatch = AsyncMock(return_value="result-data")
        scheduler = TaskScheduler(
            store, dispatch, history_store=history, poll_interval=0.05
        )
        await scheduler.start()
        await asyncio.sleep(0.3)
        await scheduler.stop()

        records = history.get_history(entry.id)
        assert len(records) >= 1


# =========================================================================
# Chat command parsing
# =========================================================================


class TestParseScheduleAdd:
    def test_quoted_action(self):
        action, trigger = parse_schedule_add('"run equity report" every day at 9am')
        assert action == "run equity report"
        assert trigger == "every day at 9am"

    def test_single_quoted_action(self):
        action, trigger = parse_schedule_add("'check portfolio' daily at 8am")
        assert action == "check portfolio"
        assert trigger == "daily at 8am"

    def test_unquoted_with_every(self):
        action, trigger = parse_schedule_add("run report every 6h")
        assert action == "run report"
        assert trigger == "every 6h"

    def test_unquoted_with_daily(self):
        action, trigger = parse_schedule_add("check status daily at 9am")
        assert action == "check status"
        assert trigger == "daily at 9am"

    def test_unquoted_with_weekday(self):
        action, trigger = parse_schedule_add("send summary weekdays at 8am")
        assert action == "send summary"
        assert trigger == "weekdays at 8am"


# =========================================================================
# Chat command handler
# =========================================================================


class TestScheduleCommand:
    @pytest.fixture
    def store(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        s = ScheduleStore(path=path)
        s.load()
        return s

    @pytest.fixture
    def history(self, tmp_path):
        path = str(tmp_path / "history.json")
        h = ScheduleHistoryStore(path=path)
        h.load()
        return h

    def test_add_command(self, store):
        result = handle_schedule_command(
            '/schedule add "run report" every 6h', store
        )
        assert "Scheduled" in result
        assert "run report" in result
        assert "ID:" in result
        assert len(store.list_all()) == 1

    def test_add_with_daily(self, store):
        result = handle_schedule_command(
            '/schedule add "morning check" daily at 9am', store
        )
        assert "Scheduled" in result
        assert "morning check" in result

    def test_add_missing_args(self, store):
        result = handle_schedule_command("/schedule add", store)
        assert "Usage" in result

    def test_workflow_command(self, store):
        result = handle_schedule_command(
            "/schedule workflow equity-report daily at 9am",
            store,
        )
        assert "Scheduled workflow" in result
        entry = store.list_all()[0]
        assert entry.workflow_id == "equity-report"
        assert entry.action == "run workflow equity-report"

    def test_workflow_command_uses_current_workflow(self, store):
        result = handle_schedule_command(
            "/schedule workflow current weekdays at 8am",
            store,
            default_workflow_id="wf-current",
        )
        assert "Scheduled workflow" in result
        entry = store.list_all()[0]
        assert entry.workflow_id == "wf-current"

    def test_workflow_command_accepts_inputs_and_profile(self, store):
        result = handle_schedule_command(
            "/schedule workflow equity-report daily at 9am --input watchlist_path=/tmp/watchlist.csv --input max_items=25 --profile long_running",
            store,
        )
        assert "Scheduled workflow" in result
        assert "Inputs: max_items, watchlist_path" in result
        assert "Run profile: `long_running`" in result
        entry = store.list_all()[0]
        assert entry.workflow_id == "equity-report"
        assert entry.workflow_inputs == {
            "watchlist_path": "/tmp/watchlist.csv",
            "max_items": 25,
        }
        assert entry.workflow_run_policy == {"profile": "long_running"}

    def test_workflow_command_requires_current_context_when_requested(self, store):
        result = handle_schedule_command(
            "/schedule workflow current daily at 9am",
            store,
        )
        assert "No current workflow is available" in result

    def test_list_empty(self, store):
        result = handle_schedule_command("/schedule list", store)
        assert "No scheduled" in result

    def test_list_with_entries(self, store):
        store.add(ScheduleEntry(name="task-1", trigger="every 1h", action="x"))
        store.add(ScheduleEntry(name="task-2", trigger="every 2h", action="y"))
        result = handle_schedule_command("/schedule list", store)
        assert "task-1" in result
        assert "task-2" in result
        assert "Scheduled Tasks" in result

    def test_remove_command(self, store):
        entry = ScheduleEntry(name="rm-me", trigger="every 1h", action="x")
        store.add(entry)
        result = handle_schedule_command(f"/schedule remove {entry.id}", store)
        assert "Removed" in result
        assert store.list_all() == []

    def test_remove_by_name(self, store):
        store.add(ScheduleEntry(name="rm-me", trigger="every 1h", action="x"))
        result = handle_schedule_command("/schedule remove rm-me", store)
        assert "Removed" in result

    def test_remove_not_found(self, store):
        result = handle_schedule_command("/schedule remove ghost", store)
        assert "not found" in result

    def test_pause_command(self, store):
        entry = ScheduleEntry(name="pausable", trigger="every 1h", action="x")
        store.add(entry)
        result = handle_schedule_command(f"/schedule pause {entry.id}", store)
        assert "Paused" in result
        assert store.get(entry.id).enabled is False

    def test_resume_command(self, store):
        entry = ScheduleEntry(
            name="resumable", trigger="every 1h", action="x", enabled=False
        )
        store.add(entry)
        result = handle_schedule_command(f"/schedule resume {entry.id}", store)
        assert "Resumed" in result
        assert store.get(entry.id).enabled is True

    def test_pause_not_found(self, store):
        result = handle_schedule_command("/schedule pause ghost", store)
        assert "not found" in result

    def test_resume_not_found(self, store):
        result = handle_schedule_command("/schedule resume ghost", store)
        assert "not found" in result

    def test_history_command(self, store, history):
        entry = ScheduleEntry(name="with-hist", trigger="every 1h", action="x")
        store.add(entry)
        history.add_record(ScheduleRunRecord(
            schedule_id=entry.id,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            status="success",
            result_summary="all good",
        ))
        result = handle_schedule_command(
            f"/schedule history {entry.id}", store, history
        )
        assert "Run History" in result
        assert "OK" in result

    def test_history_empty(self, store, history):
        entry = ScheduleEntry(name="no-hist", trigger="every 1h", action="x")
        store.add(entry)
        result = handle_schedule_command(
            f"/schedule history {entry.id}", store, history
        )
        assert "No run history" in result

    def test_history_no_store(self, store):
        entry = ScheduleEntry(name="no-store", trigger="every 1h", action="x")
        store.add(entry)
        result = handle_schedule_command(f"/schedule history {entry.id}", store)
        assert "No history store" in result

    def test_unknown_subcommand(self, store):
        result = handle_schedule_command("/schedule blah", store)
        assert "Usage" in result

    def test_no_subcommand(self, store):
        result = handle_schedule_command("/schedule", store)
        assert "Usage" in result

    def test_without_slash_prefix(self, store):
        result = handle_schedule_command(
            'add "test task" every 2h', store
        )
        assert "Scheduled" in result

    def test_list_shows_paused_status(self, store):
        entry = ScheduleEntry(
            name="paused-task", trigger="every 1h", action="x", enabled=False
        )
        store.add(entry)
        result = handle_schedule_command("/schedule list", store)
        assert "paused" in result

    def test_nl_fallback_creates_schedule(self, store):
        """Unrecognized subcommand that matches NL pattern auto-creates a schedule."""
        result = handle_schedule_command(
            "remind me to check the portfolio every Monday", store
        )
        assert "Scheduled" in result
        assert len(store.list_all()) == 1
        assert store.list_all()[0].action == "check the portfolio"

    def test_nl_fallback_with_schedule_prefix(self, store):
        result = handle_schedule_command(
            "/schedule run equity report daily", store
        )
        assert "Scheduled" in result
        assert len(store.list_all()) == 1

    def test_nl_fallback_non_matching_returns_usage(self, store):
        """Gibberish that doesn't match NL patterns still returns usage."""
        result = handle_schedule_command("hello world foo bar", store)
        assert "Usage" in result


# =========================================================================
# Delivery target parsing
# =========================================================================


class TestDeliveryTargetParsing:
    def test_default_surface(self):
        dt = DeliveryTarget()
        assert dt.surface == "cli"

    def test_telegram_surface(self):
        dt = DeliveryTarget(surface="telegram", conversation_key="chat-123")
        assert dt.surface == "telegram"
        assert dt.conversation_key == "chat-123"

    def test_fallback_policies(self):
        for policy in ("store_and_notify", "private_surface", "drop"):
            dt = DeliveryTarget(fallback_policy=policy)
            assert dt.fallback_policy == policy

    def test_schedule_entry_with_delivery_target(self):
        entry = ScheduleEntry(
            name="with-target",
            trigger="every 1h",
            action="report",
            delivery_target=DeliveryTarget(
                surface="telegram",
                conversation_key="group-1",
                fallback_policy="private_surface",
            ),
        )
        assert entry.delivery_target.surface == "telegram"
        data = entry.model_dump(mode="json")
        restored = ScheduleEntry.model_validate(data)
        assert restored.delivery_target.surface == "telegram"
        assert restored.delivery_target.fallback_policy == "private_surface"

    def test_schedule_entry_with_workflow_binding(self):
        entry = ScheduleEntry(
            name="run workflow equity-report",
            trigger="daily at 9am",
            action="run workflow equity-report",
            workflow_id="equity-report",
            workflow_inputs={"watchlist_path": "/tmp/watchlist.csv"},
            workflow_run_policy={"profile": "long_running"},
        )
        data = entry.model_dump(mode="json")
        restored = ScheduleEntry.model_validate(data)
        assert restored.workflow_id == "equity-report"
        assert restored.workflow_inputs == {"watchlist_path": "/tmp/watchlist.csv"}
        assert restored.workflow_run_policy == {"profile": "long_running"}

    def test_schedule_entry_with_trigger_context(self):
        entry = ScheduleEntry(
            name="with-ctx",
            trigger="every 1h",
            action="report",
            trigger_context=TriggerContext(
                project_id="proj-x",
                user_id="user-1",
            ),
        )
        assert entry.trigger_context.source_surface == "schedule"
        assert entry.trigger_context.project_id == "proj-x"


# =========================================================================
# SchedulerAuthority (Task 2-2)
# =========================================================================


class TestSchedulerAuthority:
    def test_enum_values(self):
        assert SchedulerAuthority.SERVICE.value == "service"
        assert SchedulerAuthority.SERVER.value == "server"

    def test_resolve_no_lease_file(self, tmp_path):
        mgr = ScheduleLeaseManager(path=str(tmp_path / "nonexistent.json"))
        with patch(
            "dan.server.concierge.scheduler.ScheduleLeaseManager",
            return_value=mgr,
        ):
            assert resolve_scheduler_authority() == SchedulerAuthority.SERVER

    def test_resolve_stale_lease(self, tmp_path):
        path = str(tmp_path / "lease.json")
        mgr = ScheduleLeaseManager(path=path, stale_after_seconds=0)
        mgr.acquire("old-owner")
        with patch(
            "dan.server.concierge.scheduler.ScheduleLeaseManager",
            return_value=mgr,
        ):
            assert resolve_scheduler_authority() == SchedulerAuthority.SERVER

    def test_resolve_active_lease(self, tmp_path):
        path = str(tmp_path / "lease.json")
        mgr = ScheduleLeaseManager(path=path, stale_after_seconds=600)
        mgr.acquire("service-abcd1234")
        with patch(
            "dan.server.concierge.scheduler.ScheduleLeaseManager",
            return_value=mgr,
        ):
            assert resolve_scheduler_authority() == SchedulerAuthority.SERVICE


# =========================================================================
# ScheduleLease + ScheduleLeaseManager (Task 2-3)
# =========================================================================


class TestScheduleLease:
    def test_lease_not_stale(self):
        now = datetime.now(timezone.utc)
        lease = ScheduleLease(
            owner_id="test",
            acquired_at=now,
            expires_at=now + timedelta(seconds=300),
            stale_after_seconds=120,
        )
        assert not lease.is_stale
        assert not lease.is_expired

    def test_lease_stale(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=200)
        lease = ScheduleLease(
            owner_id="test",
            acquired_at=old,
            expires_at=old + timedelta(seconds=300),
            stale_after_seconds=120,
        )
        assert lease.is_stale

    def test_lease_expired(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=400)
        lease = ScheduleLease(
            owner_id="test",
            acquired_at=old,
            expires_at=old + timedelta(seconds=300),
        )
        assert lease.is_expired

    def test_lease_stores_pid(self):
        now = datetime.now(timezone.utc)
        lease = ScheduleLease(
            owner_id="abc",
            pid=42,
            acquired_at=now,
            expires_at=now + timedelta(seconds=300),
        )
        assert lease.pid == 42

    def test_lease_pid_defaults_to_zero(self):
        now = datetime.now(timezone.utc)
        lease = ScheduleLease(
            owner_id="abc",
            acquired_at=now,
            expires_at=now + timedelta(seconds=300),
        )
        assert lease.pid == 0

    def test_serialization(self):
        now = datetime.now(timezone.utc)
        lease = ScheduleLease(
            owner_id="abc",
            acquired_at=now,
            expires_at=now + timedelta(seconds=300),
        )
        data = lease.model_dump(mode="json")
        restored = ScheduleLease.model_validate(data)
        assert restored.owner_id == "abc"


class TestScheduleLeaseManager:
    @pytest.fixture
    def mgr(self, tmp_path):
        return ScheduleLeaseManager(
            path=str(tmp_path / "lease.json"), stale_after_seconds=120
        )

    def test_acquire_new(self, mgr):
        lease = mgr.acquire("owner-1")
        assert lease is not None
        assert lease.owner_id == "owner-1"

    def test_acquire_conflict(self, mgr):
        mgr.acquire("owner-1")
        lease = mgr.acquire("owner-2")
        assert lease is None

    def test_acquire_same_owner(self, mgr):
        mgr.acquire("owner-1")
        lease = mgr.acquire("owner-1")
        assert lease is not None

    def test_acquire_stale(self, tmp_path):
        mgr = ScheduleLeaseManager(
            path=str(tmp_path / "lease.json"), stale_after_seconds=0
        )
        mgr.acquire("owner-1")
        lease = mgr.acquire("owner-2")
        assert lease is not None
        assert lease.owner_id == "owner-2"

    def test_renew(self, mgr):
        mgr.acquire("owner-1")
        renewed = mgr.renew("owner-1")
        assert renewed is not None
        assert renewed.owner_id == "owner-1"

    def test_renew_wrong_owner(self, mgr):
        mgr.acquire("owner-1")
        assert mgr.renew("owner-2") is None

    def test_release(self, mgr):
        mgr.acquire("owner-1")
        assert mgr.release("owner-1") is True
        assert mgr.read_lease() is None

    def test_release_wrong_owner(self, mgr):
        mgr.acquire("owner-1")
        assert mgr.release("owner-2") is False
        assert mgr.read_lease() is not None

    def test_release_no_lease(self, mgr):
        assert mgr.release("anyone") is True

    def test_acquire_stores_current_pid(self, mgr):
        lease = mgr.acquire("owner-1")
        assert lease is not None
        assert lease.pid == os.getpid()

    def test_acquire_takes_over_dead_pid(self, tmp_path):
        """Lease with a dead PID can be taken over by a new owner."""
        path = str(tmp_path / "dead_lease.json")
        mgr = ScheduleLeaseManager(path=path, stale_after_seconds=99999)
        dead_pid = 99999999
        lease_data = {
            "owner_id": "dead-owner",
            "pid": dead_pid,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "stale_after_seconds": 99999,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(lease_data, f)

        new_lease = mgr.acquire("new-owner")
        assert new_lease is not None
        assert new_lease.owner_id == "new-owner"
        assert new_lease.pid == os.getpid()

    def test_is_held_by_current_process_yes(self, mgr):
        mgr.acquire("owner-1")
        assert mgr.is_held_by_current_process() is True

    def test_is_held_by_current_process_no_lease(self, mgr):
        assert mgr.is_held_by_current_process() is False

    def test_is_held_by_current_process_other_pid(self, tmp_path):
        path = str(tmp_path / "other_pid_lease.json")
        mgr = ScheduleLeaseManager(path=path)
        lease_data = {
            "owner_id": "other",
            "pid": 99999999,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(lease_data, f)
        assert mgr.is_held_by_current_process() is False

    def test_read_nonexistent(self, mgr):
        assert mgr.read_lease() is None

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "lease.json")
        mgr1 = ScheduleLeaseManager(path=path)
        mgr1.acquire("owner-1")

        mgr2 = ScheduleLeaseManager(path=path)
        lease = mgr2.read_lease()
        assert lease is not None
        assert lease.owner_id == "owner-1"


# =========================================================================
# TaskScheduler with lease (Task 2-3 continued)
# =========================================================================


class TestTaskSchedulerWithLease:
    @pytest.fixture
    def store(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        s = ScheduleStore(path=path)
        s.load()
        return s

    @pytest.mark.asyncio
    async def test_start_acquires_lease(self, store, tmp_path):
        lease_mgr = ScheduleLeaseManager(
            path=str(tmp_path / "lease.json"), stale_after_seconds=120
        )
        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(
            store, dispatch,
            poll_interval=0.05,
            lease_manager=lease_mgr,
            authority=SchedulerAuthority.SERVER,
        )
        await scheduler.start()
        assert scheduler._task is not None
        lease = lease_mgr.read_lease()
        assert lease is not None
        assert lease.owner_id == scheduler.owner_id
        await scheduler.stop()
        assert lease_mgr.read_lease() is None

    @pytest.mark.asyncio
    async def test_start_blocked_by_lease(self, store, tmp_path):
        lease_path = str(tmp_path / "lease.json")
        other_mgr = ScheduleLeaseManager(path=lease_path, stale_after_seconds=600)
        other_mgr.acquire("service-other")

        lease_mgr = ScheduleLeaseManager(path=lease_path, stale_after_seconds=600)
        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(
            store, dispatch,
            poll_interval=0.05,
            lease_manager=lease_mgr,
            authority=SchedulerAuthority.SERVER,
        )
        await scheduler.start()
        assert scheduler._task is None

    @pytest.mark.asyncio
    async def test_no_lease_manager_always_fires(self, store):
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="no-lease",
            trigger="every 30m",
            action="fire",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        dispatch = AsyncMock(return_value="ok")
        scheduler = TaskScheduler(store, dispatch, poll_interval=0.05)
        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop()
        assert dispatch.call_count >= 1


# =========================================================================
# Natural language schedule parsing (Task 3-5)
# =========================================================================


class TestParseNlSchedule:
    def test_remind_me_every_monday(self):
        result = parse_nl_schedule("remind me to check the portfolio every Monday")
        assert result is not None
        action, trigger = result
        assert "check the portfolio" in action
        assert "every Monday" in trigger

    def test_remind_me_daily(self):
        result = parse_nl_schedule("remind me to review tasks daily")
        assert result is not None
        action, trigger = result
        assert "review tasks" in action
        assert trigger == "every day at 9am"

    def test_do_daily(self):
        result = parse_nl_schedule("run equity report daily")
        assert result is not None
        action, trigger = result
        assert "equity report" in action

    def test_check_weekly(self):
        result = parse_nl_schedule("check portfolio weekly")
        assert result is not None
        _, trigger = result
        assert trigger == "every 7d"

    def test_run_at_time(self):
        result = parse_nl_schedule("run backup at 3pm")
        assert result is not None
        action, trigger = result
        assert "backup" in action
        assert "daily at 3pm" in trigger

    def test_run_at_time_with_minutes(self):
        result = parse_nl_schedule("send report at 8:30am")
        assert result is not None
        action, trigger = result
        assert "report" in action
        assert "8:30am" in trigger

    def test_every_n_hours(self):
        result = parse_nl_schedule("check server health every 2 hours")
        assert result is not None
        action, trigger = result
        assert "check server health" in action
        assert trigger == "every 2 hours"

    def test_no_match(self):
        assert parse_nl_schedule("hello world") is None

    def test_empty(self):
        assert parse_nl_schedule("") is None

    def test_generate_monthly(self):
        result = parse_nl_schedule("generate monthly report monthly")
        assert result is not None
        _, trigger = result
        assert trigger == "every 30d"


# =========================================================================
# Delivery routing (Task 4-1) + Fallback (Task 4-2)
# =========================================================================


class TestDeliverResult:
    @pytest.mark.asyncio
    async def test_broadcast_event(self):
        entry = ScheduleEntry(
            name="delivery-test",
            trigger="every 1h",
            action="report",
            delivery_target=DeliveryTarget(
                surface="telegram", conversation_key="chat-1"
            ),
        )
        bus = AsyncMock()
        bus.broadcast = lambda event: None
        mock_bus = type("MockBus", (), {"broadcast": lambda self, e: None})()
        await deliver_result(entry, "result data", event_bus=mock_bus)

    @pytest.mark.asyncio
    async def test_broadcast_event_content(self):
        entry = ScheduleEntry(
            name="content-test",
            trigger="every 1h",
            action="report",
            delivery_target=DeliveryTarget(surface="cli"),
        )
        captured = []

        class CaptureBus:
            def broadcast(self, event):
                captured.append(event)

        await deliver_result(entry, "the result", event_bus=CaptureBus())
        assert len(captured) == 1
        assert captured[0]["event_type"] == "schedule_result_ready"
        assert captured[0]["schedule_name"] == "content-test"
        assert captured[0]["result"] == "the result"
        assert captured[0]["surface"] == "cli"
        assert "timestamp" in captured[0]
        assert captured[0]["surface_id"] is None
        assert captured[0]["data"]["status"] == "ready"
        assert captured[0]["data"]["fallback"] is False

    @pytest.mark.asyncio
    async def test_no_event_bus(self):
        entry = ScheduleEntry(
            name="no-bus",
            trigger="every 1h",
            action="report",
        )
        await deliver_result(entry, "ok", event_bus=None)

    @pytest.mark.asyncio
    async def test_applies_fallback_on_broadcast_failure(self):
        """When delivery broadcast fails, fallback policy is applied."""
        entry = ScheduleEntry(
            name="fail-delivery",
            trigger="every 1h",
            action="report",
            delivery_target=DeliveryTarget(
                surface="telegram",
                fallback_policy="store_and_notify",
            ),
        )
        events = []

        class FailThenCaptureBus:
            def __init__(self):
                self._calls = 0

            def broadcast(self, event):
                self._calls += 1
                if self._calls == 1:
                    raise ConnectionError("adapter down")
                events.append(event)

        await deliver_result(entry, "result data", event_bus=FailThenCaptureBus())
        assert len(events) == 1
        assert events[0]["fallback"] is True
        assert events[0]["surface"] == "notification"
        assert events[0]["data"]["fallback"] is True
        assert events[0]["data"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_no_fallback_when_broadcast_succeeds(self):
        """Successful broadcast should not trigger fallback."""
        entry = ScheduleEntry(
            name="ok-delivery",
            trigger="every 1h",
            action="report",
            delivery_target=DeliveryTarget(surface="cli"),
        )
        events = []

        class CaptureBus:
            def broadcast(self, event):
                events.append(event)

        await deliver_result(entry, "result", event_bus=CaptureBus())
        assert len(events) == 1
        assert events[0].get("fallback") is None


class TestApplyFallbackPolicy:
    @pytest.mark.asyncio
    async def test_drop_policy(self):
        entry = ScheduleEntry(
            name="drop-test",
            trigger="every 1h",
            action="x",
            delivery_target=DeliveryTarget(fallback_policy="drop"),
        )
        await apply_fallback_policy(entry, "error msg")

    @pytest.mark.asyncio
    async def test_store_and_notify(self):
        entry = ScheduleEntry(
            name="notify-test",
            trigger="every 1h",
            action="x",
            delivery_target=DeliveryTarget(fallback_policy="store_and_notify"),
        )
        captured = []

        class CaptureBus:
            def broadcast(self, event):
                captured.append(event)

        await apply_fallback_policy(entry, "boom", event_bus=CaptureBus())
        assert len(captured) == 1
        assert captured[0]["surface"] == "notification"
        assert captured[0]["fallback"] is True
        assert "boom" in captured[0]["result"]
        assert captured[0]["data"]["status"] == "error"
        assert captured[0]["data"]["fallback"] is True

    @pytest.mark.asyncio
    async def test_private_surface(self):
        entry = ScheduleEntry(
            name="private-test",
            trigger="every 1h",
            action="x",
            delivery_target=DeliveryTarget(
                fallback_policy="private_surface",
                user_id="user-42",
            ),
        )
        captured = []

        class CaptureBus:
            def broadcast(self, event):
                captured.append(event)

        await apply_fallback_policy(entry, "failed", event_bus=CaptureBus())
        assert len(captured) == 1
        assert captured[0]["surface"] == "private"
        assert captured[0]["user_id"] == "user-42"
        assert captured[0]["data"]["status"] == "error"


# =========================================================================
# Factory functions (Task 2-4 / 2-5)
# =========================================================================


class TestFactoryFunctions:
    @pytest.fixture
    def store(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        s = ScheduleStore(path=path)
        s.load()
        return s

    def test_create_service_scheduler(self, store, tmp_path):
        dispatch = AsyncMock(return_value="ok")
        scheduler = create_service_scheduler(
            store, dispatch,
            lease_path=str(tmp_path / "lease.json"),
        )
        assert scheduler.authority == SchedulerAuthority.SERVICE
        assert scheduler._lease_manager is not None

    def test_create_server_scheduler(self, store, tmp_path):
        dispatch = AsyncMock(return_value="ok")
        scheduler = create_server_scheduler(
            store, dispatch,
            lease_path=str(tmp_path / "lease.json"),
        )
        assert scheduler.authority == SchedulerAuthority.SERVER
        assert scheduler._lease_manager is not None


# =========================================================================
# Lease integration test (Task 5-3)
# =========================================================================


class TestLeaseIntegration:
    """Integration tests verifying lease prevents double-firing."""

    @pytest.fixture
    def store(self, tmp_path):
        path = str(tmp_path / "schedules.json")
        s = ScheduleStore(path=path)
        s.load()
        return s

    @pytest.fixture
    def lease_path(self, tmp_path):
        return str(tmp_path / "lease.json")

    @pytest.mark.asyncio
    async def test_service_holds_lease_server_backs_off(self, store, lease_path):
        """When service holds the lease, server cannot start its scheduler."""
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="shared-task",
            trigger="every 30m",
            action="do work",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        service_dispatch = AsyncMock(return_value="service-ok")
        server_dispatch = AsyncMock(return_value="server-ok")

        service_scheduler = create_service_scheduler(
            store, service_dispatch, lease_path=lease_path,
        )
        await service_scheduler.start()
        assert service_scheduler._task is not None

        server_scheduler = create_server_scheduler(
            store, server_dispatch, lease_path=lease_path,
        )
        await server_scheduler.start()
        assert server_scheduler._task is None

        await asyncio.sleep(0.2)
        await service_scheduler.stop()

        assert service_dispatch.call_count >= 1
        server_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_lease_allows_takeover(self, store, lease_path):
        """When a service lease goes stale, server can take over."""
        lease_mgr = ScheduleLeaseManager(path=lease_path, stale_after_seconds=0)
        lease_mgr.acquire("dead-service")

        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="orphan-task",
            trigger="every 30m",
            action="rescue me",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        server_dispatch = AsyncMock(return_value="server-ok")
        server_scheduler = TaskScheduler(
            store, server_dispatch,
            poll_interval=0.05,
            lease_manager=ScheduleLeaseManager(
                path=lease_path, stale_after_seconds=0
            ),
            authority=SchedulerAuthority.SERVER,
        )
        await server_scheduler.start()
        assert server_scheduler._task is not None

        await asyncio.sleep(0.2)
        await server_scheduler.stop()
        assert server_dispatch.call_count >= 1

    @pytest.mark.asyncio
    async def test_dead_pid_lease_allows_server_takeover(self, store, lease_path):
        """When the lease owner's PID is dead, server scheduler takes over."""
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="orphan-task",
            trigger="every 30m",
            action="rescue me",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        dead_pid = 99999999
        lease_data = {
            "owner_id": "dead-service",
            "pid": dead_pid,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "stale_after_seconds": 99999,
        }
        Path(lease_path).parent.mkdir(parents=True, exist_ok=True)
        with open(lease_path, "w") as f:
            json.dump(lease_data, f)

        server_dispatch = AsyncMock(return_value="server-ok")
        server_scheduler = TaskScheduler(
            store, server_dispatch,
            poll_interval=0.05,
            lease_manager=ScheduleLeaseManager(path=lease_path),
            authority=SchedulerAuthority.SERVER,
        )
        await server_scheduler.start()
        assert server_scheduler._task is not None

        await asyncio.sleep(0.2)
        await server_scheduler.stop()
        assert server_dispatch.call_count >= 1

    @pytest.mark.asyncio
    async def test_no_double_fire_with_lease(self, store, lease_path):
        """Two schedulers with leases: only one fires."""
        now = datetime.now(timezone.utc)
        entry = ScheduleEntry(
            name="exclusive-task",
            trigger="every 30m",
            action="only once",
            next_run=now - timedelta(seconds=5),
        )
        store.add(entry)

        dispatch_a = AsyncMock(return_value="a-ok")
        dispatch_b = AsyncMock(return_value="b-ok")

        sched_a = TaskScheduler(
            store, dispatch_a,
            poll_interval=0.05,
            lease_manager=ScheduleLeaseManager(
                path=lease_path, stale_after_seconds=600
            ),
            authority=SchedulerAuthority.SERVICE,
        )
        sched_b = TaskScheduler(
            store, dispatch_b,
            poll_interval=0.05,
            lease_manager=ScheduleLeaseManager(
                path=lease_path, stale_after_seconds=600
            ),
            authority=SchedulerAuthority.SERVER,
        )

        await sched_a.start()
        await sched_b.start()

        await asyncio.sleep(0.2)
        await sched_a.stop()
        await sched_b.stop()

        total_fires = dispatch_a.call_count + dispatch_b.call_count
        assert total_fires >= 1
        assert dispatch_b.call_count == 0 or dispatch_a.call_count == 0
