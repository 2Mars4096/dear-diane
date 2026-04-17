from __future__ import annotations

from dan.worker.organisms.incident_execution import (
    IncidentExecutionRequest,
    execute_incident_action,
)


def test_execute_incident_retry_runs_full_loop_and_resolves() -> None:
    report = execute_incident_action(
        IncidentExecutionRequest(
            scenario_id="failed_scheduled_workflow",
            action_id="retry",
            target="nightly-report",
            objective="Retry the failed scheduled workflow once.",
            evidence={"retry_result": "succeeded"},
            verification_checks=["Confirm the latest run status."],
        )
    )

    assert report.terminal_state == "resolved"
    assert report.verification_result.verified is True
    assert report.action_result.changed_state is True
    assert [record.phase for record in report.phase_trace] == [
        "investigate",
        "action_gate",
        "act",
        "verify",
        "close",
    ]
    assert "ended as `resolved`" in report.public_summary


def test_execute_incident_containment_closes_as_contained() -> None:
    report = execute_incident_action(
        IncidentExecutionRequest(
            scenario_id="failed_external_surface_session",
            action_id="contain",
            target="wechat-delivery-adapter",
            objective="Contain failed delivery while preserving operator control.",
            evidence={"containment_result": "paused"},
        )
    )

    assert report.terminal_state == "contained"
    assert report.verification_result.verified is True
    assert report.action_result.status == "completed"


def test_execute_incident_pause_closes_as_contained() -> None:
    report = execute_incident_action(
        IncidentExecutionRequest(
            scenario_id="failed_scheduled_workflow",
            action_id="pause",
            target="nightly-report",
            evidence={"pause_result": "paused"},
        )
    )

    assert report.terminal_state == "contained"
    assert report.action_result.changed_state is True


def test_execute_incident_investigate_blocks_without_evidence() -> None:
    report = execute_incident_action(
        IncidentExecutionRequest(
            scenario_id="failed_scheduled_workflow",
            action_id="investigate",
            target="nightly-report",
        )
    )

    assert report.terminal_state == "blocked"
    assert report.verification_result.verified is False
    assert "current incident state evidence is missing" in report.action_result.blockers


def test_execute_incident_rollback_requires_approval() -> None:
    report = execute_incident_action(
        IncidentExecutionRequest(
            scenario_id="failed_scheduled_workflow",
            action_id="rollback",
            target="nightly-report",
            evidence={"rollback_result": "restored"},
        )
    )

    assert report.terminal_state == "needs_approval"
    assert report.action_result.status == "needs_approval"
    assert "operator approval required" in report.verification_result.required_follow_up


def test_execute_incident_approved_rollback_can_resolve() -> None:
    report = execute_incident_action(
        IncidentExecutionRequest(
            scenario_id="failed_scheduled_workflow",
            action_id="rollback",
            target="nightly-report",
            approval_granted=True,
            evidence={"rollback_result": "restored"},
        )
    )

    assert report.terminal_state == "resolved"
    assert report.verification_result.verified is True
    assert report.action_result.changed_state is True


def test_execute_incident_repair_delegates_to_code_lane_without_closure() -> None:
    report = execute_incident_action(
        IncidentExecutionRequest(
            scenario_id="broken_coding_run_or_stale_run_state",
            action_id="repair",
            target="failing-ci-build",
            objective="Repair the failing CI build.",
            preferred_lane="code",
        )
    )

    assert report.terminal_state == "open"
    assert report.action_result.status == "delegated"
    assert report.action_result.next_lane == "code"
    assert report.verification_result.required_follow_up == [
        "wait for delegated worker report before closing the incident"
    ]
