from __future__ import annotations

from typing import get_args

from dan.worker.organisms.dan_conversation import SupervisorBrief
from dan.worker.organisms.incident_conversation import (
    IncidentCommanderController,
    IncidentConversationContext,
    IncidentConversationFacts,
    IncidentConversationTurnDecision,
    _fallback_turn_decision,
    build_incident_commander_worker,
)
from dan.worker.organisms.incident_execution import (
    FROZEN_INCIDENT_SCENARIOS,
    IncidentTerminalState,
    classify_incident_scenario,
)


def _context() -> IncidentConversationContext:
    return IncidentConversationContext(
        workflow_id="wf-incident",
        model="gpt-test",
        workspace_root="/workspace",
        requested_mode="auto",
        normalized_mode="agent",
        incoming_brief=SupervisorBrief(
            lane="incident",
            desired_delta="Investigate the failed workflow.",
        ),
        facts=IncidentConversationFacts(
            workflow_id="wf-incident",
            thread_id="thread-1",
            session_id="session-1",
            surface="server",
            workspace_root="/workspace",
            active_model="gpt-test",
            requested_mode="auto",
            normalized_mode="agent",
            available_action_lanes=["code", "legacy"],
            frozen_scenarios=[
                scenario.scenario_id for scenario in FROZEN_INCIDENT_SCENARIOS
            ],
            current_timestamp="2026-04-17T00:00:00+08:00",
            current_date="2026-04-17",
            timezone="Asia/Hong_Kong",
        ),
    )


def test_frozen_incident_matrix_has_three_proving_scenarios() -> None:
    scenario_ids = {scenario.scenario_id for scenario in FROZEN_INCIDENT_SCENARIOS}

    assert scenario_ids == {
        "failed_scheduled_workflow",
        "broken_coding_run_or_stale_run_state",
        "failed_external_surface_session",
    }


def test_incident_commander_worker_uses_literal_terminal_state_metadata() -> None:
    worker = build_incident_commander_worker(
        worker_id="dan.incident-commander.test",
        model="gpt-test",
    )

    assert worker.metadata["terminal_states"] == list(get_args(IncidentTerminalState))


def test_classifies_failed_scheduled_workflow() -> None:
    scenario = classify_incident_scenario(
        "The nightly scheduled workflow failed and left stale run state."
    )

    assert scenario is not None
    assert scenario.scenario_id == "failed_scheduled_workflow"
    assert scenario.default_action_lane == "legacy"


def test_fallback_delegates_bounded_incident_action() -> None:
    decision = _fallback_turn_decision(
        user_message="The scheduled workflow failed again, please investigate.",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "delegate"
    assert decision.terminal_state == "open"
    assert decision.incident_scenario_id == "failed_scheduled_workflow"
    assert decision.action_lane == "legacy"
    assert decision.verification_checks


def test_fallback_stops_on_approval_boundary() -> None:
    decision = _fallback_turn_decision(
        user_message="Rollback the production workflow now.",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "respond"
    assert decision.terminal_state == "needs_approval"
    assert decision.chosen_action == "request_approval"
    assert decision.action_lane == ""


def test_incident_supervisor_brief_carries_verification_checks() -> None:
    decision = IncidentConversationTurnDecision(
        action="delegate",
        public_response="Investigating.",
        terminal_state="open",
        incident_scenario_id="failed_scheduled_workflow",
        chosen_action="retry",
        action_lane="legacy",
        desired_delta="Retry the failed scheduled workflow safely.",
        success_criteria=["Retry once."],
        verification_checks=["Confirm the new run status."],
    )

    brief = IncidentCommanderController.build_supervisor_brief(decision)

    assert brief is not None
    assert brief.lane == "legacy"
    assert brief.desired_delta == "Retry the failed scheduled workflow safely."
    assert brief.success_criteria == [
        "Retry once.",
        "Verification checks: Confirm the new run status.",
    ]
