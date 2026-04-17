from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dan.server.control_plane import DANV2Runtime
from dan.worker.organisms.coding_conversation import CodingConversationTurnDecision
from dan.worker.organisms.dan_conversation import (
    DANConversationController,
    DANConversationTurnDecision,
)
from dan.worker.organisms.incident_conversation import (
    IncidentCommanderController,
    IncidentConversationTurnDecision,
)


class _FakeDANController:
    def __init__(self, decision: DANConversationTurnDecision) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    def load_session(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        return {"loaded": payload or {}}

    @staticmethod
    def dump_session(_session: Any) -> dict[str, Any]:
        return {"session": "controller"}

    async def decide_user_turn(self, **kwargs: Any):
        self.calls.append(kwargs)
        return self.decision, {"session": "controller"}

    @staticmethod
    def build_supervisor_brief(decision: DANConversationTurnDecision):
        return DANConversationController.build_supervisor_brief(decision)


class _FakeIncidentController:
    def __init__(self, decision: IncidentConversationTurnDecision) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    def load_session(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        return {"loaded": payload or {}}

    @staticmethod
    def dump_session(_session: Any) -> dict[str, Any]:
        return {"session": "incident"}

    async def decide_user_turn(self, **kwargs: Any):
        self.calls.append(kwargs)
        return self.decision, {"session": "incident"}

    @staticmethod
    def build_supervisor_brief(decision: IncidentConversationTurnDecision):
        return IncidentCommanderController.build_supervisor_brief(decision)


class _FakeCodeController:
    def __init__(self, decision: CodingConversationTurnDecision) -> None:
        self.decision = decision
        self.calls: list[dict[str, Any]] = []

    def load_session(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        return {"loaded": payload or {}}

    @staticmethod
    def dump_session(_session: Any) -> dict[str, Any]:
        return {"session": "code"}

    async def decide_user_turn(self, **kwargs: Any):
        self.calls.append(kwargs)
        return self.decision, {"session": "code"}


class _UnusedController:
    def load_session(self, _payload: dict[str, Any] | None) -> None:
        return None


class _FakeRunManager:
    def __init__(
        self,
        runs: list[dict[str, Any]],
        *,
        cancel_results: dict[str, bool] | None = None,
    ) -> None:
        self.runs = list(runs)
        self.calls = 0
        self.cancel_calls: list[str] = []
        self.cancel_results = dict(cancel_results or {})
        self.retry_calls: list[str] = []
        self.retry_results: dict[str, Any] = {}

    def list_runs(self) -> list[dict[str, Any]]:
        self.calls += 1
        return list(self.runs)

    def cancel_run(self, run_id: str) -> bool:
        self.cancel_calls.append(run_id)
        return self.cancel_results.get(run_id, True)

    async def retry_run(self, run_id: str):
        self.retry_calls.append(run_id)
        return self.retry_results.get(run_id, SimpleNamespace(run_id=f"retry-{run_id}"))


def _runtime(
    *,
    top_decision: DANConversationTurnDecision,
    incident_decision: IncidentConversationTurnDecision,
    code_decision: CodingConversationTurnDecision | None = None,
    run_manager: Any | None = None,
) -> DANV2Runtime:
    runtime = object.__new__(DANV2Runtime)
    runtime._model = "gpt-test"
    runtime._provider = object()
    runtime._stream_text_responses = False
    runtime._provider_request_overrides = {}
    runtime._run_manager = run_manager
    runtime._controller = _FakeDANController(top_decision)
    runtime._incident_controller = _FakeIncidentController(incident_decision)
    runtime._code_controller = (
        _FakeCodeController(code_decision)
        if code_decision is not None
        else _UnusedController()
    )
    runtime._research_controller = _UnusedController()
    return runtime


def _direct_report(**overrides: Any) -> dict[str, Any]:
    payload = {
        "status": "completed",
        "trace_id": "trace-direct-1",
        "organism_id": "coding-organism",
        "organ_id": "coding-build",
        "task_id": "server-control-plane:wf-1:session-1",
        "objective": "Repair the failing CI build.",
        "candidate_id": "candidate-1",
        "change_summary": "Updated the failing CI path and added focused coverage.",
        "target_files": ["src/ci.py", "tests/test_ci.py"],
        "test_plan": ["pytest tests/test_ci.py"],
        "risks": ["Manual end-to-end CI smoke is still pending."],
        "outputs": {},
        "handoff_count": 2,
        "signal_count": 5,
        "error": None,
        "trace_rows": [],
    }
    payload.update(overrides)
    return payload


def _request(message: str) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_id="wf-1",
        thread_id="thread-1",
        session_id="session-1",
        surface="server",
        mode="agent",
        history=[{"role": "user", "content": message}],
        message=message,
        surface_context={"workspace_root": "/workspace"},
    )


@pytest.mark.asyncio
async def test_runtime_routes_incident_lane_to_incident_commander_handoff() -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Routing through Incident Commander.",
            selected_lane="incident",
            why_now="The workflow failed.",
            desired_delta="Investigate the failed workflow.",
            success_criteria=["Stop with an explicit terminal state."],
        ),
        incident_decision=IncidentConversationTurnDecision(
            action="delegate",
            public_response="I will investigate the failed workflow.",
            terminal_state="open",
            incident_scenario_id="failed_scheduled_workflow",
            severity="medium",
            chosen_action="investigate",
            action_lane="legacy",
            desired_delta="Inspect the failed scheduled workflow and stale run state.",
            success_criteria=["Identify current state."],
            verification_checks=["Confirm the latest run status."],
        ),
    )

    outcome = await runtime.triage_user_turn(
        req=_request("The scheduled workflow failed and the run state is stale."),
        normalized_mode="agent",
        incident_session_payload={"previous": "incident"},
    )

    incident_controller = runtime._incident_controller
    assert incident_controller.calls
    assert incident_controller.calls[0]["context"].incoming_brief.lane == "incident"
    assert outcome.direct_response is None
    assert outcome.supervisor_brief is not None
    assert outcome.supervisor_brief.lane == "legacy"
    assert outcome.worker_report.lane == "incident"
    assert outcome.review_decision.next_lane == "legacy"
    assert outcome.handoff_metadata == {
        "selected_lane": "incident",
        "incident_action_lane": "legacy",
        "incident_scenario_id": "failed_scheduled_workflow",
        "incident_terminal_state": "open",
        "incident_action": "investigate",
        "incident_severity": "medium",
        "incident_execution_mode": "handoff",
        "verification_checks": ["Confirm the latest run status."],
    }
    assert "Incident Commander handoff" in outcome.handoff_prompt_context
    assert "failed_scheduled_workflow" in outcome.handoff_prompt_context
    assert outcome.incident_session_payload == {"session": "incident"}


