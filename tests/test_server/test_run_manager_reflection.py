from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from dan.engine.scheduler import RunResult
from dan.models.graph import Graph, GraphMetadata
from dan.server.run_finalization import RunFinalizer
from dan.server.run_manager import RunManager, RunRecord, RunStatus
from dan.server.workflow_guards import WorkflowContractError
from dan.worker.model import Worker


def _empty_graph() -> Graph:
    return Graph(
        metadata=GraphMetadata(name="empty"),
        nodes=[],
        edges=[],
        entry_points=[],
        exit_points=[],
    )


def _build_finalizer(
    *,
    get_run=None,
    emit_learning_event=None,
    launch_reflection_run=None,
    run_store=None,
    get_experience_store=None,
) -> RunFinalizer:
    async def _noop_launch_reflection_run(graph, graph_id, inputs, run_id):
        return RunRecord(run_id=run_id, graph_id=graph_id)

    def _noop_emit_learning_event(record, event_type, data, node_id=None):
        return None

    return RunFinalizer(
        config=SimpleNamespace(
            default_model="test-model",
            reflection_trigger="on_failure",
            self_evolving_rules_enabled=False,
            error_memory_enabled=False,
            experience_consolidation_interval=5,
        ),
        run_store=run_store,
        telemetry_store=None,
        memory_kernel=None,
        get_run=get_run or (lambda _run_id: None),
        emit_learning_event=emit_learning_event or _noop_emit_learning_event,
        launch_reflection_run=launch_reflection_run or _noop_launch_reflection_run,
        get_error_memory_index=lambda: None,
        get_principle_store=lambda: None,
        get_rule_lifecycle_manager=lambda: None,
        get_experience_store=get_experience_store or (lambda: None),
    )


@pytest.mark.asyncio
async def test_run_finalizer_schedules_reflection_helper_graph() -> None:
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
    emitted: list[tuple[str, dict[str, object]]] = []

    async def fake_launch_reflection_run(graph, graph_id, inputs, run_id):
        captured["graph"] = graph
        captured["graph_id"] = graph_id
        captured["inputs"] = inputs
        captured["run_id"] = run_id
        return RunRecord(run_id=run_id, graph_id=graph_id)

    def emit_learning_event(record, event_type, data, node_id=None):
        emitted.append((event_type, data))

    finalizer = _build_finalizer(
        emit_learning_event=emit_learning_event,
        launch_reflection_run=fake_launch_reflection_run,
    )

    await finalizer.finalize(record)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert captured["graph_id"] == "workflow-main"
    assert captured["run_id"] == "reflection-run-main"
    assert emitted == [
        (
            "reflection_started",
            {
                "source_run_id": "run-main",
                "reflection_run_id": "reflection-run-main",
                "tier": "reflection",
            },
        )
    ]
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


@pytest.mark.asyncio
async def test_run_finalizer_backgrounds_experience_indexing() -> None:
    from dan.engine.experience import WorkflowExperience

    record = RunRecord(
        run_id="run-experience",
        graph_id="workflow-experience",
        status=RunStatus.COMPLETED,
    )
    record.result = RunResult(run_id=record.run_id, success=True)

    emitted: list[tuple[str, dict[str, object]]] = []

    class FakeExpStore:
        def __init__(self) -> None:
            self.persisted: list[WorkflowExperience] = []
            self.index_started = asyncio.Event()
            self.allow_index_finish = asyncio.Event()

        def has_index(self) -> bool:
            return True

        async def load_experience(self, workflow_id: str) -> WorkflowExperience:
            return WorkflowExperience(workflow_id=workflow_id, name="workflow-experience")

        async def write_experience(self, experience: WorkflowExperience) -> None:
            self.persisted.append(experience)

        async def index_saved_experience(self, experience: WorkflowExperience) -> None:
            self.index_started.set()
            await self.allow_index_finish.wait()

    store = FakeExpStore()

    def emit_learning_event(record, event_type, data, node_id=None):
        emitted.append((event_type, data))

    finalizer = _build_finalizer(
        emit_learning_event=emit_learning_event,
        get_experience_store=lambda: store,
    )

    await asyncio.wait_for(finalizer.finalize(record, graph=_empty_graph()), timeout=0.5)
    assert store.persisted
    assert store.persisted[0].workflow_id == "workflow-experience"

    await asyncio.sleep(0)
    assert store.index_started.is_set()
    assert [event_type for event_type, _ in emitted] == [
        "experience_consolidated",
        "experience_indexed",
    ]

    store.allow_index_finish.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_persist_terminal_record_keeps_usage_persistence_in_manager() -> None:
    class FakeRunStore:
        def __init__(self) -> None:
            self.saved: list[tuple[str, str, dict[str, object]]] = []

        def cleanup(self, max_age_days: int) -> int:
            return 0

        def list_summaries(self, limit: int = 10000, workflow_id: str | None = None):
            return []

        def save_summary(self, graph_id: str, run_id: str, summary: dict[str, object]) -> None:
            self.saved.append((graph_id, run_id, summary))

    store = FakeRunStore()
    manager = RunManager(run_store=store)
    record = RunRecord(
        run_id="run-complete",
        graph_id="wf-complete",
        status=RunStatus.COMPLETED,
    )
    record.result = RunResult(
        run_id=record.run_id,
        success=True,
        metadata={
            "writer": {
                "model": "test-model",
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                },
            },
        },
    )

    captured: dict[str, object] = {}

    async def fake_finalize(record_arg: RunRecord, *, graph: Graph | None = None) -> None:
        captured["record"] = record_arg
        captured["graph"] = graph

    manager._finalizer = SimpleNamespace(finalize=fake_finalize)
    graph = _empty_graph()

    await manager._persist_terminal_record(record, graph=graph)

    assert record.node_usage == {
        "writer": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
        }
    }
    assert record.total_prompt_tokens == 11
    assert record.total_completion_tokens == 7
    assert record.total_tokens == 18
    assert store.saved == [("wf-complete", "run-complete", record.snapshot())]
    assert captured == {"record": record, "graph": graph}


