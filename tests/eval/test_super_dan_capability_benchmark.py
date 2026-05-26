from __future__ import annotations

import json

from tests.eval import super_dan_capability_benchmark as benchmark


def test_super_dan_capability_matrix_covers_lengths_families_and_metrics() -> None:
    cases = benchmark._benchmark_cases()

    assert {case.task_length for case in cases} == {"short", "medium", "long"}
    assert {case.family for case in cases} == {
        "workspace_artifact",
        "validation_gate",
        "website_patch",
        "source_repair",
        "research_report",
        "greenfield_project",
        "followup_adaptation",
        "cross_surface_operator",
    }
    assert any("browser_control" in case.capability_packs for case in cases)
    assert any(case.requires_validation for case in cases)
    assert all(case.target_total_tokens > 0 for case in cases)
    assert all(case.target_wall_time_seconds > 0 for case in cases)
    assert all(
        {
            "delivered_performance",
            "token_efficiency",
            "time_efficiency",
            "validation_evidence",
        }
        <= set(case.scoring_dimensions)
        for case in cases
    )


def test_super_dan_capability_scoring_rewards_delivery_and_penalizes_budget_waste() -> None:
    case = next(case for case in benchmark._benchmark_cases() if case.case_id == "medium-source-repair")
    good = benchmark.SuperDanCapabilityObservation(
        case_id=case.case_id,
        status="completed",
        wall_time_seconds=180,
        token_usage={"prompt_tokens": 9_000, "completion_tokens": 4_000, "total_tokens": 13_000},
        validation_passed=True,
        artifacts_changed=("src/fix.py", "tests/test_fix.py"),
        tests_passed=1,
        event_count=20,
        tool_call_count=8,
        output_quality_score=0.91,
        answer_present=True,
    )
    wasteful = benchmark.SuperDanCapabilityObservation(
        case_id=case.case_id,
        status="completed",
        wall_time_seconds=1_800,
        token_usage={"prompt_tokens": 300_000, "completion_tokens": 40_000, "total_tokens": 340_000},
        validation_passed=True,
        artifacts_changed=("src/fix.py",),
        tests_passed=1,
        event_count=20,
        tool_call_count=8,
        output_quality_score=0.82,
        answer_present=True,
    )

    good_score = benchmark.score_observed_run(case, good)
    wasteful_score = benchmark.score_observed_run(case, wasteful)

    assert good_score.passed is True
    assert good_score.token_efficiency == 1.0
    assert good_score.time_efficiency == 1.0
    assert wasteful_score.overall_score < good_score.overall_score
    assert wasteful_score.token_efficiency < 0.2
    assert wasteful_score.time_efficiency < 0.3


def test_super_dan_capability_scoring_requires_token_accounting_and_validation() -> None:
    case = next(case for case in benchmark._benchmark_cases() if case.case_id == "short-validation-truth")
    missing_metrics = benchmark.SuperDanCapabilityObservation(
        case_id=case.case_id,
        status="completed",
        wall_time_seconds=12,
        token_usage={},
        validation_passed=False,
        artifacts_changed=(),
        event_count=2,
        answer_present=True,
    )

    score = benchmark.score_observed_run(case, missing_metrics)

    assert score.passed is False
    assert "missing_token_usage" in score.reasons
    assert "missing_fresh_validation" in score.reasons


def test_super_dan_event_log_reader_extracts_usage_time_delivery_and_validation(tmp_path) -> None:
    event_log = tmp_path / "events.jsonl"
    rows = [
        {
            "event": "run.log.started",
            "timestamp": "2026-05-26T01:00:00+00:00",
            "status": "running",
        },
        {
            "event": "model.responded",
            "timestamp": "2026-05-26T01:00:05+00:00",
            "token_usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
            },
        },
        {
            "event": "tool.completed",
            "timestamp": "2026-05-26T01:00:08+00:00",
            "result": {"workspace_changes": {"changed_paths": ["src/app.py"]}},
        },
        {
            "event": "live.validation.shell_check.completed",
            "timestamp": "2026-05-26T01:00:12+00:00",
            "exit_code": 0,
        },
        {
            "event": "run.log.completed",
            "timestamp": "2026-05-26T01:00:15+00:00",
            "status": "completed",
            "validation_passed": True,
            "tool_calls": 3,
            "target_files": ["tests/test_app.py"],
            "overall_score": 0.88,
        },
    ]
    event_log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    observation = benchmark.load_event_log_observation(event_log, case_id="medium-source-repair")

    assert observation.status == "completed"
    assert observation.wall_time_seconds == 15
    assert observation.token_usage["total_tokens"] == 150
    assert observation.validation_passed is True
    assert observation.tests_passed == 1
    assert observation.tool_call_count == 3
    assert observation.artifacts_changed == ("src/app.py", "tests/test_app.py")
    assert observation.output_quality_score == 0.88


