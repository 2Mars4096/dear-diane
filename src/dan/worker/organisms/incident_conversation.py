"""Durable Incident Commander conversation controller."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.providers import LLMProvider
from dan.worker.core.contracts import (
    AcquisitionPolicy,
    AcquisitionRequest,
    ExecutionRequest,
    OutputContract,
)
from dan.worker.core.model import WorkerDefinition
from dan.worker.organisms.coding_conversation import ProviderCompletionAdapter
from dan.worker.organisms.dan_conversation import SupervisorBrief, WorkerReport
from dan.worker.organisms.incident_execution import (
    FROZEN_INCIDENT_SCENARIOS,
    IncidentTerminalState,
    classify_incident_scenario,
    resolve_incident_action_boundary,
)
from dan.worker.runner import DurableAgentRunner, DurableAgentSessionState
from dan.worker.structured_payload import parse_jsonish_payload


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _dedupe(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


_DECISION_KEYS = frozenset(
    {
        "action",
        "public_response",
        "clarifying_question",
        "terminal_state",
        "incident_scenario_id",
        "severity",
        "chosen_action",
        "action_lane",
        "why_now",
        "desired_delta",
        "success_criteria",
        "avoid",
        "stop_and_ask_when",
        "verification_checks",
    }
)

_INCIDENT_CUES = (
    "incident",
    "outage",
    "degraded",
    "failed",
    "failure",
    "stuck",
    "stalled",
    "stale",
    "broken",
    "rollback",
    "recover",
    "remediate",
    "contain",
    "escalate",
)

_APPROVAL_CUES = (
    "rollback",
    "revert",
    "restore backup",
    "delete",
    "drop ",
    "send message",
    "post to",
    "restart production",
    "shutdown",
    "shut down",
)

_RETRY_CUES = ("retry", "rerun", "run again", "refresh")
_CONTAIN_CUES = ("contain", "pause", "stop the bleeding", "limit blast", "disable")
_REPAIR_CUES = ("repair", "patch", "fix code", "fix the build", "broken build", "ci failed")


def build_incident_commander_worker(
    *,
    worker_id: str,
    model: str | None,
) -> WorkerDefinition:
    return WorkerDefinition(
        id=worker_id,
        role="incident_commander",
        instruction=(
            "You are the Incident Commander organism on top of the universal-agent substrate. "
            "For each incident turn, classify the incident, choose the safest bounded next action, "
            "and converge toward an explicit terminal state. Use action=respond when the right "
            "answer is a terminal stop such as resolved, contained, blocked, escalated, or "
            "needs_approval. Use action=clarify when one missing detail blocks safe progress. "
            "Use action=delegate only when one bounded action should run through exactly one "
            "underlying lane: action_lane=code for repo/script repair and action_lane=legacy for "
            "deterministic operator, workflow, browser, adapter, or session handling. Prefer "
            "investigation, containment, and safe retries before open-ended repair. Never choose "
            "rollback, destructive actions, desktop input, browser input, or outbound messaging "
            "without making the approval boundary explicit."
        ),
        model=model,
    )


class IncidentConversationMessage(BaseModel):
    role: str
    text: str
    kind: str = "message"


class IncidentConversationFacts(BaseModel):
    workflow_id: str
    thread_id: str = ""
    session_id: str = ""
    surface: str = ""
    workspace_root: str = ""
    active_model: str = ""
    requested_mode: str = ""
    normalized_mode: str = ""
    available_action_lanes: list[str] = Field(default_factory=list)
    frozen_scenarios: list[str] = Field(default_factory=list)
    recent_status: str = ""
    recent_objective: str = ""
    current_timestamp: str = ""
    current_date: str = ""
    timezone: str = ""


class IncidentConversationContext(BaseModel):
    workflow_id: str
    model: str
    workspace_root: str = ""
    requested_mode: str = ""
    normalized_mode: str = ""
    pending_clarification: str | None = None
    incoming_brief: SupervisorBrief | None = None
    facts: IncidentConversationFacts
    recent_conversation: list[IncidentConversationMessage] = Field(default_factory=list)
    recent_worker_reports: list[WorkerReport] = Field(default_factory=list)


class IncidentConversationTurnDecision(BaseModel):
    action: Literal["respond", "clarify", "delegate"] = "respond"
    public_response: str = ""
    clarifying_question: str = ""
    terminal_state: IncidentTerminalState = "open"
    incident_scenario_id: str = ""
    severity: Literal["low", "medium", "high"] = "medium"
    chosen_action: Literal[
        "investigate",
        "retry",
        "contain",
        "pause",
        "rollback",
        "repair",
        "request_approval",
        "escalate",
        "report",
    ] = "investigate"
    action_lane: Literal["", "code", "legacy"] = ""
    why_now: str = ""
    desired_delta: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    stop_and_ask_when: list[str] = Field(default_factory=list)
    verification_checks: list[str] = Field(default_factory=list)


def _conversation_turn_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["respond", "clarify", "delegate"]},
            "public_response": {"type": "string"},
            "clarifying_question": {"type": "string"},
            "terminal_state": {
                "type": "string",
                "enum": [
                    "open",
                    "resolved",
                    "contained",
                    "blocked",
                    "escalated",
                    "needs_approval",
                ],
            },
            "incident_scenario_id": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "chosen_action": {
                "type": "string",
                "enum": [
                    "investigate",
                    "retry",
                    "contain",
                    "pause",
                    "rollback",
                    "repair",
                    "request_approval",
                    "escalate",
                    "report",
                ],
            },
            "action_lane": {"type": "string", "enum": ["", "code", "legacy"]},
            "why_now": {"type": "string"},
            "desired_delta": {"type": "string"},
            "success_criteria": {"type": "array", "items": {"type": "string"}},
            "avoid": {"type": "array", "items": {"type": "string"}},
            "stop_and_ask_when": {"type": "array", "items": {"type": "string"}},
            "verification_checks": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["action", "public_response", "terminal_state"],
    }


def _conversation_turn_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Return one Incident Commander decision. Classify the incident scenario when "
            "possible. Use action=delegate only for one bounded next action with action_lane "
            "set to code or legacy. Use terminal_state=open only while more bounded work is "
            "needed; otherwise use resolved, contained, blocked, escalated, or needs_approval."
        ),
        expected_return_shape=json.dumps(
            {
                "action": "respond|clarify|delegate",
                "public_response": "<required>",
                "clarifying_question": "<optional>",
                "terminal_state": "open|resolved|contained|blocked|escalated|needs_approval",
                "incident_scenario_id": "<optional frozen scenario id>",
                "severity": "low|medium|high",
                "chosen_action": "investigate|retry|contain|pause|rollback|repair|request_approval|escalate|report",
                "action_lane": "code|legacy|",
                "why_now": "<optional>",
                "desired_delta": "<optional>",
                "success_criteria": ["<optional>"],
                "avoid": ["<optional>"],
                "stop_and_ask_when": ["<optional>"],
                "verification_checks": ["<optional>"],
            },
            sort_keys=True,
        ),
        output_schema=_conversation_turn_schema(),
    )


def _embedded_decision_payload(value: Any) -> dict[str, Any] | None:
    parsed = parse_jsonish_payload(value)
    if isinstance(parsed, dict) and any(key in parsed for key in _DECISION_KEYS):
        return dict(parsed)
    return None


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        payload = dict(raw)
        for key in ("result", "text", "public_response", "message", "content"):
            nested_payload = _embedded_decision_payload(payload.get(key))
            if nested_payload is not None and nested_payload != payload:
                return _parse_payload(nested_payload)
        if any(key in payload for key in _DECISION_KEYS):
            return payload
        nested = payload.get("result", payload.get("text"))
        if nested is not None and nested is not raw:
            return _parse_payload(nested)
        return payload
    parsed = parse_jsonish_payload(raw)
    if isinstance(parsed, dict):
        return _parse_payload(parsed)
    text = str(parsed or "").strip()
    return {"public_response": text} if text else {}


def _looks_like_incident_request(message: str) -> bool:
    lowered = _clean_text(message).lower()
    return any(cue in lowered for cue in _INCIDENT_CUES)


def _requires_approval(message: str) -> bool:
    lowered = _clean_text(message).lower()
    return any(cue in lowered for cue in _APPROVAL_CUES)


def _choose_action(message: str, *, default_lane: str) -> tuple[str, str]:
    lowered = _clean_text(message).lower()
    if any(cue in lowered for cue in _REPAIR_CUES) or default_lane == "code":
        return "repair", "code"
    if any(cue in lowered for cue in _CONTAIN_CUES):
        return "contain", "legacy"
    if any(cue in lowered for cue in _RETRY_CUES):
        return "retry", "legacy"
    return "investigate", default_lane if default_lane in {"code", "legacy"} else "legacy"


def _default_success_criteria(scenario_id: str) -> list[str]:
    criteria = [
        "Identify the current incident state and affected target.",
        "Choose one terminal state: resolved, contained, blocked, escalated, or needs_approval.",
        "Verify the chosen action before claiming resolution.",
    ]
    if scenario_id:
        criteria.insert(0, f"Keep the incident bounded to scenario `{scenario_id}`.")
    return criteria


def _fallback_turn_decision(
    *,
    user_message: str,
    pending_clarification: str | None,
    context: IncidentConversationContext,
) -> IncidentConversationTurnDecision:
    if pending_clarification:
        return IncidentConversationTurnDecision(
            action="clarify",
            public_response="I need one concrete incident detail before choosing an action.",
            clarifying_question=pending_clarification,
            terminal_state="blocked",
            stop_and_ask_when=[pending_clarification],
        )
    cleaned = _clean_text(user_message)
    if not cleaned:
        return IncidentConversationTurnDecision(
            action="respond",
            public_response=(
                "Give me the failing workflow, run, browser/session, adapter, or coding run "
                "and I can handle it as a bounded incident."
            ),
            terminal_state="blocked",
        )

    scenario = classify_incident_scenario(cleaned)
    if _requires_approval(cleaned):
        return IncidentConversationTurnDecision(
            action="respond",
            public_response=(
                "This incident path crosses an approval boundary. I need explicit approval "
                "before rollback, destructive recovery, desktop/browser input, or outbound messaging."
            ),
            terminal_state="needs_approval",
            incident_scenario_id=scenario.scenario_id if scenario is not None else "",
            chosen_action="request_approval",
            action_lane="",
            why_now="The requested remediation could change external state or undo previous work.",
            desired_delta=cleaned,
            success_criteria=["Receive explicit operator approval before taking the risky action."],
            stop_and_ask_when=["Approval is not explicit in the user turn."],
        )

    if scenario is None and not _looks_like_incident_request(cleaned):
        return IncidentConversationTurnDecision(
            action="respond",
            public_response=(
                "Incident Commander is for bounded operational incidents. Give me the failing "
                "run, workflow, browser/download session, adapter, or coding run to inspect."
            ),
            terminal_state="blocked",
        )

    if scenario is None:
        return IncidentConversationTurnDecision(
            action="clarify",
            public_response="I can take this as an incident, but the failing target is still ambiguous.",
            clarifying_question="Which workflow, run, browser/download session, adapter, or coding run should I inspect first?",
            terminal_state="blocked",
            stop_and_ask_when=["The target incident surface is ambiguous."],
        )

    chosen_action, action_lane = _choose_action(
        cleaned,
        default_lane=scenario.default_action_lane,
    )
    boundary = resolve_incident_action_boundary(chosen_action, preferred_lane=action_lane)
    if boundary.requires_approval:
        return IncidentConversationTurnDecision(
            action="respond",
            public_response="This incident action needs explicit approval before I continue.",
            terminal_state="needs_approval",
            incident_scenario_id=scenario.scenario_id,
            chosen_action="request_approval",
            why_now="The chosen action crosses an approval boundary.",
            desired_delta=cleaned,
            success_criteria=["Receive explicit approval for the requested incident action."],
            stop_and_ask_when=["Approval is not explicit in the user turn."],
        )

    return IncidentConversationTurnDecision(
        action="delegate",
        public_response="I will run this through Incident Commander and keep it bounded.",
        terminal_state="open",
        incident_scenario_id=scenario.scenario_id,
        severity="medium",
        chosen_action=chosen_action,  # type: ignore[arg-type]
        action_lane=boundary.lane if boundary.lane in {"code", "legacy"} else action_lane,  # type: ignore[arg-type]
        why_now="The user is reporting an operational failure that needs diagnosis, action, and verification.",
        desired_delta=cleaned,
        success_criteria=_default_success_criteria(scenario.scenario_id),
        avoid=[
            "Do not take rollback, destructive, browser input, desktop input, or outbound messaging actions without approval.",
            "Do not loop indefinitely; stop with an explicit terminal state when blocked.",
        ],
        stop_and_ask_when=[
            "The failing target or account is ambiguous.",
            "The next action would cross an approval boundary.",
        ],
        verification_checks=[
            "Check the current run/session/delivery state after the action.",
            "Report the evidence that supports the terminal state.",
        ],
    )


def _normalize_turn_decision(
    payload: dict[str, Any],
    *,
    user_message: str,
    pending_clarification: str | None,
    context: IncidentConversationContext,
) -> IncidentConversationTurnDecision:
    normalized_payload = dict(_parse_payload(payload or {}))
    try:
        decision = IncidentConversationTurnDecision.model_validate(normalized_payload)
    except Exception:
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification,
            context=context,
        )

    if decision.action == "clarify" and not _clean_text(decision.clarifying_question):
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification or "Which failing target should I inspect first?",
            context=context,
        )
    if decision.action == "delegate" and decision.action_lane not in {"code", "legacy"}:
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification,
            context=context,
        )

    boundary = resolve_incident_action_boundary(
        decision.chosen_action,
        preferred_lane=decision.action_lane,
    )
    action = decision.action
    terminal_state = decision.terminal_state
    action_lane = decision.action_lane
    if boundary.requires_approval or terminal_state == "needs_approval":
        action = "respond"
        terminal_state = "needs_approval"
        action_lane = ""

    scenario = classify_incident_scenario(user_message)
    incident_scenario_id = _clean_text(decision.incident_scenario_id)
    if not incident_scenario_id and scenario is not None:
        incident_scenario_id = scenario.scenario_id

    return decision.model_copy(
        update={
            "action": action,
            "public_response": _clean_text(decision.public_response)
            or (
                "I will run one bounded incident step."
                if action == "delegate"
                else "Here is the incident status."
            ),
            "clarifying_question": _clean_text(decision.clarifying_question),
            "terminal_state": terminal_state,
            "incident_scenario_id": incident_scenario_id,
            "action_lane": action_lane,
            "why_now": _clean_text(decision.why_now),
            "desired_delta": _clean_text(decision.desired_delta) or _clean_text(user_message),
            "success_criteria": _dedupe(list(decision.success_criteria)),
            "avoid": _dedupe(list(decision.avoid)),
            "stop_and_ask_when": _dedupe(list(decision.stop_and_ask_when)),
            "verification_checks": _dedupe(list(decision.verification_checks)),
        }
    )


class IncidentCommanderController:
    """Durable Incident Commander controller for bounded operational incidents."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        stream_text_responses: bool = False,
        provider_request_overrides: dict[str, Any] | None = None,
        event_callback=None,
    ) -> None:
        self._worker = build_incident_commander_worker(
            worker_id="dan.incident-commander.conversation",
            model=str(model),
        )
        self._completion_adapter = ProviderCompletionAdapter(
            provider=provider,
            default_model=str(model),
            stream_text_responses=stream_text_responses,
            provider_request_overrides=provider_request_overrides,
            event_callback=event_callback,
        )
        self._runner = DurableAgentRunner(completion_provider=self._completion_adapter)

    def create_session(self, *, metadata: dict[str, Any] | None = None) -> DurableAgentSessionState:
        return self._runner.create_session(self._worker, metadata=metadata)

    @staticmethod
    def load_session(payload: dict[str, Any] | None) -> DurableAgentSessionState | None:
        if not payload:
            return None
        return DurableAgentSessionState.model_validate(payload)

    @staticmethod
    def dump_session(session: DurableAgentSessionState) -> dict[str, Any]:
        return session.model_dump(mode="json")

    async def decide_user_turn(
        self,
        *,
        session: DurableAgentSessionState | None,
        user_message: str,
        pending_clarification: str | None,
        context: IncidentConversationContext,
    ) -> tuple[IncidentConversationTurnDecision, DurableAgentSessionState]:
        durable_session = session or self.create_session(
            metadata={"surface": "dan", "kind": "incident-commander"}
        )
        request = ExecutionRequest.from_harness(
            task=(
                "Handle the next Incident Commander turn. Classify the incident, choose "
                "whether to respond, ask one clarifying question, or delegate one bounded action."
            ),
            acquisition=AcquisitionRequest(
                policy=AcquisitionPolicy(reuse_continuation=False)
            ),
            output_contract=_conversation_turn_contract(),
            input_payload={
                "mode": "incident_turn",
                "user_message": user_message,
                "pending_clarification": pending_clarification or "",
                "frozen_incident_scenarios": [
                    scenario.model_dump(mode="json")
                    for scenario in FROZEN_INCIDENT_SCENARIOS
                ],
                "context": context.model_dump(mode="json"),
            },
            metadata={"surface": "dan", "turn_kind": "incident_turn"},
        )
        self._runner.enqueue_message(durable_session, request)
        result = await self._runner.process_next(self._worker, durable_session)
        payload = _parse_payload(result.outputs if result is not None else {})
        return (
            _normalize_turn_decision(
                payload,
                user_message=user_message,
                pending_clarification=pending_clarification,
                context=context,
            ),
            durable_session,
        )

    @staticmethod
    def build_supervisor_brief(
        decision: IncidentConversationTurnDecision,
    ) -> SupervisorBrief | None:
        if decision.action != "delegate" or decision.action_lane not in {"code", "legacy"}:
            return None
        criteria = list(decision.success_criteria)
        if decision.verification_checks:
            criteria.append(
                "Verification checks: " + "; ".join(decision.verification_checks)
            )
        return SupervisorBrief(
            lane=decision.action_lane,
            why_now=decision.why_now,
            desired_delta=decision.desired_delta,
            success_criteria=criteria,
            avoid=list(decision.avoid),
            stop_and_ask_when=list(decision.stop_and_ask_when),
        )


__all__ = [
    "IncidentCommanderController",
    "IncidentConversationContext",
    "IncidentConversationFacts",
    "IncidentConversationMessage",
    "IncidentConversationTurnDecision",
    "_fallback_turn_decision",
    "_normalize_turn_decision",
    "build_incident_commander_worker",
]