@pytest.mark.asyncio
async def test_runtime_can_execute_code_lane_directly_inside_v2_runtime() -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Route this through DAN Code.",
            selected_lane="code",
            desired_delta="Repair the failing CI build.",
            success_criteria=["Ship one bounded repair candidate."],
        ),
        incident_decision=IncidentConversationTurnDecision(action="respond"),
        code_decision=CodingConversationTurnDecision(
            action="code",
            public_response="Routing this through DAN Code.",
            coding_objective="Repair the failing CI build and validate the fix.",
            acceptance_criteria=["Ship one bounded repair candidate."],
            repair_brief="The CI build is failing on the publish path.",
        ),
    )

    calls: list[dict[str, Any]] = []

    async def _runner(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _direct_report(
            objective=kwargs["objective"],
            trace_id="trace-direct-top",
        )

    runtime._direct_code_runner = _runner
    req = _request("Repair the failing CI build.")
    req.surface_context = {
        "workspace_root": "/workspace",
        "direct_code_execution": True,
    }

    outcome = await runtime.triage_user_turn(
        req=req,
        normalized_mode="agent",
    )

    assert calls
    assert calls[0]["objective"] == "Repair the failing CI build and validate the fix."
    assert calls[0]["acceptance_criteria"] == ["Ship one bounded repair candidate."]
    assert outcome.direct_response is not None
    assert "DAN Code completed one bounded pass." in outcome.direct_response
    assert "Updated the failing CI path and added focused coverage." in outcome.direct_response
    assert "Target files: src/ci.py, tests/test_ci.py" in outcome.direct_response
    assert outcome.review_decision.action == "stop"
    assert outcome.worker_report.lane == "code"
    assert outcome.handoff_metadata == {
        "selected_lane": "code",
        "code_execution_mode": "direct",
        "coding_objective": "Repair the failing CI build and validate the fix.",
        "acceptance_criteria": ["Ship one bounded repair candidate."],
        "repair_brief": "The CI build is failing on the publish path.",
        "coding_status": "completed",
        "candidate_id": "candidate-1",
        "trace_id": "trace-direct-top",
        "target_files": ["src/ci.py", "tests/test_ci.py"],
        "test_plan": ["pytest tests/test_ci.py"],
        "risks": ["Manual end-to-end CI smoke is still pending."],
    }


@pytest.mark.asyncio
async def test_runtime_stops_when_incident_commander_needs_approval() -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Routing through Incident Commander.",
            selected_lane="incident",
            desired_delta="Rollback the workflow.",
        ),
        incident_decision=IncidentConversationTurnDecision(
            action="respond",
            public_response="I need approval before rollback.",
            terminal_state="needs_approval",
            chosen_action="request_approval",
            desired_delta="Rollback the workflow.",
            success_criteria=["Receive explicit approval."],
        ),
    )

    outcome = await runtime.triage_user_turn(
        req=_request("Rollback the failed workflow."),
        normalized_mode="agent",
    )

    assert outcome.direct_response == "I need approval before rollback."
    assert outcome.worker_report.lane == "incident"
    assert outcome.review_decision.action == "stop"
    assert outcome.supervisor_brief is not None
    assert outcome.supervisor_brief.lane == "incident"
    assert outcome.incident_session_payload == {"session": "incident"}


