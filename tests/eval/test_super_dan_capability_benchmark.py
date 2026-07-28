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
        "human_assist_indie_game",
        "human_assist_academic_draft",
        "human_assist_market_strategy",
        "flagship_product_site",
        "flagship_browser_rts",
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
    flagship_cases = [case for case in cases if case.family.startswith("flagship_")]
    assert len(flagship_cases) == 2
    assert all(
        "artifact_acceptance" in case.scoring_dimensions for case in flagship_cases
    )
    assert all(
        "steering_delivery" in case.scoring_dimensions for case in flagship_cases
    )
    human_assist_cases = [
        case for case in cases if case.family.startswith("human_assist_")
    ]
    assert len(human_assist_cases) == 3
    assert all(case.requires_validation for case in human_assist_cases)
    assert all(
        "human-review.md" in case.required_outputs for case in human_assist_cases
    )


def test_super_dan_capability_scoring_rewards_delivery_and_penalizes_budget_waste() -> (
    None
):
    case = next(
        case
        for case in benchmark._benchmark_cases()
        if case.case_id == "medium-source-repair"
    )
    good = benchmark.SuperDanCapabilityObservation(
        case_id=case.case_id,
        status="completed",
        wall_time_seconds=180,
        token_usage={
            "prompt_tokens": 9_000,
            "completion_tokens": 4_000,
            "total_tokens": 13_000,
        },
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
        token_usage={
            "prompt_tokens": 300_000,
            "completion_tokens": 40_000,
            "total_tokens": 340_000,
        },
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


def test_super_dan_capability_scoring_uses_latest_test_command_for_validation() -> None:
    case = next(
        case
        for case in benchmark._benchmark_cases()
        if case.case_id == "medium-source-repair"
    )
    observation = benchmark.SuperDanCapabilityObservation(
        case_id=case.case_id,
        status="completed",
        wall_time_seconds=120,
        token_usage={"total_tokens": 20_000},
        validation_passed=True,
        artifacts_changed=("src/fix.py", "tests/test_fix.py"),
        tests_passed=1,
        tests_failed=1,
        latest_test_exit_code=0,
        event_count=20,
        tool_call_count=8,
        output_quality_score=0.91,
        answer_present=True,
    )

    score = benchmark.score_observed_run(case, observation)

    assert score.passed is True
    assert score.validation_evidence == 1.0


def test_super_dan_capability_scoring_requires_token_accounting_and_validation() -> (
    None
):
    case = next(
        case
        for case in benchmark._benchmark_cases()
        if case.case_id == "short-validation-truth"
    )
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


def test_super_dan_capability_scoring_requires_declared_output_paths() -> None:
    case = next(
        case
        for case in benchmark._benchmark_cases()
        if case.case_id == "long-market-strategy-framework"
    )
    observation = benchmark.SuperDanCapabilityObservation(
        case_id=case.case_id,
        status="completed",
        wall_time_seconds=120,
        token_usage={"total_tokens": 20_000},
        validation_passed=True,
        artifacts_changed=("a", "b", "c", "d", "e"),
        tests_passed=1,
        latest_test_exit_code=0,
        event_count=20,
        tool_call_count=8,
        output_quality_score=0.91,
        answer_present=True,
        domain_acceptance_passed=True,
    )

    score = benchmark.score_observed_run(case, observation)

    assert score.passed is False
    assert any(
        reason.startswith("missing_required_outputs:") for reason in score.reasons
    )
    assert score.delivered_performance < case.min_delivery_score


def test_super_dan_capability_scoring_requires_domain_acceptance() -> None:
    case = next(
        case
        for case in benchmark._benchmark_cases()
        if case.case_id == "long-market-strategy-framework"
    )
    observation = benchmark.SuperDanCapabilityObservation(
        case_id=case.case_id,
        status="completed",
        wall_time_seconds=120,
        token_usage={"total_tokens": 20_000},
        validation_passed=True,
        artifacts_changed=case.required_outputs,
        tests_passed=1,
        latest_test_exit_code=0,
        event_count=20,
        tool_call_count=8,
        output_quality_score=0.91,
        answer_present=True,
    )

    score = benchmark.score_observed_run(case, observation)

    assert score.passed is False
    assert "missing_domain_acceptance" in score.reasons
    assert "missing_fresh_validation" in score.reasons


def test_super_dan_event_log_reader_extracts_usage_time_delivery_and_validation(
    tmp_path,
) -> None:
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
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    observation = benchmark.load_event_log_observation(
        event_log, case_id="medium-source-repair"
    )

    assert observation.status == "completed"
    assert observation.wall_time_seconds == 15
    assert observation.token_usage["total_tokens"] == 150
    assert observation.validation_passed is True
    assert observation.tests_passed == 1
    assert observation.latest_test_exit_code == 0
    assert observation.tool_call_count == 3
    assert observation.artifacts_changed == ("src/app.py", "tests/test_app.py")
    assert observation.output_quality_score == 0.88


def test_super_dan_event_log_reader_scores_exported_chat_v2_agent_events(
    tmp_path,
) -> None:
    event_log = tmp_path / "agent-events.jsonl"
    rows = [
        {
            "type": "worker_started",
            "source_event_type": "run.log.started",
            "payload": {"timestamp": "2026-07-24T01:00:00+00:00"},
        },
        {
            "type": "artifact_changed",
            "source_event_type": "tool.completed",
            "artifact_refs": [{"path": "index.html"}],
            "payload": {
                "timestamp": "2026-07-24T01:00:05+00:00",
                "event": "tool.completed",
                "tool_id": "file_write",
                "result": {"path": "index.html"},
            },
        },
        {
            "type": "token_usage_recorded",
            "source_event_type": "model.responded",
            "token_usage_delta": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
            "token_usage_total": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
            "payload": {"timestamp": "2026-07-24T01:00:08+00:00"},
        },
        {
            "type": "completed",
            "source_event_type": "run.log.completed",
            "summary": "Site completed and validated.",
            "payload": {
                "timestamp": "2026-07-24T01:00:15+00:00",
                "validation_passed": True,
                "overall_score": 0.91,
                "tool_calls": 4,
            },
        },
    ]
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    observation = benchmark.load_event_log_observation(
        event_log,
        case_id="flagship-premium-site",
    )

    assert observation.status == "completed"
    assert observation.wall_time_seconds == 15
    assert observation.token_usage["total_tokens"] == 120
    assert observation.validation_passed is True
    assert observation.artifacts_changed == ("index.html",)
    assert observation.tool_call_count == 1
    assert observation.output_quality_score == 0.91
    assert observation.answer_present is True


def test_agent_event_token_round_ids_keep_identical_usage_deltas_distinct(
    tmp_path,
) -> None:
    event_log = tmp_path / "agent-token-events.jsonl"
    rows = [
        {
            "type": "token_usage_recorded",
            "source_event_type": "model.responded",
            "token_usage_delta": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
            },
            "token_usage_round": {
                "round": 1,
                "model_call_id": "model-call-1",
            },
        },
        {
            "type": "token_usage_recorded",
            "source_event_type": "model.responded",
            "token_usage_delta": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
            },
            "token_usage_round": {
                "round": 2,
                "model_call_id": "model-call-2",
            },
        },
        {"type": "completed", "source_event_type": "run.log.completed"},
    ]
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    observation = benchmark.load_event_log_observation(
        event_log,
        case_id="flagship-premium-site",
    )

    assert observation.token_usage == {
        "prompt_tokens": 200,
        "completion_tokens": 20,
        "total_tokens": 220,
    }


