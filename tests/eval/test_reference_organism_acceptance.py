from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "reference_organism_acceptance",
    Path(__file__).with_name("reference_organism_acceptance.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
run_reference_organism_acceptance = _MODULE.run_reference_organism_acceptance


@pytest.mark.asyncio
async def test_reference_organism_improves_after_validator_repair(tmp_path) -> None:
    report = await run_reference_organism_acceptance(tmp_path)

    assert report.status == "completed"
    assert report.validation_attempts == 2
    assert report.initial_score is not None and report.final_score is not None
    assert report.final_score > report.initial_score
    assert report.improved_via_repair is True
    assert report.selected_attempt == 2
    assert report.build_candidate_ids == ["candidate-1", "candidate-2"]


@pytest.mark.asyncio
async def test_reference_organism_observability_keeps_handoffs_and_accountability_inspectable(tmp_path) -> None:
    report = await run_reference_organism_acceptance(tmp_path)

    assert report.handoff_count >= 12
    assert report.signal_count >= 12
    assert report.stage_sequence == [
        "planning:0",
        "research:0",
        "build:1",
        "validate:1",
        "build:2",
        "validate:2",
        "synthesis:0",
    ]
    assert report.final_output["final_candidate"]["candidate_id"] == "candidate-2"
    assert report.final_output["validation_summary"]["passed"] is True