@pytest.mark.asyncio
async def test_runtime_can_close_incident_from_deterministic_execution_context() -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Routing through Incident Commander.",
            selected_lane="incident",
            desired_delta="Retry the failed workflow.",
        ),
        incident_decision=IncidentConversationTurnDecision(
            action="delegate",
            public_response="I will retry the failed workflow.",
            terminal_state="open",
            incident_scenario_id="failed_scheduled_workflow",
            chosen_action="retry",
            action_lane="legacy",
            desired_delta="Retry the failed workflow.",
            success_criteria=["Retry once and verify the run status."],
            verification_checks=["Confirm the latest run status."],
        ),
    )

    req = _request("Retry the failed workflow.")
    req.surface_context = {
        "workspace_root": "/workspace",
        "incident_execution": {
            "target": "nightly-report",
            "evidence": {"retry_result": "succeeded"},
            "verification_checks": ["Confirm the latest run status."],
        },
    }

    outcome = await runtime.triage_user_turn(
        req=req,
        normalized_mode="agent",
    )

    assert outcome.direct_response is not None
    assert "ended as `resolved`" in outcome.direct_response
    assert outcome.review_decision.action == "stop"
    assert outcome.worker_report.lane == "incident"
    assert outcome.handoff_metadata == {
        "selected_lane": "incident",
        "incident_terminal_state": "resolved",
        "incident_action": "retry",
        "incident_execution_mode": "deterministic",
    }


