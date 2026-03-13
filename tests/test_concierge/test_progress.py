"""Tests for concierge ProgressReporter — push progress model."""

from __future__ import annotations

import asyncio

import pytest

from dan.server.concierge.models import Project
from dan.server.concierge.progress import ProgressReporter, ProgressSnapshot


def _project(project_id: str = "p1", label: str = "test") -> Project:
    return Project(project_id=project_id, surface_id="whatsapp", label=label)


class FakeRunManager:
    def __init__(self, runs: list[dict] | None = None) -> None:
        self._runs = runs or []

    def list_runs(self) -> list[dict]:
        return self._runs


class TestStartPush:
    @pytest.mark.asyncio
    async def test_push_calls_callback(self):
        runs = [
            {
                "run_id": "r1",
                "status": "running",
                "progress": {"completed_nodes": 2, "total_nodes": 5},
                "usage": {"total_cost_usd": 0.1},
            }
        ]
        project = _project()
        project.linked_run_ids = ["r1"]
        reporter = ProgressReporter(FakeRunManager(runs), activity_tracker=None)

        received: list[str] = []

        async def cb(text: str) -> None:
            received.append(text)

        await reporter.start_push(project, "whatsapp", interval=0.05, callback=cb)

        await asyncio.sleep(0.15)
        reporter.stop_push(project.project_id)
        assert len(received) >= 1
        assert "test" in received[0]

    @pytest.mark.asyncio
    async def test_stop_push_cancels_task(self):
        project = _project()
        project.linked_run_ids = ["r1"]
        runs = [{"run_id": "r1", "status": "running", "progress": {}, "usage": {}}]
        reporter = ProgressReporter(FakeRunManager(runs), activity_tracker=None)

        await reporter.start_push(project, "whatsapp", interval=0.05, callback=_noop_cb)
        assert project.project_id in reporter._push_tasks

        reporter.stop_push(project.project_id)
        assert project.project_id not in reporter._push_tasks

    @pytest.mark.asyncio
    async def test_push_stops_on_completion(self):
        call_count = 0
        runs = [{"run_id": "r1", "status": "completed", "progress": {}, "usage": {}}]
        project = _project()
        project.linked_run_ids = ["r1"]
        reporter = ProgressReporter(FakeRunManager(runs), activity_tracker=None)

        async def cb(text: str) -> None:
            nonlocal call_count
            call_count += 1

        await reporter.start_push(project, "whatsapp", interval=0.05, callback=cb)
        await asyncio.sleep(0.2)

        assert call_count == 1
        task = reporter._push_tasks.get(project.project_id)
        assert task is None or task.done()

    @pytest.mark.asyncio
    async def test_stop_all(self):
        reporter = ProgressReporter(
            FakeRunManager([{"run_id": "r1", "status": "running", "progress": {}, "usage": {}}]),
            activity_tracker=None,
        )
        p1 = _project("p1", "proj-1")
        p1.linked_run_ids = ["r1"]
        p2 = _project("p2", "proj-2")
        p2.linked_run_ids = ["r1"]

        await reporter.start_push(p1, "wa", interval=0.05, callback=_noop_cb)
        await reporter.start_push(p2, "wa", interval=0.05, callback=_noop_cb)
        assert len(reporter._push_tasks) == 2

        reporter.stop_all()
        assert len(reporter._push_tasks) == 0

    @pytest.mark.asyncio
    async def test_start_push_no_callback_is_noop(self):
        reporter = ProgressReporter(FakeRunManager(), activity_tracker=None)
        project = _project()
        await reporter.start_push(project, "whatsapp", interval=0.05)
        assert project.project_id not in reporter._push_tasks


async def _noop_cb(text: str) -> None:
    pass
