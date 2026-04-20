from __future__ import annotations

from collections import defaultdict

import pytest

from dan.server.control_plane import (
    classify_operator_profile,
    resolve_operator_execution_boundary,
)

from tests.eval.operator_plane_benchmark_matrix import _benchmark_cases


def test_operator_plane_benchmark_matrix_covers_frozen_family_set() -> None:
    cases = _benchmark_cases()
    assert {case.family for case in cases} == {
        "ask_research",
        "local_mutate",
        "browser_download",
        "desktop_messaging",
        "cross_surface_operator",
    }
    assert len([case for case in cases if case.cross_surface_chain]) == 1


@pytest.mark.parametrize(
    "case",
    _benchmark_cases(),
    ids=lambda case: case.benchmark_id,
)
def test_operator_plane_benchmark_case_matches_runtime_contract(case) -> None:
    profile = classify_operator_profile(
        message=case.prompt,
        requested_mode=case.mode,
    )
    boundary = resolve_operator_execution_boundary(
        profile=profile,
        available_tool_families=list(case.available_tool_families),
        available_adapters=list(case.available_adapters),
    )

    assert profile.use_case_pack == case.expected_operator_use_case_pack
    assert profile.safety_envelope == case.expected_safety_envelope
    assert profile.supervision_policy == case.expected_supervision_policy
    assert boundary.execution_target == case.expected_execution_target
    assert boundary.deterministic_capability_sets == case.expected_capability_sets
    assert boundary.deterministic_adapters == case.expected_deterministic_adapters
    assert boundary.shared_control_membrane == "supervisor_brief_worker_report_review_v1"
    assert boundary.non_goals == (
        "No raw unrestricted AppleScript surface.",
        "No unsandboxed system-administration autonomy.",
        "No silent outbound messaging.",
        "No pseudo-motivational filler instead of concrete direction.",
    )


def test_operator_plane_benchmark_matrix_requires_multi_loop_scoring_per_family() -> None:
    cases_by_family = defaultdict(list)
    for case in _benchmark_cases():
        cases_by_family[case.family].append(case)

    required_dimensions = {
        "uncertainty_narrowing",
        "delta_quality",
        "busywork_avoidance",
    }
    for family, cases in cases_by_family.items():
        qualifying = [
            case for case in cases
            if case.minimum_supervision_loops >= 2
            and required_dimensions <= set(case.scoring_dimensions)
        ]
        assert qualifying, f"{family} is missing a 2+ loop benchmark with the required scoring dimensions"


def test_cross_surface_operator_benchmark_is_frozen_as_fixture_not_route() -> None:
    cross_surface_cases = [case for case in _benchmark_cases() if case.cross_surface_chain]
    assert [case.fixture_style for case in cross_surface_cases] == ["family_fixture"]