@pytest.mark.asyncio
async def test_runtime_can_infer_failed_workflow_evidence_from_run_manager() -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Routing through Incident Commander.",
            selected_lane="incident",
            desired_delta="Investigate the failed nightly workflow.",
        ),
        incident_decision=IncidentConversationTurnDecision(
            action="delegate",
            public_response="I will investigate the failed workflow.",
            terminal_state="open",
            incident_scenario_id="failed_scheduled_workflow",
            chosen_action="investigate",
            action_lane="legacy",
            desired_delta="Investigate the failed nightly workflow.",
            success_criteria=["Identify the current incident state."],
            verification_checks=["Confirm the latest run status."],
        ),
        run_manager=_FakeRunManager(
            [
                {
                    "run_id": "run-42",
                    "graph_id": "nightly-report",
                    "status": "failed",
                    "phase": "completed",
                    "error": "Timed out while generating the report.",
                    "started_at": 10.0,
                    "finished_at": 20.0,
                }
            ]
        ),
    )

    outcome = await runtime.triage_user_turn(
        req=_request("The nightly-report workflow failed and the run state is stale."),
        normalized_mode="agent",
    )

    assert runtime._run_manager.calls == 1
    assert outcome.direct_response is not None
    assert "ended as `blocked`" in outcome.direct_response
    assert outcome.review_decision.action == "stop"
    assert outcome.worker_report.lane == "incident"
    assert outcome.handoff_metadata == {
        "selected_lane": "incident",
        "incident_terminal_state": "blocked",
        "incident_action": "investigate",
        "incident_execution_mode": "deterministic",
    }


@pytest.mark.asyncio
async def test_runtime_can_live_contain_running_workflow_via_run_manager() -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Routing through Incident Commander.",
            selected_lane="incident",
            desired_delta="Contain the stuck nightly workflow.",
        ),
        incident_decision=IncidentConversationTurnDecision(
            action="delegate",
            public_response="I will contain the running workflow.",
            terminal_state="open",
            incident_scenario_id="failed_scheduled_workflow",
            chosen_action="contain",
            action_lane="legacy",
            desired_delta="Contain the stuck nightly workflow.",
            success_criteria=["Contain the active workflow safely."],
            verification_checks=["Confirm the run is no longer active."],
        ),
        run_manager=_FakeRunManager(
            [
                {
                    "run_id": "run-live",
                    "graph_id": "nightly-report",
                    "status": "running",
                    "phase": "active",
                    "started_at": 10.0,
                    "finished_at": None,
                }
            ]
        ),
    )

    outcome = await runtime.triage_user_turn(
        req=_request("Contain the stuck nightly-report workflow; it is still running."),
        normalized_mode="agent",
    )

    assert runtime._run_manager.calls == 1
    assert runtime._run_manager.cancel_calls == ["run-live"]
    assert outcome.direct_response is not None
    assert "ended as `contained`" in outcome.direct_response
    assert outcome.review_decision.action == "stop"
    assert outcome.worker_report.lane == "incident"
    assert outcome.handoff_metadata == {
        "selected_lane": "incident",
        "incident_terminal_state": "contained",
        "incident_action": "contain",
        "incident_execution_mode": "live",
    }


