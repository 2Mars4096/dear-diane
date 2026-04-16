from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from dan.worker import (
    CrossCellTraceLog,
    EvidenceRef,
    recommended_deep_research_reader_count,
    ProjectExecutionTask,
    WorkerCoreExecutor,
    execute_project_execution_organism,
    project_execution_reference_organism,
)
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse


class ReferenceOrganismAcceptanceReport(BaseModel):
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


ReferenceOrganismAcceptanceReport.model_rebuild()


class _ReferenceOrganismCompletionProvider:
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
                text="Grounded research note.",
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
                                "summary": "The deterministic acceptance run is grounded in supplied static evidence.",
                                "evidence_ref": "brief:issue",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "scope_boundary",
                                "status": "pass",
                                "summary": "The scope is limited to the supplied repo-change acceptance task.",
                                "evidence_ref": "brief:acceptance",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "source_authority",
                                "status": "pass",
                                "summary": "The supplied acceptance fixture is the source of truth.",
                                "evidence_ref": "brief:test-gap",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "numeric_reconciliation",
                                "status": "not_applicable",
                                "summary": "No numeric conflict is present.",
                                "evidence_ref": "",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "claim_object_fit",
                                "status": "pass",
                                "summary": "The recommendation maps to the explicit validation command gap.",
                                "evidence_ref": "brief:test-gap",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "final_status",
                                "status": "pass",
                                "summary": "Grounded bounded report.",
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


def _task(tmp_path: Path) -> ProjectExecutionTask:
    return ProjectExecutionTask(
        task_id="bounded-project-execution",
        objective="Repair the bounded project-execution candidate so validator-driven delivery requirements stay explicit.",
        acceptance_criteria=[
            "Ground the change in the supplied evidence.",
            "Return one bounded candidate with explicit target files.",
            "Include one focused validation command.",
            "End with an evidence-backed delivery summary and accountability.",
        ],
        delivery_target="repo-change brief",
        focused_validation_commands=[
            "PYTHONPATH=src pytest -q tests/eval/test_reference_organism_acceptance.py",
        ],
        hard_constraints=[
            "Stay bounded and inspectable.",
            "Do not collapse the biology stack into one oversized manager prompt.",
        ],
        soft_constraints=[
            "Prefer the smallest viable change that still closes the validator gap.",
        ],
        evidence_refs=[
            EvidenceRef(
                ref_id="brief:issue",
                label="Issue brief",
                summary="The first candidate often omits an explicit focused validation command.",
                source="acceptance-brief",
                locator=str(tmp_path / "issue.md"),
            ),
            EvidenceRef(
                ref_id="brief:acceptance",
                label="Acceptance criteria",
                summary="The final delivery must preserve missing-requirement reporting and accountability.",
                source="acceptance-brief",
                locator=str(tmp_path / "acceptance.md"),
            ),
            EvidenceRef(
                ref_id="brief:test-gap",
                label="Validation gap note",
                summary="Validator should request repair when the focused test command is absent.",
                source="acceptance-brief",
                locator=str(tmp_path / "validation-gap.md"),
            ),
        ],
    )


async def run_reference_organism_acceptance(tmp_path: Path) -> ReferenceOrganismAcceptanceReport:
    provider = _ReferenceOrganismCompletionProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    task = _task(tmp_path)
    organism = project_execution_reference_organism(
        model="stub-model",
        research_reader_count=recommended_deep_research_reader_count(task),
    )
    trace_log = CrossCellTraceLog()

    execution = await execute_project_execution_organism(
        executor=executor,
        organism=organism,
        task=task,
        trace_log=trace_log,
    )
    assert execution.result is not None

    validation_scores = list(execution.result.validation_scores)
    trace_rows = execution.result.observability.trace_rows
    return ReferenceOrganismAcceptanceReport(
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
            f'{record.stage}:{record.attempt or 0}'
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
