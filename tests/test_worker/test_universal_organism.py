from __future__ import annotations

import asyncio
import time

import pytest

from dan.worker.brief import RoleSpec
from dan.worker.context_capsules import ContextCapsule, ReadinessSignal
from dan.worker.contracts.templates import coding_brief, research_brief
from dan.worker.core.executor import WorkerExecutionResult
from dan.worker.organism_log import ORGANISM_LOG_SCHEMA, OrganismLogWriter, read_organism_log
from dan.worker.organisms.universal_organism import (
    OrganismDependency,
    OrganismEvent,
    OrganismPlan,
    OrganismPolicy,
    OrganismTask,
    SemanticDecisionProposal,
    SemanticObserverQueue,
    admit_semantic_decision,
    execute_universal_organism,
)
from dan.worker.organisms.legacy_facades import (
    compose_coding_universal_plan,
    compose_incident_universal_plan,
    compose_project_execution_universal_plan,
    compose_super_organism_universal_plan,
)
from dan.worker.organisms.contracts import (
    CodingTask as ContractCodingTask,
    IncidentExecutionRequest as ContractIncidentExecutionRequest,
    ProjectExecutionTask as ContractProjectExecutionTask,
    SuperOrganismScenario as ContractSuperOrganismScenario,
)


class _FakeExecutor:
    def __init__(
        self,
        delays: dict[str, float] | None = None,
        metadata_by_task: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.delays = delays or {}
        self.metadata_by_task = metadata_by_task or {}
        self.active = 0
        self.max_active = 0
        self.starts: dict[str, float] = {}
        self.finishes: dict[str, float] = {}
        self.requests = {}

    async def execute(self, worker, request):
        task_id = str(request.metadata.get("test_task_id") or request.metadata.get("trace_role") or worker.id)
        self.requests[task_id] = request
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.starts[task_id] = time.monotonic()
        await asyncio.sleep(self.delays.get(task_id, 0.0))
        self.finishes[task_id] = time.monotonic()
        self.active -= 1
        return WorkerExecutionResult(
            status="completed",
            outputs={"task_id": task_id, "role": worker.role},
            metadata=dict(self.metadata_by_task.get(task_id, {})),
        )


def _task(
    task_id: str,
    *,
    kind: str = "coding",
    deps: list[str] | None = None,
    dependency_edges: list[OrganismDependency] | None = None,
    delay: float = 1.0,
) -> OrganismTask:
    role = RoleSpec(role_label=f"{kind}-{task_id}", responsibility=f"{kind} responsibility")
    if kind == "research":
        brief = research_brief(role=role, task=f"Research {task_id}")
    else:
        brief = coding_brief(role=role, task=f"Code {task_id}")
    brief.metadata["test_task_id"] = task_id
    return OrganismTask(
        task_id=task_id,
        brief=brief,
        dependencies=dependency_edges
        if dependency_edges is not None
        else [OrganismDependency(upstream_task_id=dep) for dep in deps or []],
        estimated_duration_seconds=delay,
    )


@pytest.mark.asyncio
async def test_universal_organism_runs_coding_and_research_shapes() -> None:
    executor = _FakeExecutor()
    plan = OrganismPlan(
        plan_id="mixed",
        tasks=[
            _task("code", kind="coding"),
            _task("research", kind="research"),
        ],
        policy=OrganismPolicy(max_concurrency=2),
    )

    result = await execute_universal_organism(plan, executor=executor)

    assert result.status == "completed"
    assert result.task_results["code"].outputs["role"] == "coding-code"
    assert result.task_results["research"].outputs["role"] == "research-research"


@pytest.mark.asyncio
async def test_completion_driven_unlock_starts_downstream_before_unrelated_sibling_finishes() -> None:
    executor = _FakeExecutor({"a": 0.01, "b": 0.08, "c": 0.01})
    plan = OrganismPlan(
        plan_id="unlock",
        tasks=[
            _task("a", delay=1.0),
            _task("b", delay=8.0),
            _task("c", deps=["a"], delay=1.0),
        ],
        policy=OrganismPolicy(max_concurrency=2),
    )

    result = await execute_universal_organism(plan, executor=executor)

    assert result.status == "completed"
    assert executor.starts["c"] < executor.finishes["b"]
    assert any(event.event == "organism.dependency.unlocked" and event.task_id == "c" for event in result.events)


@pytest.mark.asyncio
async def test_ready_workers_dispatch_concurrently_up_to_policy_limit() -> None:
    executor = _FakeExecutor({"a": 0.02, "b": 0.02, "c": 0.02, "d": 0.02})
    plan = OrganismPlan(
        plan_id="fanout",
        tasks=[_task(task_id) for task_id in ["a", "b", "c", "d"]],
        policy=OrganismPolicy(max_concurrency=4),
    )

    result = await execute_universal_organism(plan, executor=executor)

    assert result.status == "completed"
    assert executor.max_active == 4
    assert result.metadata["diagnostics"]["capacity_lower_bound_seconds"] == 1.0
    assert {event.event for event in result.events} >= {
        "organism.plan.snapshot",
        "organism.ready_queue.dispatched",
        "organism.task.completed",
        "organism.completed",
    }


@pytest.mark.asyncio
async def test_universal_organism_events_carry_identity_and_policy_provenance() -> None:
    callback_rows: list[dict[str, object]] = []
    executor = _FakeExecutor({"a": 0.01})
    plan = OrganismPlan(
        plan_id="provenance",
        run_id="run-123",
        tasks=[_task("a")],
        policy=OrganismPolicy(max_concurrency=1, retry_budget=2),
    )

    result = await execute_universal_organism(
        plan,
        executor=executor,
        event_callback=callback_rows.append,
    )

    dispatch_event = next(
        event for event in result.events if event.event == "organism.ready_queue.dispatched"
    )
    run_state = result.metadata["run_state"]

    assert result.run_id == "run-123"
    assert callback_rows
    assert all(row["run_id"] == "run-123" for row in callback_rows)
    assert all(row["plan_id"] == "provenance" for row in callback_rows)
    assert dispatch_event.payload["policy"]["retry_budget"] == 2
    assert dispatch_event.payload["policy_provenance"]["sampling_policy"]["profile"] == "deterministic"
    assert "prompt_stable_fingerprint" in dispatch_event.payload["policy_provenance"]
    assert run_state["status"] == "completed"
    assert run_state["task_statuses"] == {"a": "completed"}
    assert run_state["completed_task_ids"] == ["a"]


@pytest.mark.asyncio
async def test_universal_organism_can_persist_organism_log_v1(tmp_path) -> None:
    executor = _FakeExecutor({"a": 0.01})
    plan = OrganismPlan(
        plan_id="loggable",
        run_id="run-loggable",
        tasks=[_task("a")],
        policy=OrganismPolicy(max_concurrency=1),
    )
    writer = OrganismLogWriter(
        path=tmp_path / "organism.jsonl",
        stream="universal_organism",
        base_context={"product": "plan56"},
    )

    try:
        result = await execute_universal_organism(plan, executor=executor, log_writer=writer)
    finally:
        writer.close()

    rows = read_organism_log(tmp_path / "organism.jsonl")
    events = {row["event"] for row in rows}
    dispatch_row = next(row for row in rows if row["event"] == "organism.ready_queue.dispatched")

    assert result.status == "completed"
    assert rows
    assert all(row["schema"] == ORGANISM_LOG_SCHEMA for row in rows)
    assert {
        "organism.plan.snapshot",
        "organism.ready_queue.evaluated",
        "organism.task.status_changed",
        "organism.completed",
    }.issubset(events)
    assert dispatch_row["run_id"] == "run-loggable"
    assert dispatch_row["plan_id"] == "loggable"
    assert dispatch_row["organism_event_sequence"] > 0
    assert dispatch_row["payload"]["policy_provenance"]["sampling_policy"]["profile"] == "deterministic"


@pytest.mark.asyncio
async def test_dependency_readiness_capsules_unlock_downstream_context() -> None:
    capsule = ContextCapsule(
        capsule_id="capsule-a",
        kind="implementation_delta",
        source_task_id="a",
        summary="A produced the implementation delta",
        unlocks=["artifact:a-ready"],
        metadata={"artifact_kind": "implementation_delta"},
    )
    signal = ReadinessSignal(
        readiness_id="ready-a",
        source_task_id="a",
        capsule_ids=["capsule-a"],
        ready_for_downstream=True,
        predicate="artifact_ready",
        downstream_task_ids=["b"],
    )
    executor = _FakeExecutor(
        {"a": 0.01, "b": 0.01},
        metadata_by_task={
            "a": {
                "context_capsules": [capsule.model_dump(mode="json", exclude_none=True)],
                "readiness_signals": [signal.model_dump(mode="json", exclude_none=True)],
            }
        },
    )
    plan = OrganismPlan(
        plan_id="readiness",
        tasks=[
            _task("a"),
            _task(
                "b",
                dependency_edges=[
                    OrganismDependency(
                        upstream_task_id="a",
                        dependency_id="dep-a-ready",
                        readiness_predicates=["artifact_ready"],
                        artifact_kinds=["implementation_delta"],
                        unlock_keys=["artifact:a-ready"],
                    )
                ],
            ),
        ],
        policy=OrganismPolicy(max_concurrency=2),
    )

    result = await execute_universal_organism(plan, executor=executor)

    assert result.status == "completed"
    assert executor.starts["b"] >= executor.finishes["a"]
    assert any(event.event == "organism.readiness.evaluated" and event.task_id == "b" for event in result.events)
    assert any(event.event == "organism.dependency.unlocked" and event.task_id == "b" for event in result.events)
    rendered_b_prompt = executor.requests["b"].metadata["brief_rendered_user_prompt"]
    assert "readiness_context" in rendered_b_prompt
    assert "capsule-a" in rendered_b_prompt
    assert "artifact:a-ready" in rendered_b_prompt


@pytest.mark.asyncio
async def test_universal_organism_logs_control_plane_decisions_and_progress_rows() -> None:
    proposal = SemanticDecisionProposal(
        source="compiled_policy",
        proposed_action="revise_dependency",
        payload={
            "target_task_id": "b",
            "upstream_task_id": "a",
            "dependency_id": "dynamic:a",
            "reason": "A produced the required candidate.",
        },
    )
    executor = _FakeExecutor(
        {"a": 0.01, "b": 0.01},
        metadata_by_task={
            "a": {
                "semantic_status": {
                    "current_focus": "handoff candidate ready",
                    "risk_flags": ["needs-review"],
                    "artifact_refs": ["artifact:a"],
                },
                "scheduler_proposals": [{"proposal_id": "sched-a", "admitted": True}],
                "semantic_decision_proposals": [
                    proposal.model_dump(mode="json", exclude_none=True)
                ],
                "reducer_output": {"summary": "candidate merged"},
                "speculative_validation": {"status": "reused"},
                "promotion_decision": {"status": "promoted"},
            }
        },
    )
    plan = OrganismPlan(
        plan_id="control-plane",
        tasks=[_task("a"), _task("b")],
        policy=OrganismPolicy(max_concurrency=1, semantic_observer_cadence_events=1),
        decision_policy={"allowed_actions": ["revise_dependency"]},
    )

    result = await execute_universal_organism(plan, executor=executor)
    event_names = [event.event for event in result.events]

    assert result.status == "completed"
    assert "organism.capacity.decision" in event_names
    assert "organism.run_state.delta" in event_names
    assert "organism.status.semantic" in event_names
    assert "organism.semantic.snapshot" in event_names
    assert "organism.scheduler.proposal" in event_names
    assert "organism.scheduler.admitted" in event_names
    assert "organism.reducer.output" in event_names
    assert "organism.validation.speculative" in event_names
    assert "organism.promotion.decision" in event_names
    assert "organism.decision.admitted" in event_names
    assert "organism.command.enqueued" in event_names
    assert "organism.command.applied" in event_names
    assert "organism.dependency.revised" in event_names
    assert result.metadata["decision_ledger"][0]["admission_result"] == "accepted"
    assert result.metadata["admitted_commands"][0]["idempotency_key"]
    assert result.metadata["semantic_snapshots"][0]["task_ids"] == ["a"]


@pytest.mark.asyncio
async def test_legacy_facade_plan_composers_run_through_universal_organism() -> None:
    coding = compose_coding_universal_plan(
        {
            "task_id": "code-1",
            "objective": "Fix the navbar",
            "acceptance_criteria": ["hover state works"],
            "hard_constraints": ["Stay scoped"],
        }
    )
    project = compose_project_execution_universal_plan(
        {
            "task_id": "project-1",
            "objective": "Research and patch a docs issue",
            "focused_validation_commands": ["pytest -q"],
        }
    )
    incident = compose_incident_universal_plan(
        {
            "scenario_id": "failed-run",
            "action_id": "investigate",
            "objective": "Recover the failed scheduled run",
        }
    )
    super_dan = compose_super_organism_universal_plan({"target": "Build a website"})

    assert coding.metadata["compatibility_facade"] == "coding_execution_organism"
    assert project.metadata["compatibility_facade"] == "project_execution_reference_organism"
    assert incident.metadata["compatibility_facade"] == "execute_incident_action"
    assert super_dan.metadata["compatibility_facade"] == "run_super_organism_demo"
    assert {task.brief.role.trace_role for task in coding.tasks} == {
        "coding.implementer",
        "coding.validator",
    }
    assert any(task.brief.role.trace_role == "project.researcher" for task in project.tasks)
    assert any(task.brief.role.trace_role == "incident.verifier" for task in incident.tasks)
    assert any(task.brief.role.trace_role == "super_dan.synthesis" for task in super_dan.tasks)

    result = await execute_universal_organism(coding, executor=_FakeExecutor())

    assert result.status == "completed"
    assert set(result.task_results) == {"code-1:implement", "code-1:validate"}


def test_legacy_contract_namespace_exports_public_models() -> None:
    coding = ContractCodingTask(task_id="contract-code", objective="Patch")
    project = ContractProjectExecutionTask(task_id="contract-project", objective="Research")
    incident = ContractIncidentExecutionRequest(action_id="investigate")

    assert coding.task_id == "contract-code"
    assert project.objective == "Research"
    assert incident.action_id == "investigate"
    assert ContractSuperOrganismScenario.WEBSITE_BUILD.value == "website_build"


def test_semantic_observer_queue_batches_status_and_blocks_unadmitted_mutations() -> None:
    queue = SemanticObserverQueue(cadence_event_limit=4, max_snapshot_chars=240)
    snapshots = []
    rows = [
        OrganismEvent(
            sequence=index,
            event="organism.status.semantic",
            run_id="run-semantic",
            plan_id="plan-semantic",
            task_id="worker-a",
            state_version=index,
            payload={
                "current_focus": f"focus-{index}",
                "blocker": "waiting-for-artifact" if index == 3 else "",
                "risk_flags": ["scope-drift"] if index % 2 else [],
                "artifact_refs": [f"artifact:{index}"],
            },
        )
        for index in range(10)
    ]

    for row in rows:
        snapshot = queue.add_event(row)
        if snapshot is not None:
            snapshots.append(snapshot)
    final_snapshot = queue.flush()
    if final_snapshot is not None:
        snapshots.append(final_snapshot)

    assert len(snapshots) == 3
    assert sum(snapshot.source_event_count for snapshot in snapshots) == len(rows)
    assert snapshots[-1].current_focus_by_task["worker-a"] == "focus-9"
    assert all(len(snapshot.compact_text) <= 240 for snapshot in snapshots)

    observer_proposal = SemanticDecisionProposal(
        source="observer_llm",
        snapshot_id=snapshots[-1].snapshot_id,
        trigger_event_ids=snapshots[-1].event_ids,
        proposed_action="add_task",
        payload={"task_id": "new-worker"},
    )

    rejected = admit_semantic_decision(
        observer_proposal,
        current_state_version=10,
        allowed_actions=("add_task",),
    )
    accepted = admit_semantic_decision(
        observer_proposal.model_copy(update={"source": "compiled_policy"}),
        current_state_version=11,
        allowed_actions=("add_task",),
    )

    assert rejected.admission_result == "rejected"
    assert rejected.rejection_reason == "source_not_admitted:observer_llm"
    assert rejected.admitted_command_id == ""
    assert accepted.admission_result == "accepted"
    assert accepted.admitted_command_id.startswith("command-")
    assert accepted.idempotency_key.endswith(":11")
