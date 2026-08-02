"""Tests for gateway approval/cancel race condition (22-3 Task 2)."""

from __future__ import annotations

import asyncio

import pytest

from dan.builder import workflow
from dan.server.run_manager import RunManager, RunRecord, RunStatus


@pytest.fixture
def rm() -> RunManager:
    return RunManager()


def _make_pending_record(rm: RunManager, run_id: str) -> tuple[RunRecord, str]:
    """Register a pending approval run and return (record, request_id)."""
    request_id = f"req-{run_id}"
    evt = rm.register_meta_approval(request_id, run_id=run_id)
    record = RunRecord(run_id=run_id, graph_id="test-wf", status=RunStatus.PENDING)
    rm._runs[run_id] = record
    return record, request_id


class TestApproveAndStartRace:
    @pytest.mark.asyncio
    async def test_cancelled_then_approve_returns_none(self, rm: RunManager):
        """If a run is cancelled before approve_and_start, it should return None."""
        from dan.models.graph import Graph

        record, request_id = _make_pending_record(rm, "race-1")
        rm.cancel_run("race-1")
        assert record.status == RunStatus.CANCELLED

        graph = Graph()
        result = await rm.approve_and_start(
            run_id="race-1",
            graph=graph,
            graph_id="test-wf",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_and_start_succeeds_when_pending(self, rm: RunManager):
        """If a run is still pending, approve_and_start should start it."""
        wf = workflow("test-wf")
        wf.code("compute", code="result = 'ok'")
        graph = wf.build()

        record, request_id = _make_pending_record(rm, "race-2")
        assert record.status == RunStatus.PENDING

        result = await rm.approve_and_start(
            run_id="race-2",
            graph=graph,
            graph_id="test-wf",
        )
        assert result is not None
        assert result.run_id == "race-2"

    @pytest.mark.asyncio
    async def test_nonexistent_run_returns_none(self, rm: RunManager):
        """approve_and_start on a missing run_id returns None."""
        from dan.models.graph import Graph

        graph = Graph()
        result = await rm.approve_and_start(
            run_id="does-not-exist",
            graph=graph,
            graph_id="test-wf",
        )
        assert result is None