@pytest.mark.asyncio
async def test_start_run_enforces_run_readiness_by_default() -> None:
    manager = RunManager()

    with pytest.raises(WorkflowContractError):
        await manager.start_run(_empty_graph(), graph_id="wf-empty")

    assert manager.list_runs() == []


@pytest.mark.asyncio
async def test_start_run_can_bypass_run_readiness_for_synthetic_graphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RunManager()
    captured: dict[str, Graph] = {}

    async def fake_run_task(
        record: RunRecord,
        graph: Graph,
        inputs,
        session_id=None,
        run_policy=None,
    ) -> None:
        captured["graph"] = graph
        record.status = RunStatus.COMPLETED

    monkeypatch.setattr(manager, "_run_task", fake_run_task)

    record = await manager.start_run(
        _empty_graph(),
        graph_id="wf-empty",
        enforce_run_readiness=False,
    )
    await asyncio.wait_for(manager._tasks[record.run_id], timeout=1.0)

    assert captured["graph"].metadata.name == "empty"
    assert record.launch_request == {
        "kind": "start_run",
        "inputs": None,
        "session_id": None,
        "goal_context": None,
        "run_policy": None,
        "guard_action": "run",
    }


@pytest.mark.asyncio
async def test_retry_run_replays_saved_launch_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RunManager(graph_loader=lambda graph_id: {"graph_id": graph_id})
    manager._runs["run-failed"] = RunRecord(
        run_id="run-failed",
        graph_id="wf-retry",
        status=RunStatus.FAILED,
        goal_context={"origin": "scheduler"},
        launch_request={
            "kind": "start_run",
            "inputs": {"topic": "markets"},
            "session_id": "sched-1",
            "goal_context": {"origin": "scheduler"},
            "run_policy": {"profile": "long_running"},
            "guard_action": "schedule_execution",
        },
    )
    captured: dict[str, object] = {}
    retried = RunRecord(run_id="run-retry", graph_id="wf-retry")

    async def fake_start_run(
        graph,
        graph_id: str,
        inputs=None,
        run_id=None,
        session_id=None,
        goal_context=None,
        run_policy=None,
        *,
        enforce_run_readiness=True,
        guard_action="run",
    ) -> RunRecord:
        captured.update(
            {
                "graph": graph,
                "graph_id": graph_id,
                "inputs": inputs,
                "run_id": run_id,
                "session_id": session_id,
                "goal_context": goal_context,
                "run_policy": run_policy,
                "enforce_run_readiness": enforce_run_readiness,
                "guard_action": guard_action,
            }
        )
        return retried

    monkeypatch.setattr(manager, "start_run", fake_start_run)

    result = await manager.retry_run("run-failed")

    assert result is retried
    assert captured == {
        "graph": {"graph_id": "wf-retry"},
        "graph_id": "wf-retry",
        "inputs": {"topic": "markets"},
        "run_id": None,
        "session_id": "sched-1",
        "goal_context": {"origin": "scheduler"},
        "run_policy": {"profile": "long_running"},
        "enforce_run_readiness": True,
        "guard_action": "schedule_execution",
    }