@pytest.mark.asyncio
async def test_runtime_can_live_retry_failed_workflow_via_run_manager() -> None:
    run_manager = _FakeRunManager(
        [
            {
                "run_id": "run-failed",
                "graph_id": "nightly-report",
                "status": "failed",
                "phase": "failed",
                "started_at": 10.0,
                "finished_at": 20.0,
            }
        ]
    )
    run_manager.retry_results["run-failed"] = SimpleNamespace(run_id="run-retry-1")
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Routing through Incident Commander.",
            selected_lane="incident",
            desired_delta="Retry the failed nightly workflow.",
        ),
        incident_decision=IncidentConversationTurnDecision(
            action="delegate",
            public_response="I will retry the failed workflow.",
            terminal_state="open",
            incident_scenario_id="failed_scheduled_workflow",
            chosen_action="retry",
            action_lane="legacy",
            desired_delta="Retry the failed nightly workflow.",
            success_criteria=["Launch one honest retry."],
            verification_checks=["Wait for the new run to settle before claiming success."],
        ),
        run_manager=run_manager,
    )

    outcome = await runtime.triage_user_turn(
        req=_request("Retry the failed nightly-report workflow."),
        normalized_mode="agent",
    )

    assert runtime._run_manager.calls == 1
    assert runtime._run_manager.retry_calls == ["run-failed"]
    assert outcome.direct_response is not None
    assert "ended as `open`" in outcome.direct_response
    assert outcome.review_decision.action == "stop"
    assert outcome.worker_report.lane == "incident"
    assert outcome.handoff_metadata == {
        "selected_lane": "incident",
        "incident_terminal_state": "open",
        "incident_action": "retry",
        "incident_execution_mode": "live",
        "retry_run_id": "run-retry-1",
    }


@pytest.mark.asyncio
async def test_runtime_uses_deterministic_incident_execution_then_hands_repair_to_code_lane() -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Routing through Incident Commander.",
            selected_lane="incident",
            desired_delta="Repair the failing CI build.",
        ),
        incident_decision=IncidentConversationTurnDecision(
            action="delegate",
            public_response="I will route the repair through the code lane.",
            terminal_state="open",
            incident_scenario_id="broken_coding_run_or_stale_run_state",
            chosen_action="repair",
            action_lane="code",
            desired_delta="Repair the failing CI build.",
            success_criteria=["Hand off one bounded repair objective."],
            verification_checks=["Wait for the code-lane worker report."],
        ),
        code_decision=CodingConversationTurnDecision(
            action="code",
            public_response="Routing this through DAN Code.",
            coding_objective="Repair the failing CI build and validate the fix.",
            acceptance_criteria=["Produce one bounded repair candidate."],
            repair_brief="The CI build is failing in a reproducible way.",
        ),
    )

    req = _request("Repair the failing CI build.")
    req.surface_context = {
        "workspace_root": "/workspace",
        "incident_execution": {
            "target": "failing-ci-build",
            "objective": "Repair the failing CI build.",
            "preferred_lane": "code",
        },
    }

    outcome = await runtime.triage_user_turn(
        req=req,
        normalized_mode="agent",
    )

    code_controller = runtime._code_controller
    assert code_controller.calls
    assert code_controller.calls[0]["user_message"] == "Repair the failing CI build."
    assert outcome.direct_response is None
    assert outcome.supervisor_brief is not None
    assert outcome.supervisor_brief.lane == "code"
    assert outcome.review_decision.next_lane == "code"
    assert outcome.handoff_metadata == {
        "selected_lane": "incident",
        "incident_action_lane": "code",
        "incident_scenario_id": "broken_coding_run_or_stale_run_state",
        "incident_terminal_state": "open",
        "incident_action": "repair",
        "incident_severity": "medium",
        "incident_execution_mode": "deterministic",
        "nested_lane": "code",
        "coding_objective": "Repair the failing CI build and validate the fix.",
        "acceptance_criteria": ["Produce one bounded repair candidate."],
        "repair_brief": "The CI build is failing in a reproducible way.",
        "verification_checks": ["Wait for the code-lane worker report."],
    }
    assert outcome.worker_report.lane == "code"
    assert "DAN Code handoff" in outcome.handoff_prompt_context
    assert "Execution summary" in outcome.handoff_prompt_context