def test_super_dan_event_log_reader_handles_live_tool_artifacts_and_usage_totals(tmp_path) -> None:
    event_log = tmp_path / "events.jsonl"
    rows = [
        {
            "event": "model.responded",
            "span_kind": "model_call",
            "model_call_id": "model-call:1",
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
            },
            "usage_totals": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
            },
        },
        {
            "event": "model.responded",
            "span_kind": "model_call",
            "model_call_id": "model-call:2",
            "worker_id": "builder",
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 20,
                "total_tokens": 140,
            },
            "usage_totals": {
                "prompt_tokens": 220,
                "completion_tokens": 30,
                "total_tokens": 250,
            },
        },
        {
            "event": "model.responded",
            "span_kind": "model_call",
            "model_call_id": "model-call:2",
            "worker_id": "validator",
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "total_tokens": 55,
            },
            "usage_totals": {
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "total_tokens": 55,
            },
        },
        {
            "event": "tool.completed",
            "tool_id": "file_write",
            "result": {"bytes_written": 42, "path": "note.md"},
        },
        {
            "event": "live.validation.completed",
            "changed_required_files": ["note.md"],
            "passed": True,
            "overall_score": 0.88,
        },
        {"event": "run.log.completed", "status": "completed"},
    ]
    event_log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    observation = benchmark.load_event_log_observation(event_log, case_id="short-note-create")

    assert observation.token_usage["total_tokens"] == 305
    assert observation.artifacts_changed == ("note.md",)
    assert observation.validation_passed is True
    assert observation.output_quality_score == 0.88


def test_super_dan_event_log_reader_does_not_treat_tool_completion_as_run_completion(tmp_path) -> None:
    event_log = tmp_path / "events.jsonl"
    rows = [
        {"event": "run.log.started", "timestamp": "2026-05-26T01:00:00+00:00"},
        {
            "event": "tool.completed",
            "tool_id": "shell_command",
            "timestamp": "2026-05-26T01:00:02+00:00",
            "status": "completed",
            "arguments": {"command": "python -m pytest -q"},
            "result": {"exit_code": 0, "stdout": "1 passed\n"},
        },
        {
            "event": "super.heartbeat",
            "timestamp": "2026-05-26T01:01:10+00:00",
            "elapsed_seconds": 70,
        },
    ]
    event_log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    observation = benchmark.load_event_log_observation(event_log, case_id="short-validation-truth")

    assert observation.status == "incomplete"
    assert observation.tests_passed == 1
    assert observation.wall_time_seconds == 70


def test_super_dan_capability_summary_groups_lengths_and_surfaces_outliers() -> None:
    cases = benchmark._benchmark_cases()
    observations = [
        benchmark.SuperDanCapabilityObservation(
            case_id="short-note-create",
            status="completed",
            wall_time_seconds=20,
            token_usage={"total_tokens": 1_000},
            artifacts_changed=("note.md",),
            event_count=4,
            tool_call_count=1,
            answer_present=True,
        ),
        benchmark.SuperDanCapabilityObservation(
            case_id="long-greenfield-project",
            status="failed",
            wall_time_seconds=2_500,
            token_usage={"total_tokens": 190_000},
            validation_passed=False,
            artifacts_changed=("README.md",),
            event_count=40,
            tool_call_count=12,
            blockers=("missing tests",),
        ),
    ]

    summary = benchmark.summarize_observations(cases, observations)

    assert summary["total"] == 2
    assert summary["passed"] == 1
    assert summary["failed"] == 1
    assert summary["by_task_length"]["short"]["passed"] == 1
    assert summary["by_task_length"]["long"]["failed"] == 1
    assert summary["top_token_consumers"][0]["case_id"] == "long-greenfield-project"
    assert summary["top_slowest"][0]["case_id"] == "long-greenfield-project"
