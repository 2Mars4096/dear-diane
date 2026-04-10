"""First bounded reference organism for project-execution tasks."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.worker.composition import CrossCellTraceLog, HandoffExecution, execute_cell_handoff
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.model import WorkerDefinition
from dan.worker.model import WorkerAuthority
from dan.worker.organs import (
    OrganExecution,
    OrganPattern,
    coding_build_organ,
    deep_research_organ,
    execute_organ_pattern,
    synthesis_organ,
    universal_validator_organ,
)
from dan.worker.structured_payload import parse_jsonish_payload
from dan.worker.signaling import (
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    EvidenceRef,
    HandoffTask,
    SignalTrace,
)
from dan.worker.core.contracts import OutputContract


def _parse_payload(outputs: dict[str, Any]) -> dict[str, Any]:
    raw = outputs.get("result", outputs.get("text", outputs))
    if isinstance(raw, dict):
        return dict(raw)
    parsed = parse_jsonish_payload(raw)
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


class ProjectExecutionTask(BaseModel):
    """Exact bounded proving task for the first reference organism."""

    task_id: str
    objective: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    delivery_target: str = "bounded repo-change request"
    focused_validation_commands: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ProjectExecutionOrganism(BaseModel):
    """Composed reference organism with one planner and four bounded organs."""

    organism_id: str
    planner_address: CellAddress
    planner_worker: WorkerDefinition
    research_organ: OrganPattern
    validator_organ: OrganPattern
    coding_organ: OrganPattern
    synthesis_organ: OrganPattern
    pass_threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    max_repair_rounds: int = Field(default=1, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrganismStageRecord(BaseModel):
    """Inspectable stage-level accountability record."""

    stage: str
    attempt: int = 0
    organ_id: str | None = None
    packet_id: str
    sender_cell_id: str
    recipient_cell_id: str
    accountable_cell_id: str
    status: str
    summary: str
    score: float | None = None
    output_keys: list[str] = Field(default_factory=list)


class OrganismObservability(BaseModel):
    """Compact organism-level observability payload."""

    trace_id: str
    stage_records: list[OrganismStageRecord] = Field(default_factory=list)
    trace_rows: list[dict[str, Any]] = Field(default_factory=list)


class ProjectExecutionOrganismResult(BaseModel):
    """Normalized reference-organism outcome."""

    status: Literal["completed", "failed"]
    final_output: dict[str, Any] = Field(default_factory=dict)
    selected_attempt: int | None = None
    validation_scores: list[float] = Field(default_factory=list)
    improved_via_repair: bool = False
    error: str | None = None
    observability: OrganismObservability
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class ProjectExecutionOrganismExecution:
    """Recorded runtime for the full reference organism."""

    organism: ProjectExecutionOrganism
    task: ProjectExecutionTask
    planner_run: HandoffExecution
    research_execution: OrganExecution | None = None
    build_executions: list[OrganExecution] = field(default_factory=list)
    validation_executions: list[OrganExecution] = field(default_factory=list)
    synthesis_execution: OrganExecution | None = None
    trace_log: CrossCellTraceLog | None = None
    result: ProjectExecutionOrganismResult | None = None


def _planner_output_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Return a bounded plan that decomposes the project-execution task into "
            "research, build, validation, and synthesis instructions."
        ),
        expected_return_shape=json.dumps(
            {
                "research_instruction": "<required>",
                "build_instruction": "<required>",
                "validator_focus": "<required>",
                "synthesis_focus": "<required>",
                "pass_threshold": "<required>",
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


def _planner_packet(
    *,
    organism: ProjectExecutionOrganism,
    task: ProjectExecutionTask,
    trace: SignalTrace,
) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=trace,
        sender=_user_address(organism.organism_id),
        recipient=organism.planner_address.model_copy(deep=True),
        task=HandoffTask(
            task_id=f"{task.task_id}:plan",
            instruction=(
                "Decompose this bounded project-execution request into research, build, "
                "validation, and synthesis steps."
            ),
            scope="project-execution",
            hard_constraints=list(task.hard_constraints),
            soft_constraints=list(task.soft_constraints),
            input_payload={
                "objective": task.objective,
                "acceptance_criteria": list(task.acceptance_criteria),
                "delivery_target": task.delivery_target,
                "focused_validation_commands": list(task.focused_validation_commands),
            },
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in task.evidence_refs],
        output_contract=_planner_output_contract(),
        budget_limits=CellBudgetLimits(max_completion_rounds=1),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.LEAD,
            max_spawned_cells=0,
        ),
        metadata={"organism_id": organism.organism_id, "organism_stage": "planning"},
    )


def _organ_packet(
    *,
    organism: ProjectExecutionOrganism,
    organ: OrganPattern,
    task_id: str,
    parent_packet: CellHandoffPacket,
    parent_signal_id: str,
    instruction: str,
    scope: str,
    input_payload: dict[str, Any],
    evidence_refs: list[EvidenceRef],
) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=SignalTrace(
            trace_id=parent_packet.trace.trace_id,
            root_task_id=parent_packet.trace.root_task_id or parent_packet.task.task_id,
            parent_packet_id=parent_packet.packet_id,
            parent_signal_id=parent_signal_id,
            lineage=[*parent_packet.trace.lineage, f"organ:{organ.organ_id}"],
        ),
        sender=parent_packet.recipient.model_copy(deep=True),
        recipient=organ.boundary_address.model_copy(deep=True),
        task=HandoffTask(
            task_id=task_id,
            instruction=instruction,
            scope=scope,
            hard_constraints=list(parent_packet.task.hard_constraints),
            soft_constraints=list(parent_packet.task.soft_constraints),
            input_payload=dict(input_payload),
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in evidence_refs],
        output_contract=OutputContract(
            definition_of_done=f"Return the bounded public output for organ {organ.organ_id}.",
            expected_return_shape=json.dumps(
                {key: "<required>" for key in organ.boundary_contract.required_output_keys},
                sort_keys=True,
            ),
        ),
        budget_limits=CellBudgetLimits(max_completion_rounds=1),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.DELEGATE,
            max_spawned_cells=0,
        ),
        metadata={
            "organism_id": organism.organism_id,
            "organ_id": organ.organ_id,
            "organ_kind": organ.kind.value,
        },
    )


def project_execution_reference_organism(
    *,
    organism_id: str = "reference-project-execution",
    model: str | None = None,
) -> ProjectExecutionOrganism:
    """Build the first bounded reference organism preset."""

    planner_address = CellAddress(
        cell_id=f"{organism_id}.planner",
        organ_id="planner-brain",
        organism_id=organism_id,
    )
    return ProjectExecutionOrganism(
        organism_id=organism_id,
        planner_address=planner_address,
        planner_worker=WorkerDefinition(
            id=planner_address.cell_id,
            role="planner_brain",
            instruction="Plan bounded research, build, validation, and synthesis steps.",
            model=model,
        ),
        research_organ=deep_research_organ(organism_id=organism_id, model=model),
        validator_organ=universal_validator_organ(organism_id=organism_id, model=model),
        coding_organ=coding_build_organ(organism_id=organism_id, model=model),
        synthesis_organ=synthesis_organ(organism_id=organism_id, model=model),
        metadata={
            "later_builder_promotion": (
                "Keep the meta workflow builder out of the baseline organism. "
                "A later promotion path can consume passed validator reports from 45-4 "
                "as one additional bounded organ without replacing this organism surface."
            ),
        },
    )


async def execute_project_execution_organism(
    *,
    executor: WorkerCoreExecutor,
    organism: ProjectExecutionOrganism,
    task: ProjectExecutionTask,
    trace_log: CrossCellTraceLog | None = None,
) -> ProjectExecutionOrganismExecution:
    """Run the first bounded reference organism end to end."""

    trace_log = trace_log or CrossCellTraceLog()
    stage_records: list[OrganismStageRecord] = []
    planner_packet = _planner_packet(
        organism=organism,
        task=task,
        trace=_trace(task.task_id, organism.organism_id),
    )
    planner_run = await execute_cell_handoff(
        executor=executor,
        worker=organism.planner_worker,
        packet=planner_packet,
        trace_log=trace_log,
    )
    planner_payload = _parse_payload(planner_run.result.outputs)
    stage_records.append(
        _record(
            stage="planning",
            attempt=0,
            packet=planner_packet,
            status=planner_run.result.status,
            summary=planner_run.signals[-1].summary,
            organ_id="planner-brain",
            output_keys=sorted(planner_payload),
        )
    )

    research_packet = _organ_packet(
        organism=organism,
        organ=organism.research_organ,
        task_id=f"{task.task_id}:research",
        parent_packet=planner_packet,
        parent_signal_id=planner_run.signals[-1].signal_id,
        instruction=str(planner_payload.get("research_instruction") or "Ground the task with the supplied evidence."),
        scope="project-execution.research",
        input_payload={
            "objective": task.objective,
            "acceptance_criteria": list(task.acceptance_criteria),
            "delivery_target": task.delivery_target,
        },
        evidence_refs=task.evidence_refs,
    )
    research_execution = await execute_organ_pattern(
        executor=executor,
        pattern=organism.research_organ,
        packet=research_packet,
        trace_log=trace_log,
    )
    stage_records.append(
        _record(
            stage="research",
            attempt=0,
            packet=research_packet,
            status=research_execution.result.status,
            summary=research_execution.signals[-1].summary,
            organ_id=organism.research_organ.organ_id,
            output_keys=sorted(research_execution.result.outputs),
        )
    )
    if research_execution.result.status != "completed":
        observability = OrganismObservability(
            trace_id=planner_packet.trace.trace_id,
            stage_records=stage_records,
            trace_rows=trace_log.inspect_trace(planner_packet.trace.trace_id),
        )
        result = ProjectExecutionOrganismResult(
            status="failed",
            error=research_execution.result.error or "Research organ failed.",
            observability=observability,
            metadata={"failed_stage": "research"},
        )
        return ProjectExecutionOrganismExecution(
            organism=organism,
            task=task,
            planner_run=planner_run,
            research_execution=research_execution,
            trace_log=trace_log,
            result=result,
        )

    build_executions: list[OrganExecution] = []
    validation_executions: list[OrganExecution] = []
    best_score = -1.0
    best_attempt: int | None = None
    best_build: OrganExecution | None = None
    best_validation: OrganExecution | None = None
    repair_history: list[dict[str, Any]] = []
    repair_brief = ""
    for attempt in range(1, organism.max_repair_rounds + 2):
        build_packet = _organ_packet(
            organism=organism,
            organ=organism.coding_organ,
            task_id=f"{task.task_id}:build:{attempt}",
            parent_packet=research_packet,
            parent_signal_id=research_execution.signals[-1].signal_id,
            instruction=str(planner_payload.get("build_instruction") or "Build one bounded candidate."),
            scope="project-execution.build",
            input_payload={
                "objective": task.objective,
                "acceptance_criteria": list(task.acceptance_criteria),
                "research_findings": dict(research_execution.result.outputs),
                "repair_brief": repair_brief,
            },
            evidence_refs=[
                *task.evidence_refs,
                *research_execution.result.output_refs,
            ],
        )
        build_execution = await execute_organ_pattern(
            executor=executor,
            pattern=organism.coding_organ,
            packet=build_packet,
            trace_log=trace_log,
        )
        build_executions.append(build_execution)
        stage_records.append(
            _record(
                stage="build",
                attempt=attempt,
                packet=build_packet,
                status=build_execution.result.status,
                summary=build_execution.signals[-1].summary,
                organ_id=organism.coding_organ.organ_id,
                output_keys=sorted(build_execution.result.outputs),
            )
        )
        if build_execution.result.status != "completed":
            break

        validation_packet = _organ_packet(
            organism=organism,
            organ=organism.validator_organ,
            task_id=f"{task.task_id}:validate:{attempt}",
            parent_packet=build_packet,
            parent_signal_id=build_execution.signals[-1].signal_id,
            instruction=str(planner_payload.get("validator_focus") or "Score the candidate and emit a repair brief when needed."),
            scope="project-execution.validate",
            input_payload={
                "candidate": dict(build_execution.result.outputs),
                "acceptance_criteria": list(task.acceptance_criteria),
                "quality_bar": float(planner_payload.get("pass_threshold") or organism.pass_threshold),
                "comparison_context": {
                    "attempt": attempt,
                    "best_score_so_far": None if best_score < 0 else best_score,
                    "repair_brief": repair_brief,
                },
            },
            evidence_refs=[
                *research_execution.result.output_refs,
                *build_execution.result.output_refs,
            ],
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
                stage="validate",
                attempt=attempt,
                packet=validation_packet,
                status=validation_execution.result.status,
                summary=validation_execution.signals[-1].summary,
                organ_id=organism.validator_organ.organ_id,
                score=score,
                output_keys=sorted(validation_execution.result.outputs),
            )
        )
        if validation_execution.result.status != "completed":
            break

        passed = bool(validation_execution.result.outputs.get("passed"))
        if score is not None and score > best_score:
            best_score = score
            best_attempt = attempt
            best_build = build_execution
            best_validation = validation_execution
        repair_brief = str(validation_execution.result.outputs.get("repair_brief") or "")
        repair_history.append(
            {
                "attempt": attempt,
                "candidate_id": build_execution.result.outputs.get("candidate_id"),
                "score": score,
                "passed": passed,
                "repair_brief": repair_brief,
            }
        )
        if passed and score is not None and score >= organism.pass_threshold:
            break

    synthesis_execution: OrganExecution | None = None
    if best_build is not None and best_validation is not None:
        synthesis_packet = _organ_packet(
            organism=organism,
            organ=organism.synthesis_organ,
            task_id=f"{task.task_id}:synthesis",
            parent_packet=planner_packet,
            parent_signal_id=(best_validation.signals[-1].signal_id if best_validation.signals else planner_run.signals[-1].signal_id),
            instruction=str(planner_payload.get("synthesis_focus") or "Produce the final delivery summary."),
            scope="project-execution.synthesis",
            input_payload={
                "objective": task.objective,
                "selected_candidate": dict(best_build.result.outputs),
                "validation_report": dict(best_validation.result.outputs),
                "research_findings": dict(research_execution.result.outputs),
                "repair_history": list(repair_history),
            },
            evidence_refs=[
                *research_execution.result.output_refs,
                *best_build.result.output_refs,
                *best_validation.result.output_refs,
            ],
        )
        synthesis_execution = await execute_organ_pattern(
            executor=executor,
            pattern=organism.synthesis_organ,
            packet=synthesis_packet,
            trace_log=trace_log,
        )
        stage_records.append(
            _record(
                stage="synthesis",
                attempt=0,
                packet=synthesis_packet,
                status=synthesis_execution.result.status,
                summary=synthesis_execution.signals[-1].summary,
                organ_id=organism.synthesis_organ.organ_id,
                output_keys=sorted(synthesis_execution.result.outputs),
            )
        )

    observability = OrganismObservability(
        trace_id=planner_packet.trace.trace_id,
        stage_records=stage_records,
        trace_rows=trace_log.inspect_trace(planner_packet.trace.trace_id),
    )
    validation_scores = [
        float(execution.result.outputs.get("overall_score") or 0.0)
        for execution in validation_executions
        if execution.result.status == "completed"
    ]
    status = (
        "completed"
        if synthesis_execution is not None and synthesis_execution.result.status == "completed"
        else "failed"
    )
    result = ProjectExecutionOrganismResult(
        status=status,
        final_output=(
            dict(synthesis_execution.result.outputs)
            if synthesis_execution is not None and synthesis_execution.result.status == "completed"
            else {}
        ),
        selected_attempt=best_attempt,
        validation_scores=validation_scores,
        improved_via_repair=len(validation_scores) >= 2 and validation_scores[-1] > validation_scores[0],
        error=(
            None
            if status == "completed"
            else (
                synthesis_execution.result.error
                if synthesis_execution is not None
                else "The organism did not produce a validated candidate."
            )
        ),
        observability=observability,
        metadata={
            "repair_history": repair_history,
            "planner_outputs": planner_payload,
            "focused_validation_commands": list(task.focused_validation_commands),
            "deferred_builder_promotion": organism.metadata.get("later_builder_promotion"),
        },
    )
    return ProjectExecutionOrganismExecution(
        organism=organism,
        task=task,
        planner_run=planner_run,
        research_execution=research_execution,
        build_executions=build_executions,
        validation_executions=validation_executions,
        synthesis_execution=synthesis_execution,
        trace_log=trace_log,
        result=result,
    )


__all__ = [
    "OrganismObservability",
    "OrganismStageRecord",
    "ProjectExecutionOrganism",
    "ProjectExecutionOrganismExecution",
    "ProjectExecutionOrganismResult",
    "ProjectExecutionTask",
    "execute_project_execution_organism",
    "project_execution_reference_organism",
]
