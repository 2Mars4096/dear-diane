from __future__ import annotations

import asyncio

import pytest

from dan.engine.scheduler import RunResult
from dan.server.run_manager import RunManager, RunRecord, RunStatus
from dan.worker.model import Worker


@pytest.mark.asyncio
async def test_schedule_reflection_background_uses_worker_helper_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = RunManager()
    record = RunRecord(
        run_id="run-main",
        graph_id="workflow-main",
        status=RunStatus.FAILED,
        node_statuses={"writer": "failed"},
    )
    record.result = RunResult(
        run_id=record.run_id,
        success=False,
        errors={"writer": "boom"},
        metadata={
            "repair_lineage": {"writer": {"attempts": 1}},
            "writer": {
                "runtime_repair_summary": {
                    "action": "retry",
                    "reason": "bad output",
                },
            },
        },
    )
    record.events = [
        {
            "event_type": "node_failed",
            "node_id": "writer",
            "data": {"message": "boom"},
        },
    ]

    captured: dict[str, object] = {}

    async def fake_start_run(graph, graph_id, inputs=None, run_id=None, **kwargs):
        captured["graph"] = graph
        captured["graph_id"] = graph_id
        captured["inputs"] = inputs
        captured["run_id"] = run_id
        return RunRecord(run_id=run_id or "reflection-run", graph_id=graph_id)

    monkeypatch.setattr(manager, "start_run", fake_start_run)

    manager._schedule_reflection_background(record)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert captured["graph_id"] == "workflow-main"
    assert captured["run_id"] == "reflection-run-main"
    inputs = captured["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["run_id"] == "run-main"
    assert inputs["run_errors"] == [{"node_id": "writer", "error": "boom", "node_type": "failed"}]
    assert inputs["runtime_repair_lineage"] == {"writer": {"attempts": 1}}

    graph = captured["graph"]
    assert hasattr(graph, "node_by_id")
    node = graph.node_by_id("reflection-auto")
    assert isinstance(node, Worker)
    assert node.role == "reflection"
    assert node.metadata["reflection_source"] == "last_run"
    assert node.metadata["scoped_helper"] == "auto_reflection"
    assert [port.name for port in node.output_ports] == [
        "principles",
        "principle_count",
        "source",
        "text",
    ]
    assert {port.name for port in node.input_ports} == {
        "run_id",
        "run_errors",
        "run_events",
        "node_statuses",
        "runtime_repair_lineage",
        "runtime_repair_summaries",
    }
    assert graph.entry_points == ["reflection-auto"]
    assert graph.exit_points == ["reflection-auto"]