@pytest.mark.asyncio
async def test_runtime_can_execute_nested_incident_repair_directly() -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Routing through Incident Commander.",
            selected_lane="incident",
            desired_delta="Repair the failing CI build.",
        ),
        incident_decision=IncidentConversationTurnDecision(
            action="delegate",
            public_response="I will route the repair through the code lane.",
            terminal_state="open",
            incident_scenario_id="broken_coding_run_or_stale_run_state",
            chosen_action="repair",
            action_lane="code",
            desired_delta="Repair the failing CI build.",
            success_criteria=["Close the failing CI repair with one bounded pass."],
            verification_checks=["Verify the CI path locally."],
        ),
        code_decision=CodingConversationTurnDecision(
            action="code",
            public_response="Routing this through DAN Code.",
            coding_objective="Repair the failing CI build and validate the fix.",
            acceptance_criteria=["Produce one bounded repair candidate."],
            repair_brief="The CI build is failing in a reproducible way.",
        ),
    )

    async def _runner(**kwargs: Any) -> dict[str, Any]:
        return _direct_report(
            objective=kwargs["objective"],
            trace_id="trace-direct-nested",
        )

    runtime._direct_code_runner = _runner
    req = _request("Repair the failing CI build.")
    req.surface_context = {
        "workspace_root": "/workspace",
        "direct_code_execution": True,
        "incident_execution": {
            "target": "failing-ci-build",
            "objective": "Repair the failing CI build.",
            "preferred_lane": "code",
        },
    }

    outcome = await runtime.triage_user_turn(
        req=req,
        normalized_mode="agent",
    )

    assert outcome.direct_response is not None
    assert "DAN Code completed one bounded pass." in outcome.direct_response
    assert outcome.review_decision.action == "stop"
    assert outcome.worker_report.lane == "code"
    assert outcome.handoff_metadata == {
        "selected_lane": "incident",
        "code_execution_mode": "direct",
        "coding_objective": "Repair the failing CI build and validate the fix.",
        "acceptance_criteria": ["Produce one bounded repair candidate."],
        "repair_brief": "The CI build is failing in a reproducible way.",
        "coding_status": "completed",
        "candidate_id": "candidate-1",
        "trace_id": "trace-direct-nested",
        "target_files": ["src/ci.py", "tests/test_ci.py"],
        "test_plan": ["pytest tests/test_ci.py"],
        "risks": ["Manual end-to-end CI smoke is still pending."],
        "incident_action_lane": "code",
        "incident_scenario_id": "broken_coding_run_or_stale_run_state",
        "incident_terminal_state": "open",
        "incident_action": "repair",
        "incident_severity": "medium",
        "incident_execution_mode": "deterministic",
        "verification_checks": ["Verify the CI path locally."],
        "nested_lane": "code",
    }


@pytest.mark.asyncio
async def test_runtime_falls_back_to_handoff_when_direct_code_execution_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(
        top_decision=DANConversationTurnDecision(
            action="delegate",
            public_response="Route this through DAN Code.",
            selected_lane="code",
            desired_delta="Repair the failing CI build.",
            success_criteria=["Ship one bounded repair candidate."],
        ),
        incident_decision=IncidentConversationTurnDecision(action="respond"),
        code_decision=CodingConversationTurnDecision(
            action="code",
            public_response="Routing this through DAN Code.",
            coding_objective="Repair the failing CI build and validate the fix.",
            acceptance_criteria=["Ship one bounded repair candidate."],
            repair_brief="The CI build is failing on the publish path.",
        ),
    )

    async def _runner(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("provider timeout")

    runtime._direct_code_runner = _runner
    monkeypatch.setenv("DAN_V2_DIRECT_CODE_RUNTIME", "1")

    outcome = await runtime.triage_user_turn(
        req=_request("Repair the failing CI build."),
        normalized_mode="agent",
    )

    assert outcome.direct_response is None
    assert outcome.review_decision.action == "continue"
    assert outcome.review_decision.next_lane == "code"
    assert outcome.worker_report.lane == "code"
    assert outcome.handoff_metadata == {
        "selected_lane": "code",
        "coding_objective": "Repair the failing CI build and validate the fix.",
        "acceptance_criteria": ["Ship one bounded repair candidate."],
        "repair_brief": "The CI build is failing on the publish path.",
    }
    assert "DAN Code handoff" in outcome.handoff_prompt_context
