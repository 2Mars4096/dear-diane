from __future__ import annotations

from dan.worker.organisms.dan_conversation import (
    DANConversationContext,
    DANConversationController,
    DANConversationFacts,
    _fallback_turn_decision,
    _normalize_turn_decision,
)


def _context() -> DANConversationContext:
    return DANConversationContext(
        workflow_id="wf-incident",
        model="gpt-test",
        requested_mode="auto",
        normalized_mode="agent",
        facts=DANConversationFacts(
            workflow_id="wf-incident",
            thread_id="thread-1",
            session_id="session-1",
            surface="server",
            active_model="gpt-test",
            requested_mode="auto",
            normalized_mode="agent",
            available_organisms=["code", "research", "incident", "legacy"],
            current_timestamp="2026-04-17T00:00:00+08:00",
            current_date="2026-04-17",
            timezone="Asia/Hong_Kong",
        ),
    )


def test_fallback_routes_operational_failures_to_incident_lane() -> None:
    decision = _fallback_turn_decision(
        user_message="The scheduled workflow failed and the run state is stale.",
        pending_clarification=None,
        context=_context(),
    )

    assert decision.action == "delegate"
    assert decision.selected_lane == "incident"
    assert "terminal state" in decision.success_criteria[0]


def test_normalized_decision_accepts_incident_lane_and_builds_brief() -> None:
    decision = _normalize_turn_decision(
        {
            "action": "delegate",
            "public_response": "Routing to Incident Commander.",
            "selected_lane": "incident",
            "why_now": "A workflow failed.",
            "desired_delta": "Investigate the failed workflow.",
            "success_criteria": ["Stop with an explicit terminal state."],
        },
        user_message="workflow failed",
        pending_clarification=None,
        context=_context(),
    )

    brief = DANConversationController.build_supervisor_brief(decision)

    assert decision.selected_lane == "incident"
    assert brief is not None
    assert brief.lane == "incident"
    assert brief.desired_delta == "Investigate the failed workflow."
