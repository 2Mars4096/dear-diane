"""Local harnesses for the bounded project-execution organism."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dan.providers import LLMProvider
from dan.worker.composition import CrossCellTraceLog
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.contracts import OutputContract
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.organism_log import new_trace_id
from dan.worker.organisms.local_runtime import (
    DEFAULT_LIVE_ORGANISM_TOOL_IDS,
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
    attach_local_tooling_to_reference_organism,
    available_local_organism_tools,
)
from dan.worker.organisms.project_execution import (
    MAX_DEEP_RESEARCH_READERS,
    ProjectExecutionTask,
    ProjectExecutionOrganismExecution,
    resolve_deep_research_reader_briefs_for_task,
    execute_project_execution_organism,
    project_execution_reference_organism,
    resolve_deep_research_reader_count,
)
from dan.worker.organs import OrganExecution, execute_organ_pattern
from dan.worker.signaling import (
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    HandoffTask,
    SignalTrace,
)
from dan.worker.model import WorkerAuthority
from dan.worker.signaling import EvidenceRef

DEFAULT_REFERENCE_OBJECTIVE = (
    "Repair the bounded project-execution candidate so validator-driven delivery "
    "requirements stay explicit."
)
DEFAULT_REFERENCE_ACCEPTANCE_CRITERIA = [
    "Ground the change in the supplied evidence.",
    "Return one bounded candidate with explicit target files.",
    "Include one focused validation command.",
    "End with an evidence-backed delivery summary and accountability.",
]
DEFAULT_REFERENCE_VALIDATION_COMMANDS = [
    "PYTHONPATH=src pytest -q tests/eval/test_reference_organism_acceptance.py",
]
DEFAULT_REFERENCE_HARD_CONSTRAINTS = [
    "Stay bounded and inspectable.",
    "Do not collapse the biology stack into one oversized manager prompt.",
]
DEFAULT_REFERENCE_SOFT_CONSTRAINTS = [
    "Prefer the smallest viable change that still closes the validator gap.",
]
DEFAULT_REFERENCE_EVIDENCE_SUMMARIES = [
    "The first candidate often omits an explicit focused validation command.",
    "The final delivery must preserve missing-requirement reporting and accountability.",
    "Validator should request repair when the focused test command is absent.",
]
_DEEP_RESEARCH_READER_NOTES = [
    "The defect is that the candidate fix must carry an explicit focused test command.",
    "The acceptance bar also requires the delivery summary to name missing requirements and residual risk.",
    "The implementation surface stays narrow: the validator path is the primary repo area that should change.",
    "A strong candidate needs a concrete validation step, not just a promise that tests exist.",
    "Any live web or doc lookup should be used only to tighten grounding, not to replace the supplied evidence.",
    "The current evidence is consistent; the main risk is under-specifying the validation proof rather than contradicting the core diagnosis.",
    "The recommended change should stay bounded and explicit so downstream build and synthesis steps can stay inspectable.",
    "Confidence is high once the explicit validation step and final accountability language are both preserved.",
]


class ReferenceOrganismDemoReport(BaseModel):
    """Compact CLI-friendly report for the reference organism demo."""

    status: str
    trace_id: str
    selected_attempt: int | None = None
    initial_score: float | None = None
    final_score: float | None = None
    improved_via_repair: bool = False
    validation_attempts: int = 0
    handoff_count: int = 0
    signal_count: int = 0
    stage_sequence: list[str] = Field(default_factory=list)
    build_candidate_ids: list[str] = Field(default_factory=list)
    final_output: dict[str, Any] = Field(default_factory=dict)
    stage_records: list[dict[str, Any]] = Field(default_factory=list)


class DeepResearchOrganDemoReport(BaseModel):
    """Compact CLI-friendly report for a standalone deep-research organ run."""

    status: str
    trace_id: str
    handoff_count: int = 0
    signal_count: int = 0
    selected_reader_count: int | None = None
    selected_reader_briefs: list[str] = Field(default_factory=list)
    output_ref_ids: list[str] = Field(default_factory=list)
    final_output: dict[str, Any] = Field(default_factory=dict)
    stage_records: list[dict[str, Any]] = Field(default_factory=list)
    trace_rows: list[dict[str, Any]] = Field(default_factory=list)


def _build_report(execution: ProjectExecutionOrganismExecution) -> ReferenceOrganismDemoReport:
    assert execution.result is not None
    validation_scores = list(execution.result.validation_scores)
    trace_rows = execution.result.observability.trace_rows
    return ReferenceOrganismDemoReport(
        status=execution.result.status,
        trace_id=execution.result.observability.trace_id,
        selected_attempt=execution.result.selected_attempt,
        initial_score=validation_scores[0] if validation_scores else None,
        final_score=validation_scores[-1] if validation_scores else None,
        improved_via_repair=execution.result.improved_via_repair,
        validation_attempts=len(validation_scores),
        handoff_count=sum(1 for row in trace_rows if row["kind"] == "handoff"),
        signal_count=sum(1 for row in trace_rows if row["kind"] == "signal"),
        stage_sequence=[
            f"{record.stage}:{record.attempt or 0}"
            for record in execution.result.observability.stage_records
        ],
        build_candidate_ids=[
            build.result.outputs.get("candidate_id", "")
            for build in execution.build_executions
            if build.result.status == "completed"
        ],
        final_output=dict(execution.result.final_output),
        stage_records=[
            record.model_dump(mode="json", exclude_none=True)
            for record in execution.result.observability.stage_records
        ],
    )


def _build_research_report(
    execution: OrganExecution,
    *,
    trace_log: CrossCellTraceLog,
) -> DeepResearchOrganDemoReport:
    trace_rows = trace_log.inspect_trace(execution.packet.trace.trace_id)
    stage_records: list[dict[str, Any]] = []
    if execution.tissue_packet is not None:
        stage_records.append(
            {
                "stage": "research.tissue",
                "packet_id": execution.tissue_packet.packet_id,
                "recipient_cell_id": execution.tissue_packet.recipient.cell_id,
                "status": execution.tissue_execution.result.status if execution.tissue_execution is not None else "failed",
            }
        )
    if execution.lead_packet is not None:
        stage_records.append(
            {
                "stage": "research.lead",
                "packet_id": execution.lead_packet.packet_id,
                "recipient_cell_id": execution.lead_packet.recipient.cell_id,
                "status": execution.lead_execution.result.status if execution.lead_execution is not None else "failed",
            }
        )
    return DeepResearchOrganDemoReport(
        status=execution.result.status,
        trace_id=execution.packet.trace.trace_id,
        handoff_count=sum(1 for row in trace_rows if row["kind"] == "handoff"),
        signal_count=sum(1 for row in trace_rows if row["kind"] == "signal"),
        selected_reader_count=len(execution.pattern.tissue.members) if execution.pattern.tissue is not None else None,
        selected_reader_briefs=[
            str(brief).strip()
            for brief in list(execution.pattern.metadata.get("reader_briefs") or [])
            if str(brief).strip()
        ],
        output_ref_ids=[ref.ref_id for ref in execution.result.output_refs],
        final_output=dict(execution.result.outputs),
        stage_records=stage_records,
        trace_rows=trace_rows,
    )


def _deep_research_packet(
    *,
    organism_id: str,
    organ_id: str,
    task: ProjectExecutionTask,
    max_runtime_seconds: int | None = None,
    trace_id: str | None = None,
) -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=SignalTrace(
            trace_id=trace_id or new_trace_id(),
            root_task_id=task.task_id,
            lineage=[f"organism:{organism_id}", "surface:research-only"],
        ),
        sender=CellAddress(cell_id="user.request", organism_id=organism_id),
        recipient=CellAddress(
            cell_id=f"{organ_id}.boundary",
            organ_id=organ_id,
            organism_id=organism_id,
        ),
        task=HandoffTask(
            task_id=f"{task.task_id}:research-only",
            instruction=(
                "Ground the task with supplied evidence and any available read-only tools. "
                "Return a bounded deep-research report."
            ),
            scope="project-execution.research",
            hard_constraints=list(task.hard_constraints),
            soft_constraints=list(task.soft_constraints),
            input_payload={
                "objective": task.objective,
                "temporal_mode": task.temporal_mode,
                "temporal_anchor": task.temporal_anchor,
                "temporal_window": task.temporal_window,
                "temporal_guidance": task.temporal_guidance,
                "acceptance_criteria": list(task.acceptance_criteria),
                "delivery_target": task.delivery_target,
                "focused_validation_commands": list(task.focused_validation_commands),
                "hard_constraints": list(task.hard_constraints),
                "soft_constraints": list(task.soft_constraints),
            },
        ),
        evidence_refs=[ref.model_copy(deep=True) for ref in task.evidence_refs],
        output_contract=OutputContract(
            definition_of_done=f"Return the bounded public output for organ {organ_id}.",
            expected_return_shape=json.dumps(
                {
                    "findings": "<required>",
                    "evidence_summary": "<required>",
                    "evidence_refs": "<required>",
                    "contradictions": "<required>",
                    "open_questions": "<required>",
                    "verification_facts": "<required>",
                    "audit_issues": "<required>",
                    "quality_gates": "<required>",
                    "report_readiness": "<required>",
                    "readiness_note": "<required>",
                    "confidence": "<required>",
                    "recommended_change": "<required>",
                },
                sort_keys=True,
            ),
        ),
        budget_limits=CellBudgetLimits(
            max_completion_rounds=1,
            max_runtime_seconds=max_runtime_seconds,
        ),
        authority_limits=CellAuthorityLimits(
            acting_authority=WorkerAuthority.DELEGATE,
            max_spawned_cells=0,
        ),
        metadata={
            "organism_id": organism_id,
            "organ_id": organ_id,
            "organ_kind": "deep_research",
            "surface": "research-only",
        },
    )


def _research_reader_note(worker_id: str) -> str:
    try:
        suffix = worker_id.rsplit("-", 1)[-1]
        index = max(1, ord(suffix.lower()) - ord("a") + 1)
    except Exception:
        index = 1
    note = _DEEP_RESEARCH_READER_NOTES[(index - 1) % len(_DEEP_RESEARCH_READER_NOTES)]
    return json.dumps(
        {
            "findings": [note],
            "evidence_refs": ["brief:issue"],
            "contradictions": [],
            "open_questions": [],
            "reasoning_notes": [],
            "follow_up_queries": [],
        },
        sort_keys=True,
    )


def _reference_organism_for_task(
    *,
    task: ProjectExecutionTask,
    organism_id: str,
    model: str,
    research_reader_count: int | None,
    research_reader_briefs: list[str] | tuple[str, ...] | None = None,
):
    resolved_reader_count = resolve_deep_research_reader_count(
        task=task,
        requested_count=research_reader_count,
    )
    resolved_reader_briefs = resolve_deep_research_reader_briefs_for_task(
        task=task,
        reader_count=resolved_reader_count,
        requested_briefs=research_reader_briefs,
    )
    return project_execution_reference_organism(
        organism_id=organism_id,
        model=model,
        research_reader_count=resolved_reader_count,
        research_reader_briefs=resolved_reader_briefs,
    )


class _ReferenceOrganismDemoCompletionProvider:
    """Deterministic completion provider reused by the local demo CLI."""

    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []
        self._counts: dict[str, int] = {}

    def _count(self, worker_id: str) -> int:
        current = self._counts.get(worker_id, 0) + 1
        self._counts[worker_id] = current
        return current

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        worker_id = str(request.metadata.get("worker_id") or "")
        call_index = self._count(worker_id)

        if worker_id.endswith(".planner"):
            return CompletionResponse(
                text=json.dumps(
                    {
                        "research_instruction": "Ground the candidate in the supplied repo-change evidence.",
                        "build_instruction": "Produce one bounded repo-change candidate with explicit files and focused tests.",
                        "validator_focus": "Score contract adherence, evidence use, and repairability.",
                        "synthesis_focus": "Deliver a final evidence-backed repo-change summary with accountability.",
                        "pass_threshold": 0.9,
                    },
                    sort_keys=True,
                ),
                raw={"worker_id": worker_id},
            )

        if worker_id.startswith("deep-research.reader-"):
            return CompletionResponse(
                text=_research_reader_note(worker_id),
                raw={"worker_id": worker_id},
            )
        if worker_id == "deep-research.lead":
            return CompletionResponse(
                text=json.dumps(
                    {
                        "findings": [
                            "A passing candidate must name a focused validation command.",
                            "The final delivery must preserve missing-requirement and residual-risk reporting.",
                        ],
                        "evidence_summary": ["brief:issue", "brief:acceptance", "brief:test-gap"],
                        "evidence_refs": ["brief:issue", "brief:acceptance", "brief:test-gap"],
                        "contradictions": [],
                        "open_questions": ["No blocking open questions remain after the supplied evidence."],
                        "verification_facts": [
                            {
                                "fact": "A passing candidate must name a focused validation command.",
                                "status": "verified",
                                "source": "brief:test-gap",
                                "as_of": "",
                                "note": "Explicitly supported by the supplied validation-gap brief.",
                            }
                        ],
                        "audit_issues": [],
                        "quality_gates": [
                            {
                                "gate": "time_anchor",
                                "status": "pass",
                                "summary": "The deterministic demo is grounded in supplied static evidence.",
                                "evidence_ref": "brief:issue",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "scope_boundary",
                                "status": "pass",
                                "summary": "The claim scope is limited to the supplied repo-change brief.",
                                "evidence_ref": "brief:acceptance",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "source_authority",
                                "status": "pass",
                                "summary": "The demo uses the supplied acceptance and validation-gap evidence.",
                                "evidence_ref": "brief:test-gap",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "numeric_reconciliation",
                                "status": "not_applicable",
                                "summary": "No material numeric conflict is present in the demo evidence.",
                                "evidence_ref": "",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "claim_object_fit",
                                "status": "pass",
                                "summary": "The recommendation maps directly to the validation-step gap.",
                                "evidence_ref": "brief:test-gap",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "final_status",
                                "status": "pass",
                                "summary": "Grounded bounded report; not an external recommendation.",
                                "evidence_ref": "brief:test-gap",
                                "required_follow_up": "",
                            },
                        ],
                        "report_readiness": "grounded",
                        "readiness_note": "The supplied evidence is internally consistent and specific enough for a bounded grounded report.",
                        "confidence": 0.88,
                        "recommended_change": "Route the repair loop through a candidate that adds explicit validation steps and final reporting.",
                    },
                    sort_keys=True,
                ),
                raw={"worker_id": worker_id},
            )

        if worker_id == "coding-build.builder-a":
            return CompletionResponse(
                text="Candidate A keeps the patch narrow and touches the validator path only.",
                raw={"worker_id": worker_id, "call_index": call_index},
            )
        if worker_id == "coding-build.builder-b":
            return CompletionResponse(
                text="Candidate B expands the patch slightly so it can also carry the focused test command and delivery notes.",
                raw={"worker_id": worker_id, "call_index": call_index},
            )
        if worker_id == "coding-build.lead":
            if call_index == 1:
                payload = {
                    "candidate_id": "candidate-1",
                    "change_summary": "Patch the validator path but leave the focused test command implicit.",
                    "target_files": ["src/dan/worker/organisms/project_execution.py"],
                    "test_plan": [],
                    "risks": ["The candidate is still missing an explicit focused validation command."],
                }
            else:
                payload = {
                    "candidate_id": "candidate-2",
                    "change_summary": "Patch the validator path and add an explicit focused validation command plus delivery note coverage.",
                    "target_files": [
                        "src/dan/worker/organisms/project_execution.py",
                        "tests/eval/test_reference_organism_acceptance.py",
                    ],
                    "test_plan": [
                        "PYTHONPATH=src pytest -q tests/eval/test_reference_organism_acceptance.py",
                    ],
                    "risks": ["Residual risk is low after the repair-focused validation pass."],
                }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "universal-validator.reviewer-a":
            return CompletionResponse(text="repair" if call_index == 1 else "pass", raw={"worker_id": worker_id})
        if worker_id == "universal-validator.reviewer-b":
            return CompletionResponse(text="repair" if call_index == 1 else "pass", raw={"worker_id": worker_id})
        if worker_id == "universal-validator.lead":
            if call_index == 1:
                payload = {
                    "passed": False,
                    "overall_score": 0.74,
                    "dimension_scores": {
                        "grounding": 0.92,
                        "bounded_build": 0.71,
                        "validation_specificity": 0.58,
                    },
                    "repair_brief": "Add a focused validation command and make the final delivery summary explicit about missing requirements.",
                    "missing_requirements": [
                        "focused validation command",
                        "explicit delivery summary language",
                    ],
                    "comparison_note": "Candidate 1 is plausible but still under-specifies validation and delivery.",
                }
            else:
                payload = {
                    "passed": True,
                    "overall_score": 0.96,
                    "dimension_scores": {
                        "grounding": 0.95,
                        "bounded_build": 0.96,
                        "validation_specificity": 0.97,
                    },
                    "repair_brief": "",
                    "missing_requirements": [],
                    "comparison_note": "Candidate 2 closes the validator-identified gap and is the better bounded artifact.",
                }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        if worker_id == "synthesis.reporter-a":
            return CompletionResponse(
                text="Delivery fragment: the repaired candidate now includes an explicit focused validation command.",
                raw={"worker_id": worker_id},
            )
        if worker_id == "synthesis.reporter-b":
            return CompletionResponse(
                text="Accountability fragment: planner, research, build, validator, and synthesis stages are all inspectable.",
                raw={"worker_id": worker_id},
            )
        if worker_id == "synthesis.lead":
            payload = {
                "delivery_summary": (
                    "The reference organism grounded the repo-change request, repaired the initial candidate "
                    "after validator feedback, and delivered a bounded evidence-backed result."
                ),
                "final_candidate": {
                    "candidate_id": "candidate-2",
                    "change_summary": "Patch the validator path and add the explicit validation command.",
                },
                "validation_summary": {
                    "overall_score": 0.96,
                    "passed": True,
                },
                "accountability": {
                    "planner": "reference-project-execution.planner",
                    "research": "deep-research.lead",
                    "build": "coding-build.lead",
                    "validator": "universal-validator.lead",
                    "synthesis": "synthesis.lead",
                },
            }
            return CompletionResponse(text=json.dumps(payload, sort_keys=True), raw={"worker_id": worker_id})

        raise AssertionError(f"Unexpected worker_id: {worker_id}")


def build_reference_organism_demo_task(
    workdir: Path,
    *,
    task_id: str = "bounded-project-execution",
    objective: str = DEFAULT_REFERENCE_OBJECTIVE,
    acceptance_criteria: list[str] | None = None,
    delivery_target: str = "repo-change brief",
    focused_validation_commands: list[str] | None = None,
    hard_constraints: list[str] | None = None,
    soft_constraints: list[str] | None = None,
    evidence_summaries: list[str] | None = None,
) -> ProjectExecutionTask:
    """Build the default bounded coding task used by the local demo CLI."""

    workdir.mkdir(parents=True, exist_ok=True)
    acceptance = list(acceptance_criteria or DEFAULT_REFERENCE_ACCEPTANCE_CRITERIA)
    validations = list(focused_validation_commands or DEFAULT_REFERENCE_VALIDATION_COMMANDS)
    hard = list(hard_constraints or DEFAULT_REFERENCE_HARD_CONSTRAINTS)
    soft = list(soft_constraints or DEFAULT_REFERENCE_SOFT_CONSTRAINTS)
    evidence_texts = list(evidence_summaries or DEFAULT_REFERENCE_EVIDENCE_SUMMARIES)
    evidence_refs: list[EvidenceRef] = []
    for index, summary in enumerate(evidence_texts, start=1):
        slug = {
            1: "issue",
            2: "acceptance",
            3: "test-gap",
        }.get(index, f"evidence-{index}")
        locator = workdir / f"{slug}.md"
        locator.write_text(f"# {slug}\n\n{summary}\n", encoding="utf-8")
        evidence_refs.append(
            EvidenceRef(
                ref_id=f"brief:{slug}",
                label=slug.replace("-", " ").title(),
                summary=summary,
                source="reference-demo",
                locator=str(locator),
            )
        )

    return ProjectExecutionTask(
        task_id=task_id,
        objective=objective,
        acceptance_criteria=acceptance,
        delivery_target=delivery_target,
        focused_validation_commands=validations,
        hard_constraints=hard,
        soft_constraints=soft,
        evidence_refs=evidence_refs,
    )


async def run_reference_organism_demo(
    workdir: Path,
    *,
    task: ProjectExecutionTask | None = None,
    model: str = "stub-model",
    organism_id: str = "reference-project-execution",
    research_reader_count: int | None = None,
    research_reader_briefs: list[str] | tuple[str, ...] | None = None,
) -> ReferenceOrganismDemoReport:
    """Run the deterministic bounded project-execution organism demo."""

    provider = _ReferenceOrganismDemoCompletionProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    effective_task = task or build_reference_organism_demo_task(workdir)
    organism = _reference_organism_for_task(
        task=effective_task,
        organism_id=organism_id,
        model=model,
        research_reader_count=research_reader_count,
        research_reader_briefs=research_reader_briefs,
    )
    trace_log = CrossCellTraceLog()
    execution = await execute_project_execution_organism(
        executor=executor,
        organism=organism,
        task=effective_task,
        trace_log=trace_log,
    )
    return _build_report(execution)


async def run_deep_research_organ_demo(
    workdir: Path,
    *,
    task: ProjectExecutionTask | None = None,
    model: str = "stub-model",
    organism_id: str = "reference-project-execution",
    research_reader_count: int | None = None,
    research_reader_briefs: list[str] | tuple[str, ...] | None = None,
) -> DeepResearchOrganDemoReport:
    """Run only the bounded deep-research organ with the deterministic demo provider."""

    provider = _ReferenceOrganismDemoCompletionProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    effective_task = task or build_reference_organism_demo_task(workdir)
    organism = _reference_organism_for_task(
        task=effective_task,
        organism_id=organism_id,
        model=model,
        research_reader_count=research_reader_count,
        research_reader_briefs=research_reader_briefs,
    )
    trace_log = CrossCellTraceLog()
    packet = _deep_research_packet(
        organism_id=organism_id,
        organ_id=organism.research_organ.organ_id,
        task=effective_task,
    )
    execution = await execute_organ_pattern(
        executor=executor,
        pattern=organism.research_organ,
        packet=packet,
        trace_log=trace_log,
    )
    return _build_research_report(execution, trace_log=trace_log)


async def run_reference_organism_live(
    workdir: Path,
    *,
    llm_provider: LLMProvider,
    task: ProjectExecutionTask | None = None,
    model: str,
    organism_id: str = "reference-project-execution",
    tool_ids: list[str] | None = None,
    workspace_root: str | Path | None = None,
    max_tool_rounds: int | None = 8,
    max_tool_calls: int = 24,
    research_reader_count: int | None = None,
    research_reader_briefs: list[str] | tuple[str, ...] | None = None,
    event_callback=None,
) -> ReferenceOrganismDemoReport:
    """Run the bounded reference organism against a live provider and local tools."""

    effective_task = task or build_reference_organism_demo_task(workdir)
    tool_runtime = LocalOrganismToolRuntime(
        tool_ids=tool_ids or DEFAULT_LIVE_ORGANISM_TOOL_IDS,
        workspace_root=workspace_root,
        event_callback=event_callback,
    )
    completion_provider = ToolLoopCompletionProvider(
        provider=llm_provider,
        tool_runtime=tool_runtime,
        default_model=model,
        max_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
        event_callback=event_callback,
    )
    executor = WorkerCoreExecutor(completion_provider=completion_provider)
    organism = attach_local_tooling_to_reference_organism(
        _reference_organism_for_task(
            task=effective_task,
            organism_id=organism_id,
            model=model,
            research_reader_count=research_reader_count,
            research_reader_briefs=research_reader_briefs,
        ),
        tool_ids=tool_runtime.tool_ids,
    )
    trace_log = CrossCellTraceLog(event_callback=event_callback)
    execution = await execute_project_execution_organism(
        executor=executor,
        organism=organism,
        task=effective_task,
        trace_log=trace_log,
    )
    return _build_report(execution)


async def run_deep_research_organ_live(
    workdir: Path,
    *,
    llm_provider: LLMProvider,
    task: ProjectExecutionTask | None = None,
    model: str,
    organism_id: str = "reference-project-execution",
    tool_ids: list[str] | None = None,
    workspace_root: str | Path | None = None,
    max_tool_rounds: int | None = 8,
    max_tool_calls: int = 24,
    max_runtime_seconds: int | None = None,
    research_reader_count: int | None = None,
    research_reader_briefs: list[str] | tuple[str, ...] | None = None,
    event_callback=None,
    trace_id: str | None = None,
) -> DeepResearchOrganDemoReport:
    """Run only the bounded deep-research organ against a live provider and local tools."""

    effective_task = task or build_reference_organism_demo_task(workdir)
    tool_runtime = LocalOrganismToolRuntime(
        tool_ids=tool_ids or DEFAULT_LIVE_ORGANISM_TOOL_IDS,
        workspace_root=workspace_root,
        event_callback=event_callback,
    )
    completion_provider = ToolLoopCompletionProvider(
        provider=llm_provider,
        tool_runtime=tool_runtime,
        default_model=model,
        max_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
        event_callback=event_callback,
    )
    executor = WorkerCoreExecutor(completion_provider=completion_provider)
    organism = attach_local_tooling_to_reference_organism(
        _reference_organism_for_task(
            task=effective_task,
            organism_id=organism_id,
            model=model,
            research_reader_count=research_reader_count,
            research_reader_briefs=research_reader_briefs,
        ),
        tool_ids=tool_runtime.tool_ids,
    )
    trace_log = CrossCellTraceLog(event_callback=event_callback)
    packet = _deep_research_packet(
        organism_id=organism_id,
        organ_id=organism.research_organ.organ_id,
        task=effective_task,
        max_runtime_seconds=max_runtime_seconds,
        trace_id=trace_id,
    )
    execution = await execute_organ_pattern(
        executor=executor,
        pattern=organism.research_organ,
        packet=packet,
        trace_log=trace_log,
    )
    return _build_research_report(execution, trace_log=trace_log)


__all__ = [
    "DEFAULT_LIVE_ORGANISM_TOOL_IDS",
    "MAX_DEEP_RESEARCH_READERS",
    "DEFAULT_REFERENCE_ACCEPTANCE_CRITERIA",
    "DEFAULT_REFERENCE_EVIDENCE_SUMMARIES",
    "DEFAULT_REFERENCE_HARD_CONSTRAINTS",
    "DeepResearchOrganDemoReport",
    "DEFAULT_REFERENCE_OBJECTIVE",
    "DEFAULT_REFERENCE_SOFT_CONSTRAINTS",
    "DEFAULT_REFERENCE_VALIDATION_COMMANDS",
    "available_local_organism_tools",
    "ReferenceOrganismDemoReport",
    "build_reference_organism_demo_task",
    "run_deep_research_organ_demo",
    "run_deep_research_organ_live",
    "run_reference_organism_demo",
    "run_reference_organism_live",
]
