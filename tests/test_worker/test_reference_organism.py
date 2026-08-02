from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from dan.worker import (
    CrossCellTraceLog,
    ProjectExecutionTask,
    WorkerCoreExecutor,
    execute_project_execution_organism,
    project_execution_reference_organism,
    recommended_deep_research_reader_count,
    run_deep_research_organ_demo,
)
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse

_SPEC = importlib.util.spec_from_file_location(
    "reference_organism_acceptance",
    Path(__file__).resolve().parents[1] / "eval" / "reference_organism_acceptance.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
run_reference_organism_acceptance = _MODULE.run_reference_organism_acceptance


def test_reference_project_execution_preset_keeps_biology_stack_explicit() -> None:
    organism = project_execution_reference_organism(model="stub-model")

    assert organism.planner_worker.role == "planner_brain"
    assert organism.research_organ.tissue is not None
    assert organism.validator_organ.tissue is not None
    assert organism.coding_organ.tissue is not None
    assert organism.synthesis_organ.tissue is not None
    assert organism.research_organ.kind.value == "deep_research"
    assert organism.validator_organ.kind.value == "universal_validator"
    assert organism.coding_organ.kind.value == "coding_build"
    assert organism.synthesis_organ.kind.value == "synthesis"
    assert "later_builder_promotion" in organism.metadata


def test_reference_project_execution_allows_research_reader_override() -> None:
    organism = project_execution_reference_organism(
        model="stub-model",
        research_reader_count=7,
    )

    assert organism.metadata["research_reader_count"] == 7
    assert organism.research_organ.tissue is not None
    assert len(organism.research_organ.tissue.members) == 7
    assert organism.research_organ.tissue.limits.max_concurrency == 7


@pytest.mark.asyncio
async def test_reference_project_execution_runtime_closes_a_bounded_repair_loop(tmp_path) -> None:
    report = await run_reference_organism_acceptance(tmp_path)

    assert report.status == "completed"
    assert report.selected_attempt == 2
    assert report.improved_via_repair is True
    assert report.final_output["accountability"]["validator"] == "universal-validator.lead"


class _PlannerThresholdProvider:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def _count(self, worker_id: str) -> int:
        current = self._counts.get(worker_id, 0) + 1
        self._counts[worker_id] = current
        return current

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        worker_id = str(request.metadata.get("worker_id") or "")
        self._count(worker_id)

        if worker_id.endswith(".planner"):
            return CompletionResponse(
                text=json.dumps(
                    {
                        "research_instruction": "Ground the task.",
                        "build_instruction": "Build one bounded candidate.",
                        "validator_focus": "Score the candidate.",
                        "synthesis_focus": "Summarize the final result.",
                        "pass_threshold": 0.5,
                    },
                    sort_keys=True,
                )
            )
        if worker_id.startswith("deep-research.reader-"):
            return CompletionResponse(
                text=json.dumps(
                    {
                        "findings": ["Grounded research note."],
                        "evidence_refs": ["brief:issue"],
                        "contradictions": [],
                        "open_questions": [],
                        "reasoning_notes": [],
                        "follow_up_queries": [],
                    },
                    sort_keys=True,
                )
            )
        if worker_id == "deep-research.lead":
            return CompletionResponse(
                text=json.dumps(
                    {
                        "findings": ["Grounded finding."],
                        "evidence_summary": ["brief:issue"],
                        "evidence_refs": ["brief:issue"],
                        "contradictions": [],
                        "open_questions": [],
                        "verification_facts": [
                            {
                                "fact": "Grounded finding.",
                                "status": "verified",
                                "source": "brief:issue",
                                "as_of": "",
                                "note": "Supported by the supplied issue brief.",
                            }
                        ],
                        "audit_issues": [],
                        "quality_gates": [
                            {
                                "gate": "time_anchor",
                                "status": "pass",
                                "summary": "The deterministic test is grounded in supplied static evidence.",
                                "evidence_ref": "brief:issue",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "scope_boundary",
                                "status": "pass",
                                "summary": "The scope is limited to the supplied repo-change brief.",
                                "evidence_ref": "brief:issue",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "source_authority",
                                "status": "pass",
                                "summary": "The supplied issue brief is the authoritative test fixture.",
                                "evidence_ref": "brief:issue",
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
                                "summary": "The recommendation maps to the supplied validation gap.",
                                "evidence_ref": "brief:issue",
                                "required_follow_up": "",
                            },
                            {
                                "gate": "final_status",
                                "status": "pass",
                                "summary": "Grounded bounded report.",
                                "evidence_ref": "brief:issue",
                                "required_follow_up": "",
                            },
                        ],
                        "report_readiness": "grounded",
                        "readiness_note": "The supplied evidence is internally consistent and sufficiently grounded.",
                        "confidence": 0.8,
                        "recommended_change": "Keep the patch minimal.",
                    },
                    sort_keys=True,
                )
            )
        if worker_id in {"coding-build.builder-a", "coding-build.builder-b"}:
            return CompletionResponse(text="Candidate note.")
        if worker_id == "coding-build.lead":
            return CompletionResponse(
                text=json.dumps(
                    {
                        "candidate_id": "candidate-1",
                        "change_summary": "Patch the validator path.",
                        "target_files": ["src/dan/worker/organisms/project_execution.py"],
                        "test_plan": ["PYTHONPATH=src pytest -q tests/test_worker/test_reference_organism.py"],
                        "risks": [],
                    },
                    sort_keys=True,
                )
            )
        if worker_id in {"universal-validator.reviewer-a", "universal-validator.reviewer-b"}:
            return CompletionResponse(text="pass")
        if worker_id == "universal-validator.lead":
            return CompletionResponse(
                text=json.dumps(
                    {
                        "passed": True,
                        "overall_score": 0.6,
                        "dimension_scores": {"grounding": 0.6},
                        "repair_brief": "",
                        "missing_requirements": [],
                        "comparison_note": "Candidate clears the planner-selected bar.",
                    },
                    sort_keys=True,
                )
            )
        if worker_id in {"synthesis.reporter-a", "synthesis.reporter-b"}:
            return CompletionResponse(text="Synthesis note.")
        if worker_id == "synthesis.lead":
            return CompletionResponse(
                text=json.dumps(
                    {
                        "delivery_summary": "Threshold-respecting summary.",
                        "final_candidate": {"candidate_id": "candidate-1"},
                        "validation_summary": {"overall_score": 0.6, "passed": True},
                        "accountability": {"validator": "universal-validator.lead"},
                    },
                    sort_keys=True,
                )
            )
        raise AssertionError(f"Unexpected worker_id: {worker_id}")


@pytest.mark.asyncio
async def test_reference_project_execution_uses_planner_selected_pass_threshold(tmp_path) -> None:
    provider = _PlannerThresholdProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    trace_log = CrossCellTraceLog()
    task = ProjectExecutionTask(
        task_id="planner-threshold",
        objective="Ground and summarize one bounded candidate.",
        acceptance_criteria=["Return a bounded research-backed summary."],
        delivery_target="repo-change brief",
    )
    organism = project_execution_reference_organism(
        model="stub-model",
        research_reader_count=recommended_deep_research_reader_count(task),
    )

    execution = await execute_project_execution_organism(
        executor=executor,
        organism=organism,
        task=task,
        trace_log=trace_log,
    )

    assert execution.result is not None
    assert execution.result.status == "completed"
    assert execution.result.selected_attempt == 1
    assert execution.result.validation_scores == pytest.approx([0.6])
    assert execution.result.metadata["selected_pass_threshold"] == pytest.approx(0.5)


def test_recommended_deep_research_reader_count_caps_at_eight() -> None:
    task = ProjectExecutionTask(
        task_id="reader-cap",
        objective="Ground a broad request.",
        acceptance_criteria=[f"criterion-{index}" for index in range(6)],
        focused_validation_commands=["pytest -q", "mypy ."],
        hard_constraints=["bounded", "inspectable"],
        soft_constraints=["prefer minimal patch"],
        evidence_refs=[],
    )

    assert recommended_deep_research_reader_count(task) == 8


@pytest.mark.asyncio
async def test_standalone_deep_research_demo_reports_selected_reader_briefs(tmp_path) -> None:
    report = await run_deep_research_organ_demo(
        tmp_path,
        research_reader_count=4,
    )

    assert report.selected_reader_count == 4
    assert len(report.selected_reader_briefs) == 4
    assert all(report.selected_reader_briefs)
