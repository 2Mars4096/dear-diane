from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from dan.worker import (
    CellAddress,
    CellAuthorityLimits,
    CellBudgetLimits,
    CellHandoffPacket,
    CrossCellTraceLog,
    EvidenceRef,
    HandoffTask,
    OutputContract,
    SignalTrace,
    WorkerCoreExecutor,
    deep_research_organ,
    execute_organ_pattern,
    universal_validator_organ,
)

_SPEC = importlib.util.spec_from_file_location(
    "reference_organism_acceptance",
    Path(__file__).with_name("reference_organism_acceptance.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_ReferenceOrganismCompletionProvider = _MODULE._ReferenceOrganismCompletionProvider


def _research_packet() -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=SignalTrace(trace_id="trace:bounded-organ", root_task_id="bounded-organ"),
        sender=CellAddress(cell_id="planner", organism_id="reference-project-execution"),
        recipient=CellAddress(
            cell_id="deep-research.boundary",
            organ_id="deep-research",
            organism_id="reference-project-execution",
        ),
        task=HandoffTask(
            task_id="research-organ",
            instruction="Ground the bounded repo-change request.",
            scope="project-execution.research",
            input_payload={
                "objective": "Ground the fix in the supplied evidence.",
                "acceptance_criteria": ["Name the focused validation command."],
                "delivery_target": "repo-change brief",
            },
        ),
        evidence_refs=[
            EvidenceRef(
                ref_id="brief:issue",
                label="Issue brief",
                summary="The fix must keep the focused validation command explicit.",
            )
        ],
        output_contract=OutputContract(
            definition_of_done="Return one grounded research report.",
            expected_return_shape='{"findings": "..."}',
        ),
        budget_limits=CellBudgetLimits(max_completion_rounds=1),
        authority_limits=CellAuthorityLimits(max_spawned_cells=0),
    )


def _validator_packet() -> CellHandoffPacket:
    return CellHandoffPacket(
        trace=SignalTrace(trace_id="trace:bounded-organ-validator", root_task_id="bounded-organ-validator"),
        sender=CellAddress(cell_id="planner", organism_id="reference-project-execution"),
        recipient=CellAddress(
            cell_id="universal-validator.boundary",
            organ_id="universal-validator",
            organism_id="reference-project-execution",
        ),
        task=HandoffTask(
            task_id="validator-organ",
            instruction="Score the candidate and emit a repair brief when needed.",
            scope="project-execution.validate",
            input_payload={
                "candidate": {
                    "candidate_id": "candidate-1",
                    "change_summary": "Patch the validator path only.",
                    "target_files": ["src/dan/worker/organisms/project_execution.py"],
                    "test_plan": [],
                    "risks": ["Missing focused validation command."],
                },
                "acceptance_criteria": ["Include the focused validation command."],
                "quality_bar": 0.9,
                "comparison_context": {"attempt": 1},
            },
        ),
        evidence_refs=[
            EvidenceRef(
                ref_id="brief:test-gap",
                label="Validation gap note",
                summary="Validator should request repair when the focused test command is absent.",
            )
        ],
        output_contract=OutputContract(
            definition_of_done="Return one validator score report.",
            expected_return_shape='{"passed": false}',
        ),
        budget_limits=CellBudgetLimits(max_completion_rounds=1),
        authority_limits=CellAuthorityLimits(max_spawned_cells=0),
    )


@pytest.mark.asyncio
async def test_deep_research_organ_keeps_tissue_and_lead_bounded(tmp_path) -> None:
    provider = _ReferenceOrganismCompletionProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    organ = deep_research_organ(
        organism_id="reference-project-execution",
        model="stub-model",
        reader_count=6,
    )
    trace_log = CrossCellTraceLog()

    execution = await execute_organ_pattern(
        executor=executor,
        pattern=organ,
        packet=_research_packet(),
        trace_log=trace_log,
    )

    assert execution.result.status == "completed"
    assert execution.tissue_execution is not None
    assert execution.tissue_execution.result.status == "completed"
    assert len(organ.tissue.members) == 6
    assert organ.tissue.limits.max_members == 6
    assert organ.tissue.limits.max_concurrency == 6
    assert organ.tissue.limits.max_failures == 2
    assert execution.lead_execution is not None
    assert sorted(execution.result.outputs) == [
        "audit_issues",
        "confidence",
        "contradictions",
        "evidence_refs",
        "evidence_summary",
        "findings",
        "open_questions",
        "quality_gates",
        "readiness_note",
        "recommended_change",
        "report_readiness",
        "verification_facts",
    ]
    assert len(execution.result.output_refs) == 1
    assert len(trace_log.inspect_trace("trace:bounded-organ")) >= 8
    reader_shapes = {
        str(request.metadata.get("worker_id") or ""): request.output_contract.expected_return_shape
        for request in provider.requests
        if str(request.metadata.get("worker_id") or "").startswith("deep-research.reader-")
    }
    assert reader_shapes
    assert all("follow_up_queries" in shape for shape in reader_shapes.values())
    assert all("report_readiness" not in shape for shape in reader_shapes.values())


@pytest.mark.asyncio
async def test_universal_validator_organ_emits_scores_without_internal_leakage(tmp_path) -> None:
    provider = _ReferenceOrganismCompletionProvider()
    executor = WorkerCoreExecutor(completion_provider=provider)
    organ = universal_validator_organ(organism_id="reference-project-execution", model="stub-model")
    trace_log = CrossCellTraceLog()

    execution = await execute_organ_pattern(
        executor=executor,
        pattern=organ,
        packet=_validator_packet(),
        trace_log=trace_log,
    )

    assert execution.result.status == "completed"
    assert sorted(execution.result.outputs) == [
        "comparison_note",
        "dimension_scores",
        "missing_requirements",
        "overall_score",
        "passed",
        "repair_brief",
    ]
    assert "reviews" not in execution.result.outputs
    assert execution.result.outputs["passed"] is False
    assert execution.result.outputs["overall_score"] == pytest.approx(0.74)
