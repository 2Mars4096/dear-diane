from __future__ import annotations

from diane.worker.scheduler import analyze_scheduler_replay_rows


def test_analyze_scheduler_replay_rows_reports_lower_bounds_and_terminal_barrier() -> None:
    raw_rows = [
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:00Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "plan",
            "parallel_lane": "lane-a",
            "summary": "Plan",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:02Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "plan",
            "parallel_lane": "lane-a",
            "status": "completed",
            "summary": "Plan",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:02Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "build",
            "parallel_lane": "lane-a",
            "summary": "Build",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:06Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "build",
            "parallel_lane": "lane-a",
            "status": "completed",
            "summary": "Build",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:02Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "review",
            "parallel_lane": "lane-b",
            "summary": "Parallel review",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:05Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "review",
            "parallel_lane": "lane-b",
            "status": "completed",
            "summary": "Parallel review",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:06Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "aggregate",
            "parallel_lane": "lane-main",
            "summary": "Aggregate",
            "blocked_by": ["build", "review"],
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:07Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "aggregate",
            "parallel_lane": "lane-main",
            "status": "completed",
            "summary": "Aggregate",
            "blocked_by": ["build", "review"],
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:07Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "validate",
            "parallel_lane": "lane-main",
            "summary": "Validate",
            "blocked_by": ["aggregate"],
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:08Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "validate",
            "parallel_lane": "lane-main",
            "status": "completed",
            "summary": "Validate",
            "blocked_by": ["aggregate"],
        },
    ]

    analysis = analyze_scheduler_replay_rows(raw_rows, limit=3)

    assert analysis.capacity_hint == 2
    assert analysis.capacity_source == "observed_parallelism"
    assert analysis.observed_makespan_ms == 8000
    assert analysis.max_parallel_spans == 2
    assert analysis.total_exclusive_work_ms == 11000
    assert analysis.average_parallelism == 1.375
    assert analysis.critical_path_lower_bound_ms == 8000
    assert analysis.work_capacity_lower_bound_ms == 5500
    assert analysis.scheduler_lower_bound_ms == 8000
    assert analysis.slack_ms == 0
    assert analysis.terminal_barrier.span_ids == ["aggregate", "validate"]
    assert analysis.terminal_barrier.duration_ms == 2000
    assert analysis.top_bottlenecks[0].span_id == "build"
    assert analysis.top_bottlenecks[0].reason == "critical_path,fanout_blocker"


def test_analyze_scheduler_replay_rows_flags_lane_sequence_only_serialization() -> None:
    raw_rows = [
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:00Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "plan",
            "parallel_lane": "lane-a",
            "summary": "Plan",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:02Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "plan",
            "parallel_lane": "lane-a",
            "status": "completed",
            "summary": "Plan",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:02Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "review",
            "parallel_lane": "lane-a",
            "summary": "Review",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:04Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "review",
            "parallel_lane": "lane-a",
            "status": "completed",
            "summary": "Review",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:00Z",
            "event": "stage.started",
            "row_kind": "span_start",
            "span_kind": "control_stage",
            "span_id": "search",
            "parallel_lane": "lane-b",
            "summary": "Search",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:04Z",
            "event": "stage.completed",
            "row_kind": "span_end",
            "span_kind": "control_stage",
            "span_id": "search",
            "parallel_lane": "lane-b",
            "status": "completed",
            "summary": "Search",
        },
    ]

    analysis = analyze_scheduler_replay_rows(raw_rows, limit=5)

    assert analysis.capacity_hint == 2
    assert len(analysis.missed_parallelism) == 1
    opportunity = analysis.missed_parallelism[0]
    assert opportunity.source_span_id == "plan"
    assert opportunity.target_span_id == "review"
    assert opportunity.lane_id == "lane-a"
    assert opportunity.estimated_gain_upper_bound_ms == 2000
    assert opportunity.serial_gap_ms == 0
    assert opportunity.reason == "lane_sequence_only_serialization"


def test_analyze_scheduler_replay_rows_reports_readiness_unlock_timing() -> None:
    raw_rows = [
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:00Z",
            "event": "model.requested",
            "row_kind": "event",
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:01Z",
            "event": "context.capsule.emitted",
            "row_kind": "event",
            "capsule_count": 1,
            "capsule_kinds": ["implementation_delta"],
            "capsules": [
                {
                    "capsule_id": "ctxcap:1",
                    "kind": "implementation_delta",
                    "artifact_state": "useful_for_downstream",
                    "summary": "Changed src/app.py",
                }
            ],
        },
        {
            "schema": "organism_log_v1",
            "stream": "run",
            "product": "external",
            "timestamp": "2026-04-23T00:00:03Z",
            "event": "context.readiness.emitted",
            "row_kind": "event",
            "readiness_id": "ready:1",
            "ready_for_downstream": True,
            "predicate": "tool_context_available",
            "readiness": {
                "readiness_id": "ready:1",
                "ready_for_downstream": True,
                "predicate": "tool_context_available",
                "capsule_ids": ["ctxcap:1"],
                "blockers": [],
            },
        },
    ]

    analysis = analyze_scheduler_replay_rows(raw_rows)

    assert analysis.readiness.capsule_event_count == 1
    assert analysis.readiness.readiness_event_count == 1
    assert analysis.readiness.first_useful_artifact_ms == 1000
    assert analysis.readiness.first_downstream_ready_ms == 3000
    assert analysis.readiness.downstream_unlock_latency_ms == 2000
    assert analysis.readiness.ready_signal_ids == ["ready:1"]
    assert analysis.readiness.predicates == ["tool_context_available"]
