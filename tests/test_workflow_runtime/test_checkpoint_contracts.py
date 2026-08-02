from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
from dan.engine.executor import ExecutionContext, ExecutorRegistry
from dan.models.graph import Graph
from dan.models.nodes import CodeOperator
from dan.models.ports import OutputPort


class _RecordingCheckpointStore:
    def __init__(self) -> None:
        self.saved: dict[str, dict[str, Any]] = {}
        self.save_calls: list[str] = []
        self.load_calls: list[str] = []

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        self.save_calls.append(run_id)
        self.saved[run_id] = copy.deepcopy(state)

    async def load(self, run_id: str) -> dict[str, Any] | None:
        self.load_calls.append(run_id)
        snapshot = self.saved.get(run_id)
        return copy.deepcopy(snapshot) if snapshot is not None else None

    async def list_runs(self) -> list[str]:
        return sorted(self.saved)


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: int = 0

    async def execute(
        self,
        node: Any,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        self.calls += 1
        return NodeResult(
            outputs={"value": 7},
            status=NodeStatus.COMPLETED,
        )


def _make_engine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    checkpoint_store: _RecordingCheckpointStore,
    executor: _RecordingExecutor,
) -> Engine:
    registry = ExecutorRegistry()
    registry.register("code_operator", executor)
    monkeypatch.setattr(Engine, "_build_provider_registry", lambda self: SimpleNamespace())
    monkeypatch.setattr(Engine, "_build_embedding_registry", lambda self: SimpleNamespace())
    return Engine(
        config=EngineConfig(
            checkpoint_enabled=False,
            checkpoint_batch_size=1,
            checkpoint_interval_sec=0.0,
            memory_enabled=False,
            state_store_enabled=False,
        ),
        executor_registry=registry,
        checkpoint_store=checkpoint_store,
    )


@pytest.mark.asyncio
async def test_engine_run_persists_checkpoint_and_resume_reuses_saved_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint_store = _RecordingCheckpointStore()
    executor = _RecordingExecutor()
    engine = _make_engine(
        monkeypatch,
        checkpoint_store=checkpoint_store,
        executor=executor,
    )
    graph = Graph(
        nodes=[
            CodeOperator(
                id="emit",
                name="Emit",
                code="result = {'value': 7}",
                output_ports=[OutputPort(name="value")],
            )
        ],
        edges=[],
        entry_points=["emit"],
        exit_points=["emit"],
    )

    run_result = await engine.run(graph, run_id="run-1")
    assert run_result.success is True
    assert run_result.outputs["value"] == 7
    assert executor.calls == 1
    assert checkpoint_store.save_calls
    assert checkpoint_store.saved["run-1"]["checkpoint_data"]["completed_node_ids"] == ["emit"]
    assert checkpoint_store.saved["run-1"]["state"]["node_statuses"]["emit"] == "completed"

    resumed_result = await engine.resume(graph, run_id="run-1")
    assert resumed_result.success is True
    assert resumed_result.outputs["value"] == 7
    assert executor.calls == 1
    assert checkpoint_store.load_calls == ["run-1"]
