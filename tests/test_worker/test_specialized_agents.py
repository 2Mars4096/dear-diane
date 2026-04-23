from __future__ import annotations

from dan.worker import (
    SpecializedAgentKind,
    build_specialized_agent_worker,
    default_controller_guardrails,
    specialized_agent_membrane,
)
from dan.worker.organisms.coding_conversation import (
    build_coding_project_planner_worker,
)
from dan.worker.organisms.dan_conversation import build_dan_conversation_worker


def test_build_specialized_agent_worker_attaches_membrane_metadata() -> None:
    worker = build_specialized_agent_worker(
        worker_id="controller-1",
        role="test_controller",
        instruction="Route the request.",
        model="gpt-test",
        specialization=SpecializedAgentKind.CONTROLLER,
        contract_name="test_controller_contract",
        recurrent_loop="turn -> route -> review",
        typed_action_contract="controller_decision",
        deterministic_guardrails=default_controller_guardrails(),
        metadata={"lane_family": ["code", "research"]},
    )

    membrane = specialized_agent_membrane(worker)

    assert membrane is not None
    assert membrane.specialization == SpecializedAgentKind.CONTROLLER
    assert membrane.contract_name == "test_controller_contract"
    assert membrane.recurrent_loop == "turn -> route -> review"
    assert membrane.typed_action_contract == "controller_decision"
    assert membrane.deterministic_guardrails.require_audit_log is True
    assert worker.metadata["lane_family"] == ["code", "research"]


def test_dan_conversation_worker_uses_controller_specialization() -> None:
    worker = build_dan_conversation_worker(
        worker_id="dan.controller",
        model="gpt-test",
    )

    membrane = specialized_agent_membrane(worker)

    assert membrane is not None
    assert membrane.specialization == SpecializedAgentKind.CONTROLLER
    assert membrane.contract_name == "dan_conversation_controller"
    assert membrane.typed_action_contract == "conversation_turn_decision"
    assert worker.metadata["lane_family"] == ["code", "research", "incident", "legacy"]


def test_coding_project_planner_uses_scheduler_specialization() -> None:
    worker = build_coding_project_planner_worker(
        worker_id="dan-code.project-planner",
        model="gpt-test",
    )

    membrane = specialized_agent_membrane(worker)

    assert membrane is not None
    assert membrane.specialization == SpecializedAgentKind.SCHEDULER
    assert membrane.contract_name == "coding_project_planner"
    assert membrane.typed_action_contract == "schedule_action_proposal"
