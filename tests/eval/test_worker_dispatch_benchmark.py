from __future__ import annotations

import pytest

from tests.eval import worker_dispatch_benchmark as benchmark


def test_benchmark_cases_include_composition_surfaces() -> None:
    case_ids = {case.case_id for case in benchmark._benchmark_cases()}

    assert "code_only" in case_ids
    assert "tool_only" in case_ids
    assert "llm_only" in case_ids
    assert "code_tool_llm_chain" in case_ids
    assert "composite_body_graph_mapping" in case_ids
    assert "subworker_last_write_wins" in case_ids


@pytest.mark.asyncio
async def test_manual_worker_graph_cases_run_and_match_legacy_outputs() -> None:
    for case_factory in (
        benchmark._composite_mapping_case,
        benchmark._subworker_merge_case,
    ):
        case = case_factory()
        result = await benchmark._run_case(
            case,
            warmup=1,
            repeats=2,
            max_overhead_ratio=100.0,
        )

        assert result["case_id"] == case.case_id
        assert result["conversion_validation_errors"] == []
        assert result["output"]
        assert result["legacy"]["median_ms"] >= 0
        assert result["worker"]["median_ms"] >= 0


@pytest.mark.asyncio
async def test_benchmark_reports_interleaved_measurement_strategy() -> None:
    report = await benchmark._run_benchmark(
        warmup=1,
        repeats=2,
        max_overhead_ratio=100.0,
    )

    assert report["measurement_strategy"] == "interleaved_pairwise"
    assert report["cases"]