@pytest.mark.asyncio
async def test_retry_run_can_infer_replay_for_older_direct_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RunManager(graph_loader=lambda graph_id: {"graph_id": graph_id})
    manager._runs["run-old"] = RunRecord.from_summary(
        {
            "run_id": "run-old",
            "graph_id": "wf-legacy",
            "status": "failed",
            "phase": "failed",
            "started_at": 1.0,
            "finished_at": 2.0,
            "success": False,
            "errors": {"writer": "boom"},
            "outputs": {},
            "effective_run_policy": {"profile": "long_running"},
        }
    )
    captured: dict[str, object] = {}
    retried = RunRecord(run_id="run-retry", graph_id="wf-legacy")

    async def fake_start_run(
        graph,
        graph_id: str,
        inputs=None,
        run_id=None,
        session_id=None,
        goal_context=None,
        run_policy=None,
        *,
        enforce_run_readiness=True,
        guard_action="run",
    ) -> RunRecord:
        captured.update(
            {
                "graph": graph,
                "graph_id": graph_id,
                "inputs": inputs,
                "run_id": run_id,
                "session_id": session_id,
                "goal_context": goal_context,
                "run_policy": run_policy,
                "enforce_run_readiness": enforce_run_readiness,
                "guard_action": guard_action,
            }
        )
        return retried

    monkeypatch.setattr(manager, "start_run", fake_start_run)

    result = await manager.retry_run("run-old")

    assert result is retried
    assert captured == {
        "graph": {"graph_id": "wf-legacy"},
        "graph_id": "wf-legacy",
        "inputs": None,
        "run_id": None,
        "session_id": None,
        "goal_context": None,
        "run_policy": {"profile": "long_running"},
        "enforce_run_readiness": True,
        "guard_action": "run",
    }


@pytest.mark.asyncio
async def test_retry_run_can_replay_saved_resume_launch_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RunManager(graph_loader=lambda graph_id: {"graph_id": graph_id})
    manager._runs["run-resume"] = RunRecord(
        run_id="run-resume",
        graph_id="wf-resume",
        status=RunStatus.FAILED,
        launch_request={
            "kind": "resume_run",
            "inputs": None,
            "session_id": "resume-1",
            "goal_context": None,
            "run_policy": {"profile": "checkpoint_resume"},
            "guard_action": "run",
        },
    )
    captured: dict[str, object] = {}
    retried = RunRecord(run_id="run-resume", graph_id="wf-resume")

    async def fake_resume_run(
        graph,
        graph_id: str,
        run_id: str,
        session_id=None,
        run_policy=None,
        *,
        enforce_run_readiness=True,
        guard_action="run",
    ) -> RunRecord:
        captured.update(
            {
                "graph": graph,
                "graph_id": graph_id,
                "run_id": run_id,
                "session_id": session_id,
                "run_policy": run_policy,
                "enforce_run_readiness": enforce_run_readiness,
                "guard_action": guard_action,
            }
        )
        return retried

    monkeypatch.setattr(manager, "resume_run", fake_resume_run)

    result = await manager.retry_run("run-resume")

    assert result is retried
    assert captured == {
        "graph": {"graph_id": "wf-resume"},
        "graph_id": "wf-resume",
        "run_id": "run-resume",
        "session_id": "resume-1",
        "run_policy": {"profile": "checkpoint_resume"},
        "enforce_run_readiness": True,
        "guard_action": "run",
    }


class _FakeRunStore:
    def __init__(self, events: dict[tuple[str, str], list[dict[str, object]]] | None = None) -> None:
        self._events = events or {}

    def cleanup(self, max_age_days: int) -> int:
        return 0

    def list_summaries(
        self,
        limit: int = 10000,
        workflow_id: str | None = None,
        status: str | None = None,
        after: float | None = None,
        before: float | None = None,
        offset: int = 0,
    ):
        return []

    def load_events(
        self,
        workflow_id: str,
        run_id: str,
        *,
        node_id: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, object]]:
        events = list(self._events.get((workflow_id, run_id), []))
        if node_id is not None:
            events = [event for event in events if event.get("node_id") == node_id]
        if event_type is not None:
            events = [event for event in events if event.get("event_type") == event_type]
        return events