def test_super_dan_event_log_reader_handles_live_tool_artifacts_and_usage_totals(
    tmp_path,
) -> None:
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
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    observation = benchmark.load_event_log_observation(
        event_log, case_id="short-note-create"
    )

    assert observation.token_usage["total_tokens"] == 305
    assert observation.artifacts_changed == ("note.md",)
    assert observation.validation_passed is True
    assert observation.output_quality_score == 0.88


def test_super_dan_event_log_reader_does_not_treat_tool_completion_as_run_completion(
    tmp_path,
) -> None:
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
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    observation = benchmark.load_event_log_observation(
        event_log, case_id="short-validation-truth"
    )

    assert observation.status == "incomplete"
    assert observation.tests_passed == 1
    assert observation.latest_test_exit_code == 0
    assert observation.wall_time_seconds == 70


def test_super_dan_event_log_reader_keeps_historical_failures_but_latest_test_passes(
    tmp_path,
) -> None:
    event_log = tmp_path / "events.jsonl"
    rows = [
        {
            "event": "tool.completed",
            "tool_id": "shell_command",
            "arguments": {"command": "python -m pytest -q"},
            "result": {"exit_code": 1, "stdout": "1 failed, 3 passed\n"},
        },
        {
            "event": "tool.completed",
            "tool_id": "file_edit",
            "result": {"path": "stats_tools.py"},
        },
        {
            "event": "tool.completed",
            "tool_id": "shell_command",
            "arguments": {"command": "python -m pytest -q"},
            "result": {"exit_code": 0, "stdout": "4 passed\n"},
        },
        {
            "event": "run.log.completed",
            "status": "completed",
            "validation_passed": True,
        },
    ]
    event_log.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    observation = benchmark.load_event_log_observation(
        event_log, case_id="medium-source-repair"
    )

    assert observation.tests_failed == 1
    assert observation.tests_passed == 1
    assert observation.latest_test_exit_code == 0


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
