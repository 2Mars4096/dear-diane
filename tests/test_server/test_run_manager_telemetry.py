from __future__ import annotations

import pytest

from dan.server.run_manager import RunManager, RunRecord


@pytest.mark.asyncio
async def test_emit_workflow_telemetry_delegates_to_finalizer(monkeypatch) -> None:
    manager = RunManager()
    record = RunRecord(run_id="run-telemetry", graph_id="wf-telemetry")
    captured: list[RunRecord] = []

    async def _fake_emit(self, target: RunRecord) -> None:
        captured.append(target)

    monkeypatch.setattr(type(manager._finalizer), "_emit_workflow_telemetry", _fake_emit)

    await manager._emit_workflow_telemetry(record)

    assert captured == [record]