@pytest.mark.asyncio
async def test_retry_run_can_infer_rerun_replay_from_persisted_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeRunStore(
        events={
            ("wf-rerun", "rerun-failed"): [
                {
                    "event_type": "rerun_started",
                    "run_id": "rerun-failed",
                    "data": {
                        "provenance": {
                            "source_checkpoint_id": "run-source",
                            "rerun_scope": {
                                "scope_type": "single_node",
                                "target_node_id": "writer",
                            },
                        }
                    },
                }
            ]
        }
    )
    manager = RunManager(
        graph_loader=lambda graph_id: {"graph_id": graph_id},
        run_store=store,
    )
    manager._runs["rerun-failed"] = RunRecord.from_summary(
        {
            "run_id": "rerun-failed",
            "graph_id": "wf-rerun",
            "status": "failed",
            "phase": "failed",
            "started_at": 10.0,
            "finished_at": 20.0,
            "success": False,
            "errors": {"writer": "boom"},
            "outputs": {},
            "effective_run_policy": {"profile": "checkpoint_rerun"},
        }
    )
    captured: dict[str, object] = {}
    retried = RunRecord(run_id="rerun-next", graph_id="wf-rerun")

    async def fake_rerun_from_checkpoint(
        graph,
        graph_id: str,
        source_run_id: str,
        scope,
        session_id=None,
        run_policy=None,
        *,
        carry_runtime_lineage=False,
        automatic_recovery=None,
        enforce_run_readiness=True,
        guard_action="run",
    ) -> RunRecord:
        captured.update(
            {
                "graph": graph,
                "graph_id": graph_id,
                "source_run_id": source_run_id,
                "scope_type": getattr(scope, "scope_type", None),
                "target_node_id": getattr(scope, "target_node_id", None),
                "sub_graph_key": getattr(scope, "sub_graph_key", None),
                "session_id": session_id,
                "run_policy": run_policy,
                "carry_runtime_lineage": carry_runtime_lineage,
                "automatic_recovery": automatic_recovery,
                "enforce_run_readiness": enforce_run_readiness,
                "guard_action": guard_action,
            }
        )
        return retried

    monkeypatch.setattr(manager, "rerun_from_checkpoint", fake_rerun_from_checkpoint)

    result = await manager.retry_run("rerun-failed")

    assert result is retried
    assert captured == {
        "graph": {"graph_id": "wf-rerun"},
        "graph_id": "wf-rerun",
        "source_run_id": "run-source",
        "scope_type": "single_node",
        "target_node_id": "writer",
        "sub_graph_key": None,
        "session_id": None,
        "run_policy": {"profile": "checkpoint_rerun"},
        "carry_runtime_lineage": False,
        "automatic_recovery": None,
        "enforce_run_readiness": True,
        "guard_action": "run",
    }


@pytest.mark.asyncio
async def test_launch_run_attaches_optional_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RunManager()
    record = RunRecord(run_id="run-123", graph_id="wf-launch")
    captured: dict[str, object] = {}

    async def fake_start_run(
        graph,
        graph_id: str,
        inputs=None,
        run_id=None,
        session_id=None,
        goal_context=None,
        run_policy=None,
        *,
        enforce_run_readiness=True,
        guard_action="run",
    ) -> RunRecord:
        captured["graph_id"] = graph_id
        captured["guard_action"] = guard_action
        return record

    async def fake_relay_run_events_to_bus(*, rm, run_id, workflow_name, surface_id, bus) -> None:
        captured["relay"] = {
            "rm": rm,
            "run_id": run_id,
            "workflow_name": workflow_name,
            "surface_id": surface_id,
            "bus": bus,
        }

    monkeypatch.setattr(manager, "start_run", fake_start_run)
    import dan.server.run_relay as run_relay_mod

    monkeypatch.setattr(run_relay_mod, "relay_run_events_to_bus", fake_relay_run_events_to_bus)

    bus = object()
    handle = await manager.launch_run(
        _empty_graph(),
        graph_id="wf-launch",
        bus=bus,
        surface_id="surface-1",
        workflow_name="Workflow Launch",
    )
    assert handle.record is record
    assert handle.relay_task is not None
    await asyncio.wait_for(handle.relay_task, timeout=1.0)

    assert captured["graph_id"] == "wf-launch"
    assert captured["guard_action"] == "run"
    assert captured["relay"] == {
        "rm": manager,
        "run_id": "run-123",
        "workflow_name": "Workflow Launch",
        "surface_id": "surface-1",
        "bus": bus,
    }
