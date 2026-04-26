"""Universal organism runner driven by role/brief task DAGs."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field

from dan.worker.brief import RoleSpec, WorkerBrief, request_from_brief
from dan.worker.cell import build_cell
from dan.worker.context_capsules import ContextCapsule, ReadinessSignal
from dan.worker.core.executor import WorkerCoreExecutor, WorkerExecutionResult
from dan.worker.core.model import WorkerDefinition
from dan.worker.organism_log import OrganismLogWriter
from dan.worker.scheduler import SchedulerTask, TaskDependency, evaluate_task_readiness_from_capsules


DependencyKind = Literal["hard", "soft", "suspected", "disproven"]
TaskStatus = Literal["pending", "running", "completed", "failed", "skipped"]
DecisionProposalSource = Literal[
    "compiled_policy",
    "worker_self_status",
    "observer_llm",
    "reviewer_llm",
    "orchestrator_llm",
]
AdmissionResult = Literal["pending", "accepted", "rejected"]
CommandStatus = Literal["queued", "applied", "dead_lettered"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OrganismDependency(BaseModel):
    """Typed dependency edge between plan tasks."""

    upstream_task_id: str
    dependency_id: str = ""
    kind: DependencyKind = "hard"
    reason: str = ""
    readiness_predicates: list[str] = Field(default_factory=list)
    artifact_kinds: list[str] = Field(default_factory=list)
    unlock_keys: list[str] = Field(default_factory=list)
    parent_stage_id: str = ""
    revision: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrganismTask(BaseModel):
    """One executable task node in a universal organism plan."""

    task_id: str
    brief: WorkerBrief
    role: RoleSpec | None = None
    model: str | None = None
    dependencies: list[OrganismDependency] = Field(default_factory=list)
    estimated_duration_seconds: float = Field(default=1.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def role_spec(self) -> RoleSpec:
        return self.role or self.brief.role


class OrganismPolicy(BaseModel):
    max_concurrency: int = Field(default=4, ge=1)
    priority_policy: dict[str, Any] = Field(default_factory=dict)
    retry_budget: int = Field(default=0, ge=0)
    stale_timeout_seconds: float | None = Field(default=None, gt=0)
    coalescing_cadence_seconds: float | None = Field(default=None, gt=0)
    semantic_observer_cadence_events: int | None = Field(default=None, ge=1)
    semantic_observer_max_snapshot_chars: int = Field(default=800, ge=1)
    noncritical_update_policy: Literal["lossless", "coalesce", "drop-stale"] = "coalesce"
    dead_letter_policy: Literal["record", "fail-run"] = "record"
    command_queue_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrganismPlan(BaseModel):
    """The program consumed by the universal organism engine."""

    plan_id: str
    run_id: str | None = None
    objective: str = ""
    tasks: list[OrganismTask] = Field(default_factory=list)
    policy: OrganismPolicy = Field(default_factory=OrganismPolicy)
    repair_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_budget_policy: dict[str, Any] = Field(default_factory=dict)
    capsule_policy: dict[str, Any] = Field(default_factory=dict)
    scheduler_hooks: dict[str, Any] = Field(default_factory=dict)
    artifact_policy: dict[str, Any] = Field(default_factory=dict)
    acceptance_policy: dict[str, Any] = Field(default_factory=dict)
    semantic_observer_policy: dict[str, Any] = Field(default_factory=dict)
    decision_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrganismEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"event-{uuid4().hex}")
    sequence: int
    event: str
    timestamp: str = Field(default_factory=_utc_now)
    run_id: str = ""
    plan_id: str = ""
    task_id: str | None = None
    source: str = "universal_organism"
    state_version: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)


class OrganismTaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrganismResult(BaseModel):
    run_id: str = ""
    plan_id: str
    status: Literal["completed", "failed"]
    task_results: dict[str, OrganismTaskResult] = Field(default_factory=dict)
    events: list[OrganismEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrganismRunState(BaseModel):
    """Compact deterministic projection derived from universal organism events."""

    run_id: str = ""
    plan_id: str = ""
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    task_statuses: dict[str, TaskStatus] = Field(default_factory=dict)
    pending_task_ids: list[str] = Field(default_factory=list)
    running_task_ids: list[str] = Field(default_factory=list)
    completed_task_ids: list[str] = Field(default_factory=list)
    failed_task_ids: list[str] = Field(default_factory=list)
    skipped_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    blocked_by: dict[str, list[str]] = Field(default_factory=dict)
    capacity_available: int = 0
    event_count: int = 0


class OrganismSemanticSnapshot(BaseModel):
    """Bounded semantic projection for reviewer/observer jobs."""

    snapshot_id: str = Field(default_factory=lambda: f"semantic-snapshot-{uuid4().hex[:12]}")
    event_ids: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    current_focus_by_task: dict[str, str] = Field(default_factory=dict)
    blockers_by_task: dict[str, str] = Field(default_factory=dict)
    risk_flags_by_task: dict[str, list[str]] = Field(default_factory=dict)
    artifact_refs_by_task: dict[str, list[str]] = Field(default_factory=dict)
    compact_text: str = ""
    truncated: bool = False
    source_event_count: int = 0


class SemanticDecisionProposal(BaseModel):
    """Semantic proposal ledger row before deterministic admission."""

    proposal_id: str = Field(default_factory=lambda: f"proposal-{uuid4().hex[:12]}")
    source: DecisionProposalSource
    trigger_event_ids: list[str] = Field(default_factory=list)
    snapshot_id: str = ""
    proposed_action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    admission_result: AdmissionResult = "pending"
    rejection_reason: str = ""
    ttl_seconds: float | None = Field(default=None, gt=0)
    reversible: bool = True
    admitted_command_id: str = ""
    idempotency_key: str = ""
    state_version: int = 0


class OrganismCommand(BaseModel):
    """Admitted deterministic command derived from a proposal ledger row."""

    command_id: str
    idempotency_key: str
    proposed_action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source_proposal_id: str = ""
    source: DecisionProposalSource = "compiled_policy"
    state_version: int = 0
    status: CommandStatus = "queued"
    note: str = ""


EventCallback = Callable[[dict[str, Any]], None]
CellBuilder = Callable[[str | None, Any, str], WorkerDefinition]


def _event_id(row: OrganismEvent | Mapping[str, Any]) -> str:
    if isinstance(row, OrganismEvent):
        return row.event_id
    return str(row.get("event_id") or f"event:{row.get('sequence', '')}")


def _event_task_id(row: OrganismEvent | Mapping[str, Any]) -> str:
    if isinstance(row, OrganismEvent):
        return str(row.task_id or "")
    return str(row.get("task_id") or "")


def _event_payload(row: OrganismEvent | Mapping[str, Any]) -> dict[str, Any]:
    payload = row.payload if isinstance(row, OrganismEvent) else row.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def coalesce_semantic_status_events(
    rows: Sequence[OrganismEvent | Mapping[str, Any]],
    *,
    max_chars: int = 800,
) -> OrganismSemanticSnapshot:
    """Compress high-frequency status rows into one bounded observer snapshot."""

    current_focus_by_task: dict[str, str] = {}
    blockers_by_task: dict[str, str] = {}
    risk_flags_by_task: dict[str, list[str]] = {}
    artifact_refs_by_task: dict[str, list[str]] = {}
    event_ids: list[str] = []
    task_ids: set[str] = set()
    for row in rows:
        event_ids.append(_event_id(row))
        task_id = _event_task_id(row) or "run"
        task_ids.add(task_id)
        payload = _event_payload(row)
        focus = str(payload.get("current_focus") or payload.get("focus") or "").strip()
        if focus:
            current_focus_by_task[task_id] = focus
        blocker = str(payload.get("blocker") or "").strip()
        if blocker:
            blockers_by_task[task_id] = blocker
        risk_flags = payload.get("risk_flags")
        if isinstance(risk_flags, (list, tuple)):
            risk_flags_by_task[task_id] = [str(flag) for flag in risk_flags if str(flag).strip()]
        artifact_refs = payload.get("artifact_refs")
        if isinstance(artifact_refs, (list, tuple)):
            artifact_refs_by_task[task_id] = [str(ref) for ref in artifact_refs if str(ref).strip()]
    compact_payload = {
        "tasks": sorted(task_ids),
        "current_focus_by_task": current_focus_by_task,
        "blockers_by_task": blockers_by_task,
        "risk_flags_by_task": risk_flags_by_task,
        "artifact_refs_by_task": artifact_refs_by_task,
    }
    compact_text = json.dumps(compact_payload, ensure_ascii=False, sort_keys=True)
    truncated = False
    if len(compact_text) > max_chars:
        compact_text = compact_text[: max(max_chars - 3, 0)] + "..."
        truncated = True
    return OrganismSemanticSnapshot(
        event_ids=event_ids,
        task_ids=sorted(task_ids),
        current_focus_by_task=current_focus_by_task,
        blockers_by_task=blockers_by_task,
        risk_flags_by_task=risk_flags_by_task,
        artifact_refs_by_task=artifact_refs_by_task,
        compact_text=compact_text,
        truncated=truncated,
        source_event_count=len(rows),
    )


class SemanticObserverQueue:
    """Cadence-triggered queue for compact semantic observer snapshots."""

    def __init__(self, *, cadence_event_limit: int = 5, max_snapshot_chars: int = 800) -> None:
        self.cadence_event_limit = max(int(cadence_event_limit), 1)
        self.max_snapshot_chars = max(int(max_snapshot_chars), 1)
        self._pending: list[OrganismEvent | Mapping[str, Any]] = []

    def add_event(self, row: OrganismEvent | Mapping[str, Any]) -> OrganismSemanticSnapshot | None:
        self._pending.append(row)
        return self.flush(force=False)

    def flush(self, *, force: bool = True) -> OrganismSemanticSnapshot | None:
        if not self._pending:
            return None
        if not force and len(self._pending) < self.cadence_event_limit:
            return None
        rows = list(self._pending)
        self._pending.clear()
        return coalesce_semantic_status_events(rows, max_chars=self.max_snapshot_chars)


def admit_semantic_decision(
    proposal: SemanticDecisionProposal,
    *,
    current_state_version: int,
    allowed_actions: Sequence[str] = ("noop", "stop"),
    allowed_sources: Sequence[DecisionProposalSource] = ("compiled_policy",),
) -> SemanticDecisionProposal:
    """Apply the deterministic admission gate to a semantic proposal."""

    if proposal.source not in set(allowed_sources):
        return proposal.model_copy(
            update={
                "admission_result": "rejected",
                "rejection_reason": f"source_not_admitted:{proposal.source}",
                "state_version": current_state_version,
            }
        )
    if proposal.proposed_action not in set(allowed_actions):
        return proposal.model_copy(
            update={
                "admission_result": "rejected",
                "rejection_reason": f"action_not_admitted:{proposal.proposed_action}",
                "state_version": current_state_version,
            }
        )
    command_id = proposal.admitted_command_id or f"command-{proposal.proposal_id}"
    return proposal.model_copy(
        update={
            "admission_result": "accepted",
            "rejection_reason": "",
            "admitted_command_id": command_id,
            "idempotency_key": proposal.idempotency_key or f"{command_id}:{current_state_version}",
            "state_version": current_state_version,
        }
    )


def _hard_dependencies(task: OrganismTask) -> set[str]:
    return {edge.upstream_task_id for edge in task.dependencies if edge.kind == "hard"}


def _dependency_id(edge: OrganismDependency, index: int) -> str:
    return edge.dependency_id or f"{edge.kind}:{edge.upstream_task_id}:{index}"


def _dependency_has_readiness_requirements(edge: OrganismDependency) -> bool:
    return bool(edge.readiness_predicates or edge.artifact_kinds or edge.unlock_keys)


def _result_context_capsules(result: WorkerExecutionResult) -> list[ContextCapsule | dict[str, Any]]:
    values: list[ContextCapsule | dict[str, Any]] = []
    for source in (result.metadata, result.outputs):
        raw = source.get("context_capsules") if isinstance(source, dict) else None
        if isinstance(raw, list):
            values.extend(raw)
    return values


def _result_readiness_signals(result: WorkerExecutionResult) -> list[ReadinessSignal | dict[str, Any]]:
    values: list[ReadinessSignal | dict[str, Any]] = []
    for source in (result.metadata, result.outputs):
        raw = source.get("readiness_signals") if isinstance(source, dict) else None
        if isinstance(raw, list):
            values.extend(raw)
    return values


def _result_payload_list(result: WorkerExecutionResult, *keys: str) -> list[Any]:
    values: list[Any] = []
    for source in (result.metadata, result.outputs):
        if not isinstance(source, dict):
            continue
        for key in keys:
            raw = source.get(key)
            if raw is None:
                continue
            if isinstance(raw, list):
                values.extend(raw)
            else:
                values.append(raw)
    return values


def _result_payload_value(result: WorkerExecutionResult, *keys: str) -> Any:
    for source in (result.metadata, result.outputs):
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key in source:
                return source.get(key)
    return None


def _normalize_decision_proposal(value: Any) -> SemanticDecisionProposal | None:
    if isinstance(value, SemanticDecisionProposal):
        return value
    if not isinstance(value, Mapping):
        return None
    try:
        return SemanticDecisionProposal.model_validate(dict(value))
    except Exception:
        return None


def _dependency_blocker_label(edge: OrganismDependency, index: int, suffix: str) -> str:
    return f"{_dependency_id(edge, index)}:{suffix}"


def _critical_path_lower_bound(tasks: Mapping[str, OrganismTask]) -> float:
    memo: dict[str, float] = {}

    def visit(task_id: str, visiting: set[str] | None = None) -> float:
        if task_id in memo:
            return memo[task_id]
        visiting = set(visiting or set())
        if task_id in visiting:
            return 0.0
        visiting.add(task_id)
        task = tasks[task_id]
        upstream = [visit(dep_id, visiting) for dep_id in _hard_dependencies(task) if dep_id in tasks]
        value = float(task.estimated_duration_seconds) + (max(upstream) if upstream else 0.0)
        memo[task_id] = value
        return value

    return max((visit(task_id) for task_id in tasks), default=0.0)


def _diagnostics(plan: OrganismPlan) -> dict[str, Any]:
    tasks = {task.task_id: task for task in plan.tasks}
    total_work = sum(float(task.estimated_duration_seconds) for task in plan.tasks)
    concurrency = max(int(plan.policy.max_concurrency), 1)
    return {
        "critical_path_lower_bound_seconds": _critical_path_lower_bound(tasks),
        "capacity_lower_bound_seconds": total_work / concurrency if total_work else 0.0,
        "total_estimated_work_seconds": total_work,
        "straggler_pressure": 0.0,
        "terminal_barrier_tail_seconds": 0.0,
        "missed_parallelism_candidates": [],
    }


async def execute_universal_organism(
    plan: OrganismPlan,
    *,
    executor: WorkerCoreExecutor | None = None,
    cell_builder: CellBuilder = build_cell,
    event_callback: EventCallback | None = None,
    log_writer: OrganismLogWriter | None = None,
) -> OrganismResult:
    """Execute a role/brief DAG with completion-driven ready-queue dispatch."""

    task_by_id = {task.task_id: task for task in plan.tasks}
    run_id = plan.run_id or f"universal-organism-{uuid4().hex[:12]}"
    duplicate_count = len(plan.tasks) - len(task_by_id)
    events: list[OrganismEvent] = []
    sequence = 0

    def emit(event: str, *, task_id: str | None = None, **payload: Any) -> None:
        nonlocal sequence
        sequence += 1
        row = OrganismEvent(
            sequence=sequence,
            event=event,
            run_id=run_id,
            plan_id=plan.plan_id,
            task_id=task_id,
            state_version=sequence,
            payload=dict(payload),
        )
        events.append(row)
        row_payload = row.model_dump(mode="json", exclude_none=True)
        if log_writer is not None:
            log_writer.emit(
                {
                    **row_payload,
                    "organism_event_sequence": row.sequence,
                    "summary": event,
                    "trace_id": f"trace:{run_id}",
                }
            )
        if event_callback is not None:
            event_callback(row_payload)

    def reduce_state(status: Literal["pending", "running", "completed", "failed"] = "running") -> OrganismRunState:
        blocked_by: dict[str, list[str]] = {}
        for pending_task_id in sorted(pending):
            blockers = latest_blockers.get(pending_task_id)
            if blockers is None:
                blockers = sorted(_hard_dependencies(task_by_id[pending_task_id]) - completed)
            if blockers:
                blocked_by[pending_task_id] = blockers
        task_statuses: dict[str, TaskStatus] = {task_id: "pending" for task_id in task_by_id}
        for task_id in running.values():
            task_statuses[task_id] = "running"
        for task_id in completed:
            task_statuses[task_id] = "completed"
        for task_id in failed:
            task_statuses[task_id] = "failed"
        for task_id in skipped:
            task_statuses[task_id] = "skipped"
        return OrganismRunState(
            run_id=run_id,
            plan_id=plan.plan_id,
            status=status,
            task_statuses=dict(sorted(task_statuses.items())),
            pending_task_ids=sorted(pending),
            running_task_ids=sorted(running.values()),
            completed_task_ids=sorted(completed),
            failed_task_ids=sorted(failed),
            skipped_task_ids=sorted(skipped),
            blocked_task_ids=sorted(blocked_by),
            blocked_by=blocked_by,
            capacity_available=max(int(plan.policy.max_concurrency) - len(running), 0),
            event_count=len(events),
        )

    emit(
        "organism.plan.snapshot",
        plan=plan.model_dump(mode="json", exclude_none=True),
        diagnostics=_diagnostics(plan),
    )
    if duplicate_count:
        emit("organism.plan.warning", duplicate_task_count=duplicate_count)

    runner = executor or WorkerCoreExecutor()
    pending: set[str] = set(task_by_id)
    completed: set[str] = set()
    failed: set[str] = set()
    skipped: set[str] = set()
    running: dict[asyncio.Task[tuple[str, WorkerExecutionResult]], str] = {}
    results: dict[str, OrganismTaskResult] = {}
    capsules_by_task: dict[str, list[ContextCapsule | dict[str, Any]]] = {}
    readiness_signals_by_task: dict[str, list[ReadinessSignal | dict[str, Any]]] = {}
    ready_context_packets: dict[str, dict[str, Any]] = {}
    latest_blockers: dict[str, list[str]] = {}
    semantic_queue = SemanticObserverQueue(
        cadence_event_limit=int(
            plan.policy.semantic_observer_cadence_events
            or plan.semantic_observer_policy.get("cadence_event_limit")
            or 5
        ),
        max_snapshot_chars=int(
            plan.policy.semantic_observer_max_snapshot_chars
            or plan.semantic_observer_policy.get("max_snapshot_chars")
            or 800
        ),
    )
    semantic_snapshots: list[OrganismSemanticSnapshot] = []
    decision_ledger: list[SemanticDecisionProposal] = []
    admitted_commands: list[OrganismCommand] = []
    def _policy_str_tuple(key: str, default: Sequence[str]) -> tuple[str, ...]:
        value = plan.decision_policy.get(key, default)
        if isinstance(value, str):
            return (value,)
        if isinstance(value, Sequence):
            return tuple(str(item) for item in value)
        return tuple(default)

    allowed_decision_actions = _policy_str_tuple(
        "allowed_actions",
        ("noop", "stop", "revise_dependency", "cancel_task"),
    )
    allowed_decision_sources = _policy_str_tuple("allowed_sources", ("compiled_policy",))

    def _merge_context_packets(
        task_id: str,
        evaluations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        packets = [
            evaluation.get("context_packet")
            or (
                evaluation.get("scheduler_readiness", {}).get("context_packet")
                if isinstance(evaluation.get("scheduler_readiness"), dict)
                else None
            )
            for evaluation in evaluations
            if (
                isinstance(evaluation.get("context_packet"), dict)
                and evaluation.get("context_packet")
            )
            or (
                isinstance(evaluation.get("scheduler_readiness"), dict)
                and isinstance(evaluation["scheduler_readiness"].get("context_packet"), dict)
                and evaluation["scheduler_readiness"].get("context_packet")
            )
        ]
        if not packets:
            return {}
        return {
            "source": "universal_organism.readiness",
            "target_task_id": task_id,
            "dependency_readiness": evaluations,
            "packets": packets,
        }

    def _evaluate_task_dependencies(task: OrganismTask) -> tuple[bool, list[str], list[dict[str, Any]]]:
        blockers: list[str] = []
        evaluations: list[dict[str, Any]] = []
        for index, edge in enumerate(task.dependencies):
            dep_id = _dependency_id(edge, index)
            upstream_completed = edge.upstream_task_id in completed
            upstream_failed = edge.upstream_task_id in failed
            payload: dict[str, Any] = {
                "dependency_id": dep_id,
                "upstream_task_id": edge.upstream_task_id,
                "kind": edge.kind,
                "reason": edge.reason,
                "parent_stage_id": edge.parent_stage_id,
                "revision": edge.revision,
                "requirements": {
                    "readiness_predicates": list(edge.readiness_predicates),
                    "artifact_kinds": list(edge.artifact_kinds),
                    "unlock_keys": list(edge.unlock_keys),
                },
                "upstream_completed": upstream_completed,
                "upstream_failed": upstream_failed,
                "ready": True,
            }
            if not upstream_completed:
                payload["ready"] = False
                payload["blocker_reason"] = (
                    "upstream_failed" if upstream_failed else "upstream_not_completed"
                )
                if edge.kind == "hard":
                    blockers.append(_dependency_blocker_label(edge, index, payload["blocker_reason"]))
                evaluations.append(payload)
                continue
            if _dependency_has_readiness_requirements(edge):
                readiness_task = SchedulerTask(
                    task_id=f"{task.task_id}:dependency:{dep_id}",
                    title=f"Dependency readiness for {task.task_id} from {edge.upstream_task_id}",
                    dependencies=[
                        TaskDependency(
                            task_id=edge.upstream_task_id,
                            kind=edge.kind,
                            reason=edge.reason,
                        )
                    ],
                    required_readiness_predicates=list(edge.readiness_predicates),
                    required_artifact_kinds=list(edge.artifact_kinds),
                    required_unlocks=list(edge.unlock_keys),
                    expected_duration_seconds=task.estimated_duration_seconds,
                    metadata={
                        "downstream_task_id": task.task_id,
                        "dependency_id": dep_id,
                        "parent_stage_id": edge.parent_stage_id,
                        **dict(edge.metadata),
                    },
                )
                readiness = evaluate_task_readiness_from_capsules(
                    readiness_task,
                    capsules_by_task.get(edge.upstream_task_id, []),
                    readiness_signals=readiness_signals_by_task.get(edge.upstream_task_id, []),
                )
                readiness_payload = readiness.model_dump(mode="json", exclude_none=True)
                payload["scheduler_readiness"] = readiness_payload
                payload["ready"] = readiness.ready
                if edge.kind == "hard" and not readiness.ready:
                    for missing in readiness.missing_readiness_predicates:
                        blockers.append(_dependency_blocker_label(edge, index, f"missing_predicate:{missing}"))
                    for missing in readiness.missing_artifact_kinds:
                        blockers.append(_dependency_blocker_label(edge, index, f"missing_artifact:{missing}"))
                    for missing in readiness.missing_unlocks:
                        blockers.append(_dependency_blocker_label(edge, index, f"missing_unlock:{missing}"))
                    for blocker in readiness.blockers:
                        blockers.append(_dependency_blocker_label(edge, index, f"blocker:{blocker}"))
            evaluations.append(payload)
        blockers = list(dict.fromkeys(blockers))
        if blockers:
            ready_context_packets.pop(task.task_id, None)
        else:
            packet = _merge_context_packets(task.task_id, evaluations)
            if packet:
                ready_context_packets[task.task_id] = packet
        latest_blockers[task.task_id] = blockers
        return not blockers, blockers, evaluations

    def _emit_run_state_delta() -> None:
        emit(
            "organism.run_state.delta",
            run_state=reduce_state().model_dump(mode="json", exclude_none=True),
        )

    def _record_semantic_status(task_id: str, payload: Mapping[str, Any]) -> None:
        emit("organism.status.semantic", task_id=task_id, **dict(payload))
        snapshot = semantic_queue.add_event(events[-1])
        if snapshot is None:
            return
        semantic_snapshots.append(snapshot)
        emit(
            "organism.semantic.snapshot",
            snapshot=snapshot.model_dump(mode="json", exclude_none=True),
            trigger_event_ids=list(snapshot.event_ids),
        )

    def _record_scheduler_rows(task_id: str, worker_result: WorkerExecutionResult) -> None:
        for proposal in _result_payload_list(worker_result, "scheduler_proposal", "scheduler_proposals"):
            payload = proposal if isinstance(proposal, Mapping) else {"proposal": proposal}
            emit("organism.scheduler.proposal", task_id=task_id, proposal=dict(payload))
            admitted = payload.get("admitted", payload.get("accepted")) if isinstance(payload, Mapping) else None
            if admitted is True:
                emit("organism.scheduler.admitted", task_id=task_id, proposal=dict(payload))
            elif admitted is False:
                emit(
                    "organism.scheduler.rejected",
                    task_id=task_id,
                    proposal=dict(payload),
                    reason=str(payload.get("reason") or payload.get("rejection_reason") or ""),
                )

    def _apply_admitted_command(command: OrganismCommand) -> OrganismCommand:
        action = command.proposed_action
        payload = dict(command.payload)
        if action == "noop":
            return command.model_copy(update={"status": "applied", "note": "noop"})
        if action == "revise_dependency":
            target_task_id = str(payload.get("target_task_id") or payload.get("task_id") or "").strip()
            upstream_task_id = str(payload.get("upstream_task_id") or "").strip()
            if not target_task_id or not upstream_task_id or target_task_id not in task_by_id:
                return command.model_copy(update={"status": "dead_lettered", "note": "invalid_dependency_revision"})
            kind_value = str(payload.get("kind") or "hard")
            if kind_value not in {"hard", "soft", "suspected", "disproven"}:
                kind_value = "hard"
            task = task_by_id[target_task_id]
            replacement = OrganismDependency(
                upstream_task_id=upstream_task_id,
                dependency_id=str(payload.get("dependency_id") or f"dynamic:{upstream_task_id}"),
                kind=kind_value,  # type: ignore[arg-type]
                reason=str(payload.get("reason") or "admitted semantic command"),
                readiness_predicates=[str(item) for item in payload.get("readiness_predicates") or []],
                artifact_kinds=[str(item) for item in payload.get("artifact_kinds") or []],
                unlock_keys=[str(item) for item in payload.get("unlock_keys") or []],
                parent_stage_id=str(payload.get("parent_stage_id") or ""),
                revision=int(payload.get("revision") or 1),
                metadata=dict(payload.get("metadata") or {}),
            )
            task.dependencies = [
                edge
                for edge in task.dependencies
                if not (
                    edge.upstream_task_id == upstream_task_id
                    and _dependency_id(edge, 0) == replacement.dependency_id
                )
            ]
            task.dependencies.append(replacement)
            latest_blockers.pop(target_task_id, None)
            ready_context_packets.pop(target_task_id, None)
            emit(
                "organism.dependency.revised",
                task_id=target_task_id,
                command_id=command.command_id,
                dependency=replacement.model_dump(mode="json", exclude_none=True),
            )
            return command.model_copy(update={"status": "applied", "note": "dependency_revised"})
        if action == "cancel_task":
            target_task_id = str(payload.get("target_task_id") or payload.get("task_id") or "").strip()
            if target_task_id in pending:
                pending.remove(target_task_id)
                skipped.add(target_task_id)
                results[target_task_id] = OrganismTaskResult(
                    task_id=target_task_id,
                    status="skipped",
                    error="cancelled_by_admitted_command",
                    metadata={"command_id": command.command_id},
                )
                emit(
                    "organism.task.status_changed",
                    task_id=target_task_id,
                    previous_status="pending",
                    status="skipped",
                    command_id=command.command_id,
                )
                emit("organism.task.skipped", task_id=target_task_id, command_id=command.command_id)
                return command.model_copy(update={"status": "applied", "note": "task_cancelled"})
            return command.model_copy(update={"status": "dead_lettered", "note": "target_not_pending"})
        return command.model_copy(update={"status": "dead_lettered", "note": f"unsupported_action:{action}"})

    def _record_decision_rows(task_id: str, worker_result: WorkerExecutionResult) -> None:
        for raw in _result_payload_list(
            worker_result,
            "semantic_decision_proposal",
            "semantic_decision_proposals",
            "decision_proposal",
            "decision_proposals",
        ):
            proposal = _normalize_decision_proposal(raw)
            if proposal is None:
                emit("organism.decision.rejected", task_id=task_id, reason="invalid_proposal", proposal=raw)
                continue
            admitted = admit_semantic_decision(
                proposal,
                current_state_version=sequence,
                allowed_actions=allowed_decision_actions,
                allowed_sources=allowed_decision_sources,  # type: ignore[arg-type]
            )
            decision_ledger.append(admitted)
            emit(
                "organism.decision.proposal",
                task_id=task_id,
                proposal=admitted.model_dump(mode="json", exclude_none=True),
            )
            if admitted.admission_result == "accepted":
                command = OrganismCommand(
                    command_id=admitted.admitted_command_id,
                    idempotency_key=admitted.idempotency_key,
                    proposed_action=admitted.proposed_action,
                    payload=dict(admitted.payload),
                    source_proposal_id=admitted.proposal_id,
                    source=admitted.source,
                    state_version=admitted.state_version,
                )
                admitted_commands.append(command)
                emit(
                    "organism.decision.admitted",
                    task_id=task_id,
                    proposal_id=admitted.proposal_id,
                    command=command.model_dump(mode="json", exclude_none=True),
                )
                emit(
                    "organism.command.enqueued",
                    task_id=task_id,
                    command=command.model_dump(mode="json", exclude_none=True),
                )
                applied = _apply_admitted_command(command)
                admitted_commands[-1] = applied
                emit(
                    "organism.command.applied"
                    if applied.status == "applied"
                    else "organism.command.dead_lettered",
                    task_id=task_id,
                    command=applied.model_dump(mode="json", exclude_none=True),
                )
            else:
                emit(
                    "organism.decision.rejected",
                    task_id=task_id,
                    proposal_id=admitted.proposal_id,
                    reason=admitted.rejection_reason,
                    proposal=admitted.model_dump(mode="json", exclude_none=True),
                )

    async def run_task(task: OrganismTask) -> tuple[str, WorkerExecutionResult]:
        role = task.role_spec()
        worker = cell_builder(task.model, task.brief.sampling_policy, role.role_label)
        brief = task.brief
        readiness_context = ready_context_packets.get(task.task_id)
        if readiness_context:
            brief = task.brief.model_copy(
                update={
                    "context_packet": {
                        **dict(task.brief.context_packet),
                        "readiness_context": readiness_context,
                    }
                }
            )
        request = request_from_brief(brief)
        return task.task_id, await runner.execute(worker, request)

    while pending or running:
        launched = False
        capacity = int(plan.policy.max_concurrency) - len(running)
        dependency_state: dict[str, list[str]] = {}
        if capacity > 0:
            ready: list[str] = []
            dependency_evaluations: dict[str, list[dict[str, Any]]] = {}
            for task_id in sorted(pending):
                task_ready, blockers, evaluations = _evaluate_task_dependencies(task_by_id[task_id])
                dependency_state[task_id] = blockers
                dependency_evaluations[task_id] = evaluations
                if task_ready:
                    ready.append(task_id)
                emit(
                    "organism.dependency.evaluated",
                    task_id=task_id,
                    hard_dependency_ids=sorted(_hard_dependencies(task_by_id[task_id])),
                    blocked_by=blockers,
                    ready=task_ready,
                    evaluations=evaluations,
                )
                if any("scheduler_readiness" in evaluation for evaluation in evaluations):
                    emit(
                        "organism.readiness.evaluated",
                        task_id=task_id,
                        ready=task_ready,
                        evaluations=evaluations,
                        context_packet=ready_context_packets.get(task_id, {}),
                    )
            emit(
                "organism.ready_queue.evaluated",
                capacity_available=max(capacity, 0),
                ready_task_ids=ready,
                pending_task_ids=sorted(pending),
                running_task_ids=sorted(running.values()),
                blocked_by=dependency_state,
                dependency_evaluations=dependency_evaluations,
            )
            dispatch_task_ids = ready[:capacity]
            emit(
                "organism.capacity.decision",
                capacity_available=max(capacity, 0),
                ready_task_ids=ready,
                dispatch_task_ids=dispatch_task_ids,
                queued_ready_task_ids=ready[capacity:],
                running_task_ids=sorted(running.values()),
            )
            for task_id in dispatch_task_ids:
                task = task_by_id[task_id]
                pending.remove(task_id)
                emit(
                    "organism.ready_queue.dispatched",
                    task_id=task_id,
                    blocked_by=[],
                    role=task.role_spec().role_label,
                    policy={
                        "max_concurrency": plan.policy.max_concurrency,
                        "priority_policy": dict(plan.policy.priority_policy),
                        "retry_budget": plan.policy.retry_budget,
                        "stale_timeout_seconds": plan.policy.stale_timeout_seconds,
                        "coalescing_cadence_seconds": plan.policy.coalescing_cadence_seconds,
                        "semantic_observer_cadence_events": plan.policy.semantic_observer_cadence_events,
                        "noncritical_update_policy": plan.policy.noncritical_update_policy,
                        "dead_letter_policy": plan.policy.dead_letter_policy,
                        "command_queue_policy": dict(plan.policy.command_queue_policy),
                    },
                    policy_provenance={
                        "sampling_policy": task.brief.sampling_policy.model_dump(
                            mode="json",
                            exclude_none=True,
                        ),
                        "tool_policy": task.brief.tool_policy.model_dump(mode="json", exclude_none=True),
                        "runtime_policy": dict(task.brief.runtime_policy),
                        "validation_policy": dict(task.brief.validation_policy),
                        "plan_repair_policy": dict(plan.repair_policy),
                        "plan_runtime_budget_policy": dict(plan.runtime_budget_policy),
                        "plan_artifact_policy": dict(plan.artifact_policy),
                        "plan_acceptance_policy": dict(plan.acceptance_policy),
                        "plan_semantic_observer_policy": dict(plan.semantic_observer_policy),
                        "plan_decision_policy": dict(plan.decision_policy),
                        "prompt_context": task.brief.prompt_context.model_dump(
                            mode="json",
                            exclude_none=True,
                        ),
                        "prompt_stable_fingerprint": task.brief.prompt_context.stable_fingerprint(),
                        "prompt_slot_fingerprint": task.brief.prompt_context.slot_fingerprint(),
                    },
                )
                running_task = asyncio.create_task(run_task(task))
                running[running_task] = task_id
                emit(
                    "organism.task.status_changed",
                    task_id=task_id,
                    previous_status="pending",
                    status="running",
                )
                _emit_run_state_delta()
                emit("organism.task.started", task_id=task_id, role=task.role_spec().role_label)
                launched = True
        for task_id in sorted(pending):
            blockers = dependency_state.get(task_id)
            if blockers is None:
                _, blockers, _ = _evaluate_task_dependencies(task_by_id[task_id])
            if blockers:
                emit("organism.dependency.blocked", task_id=task_id, blocked_by=blockers)
        emit(
            "organism.status.runtime_heartbeat",
            capacity_available=max(int(plan.policy.max_concurrency) - len(running), 0),
            pending_task_ids=sorted(pending),
            running_task_ids=sorted(running.values()),
            completed_task_ids=sorted(completed),
            failed_task_ids=sorted(failed),
            skipped_task_ids=sorted(skipped),
            stale_timeout_seconds=plan.policy.stale_timeout_seconds,
        )
        _emit_run_state_delta()
        if not running:
            for task_id in sorted(pending):
                _, blockers, evaluations = _evaluate_task_dependencies(task_by_id[task_id])
                results[task_id] = OrganismTaskResult(
                    task_id=task_id,
                    status="skipped",
                    error="unresolved_dependencies: " + ", ".join(blockers),
                    metadata={"blocked_by": blockers, "dependency_evaluations": evaluations},
                )
                failed.add(task_id)
                skipped.add(task_id)
                emit(
                    "organism.task.status_changed",
                    task_id=task_id,
                    previous_status="pending",
                    status="skipped",
                    blocked_by=blockers,
                )
                _emit_run_state_delta()
                emit("organism.task.skipped", task_id=task_id, blocked_by=blockers)
            pending.clear()
            break
        if not launched:
            done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
        else:
            done = {task for task in running if task.done()}
            if not done:
                done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
        for finished in done:
            task_id = running.pop(finished)
            try:
                completed_task_id, worker_result = finished.result()
            except Exception as exc:
                completed_task_id = task_id
                worker_result = WorkerExecutionResult(status="failed", error=f"{type(exc).__name__}: {exc}")
            if worker_result.status == "completed":
                completed.add(completed_task_id)
                context_capsules = _result_context_capsules(worker_result)
                readiness_signals = _result_readiness_signals(worker_result)
                if context_capsules:
                    capsules_by_task[completed_task_id] = context_capsules
                    emit(
                        "organism.context.capsules.recorded",
                        task_id=completed_task_id,
                        capsule_count=len(context_capsules),
                    )
                if readiness_signals:
                    readiness_signals_by_task[completed_task_id] = readiness_signals
                    emit(
                        "organism.readiness.signals.recorded",
                        task_id=completed_task_id,
                        readiness_signal_count=len(readiness_signals),
                    )
                _record_scheduler_rows(completed_task_id, worker_result)
                for semantic_status in _result_payload_list(
                    worker_result,
                    "semantic_status",
                    "semantic_statuses",
                ):
                    if isinstance(semantic_status, Mapping):
                        _record_semantic_status(completed_task_id, semantic_status)
                    else:
                        _record_semantic_status(
                            completed_task_id,
                            {"current_focus": str(semantic_status)},
                        )
                reducer_output = _result_payload_value(worker_result, "reducer_output", "incremental_reducer_output")
                if reducer_output is not None:
                    emit("organism.reducer.output", task_id=completed_task_id, reducer_output=reducer_output)
                speculative_validation = _result_payload_value(
                    worker_result,
                    "speculative_validation",
                    "speculative_validation_result",
                )
                if speculative_validation is not None:
                    emit(
                        "organism.validation.speculative",
                        task_id=completed_task_id,
                        validation=speculative_validation,
                    )
                promotion_decision = _result_payload_value(
                    worker_result,
                    "promotion_decision",
                    "final_promotion_decision",
                )
                if promotion_decision is not None:
                    emit(
                        "organism.promotion.decision",
                        task_id=completed_task_id,
                        decision=promotion_decision,
                    )
                _record_decision_rows(completed_task_id, worker_result)
                emit(
                    "organism.task.status_changed",
                    task_id=completed_task_id,
                    previous_status="running",
                    status="completed",
                )
                _emit_run_state_delta()
                emit(
                    "organism.task.completed",
                    task_id=completed_task_id,
                    output_keys=sorted(worker_result.outputs),
                )
                for downstream_id in sorted(pending):
                    deps = _hard_dependencies(task_by_id[downstream_id])
                    downstream_ready, blockers, evaluations = _evaluate_task_dependencies(
                        task_by_id[downstream_id]
                    )
                    if completed_task_id in deps and downstream_ready:
                        emit(
                            "organism.dependency.unlocked",
                            task_id=downstream_id,
                            upstream_task_id=completed_task_id,
                            evaluations=evaluations,
                            context_packet=ready_context_packets.get(downstream_id, {}),
                        )
                    elif completed_task_id in deps and blockers:
                        emit(
                            "organism.dependency.blocked",
                            task_id=downstream_id,
                            upstream_task_id=completed_task_id,
                            blocked_by=blockers,
                            evaluations=evaluations,
                        )
            else:
                failed.add(completed_task_id)
                emit(
                    "organism.task.status_changed",
                    task_id=completed_task_id,
                    previous_status="running",
                    status="failed",
                    error=worker_result.error,
                )
                _emit_run_state_delta()
                emit("organism.task.failed", task_id=completed_task_id, error=worker_result.error)
            results[completed_task_id] = OrganismTaskResult(
                task_id=completed_task_id,
                status="completed" if worker_result.status == "completed" else "failed",
                outputs=dict(worker_result.outputs),
                error=worker_result.error,
                metadata=dict(worker_result.metadata),
            )

    final_snapshot = semantic_queue.flush()
    if final_snapshot is not None:
        semantic_snapshots.append(final_snapshot)
        emit(
            "organism.semantic.snapshot",
            snapshot=final_snapshot.model_dump(mode="json", exclude_none=True),
            trigger_event_ids=list(final_snapshot.event_ids),
        )
    status = "failed" if failed else "completed"
    emit("organism.completed", status=status, completed=sorted(completed), failed=sorted(failed))
    final_state = reduce_state(status=status)
    return OrganismResult(
        run_id=run_id,
        plan_id=plan.plan_id,
        status=status,
        task_results=results,
        events=events,
        metadata={
            "diagnostics": _diagnostics(plan),
            "policy": plan.policy.model_dump(mode="json", exclude_none=True),
            "run_state": final_state.model_dump(mode="json", exclude_none=True),
            "semantic_snapshots": [
                snapshot.model_dump(mode="json", exclude_none=True) for snapshot in semantic_snapshots
            ],
            "decision_ledger": [
                proposal.model_dump(mode="json", exclude_none=True) for proposal in decision_ledger
            ],
            "admitted_commands": [
                command.model_dump(mode="json", exclude_none=True) for command in admitted_commands
            ],
            "task_count": len(plan.tasks),
        },
    )


__all__ = [
    "AdmissionResult",
    "CommandStatus",
    "DecisionProposalSource",
    "OrganismDependency",
    "OrganismEvent",
    "OrganismPlan",
    "OrganismPolicy",
    "OrganismResult",
    "OrganismRunState",
    "OrganismSemanticSnapshot",
    "OrganismCommand",
    "OrganismTask",
    "OrganismTaskResult",
    "SemanticDecisionProposal",
    "SemanticObserverQueue",
    "admit_semantic_decision",
    "coalesce_semantic_status_events",
    "execute_universal_organism",
]
