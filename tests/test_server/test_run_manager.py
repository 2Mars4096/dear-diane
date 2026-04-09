from __future__ import annotations

import asyncio

import pytest

from dan.executor_defaults import (
    DEFAULT_DIRECT_LEGACY_COMPUTE_TYPES,
    DEFAULT_WORKER_RUNTIME_COMPUTE_TYPES,
)
from dan.server.run_manager import RunManager, RunRecord, RunStatus
from dan.worker.adapters import LegacyWorkerAdapterExecutor
from dan.worker.adapters import WorkerBackedLegacyComputeExecutor
from dan.worker.executor import WorkerExecutor


def test_make_executor_registry_routes_ready_families_through_worker_runtime() -> None:
    manager = RunManager()

    registry = manager._make_executor_registry()

    worker_executor = registry.get("worker")
    assert isinstance(worker_executor, WorkerExecutor)
    for node_type in DEFAULT_WORKER_RUNTIME_COMPUTE_TYPES:
        executor = registry.get(node_type)
        assert isinstance(executor, WorkerBackedLegacyComputeExecutor)
        assert executor.worker_executor is worker_executor
    for node_type in DEFAULT_DIRECT_LEGACY_COMPUTE_TYPES:
        assert not isinstance(registry.get(node_type), LegacyWorkerAdapterExecutor)
        assert not isinstance(registry.get(node_type), WorkerBackedLegacyComputeExecutor)
    assert registry.get("tool_operator").registry is worker_executor._tool.registry


@pytest.mark.asyncio
async def test_shutdown_cancels_active_run_tasks() -> None:
    manager = RunManager()
    started = asyncio.Event()

    async def _sleep_forever() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(_sleep_forever())
    await started.wait()

    record = RunRecord(
        run_id="run-active",
        graph_id="graph-active",
        status=RunStatus.RUNNING,
    )
    manager._runs[record.run_id] = record
    manager._tasks[record.run_id] = task

    await manager.shutdown(timeout=0.1)

    assert task.cancelled()
    assert manager.get_run(record.run_id) is not None
    assert manager.get_run(record.run_id).status == RunStatus.CANCELLED
    assert manager.get_run(record.run_id).error == "Cancelled: server_shutdown"
