"""Local harnesses for the bounded project-execution organism."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dan.providers import LLMProvider
from dan.worker.composition import CrossCellTraceLog
from dan.worker.core.executor import WorkerCoreExecutor
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.organisms.local_runtime import (
    DEFAULT_LIVE_ORGANISM_TOOL_IDS,
    LocalOrganismToolRuntime,
    ToolLoopCompletionProvider,
    attach_local_tooling_to_reference_organism,
    available_local_organism_tools,
)
from dan.worker.organisms.project_execution import (
    ProjectExecutionTask,
    ProjectExecutionOrganismExecution,
    execute_project_execution_organism,
    project_execution_reference_organism,
)
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

        if worker_id == "deep-research.reader-a":
            return CompletionResponse(
                text="The defect is that the candidate fix must carry an explicit focused test command.",
                raw={"worker_id": worker_id},
            )
        if worker_id == "deep-research.reader-b":
            return CompletionResponse(
                text="The acceptance bar also requires the delivery summary to name missing requirements and residual risk.",
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
                        "open_questions": ["No blocking open questions remain after the supplied evidence."],
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
) -> ReferenceOrganismDemoReport:
    """Run the deterministic bounded project-execution organism demo."""

    provider = _ReferenceOrganismDemoCompletionProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    organism = project_execution_reference_organism(organism_id=organism_id, model=model)
    trace_log = CrossCellTraceLog()
    effective_task = task or build_reference_organism_demo_task(workdir)
    execution = await execute_project_execution_organism(
        executor=executor,
        organism=organism,
        task=effective_task,
        trace_log=trace_log,
    )
    return _build_report(execution)


async def run_reference_organism_live(
    workdir: Path,
    *,
    llm_provider: LLMProvider,
    task: ProjectExecutionTask | None = None,
    model: str,
    organism_id: str = "reference-project-execution",
    tool_ids: list[str] | None = None,
    workspace_root: str | Path | None = None,
    max_tool_rounds: int = 8,
    max_tool_calls: int = 24,
) -> ReferenceOrganismDemoReport:
    """Run the bounded reference organism against a live provider and local tools."""

    effective_task = task or build_reference_organism_demo_task(workdir)
    tool_runtime = LocalOrganismToolRuntime(
        tool_ids=tool_ids or DEFAULT_LIVE_ORGANISM_TOOL_IDS,
        workspace_root=workspace_root,
    )
    completion_provider = ToolLoopCompletionProvider(
        provider=llm_provider,
        tool_runtime=tool_runtime,
        default_model=model,
        max_rounds=max_tool_rounds,
        max_tool_calls=max_tool_calls,
    )
    executor = WorkerCoreExecutor(completion_provider=completion_provider)
    organism = attach_local_tooling_to_reference_organism(
        project_execution_reference_organism(organism_id=organism_id, model=model),
        tool_ids=tool_runtime.tool_ids,
    )
    trace_log = CrossCellTraceLog()
    execution = await execute_project_execution_organism(
        executor=executor,
        organism=organism,
        task=effective_task,
        trace_log=trace_log,
    )
    return _build_report(execution)


__all__ = [
    "DEFAULT_LIVE_ORGANISM_TOOL_IDS",
    "DEFAULT_REFERENCE_ACCEPTANCE_CRITERIA",
    "DEFAULT_REFERENCE_EVIDENCE_SUMMARIES",
    "DEFAULT_REFERENCE_HARD_CONSTRAINTS",
    "DEFAULT_REFERENCE_OBJECTIVE",
    "DEFAULT_REFERENCE_SOFT_CONSTRAINTS",
    "DEFAULT_REFERENCE_VALIDATION_COMMANDS",
    "available_local_organism_tools",
    "ReferenceOrganismDemoReport",
    "build_reference_organism_demo_task",
    "run_reference_organism_demo",
    "run_reference_organism_live",
]
