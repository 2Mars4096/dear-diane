"""Dedicated coding organism built entirely from universal-worker roles."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from dan.worker.composition import CrossCellTraceLog, HandoffExecution, execute_cell_handoff
from dan.worker.core.contracts import OutputContract
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.model import WorkerDefinition
from dan.worker.model import WorkerAuthority
from dan.worker.organisms.project_execution import OrganismObservability, OrganismStageRecord
from dan.worker.organs import (
    OrganExecution,
    OrganPattern,
    coding_aggregation_organ,
    execute_organ_pattern,
    universal_validator_organ,
)
from dan.worker.signaling import (
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    CompletionSignal,
    EvidenceRef,
    HandoffTask,
    SignalTrace,
)
from dan.worker.tissue import (
    TissueExecution,
    TissueMember,
    TissuePattern,
    TissuePoolLimits,
    TissueExecutionResult,
    TissueMergeMode,
    execute_tissue_pattern,
    parallel_worker_pool,
)

OrganismEventCallback = Callable[[dict[str, Any]], None]


def _parse_payload(outputs: dict[str, Any]) -> dict[str, Any]:
    raw = outputs.get("result", outputs.get("text", outputs))
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return {"result": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"result": parsed}
    return {"result": raw}


class CodingTask(BaseModel):
    """Bounded task contract for the coding organism."""

    task_id: str
    objective: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    research_findings: list[str] = Field(default_factory=list)
    repair_brief: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    session_context: dict[str, Any] = Field(default_factory=dict)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)


class CodingOrchestratorPlan(BaseModel):
    """Normalized orchestrator plan for one coding attempt."""

    worker_count: int = Field(default=2, ge=1)
    worker_briefs: list[str] = Field(default_factory=list)
    aggregation_focus: str = (
        "Merge the worker outputs into one bounded coding candidate with explicit files and focused validation."
    )
    validator_focus: str = "Score the aggregated candidate against the acceptance criteria and emit a repair brief when needed."
    pass_threshold: float = Field(default=0.9, ge=0.0, le=1.0)


class CodingOrganism(BaseModel):
    """Composed coding organism with explicit orchestrator, worker pool, aggregator, and validator roles."""

    organism_id: str
    base_id: str
    orchestrator_address: CellAddress
    orchestrator_worker: WorkerDefinition
    worker_role: str = "coding_worker"
    worker_instruction: str = (
        "Produce one bounded coding contribution for the assigned brief. Return candidate_fragment, "
        "change_summary, target_files, test_plan, and risks."
    )
    worker_model: str | None = None
    worker_tool_ids: list[str] = Field(default_factory=list)
    aggregator_organ: OrganPattern
    validator_organ: OrganPattern
    default_worker_count: int = Field(default=2, ge=1)
    max_worker_count: int = Field(default=4, ge=1)
    max_repair_rounds: int = Field(default=1, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodingOrganismResult(BaseModel):
    """Normalized coding-organism outcome."""

    status: Literal["completed", "failed"]
    final_output: dict[str, Any] = Field(default_factory=dict)
    selected_attempt: int | None = None
    validation_scores: list[float] = Field(default_factory=list)
    improved_via_repair: bool = False
    error: str | None = None
    observability: OrganismObservability
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class CodingOrganismExecution:
    """Recorded runtime for the full coding organism."""

    organism: CodingOrganism
    task: CodingTask
    orchestrator_runs: list[HandoffExecution] = field(default_factory=list)
    worker_pool_executions: list[TissueExecution] = field(default_factory=list)
    aggregation_executions: list[OrganExecution] = field(default_factory=list)
    validation_executions: list[OrganExecution] = field(default_factory=list)
    trace_log: CrossCellTraceLog | None = None
    result: CodingOrganismResult | None = None


def _orchestrator_output_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Return the next bounded coding plan: worker count, worker briefs, aggregation focus, validator focus, and pass threshold."
        ),
        expected_return_shape=json.dumps(
            {
                "worker_count": "<required>",
                "worker_briefs": "<required>",
                "aggregation_focus": "<required>",
                "validator_focus": "<required>",
                "pass_threshold": "<required>",
            },
            sort_keys=True,
        ),
    )


def _worker_output_contract() -> OutputContract:
    return OutputContract(
        definition_of_done="Return one bounded coding contribution for the assigned brief.",
        expected_return_shape=json.dumps(
            {
                "candidate_fragment": "<required>",
                "change_summary": "<required>",
                "target_files": "<required>",
                "test_plan": "<required>",
                "risks": "<required>",
            },
            sort_keys=True,
        ),
    )


def _user_address(organism_id: str) -> CellAddress:
    return CellAddress(cell_id="user.request", organism_id=organism_id)


def _trace(root_task_id: str, organism_id: str) -> SignalTrace:
    return SignalTrace(root_task_id=root_task_id, lineage=[f"organism:{organism_id}"])


def _record(
    *,
    stage: str,
    attempt: int,
    packet: CellHandoffPacket,
    status: str,
    summary: str,
    organ_id: str | None = None,
    score: float | None = None,
    output_keys: list[str] | None = None,
) -> OrganismStageRecord:
    return OrganismStageRecord(
        stage=stage,
        attempt=attempt,
        organ_id=organ_id,
        packet_id=packet.packet_id,
        sender_cell_id=packet.sender.cell_id,
        recipient_cell_id=packet.recipient.cell_id,
        accountable_cell_id=packet.recipient.cell_id,
        status=status,
        summary=summary,
        score=score,
        output_keys=list(output_keys or []),
    )


def _child_packet(
    *,
    sender: CellAddress,
    recipient: CellAddress,
    parent_packet: CellHandoffPacket,
    parent_signal_id: str,
    lineage_suffix: str,
    task_id: str,
    instruction: str,
    scope: str,
    hard_constraints: list[str],
    soft_constraints: list[str],
    input_payload: dict[str, Any],
    evidence_refs: list[EvidenceRef],
    output_contract: OutputContract,
    authority: WorkerAuthority,
    metadata: dict[str, Any],
) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=SignalTrace(
            trace_id=parent_packet.trace.trace_id,
            root_task_id=parent_packet.trace.root_task_id or parent_packet.task.task_id,
            parent_packet_id=parent_packet.packet_id,
            parent_signal_id=parent_signal_id,
            lineage=[*parent_packet.trace.lineage, lineage_suffix],
        ),
        sender=sender.model_copy(deep=True),
        recipient=recipient.model_copy(deep=True),
        task=HandoffTask(
            task_id=task_id,
            instruction=instruction,
            scope=scope,
            hard_constraints=list(hard_constraints),
            soft_constraints=list(soft_constraints),
            input_payload=dict(input_payload),
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in evidence_refs],
        output_contract=output_contract.model_copy(deep=True),
        budget_limits=CellBudgetLimits(max_completion_rounds=1),
        authority_limits=CellAuthorityLimits(
            acting_authority=authority,
            max_spawned_cells=0,
        ),
        metadata=dict(metadata),
    )


def _root_orchestrator_packet(
    *,
    organism: CodingOrganism,
    task: CodingTask,
) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=_trace(task.task_id, organism.organism_id),
        sender=_user_address(organism.organism_id),
        recipient=organism.orchestrator_address.model_copy(deep=True),
        task=HandoffTask(
            task_id=f"{task.task_id}:orchestrate:1",
            instruction=(
                "Plan the next coding attempt. Choose how many workers to run, define distinct briefs for them, "
                "and set the aggregation and validation focus."
            ),
            scope="coding-organism.orchestrate",
            hard_constraints=list(task.hard_constraints),
            soft_constraints=list(task.soft_constraints),
            input_payload={
                "objective": task.objective,
                "acceptance_criteria": list(task.acceptance_criteria),
                "research_findings": list(task.research_findings),
                "repair_brief": task.repair_brief,
                "session_context": dict(task.session_context),
                "attempt": 1,
                "repair_history": [],
            },
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in task.evidence_refs],
        output_contract=_orchestrator_output_contract(),
        budget_limits=CellBudgetLimits(max_completion_rounds=1),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.LEAD,
            max_spawned_cells=0,
        ),
        metadata={"organism_id": organism.organism_id, "organism_stage": "orchestration"},
    )


def _repair_orchestrator_packet(
    *,
    organism: CodingOrganism,
    task: CodingTask,
    attempt: int,
    parent_packet: CellHandoffPacket,
    parent_signal_id: str,
    repair_history: list[dict[str, Any]],
    previous_validation: dict[str, Any],
    repair_brief: str,
) -> CellHandoffPacket:
    return _child_packet(
        sender=parent_packet.recipient,
        recipient=organism.orchestrator_address,
        parent_packet=parent_packet,
        parent_signal_id=parent_signal_id,
        lineage_suffix="coordinator:orchestrator",
        task_id=f"{task.task_id}:orchestrate:{attempt}",
        instruction=(
            "Plan the next coding attempt after validator feedback. Choose worker_count, worker_briefs, "
            "aggregation_focus, validator_focus, and pass_threshold."
        ),
        scope="coding-organism.orchestrate",
        hard_constraints=list(task.hard_constraints),
        soft_constraints=list(task.soft_constraints),
        input_payload={
            "objective": task.objective,
            "acceptance_criteria": list(task.acceptance_criteria),
            "research_findings": list(task.research_findings),
            "repair_brief": repair_brief,
            "session_context": dict(task.session_context),
            "attempt": attempt,
            "repair_history": list(repair_history),
            "previous_validation": dict(previous_validation),
        },
        evidence_refs=task.evidence_refs,
        output_contract=_orchestrator_output_contract(),
        authority=WorkerAuthority.LEAD,
        metadata={"organism_id": organism.organism_id, "organism_stage": "orchestration"},
    )


def _normalize_worker_briefs(
    *,
    worker_count: int,
    worker_briefs: list[str],
    objective: str,
) -> list[str]:
    normalized = [str(brief).strip() for brief in worker_briefs if str(brief).strip()]
    while len(normalized) < worker_count:
        normalized.append(
            f"Investigate one bounded candidate path for objective: {objective}"
        )
    return normalized[:worker_count]


def _normalize_orchestrator_plan(
    *,
    outputs: dict[str, Any],
    organism: CodingOrganism,
    task: CodingTask,
) -> CodingOrchestratorPlan:
    payload = _parse_payload(outputs)
    try:
        plan = CodingOrchestratorPlan.model_validate(payload)
    except Exception:
        plan = CodingOrchestratorPlan()
    worker_count = max(1, min(int(plan.worker_count), int(organism.max_worker_count)))
    if worker_count < int(organism.default_worker_count):
        worker_count = max(worker_count, 1)
    worker_briefs = _normalize_worker_briefs(
        worker_count=worker_count,
        worker_briefs=list(plan.worker_briefs),
        objective=task.objective,
    )
    return plan.model_copy(
        update={
            "worker_count": worker_count,
            "worker_briefs": worker_briefs,
        }
    )


def _worker_member(
    *,
    organism: CodingOrganism,
    attempt: int,
    index: int,
    tissue_id: str,
    brief: str,
) -> TissueMember:
    member_id = f"worker-{index}"
    cell_id = f"{organism.base_id}.{member_id}"
    return TissueMember(
        member_id=member_id,
        address=CellAddress(
            cell_id=cell_id,
            tissue_id=tissue_id,
            organ_id=f"{organism.base_id}.worker-pool",
            organism_id=organism.organism_id,
        ),
        worker=WorkerDefinition(
            id=cell_id,
            role=organism.worker_role,
            instruction=organism.worker_instruction,
            model=organism.worker_model,
            tool_ids=list(organism.worker_tool_ids),
        ),
        instruction_suffix=f"Assigned brief:\n{brief}",
        input_payload_overrides={
            "worker_brief": brief,
            "worker_index": index,
            "attempt": attempt,
        },
        metadata={
            "organism_id": organism.organism_id,
            "worker_role": organism.worker_role,
        },
    )


def _worker_pool_pattern(
    *,
    organism: CodingOrganism,
    attempt: int,
    plan: CodingOrchestratorPlan,
) -> tuple[TissuePattern, CellAddress]:
    tissue_id = f"{organism.base_id}.worker-pool.{attempt}"
    coordinator = CellAddress(
        cell_id=f"{tissue_id}.coordinator",
        tissue_id=tissue_id,
        organ_id=f"{organism.base_id}.worker-pool",
        organism_id=organism.organism_id,
    )
    members = [
        _worker_member(
            organism=organism,
            attempt=attempt,
            index=index,
            tissue_id=tissue_id,
            brief=brief,
        )
        for index, brief in enumerate(plan.worker_briefs, start=1)
    ]
    pattern = parallel_worker_pool(
        f"{organism.base_id}.worker-pool",
        members=members,
        limits=TissuePoolLimits(
            max_members=organism.max_worker_count,
            max_concurrency=max(1, len(members)),
            max_failures=0,
        ),
        merge_mode=TissueMergeMode.APPEND,
        metadata={
            "organism_id": organism.organism_id,
            "attempt": attempt,
        },
    )
    return pattern, coordinator


def _completion_output_refs(signals: list[Any]) -> list[EvidenceRef]:
    terminal_signal = signals[-1] if signals else None
    if isinstance(terminal_signal, CompletionSignal):
        return [ref.model_copy(deep=True) for ref in terminal_signal.output_refs]
    return []


def coding_execution_organism(
    *,
    organism_id: str = "coding-organism",
    model: str | None = None,
    base_id: str = "coding-build",
) -> CodingOrganism:
    """Build the coding organism preset."""

    orchestrator_address = CellAddress(
        cell_id=f"{base_id}.orchestrator",
        organ_id=f"{base_id}.orchestrator",
        organism_id=organism_id,
    )
    return CodingOrganism(
        organism_id=organism_id,
        base_id=base_id,
        orchestrator_address=orchestrator_address,
        orchestrator_worker=WorkerDefinition(
            id=orchestrator_address.cell_id,
            role="coding_orchestrator",
            instruction=(
                "Coordinate the coding organism. Choose worker_count, distinct worker briefs, aggregation focus, "
                "and validator focus. Keep the pool small, purposeful, and bounded."
            ),
            model=model,
        ),
        worker_model=model,
        aggregator_organ=coding_aggregation_organ(
            organism_id=organism_id,
            model=model,
            organ_id=f"{base_id}.aggregation",
        ),
        validator_organ=universal_validator_organ(
            organism_id=organism_id,
            model=model,
            organ_id=f"{base_id}.validator",
        ),
        metadata={
            "coding_flow": "orchestrator -> worker pool -> aggregator -> validator",
        },
    )


async def execute_coding_organism(
    *,
    executor: WorkerCoreExecutor,
    organism: CodingOrganism,
    task: CodingTask,
    trace_log: CrossCellTraceLog | None = None,
    event_callback: OrganismEventCallback | None = None,
) -> CodingOrganismExecution:
    """Run the coding organism end to end."""

    def _emit(event: str, **payload: Any) -> None:
        if event_callback is None:
            return
        event_callback({"event": event, **payload})

    trace_log = trace_log or CrossCellTraceLog()
    stage_records: list[OrganismStageRecord] = []
    orchestrator_runs: list[HandoffExecution] = []
    worker_pool_executions: list[TissueExecution] = []
    aggregation_executions: list[OrganExecution] = []
    validation_executions: list[OrganExecution] = []
    repair_history: list[dict[str, Any]] = []

    best_score = -1.0
    best_attempt: int | None = None
    best_aggregation: OrganExecution | None = None
    best_validation: OrganExecution | None = None

    prior_packet: CellHandoffPacket | None = None
    prior_signal_id: str | None = None
    repair_brief = task.repair_brief
    previous_validation_payload: dict[str, Any] = {}
    _emit(
        "organism.started",
        organism_id=organism.organism_id,
        task_id=task.task_id,
        objective=task.objective,
        max_repair_rounds=organism.max_repair_rounds,
    )

    for attempt in range(1, organism.max_repair_rounds + 2):
        _emit(
            "attempt.started",
            attempt=attempt,
            reason=("initial" if attempt == 1 else "repair"),
            repair_brief=repair_brief,
        )
        if attempt == 1:
            orchestrator_packet = _root_orchestrator_packet(organism=organism, task=task)
        else:
            assert prior_packet is not None and prior_signal_id is not None
            orchestrator_packet = _repair_orchestrator_packet(
                organism=organism,
                task=task,
                attempt=attempt,
                parent_packet=prior_packet,
                parent_signal_id=prior_signal_id,
                repair_history=repair_history,
                previous_validation=previous_validation_payload,
                repair_brief=repair_brief,
            )

        _emit(
            "stage.started",
            stage="orchestration",
            attempt=attempt,
            message=(
                "Planning the coding attempt."
                if attempt == 1
                else "Planning the repair attempt from validator feedback."
            ),
        )
        orchestrator_run = await execute_cell_handoff(
            executor=executor,
            worker=organism.orchestrator_worker,
            packet=orchestrator_packet,
            trace_log=trace_log,
        )
        orchestrator_runs.append(orchestrator_run)
        orchestrator_payload = _parse_payload(orchestrator_run.result.outputs)
        stage_records.append(
            _record(
                stage="orchestration",
                attempt=attempt,
                packet=orchestrator_packet,
                status=orchestrator_run.result.status,
                summary=orchestrator_run.signals[-1].summary,
                organ_id=f"{organism.base_id}.orchestrator",
                output_keys=sorted(orchestrator_payload),
            )
        )
        _emit(
            "stage.completed",
            stage="orchestration",
            attempt=attempt,
            status=orchestrator_run.result.status,
            worker_count=orchestrator_payload.get("worker_count"),
            worker_briefs=list(orchestrator_payload.get("worker_briefs") or []),
            pass_threshold=orchestrator_payload.get("pass_threshold"),
            message=(
                f"Planned {orchestrator_payload.get('worker_count')} coding workers."
                if orchestrator_run.result.status == "completed"
                else "Could not complete planning."
            ),
        )
        if orchestrator_run.result.status != "completed":
            prior_packet = orchestrator_packet
            prior_signal_id = orchestrator_run.signals[-1].signal_id
            break

        plan = _normalize_orchestrator_plan(
            outputs=orchestrator_run.result.outputs,
            organism=organism,
            task=task,
        )
        worker_pool_pattern, worker_pool_address = _worker_pool_pattern(
            organism=organism,
            attempt=attempt,
            plan=plan,
        )
        _emit(
            "stage.started",
            stage="workers",
            attempt=attempt,
            worker_count=plan.worker_count,
            message=f"Running {plan.worker_count} coding workers.",
        )
        worker_packet = _child_packet(
            sender=orchestrator_packet.recipient,
            recipient=worker_pool_address,
            parent_packet=orchestrator_packet,
            parent_signal_id=orchestrator_run.signals[-1].signal_id,
            lineage_suffix=f"tissue:{worker_pool_pattern.pattern_id}",
            task_id=f"{task.task_id}:workers:{attempt}",
            instruction="Run the bounded coding worker pool according to the assigned briefs.",
            scope="coding-organism.workers",
            hard_constraints=list(task.hard_constraints),
            soft_constraints=list(task.soft_constraints),
            input_payload={
                "objective": task.objective,
                "acceptance_criteria": list(task.acceptance_criteria),
                "research_findings": list(task.research_findings),
                "repair_brief": repair_brief,
                "session_context": dict(task.session_context),
                "orchestration_plan": plan.model_dump(mode="json"),
                "attempt": attempt,
            },
            evidence_refs=task.evidence_refs,
            output_contract=_worker_output_contract(),
            authority=WorkerAuthority.DELEGATE,
            metadata={
                "organism_id": organism.organism_id,
                "organism_stage": "workers",
                "worker_count": plan.worker_count,
            },
        )
        worker_execution = await execute_tissue_pattern(
            executor=executor,
            pattern=worker_pool_pattern,
            packet=worker_packet,
            trace_log=trace_log,
        )
        worker_pool_executions.append(worker_execution)
        stage_records.append(
            _record(
                stage="workers",
                attempt=attempt,
                packet=worker_packet,
                status=worker_execution.result.status,
                summary=worker_execution.signals[-1].summary,
                organ_id=f"{organism.base_id}.worker-pool",
                output_keys=sorted(worker_execution.result.outputs),
            )
        )
        _emit(
            "stage.completed",
            stage="workers",
            attempt=attempt,
            status=worker_execution.result.status,
            worker_count=plan.worker_count,
            successful_workers=list(worker_execution.result.metadata.get("successful_member_ids") or []),
            message=(
                f"Worker pool finished with {len(worker_execution.result.metadata.get('successful_member_ids') or [])} successful workers."
                if worker_execution.result.status == "completed"
                else "Worker pool did not complete."
            ),
        )
        if worker_execution.result.status != "completed":
            prior_packet = worker_packet
            prior_signal_id = worker_execution.signals[-1].signal_id
            break

        _emit(
            "stage.started",
            stage="aggregation",
            attempt=attempt,
            message="Merging worker output into one bounded candidate.",
        )
        aggregation_packet = _child_packet(
            sender=worker_packet.recipient,
            recipient=organism.aggregator_organ.boundary_address,
            parent_packet=worker_packet,
            parent_signal_id=worker_execution.signals[-1].signal_id,
            lineage_suffix=f"organ:{organism.aggregator_organ.organ_id}",
            task_id=f"{task.task_id}:aggregate:{attempt}",
            instruction=plan.aggregation_focus,
            scope="coding-organism.aggregate",
            hard_constraints=list(task.hard_constraints),
            soft_constraints=list(task.soft_constraints),
            input_payload={
                "objective": task.objective,
                "acceptance_criteria": list(task.acceptance_criteria),
                "research_findings": list(task.research_findings),
                "repair_brief": repair_brief,
                "session_context": dict(task.session_context),
                "orchestration_plan": plan.model_dump(mode="json"),
                "worker_results": dict(worker_execution.result.outputs.get("member_results") or {}),
            },
            evidence_refs=[
                *task.evidence_refs,
                *_completion_output_refs(worker_execution.signals),
            ],
            output_contract=OutputContract(
                definition_of_done="Return the aggregated bounded coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "candidate_id": "<required>",
                        "change_summary": "<required>",
                        "target_files": "<required>",
                        "test_plan": "<required>",
                        "risks": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
            authority=WorkerAuthority.DELEGATE,
            metadata={
                "organism_id": organism.organism_id,
                "organism_stage": "aggregation",
                "organ_id": organism.aggregator_organ.organ_id,
            },
        )
        aggregation_execution = await execute_organ_pattern(
            executor=executor,
            pattern=organism.aggregator_organ,
            packet=aggregation_packet,
            trace_log=trace_log,
        )
        aggregation_executions.append(aggregation_execution)
        stage_records.append(
            _record(
                stage="aggregation",
                attempt=attempt,
                packet=aggregation_packet,
                status=aggregation_execution.result.status,
                summary=aggregation_execution.signals[-1].summary,
                organ_id=organism.aggregator_organ.organ_id,
                output_keys=sorted(aggregation_execution.result.outputs),
            )
        )
        _emit(
            "stage.completed",
            stage="aggregation",
            attempt=attempt,
            status=aggregation_execution.result.status,
            candidate_id=aggregation_execution.result.outputs.get("candidate_id"),
            target_files=list(aggregation_execution.result.outputs.get("target_files") or []),
            test_plan=list(aggregation_execution.result.outputs.get("test_plan") or []),
            message=(
                "Prepared one bounded coding candidate."
                if aggregation_execution.result.status == "completed"
                else "Could not prepare a candidate."
            ),
        )
        if aggregation_execution.result.status != "completed":
            prior_packet = aggregation_packet
            prior_signal_id = aggregation_execution.signals[-1].signal_id
            break

        _emit(
            "stage.started",
            stage="validation",
            attempt=attempt,
            message="Validating the aggregated candidate.",
        )
        validation_packet = _child_packet(
            sender=aggregation_packet.recipient,
            recipient=organism.validator_organ.boundary_address,
            parent_packet=aggregation_packet,
            parent_signal_id=aggregation_execution.signals[-1].signal_id,
            lineage_suffix=f"organ:{organism.validator_organ.organ_id}",
            task_id=f"{task.task_id}:validate:{attempt}",
            instruction=plan.validator_focus,
            scope="coding-organism.validate",
            hard_constraints=list(task.hard_constraints),
            soft_constraints=list(task.soft_constraints),
            input_payload={
                "candidate": dict(aggregation_execution.result.outputs),
                "acceptance_criteria": list(task.acceptance_criteria),
                "quality_bar": float(plan.pass_threshold),
                "comparison_context": {
                    "attempt": attempt,
                    "best_score_so_far": None if best_score < 0 else best_score,
                    "repair_brief": repair_brief,
                    "worker_count": plan.worker_count,
                },
            },
            evidence_refs=[
                *task.evidence_refs,
                *_completion_output_refs(worker_execution.signals),
                *aggregation_execution.result.output_refs,
            ],
            output_contract=OutputContract(
                definition_of_done="Return the validation result for the aggregated coding candidate.",
                expected_return_shape=json.dumps(
                    {
                        "passed": "<required>",
                        "overall_score": "<required>",
                        "dimension_scores": "<required>",
                        "repair_brief": "<required>",
                        "missing_requirements": "<required>",
                        "comparison_note": "<required>",
                    },
                    sort_keys=True,
                ),
            ),
            authority=WorkerAuthority.DELEGATE,
            metadata={
                "organism_id": organism.organism_id,
                "organism_stage": "validation",
                "organ_id": organism.validator_organ.organ_id,
            },
        )
        validation_execution = await execute_organ_pattern(
            executor=executor,
            pattern=organism.validator_organ,
            packet=validation_packet,
            trace_log=trace_log,
        )
        validation_executions.append(validation_execution)
        score = None
        if validation_execution.result.status == "completed":
            score = float(validation_execution.result.outputs.get("overall_score") or 0.0)
        stage_records.append(
            _record(
                stage="validation",
                attempt=attempt,
                packet=validation_packet,
                status=validation_execution.result.status,
                summary=validation_execution.signals[-1].summary,
                organ_id=organism.validator_organ.organ_id,
                score=score,
                output_keys=sorted(validation_execution.result.outputs),
            )
        )
        validation_passed = bool(validation_execution.result.outputs.get("passed"))
        _emit(
            "stage.completed",
            stage="validation",
            attempt=attempt,
            status=validation_execution.result.status,
            score=score,
            passed=validation_passed,
            repair_brief=str(validation_execution.result.outputs.get("repair_brief") or ""),
            missing_requirements=list(validation_execution.result.outputs.get("missing_requirements") or []),
            comparison_note=str(validation_execution.result.outputs.get("comparison_note") or ""),
            message=(
                f"Validator scored the candidate at {score:.2f}."
                if validation_execution.result.status == "completed" and score is not None
                else "Validation did not complete."
            ),
        )

        prior_packet = validation_packet
        prior_signal_id = validation_execution.signals[-1].signal_id
        previous_validation_payload = dict(validation_execution.result.outputs)

        if validation_execution.result.status != "completed":
            break

        if score is not None and score > best_score:
            best_score = score
            best_attempt = attempt
            best_aggregation = aggregation_execution
            best_validation = validation_execution

        passed = validation_passed
        repair_brief = str(validation_execution.result.outputs.get("repair_brief") or "")
        repair_history.append(
            {
                "attempt": attempt,
                "worker_count": plan.worker_count,
                "worker_briefs": list(plan.worker_briefs),
                "candidate_id": aggregation_execution.result.outputs.get("candidate_id"),
                "score": score,
                "passed": passed,
                "repair_brief": repair_brief,
            }
        )
        if not passed:
            _emit(
                "repair.requested",
                attempt=attempt,
                score=score,
                repair_brief=repair_brief,
                missing_requirements=list(validation_execution.result.outputs.get("missing_requirements") or []),
            )
        if passed and score is not None and score >= plan.pass_threshold:
            break

    trace_id = (
        orchestrator_runs[0].packet.trace.trace_id
        if orchestrator_runs
        else _trace(task.task_id, organism.organism_id).trace_id
    )
    observability = OrganismObservability(
        trace_id=trace_id,
        stage_records=stage_records,
        trace_rows=trace_log.inspect_trace(trace_id),
    )
    validation_scores = [
        float(execution.result.outputs.get("overall_score") or 0.0)
        for execution in validation_executions
        if execution.result.status == "completed"
    ]

    final_output: dict[str, Any] = {}
    status: Literal["completed", "failed"] = "failed"
    error: str | None = None
    if best_aggregation is not None and best_validation is not None:
        final_output = {
            **dict(best_aggregation.result.outputs),
            "validation_report": dict(best_validation.result.outputs),
            "repair_history": list(repair_history),
        }
        if bool(best_validation.result.outputs.get("passed")):
            status = "completed"
        else:
            error = "The best candidate still failed validation."
    else:
        error = "The coding organism did not produce a validated candidate."

    result = CodingOrganismResult(
        status=status,
        final_output=final_output,
        selected_attempt=best_attempt,
        validation_scores=validation_scores,
        improved_via_repair=len(validation_scores) >= 2 and validation_scores[-1] > validation_scores[0],
        error=error,
        observability=observability,
        metadata={
            "repair_history": repair_history,
            "orchestrator_runs": len(orchestrator_runs),
            "worker_pool_attempts": len(worker_pool_executions),
            "aggregation_attempts": len(aggregation_executions),
            "validation_attempts": len(validation_executions),
            "coding_flow": organism.metadata.get("coding_flow"),
        },
    )
    _emit(
        "organism.completed",
        organism_id=organism.organism_id,
        task_id=task.task_id,
        status=result.status,
        selected_attempt=result.selected_attempt,
        error=result.error,
        validation_scores=list(result.validation_scores),
        candidate_id=result.final_output.get("candidate_id"),
    )
    return CodingOrganismExecution(
        organism=organism,
        task=task,
        orchestrator_runs=orchestrator_runs,
        worker_pool_executions=worker_pool_executions,
        aggregation_executions=aggregation_executions,
        validation_executions=validation_executions,
        trace_log=trace_log,
        result=result,
    )


__all__ = [
    "CodingOrganism",
    "CodingOrganismExecution",
    "CodingOrganismResult",
    "CodingOrchestratorPlan",
    "CodingTask",
    "coding_execution_organism",
    "execute_coding_organism",
]
