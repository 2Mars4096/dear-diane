"""Durable DAN-v2 conversation controller built on the universal-agent substrate."""

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
from dan.worker.runner import DurableAgentRunner, DurableAgentSessionState
from dan.worker.specialized_agents import (
    SpecializedAgentKind,
    build_specialized_agent_worker,
    default_controller_guardrails,
)
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
        "selected_lane",
        "why_now",
        "desired_delta",
        "success_criteria",
        "avoid",
        "stop_and_ask_when",
    }
)

_CODE_CUES = (
    "build",
    "code",
    "repo",
    "repository",
    "file",
    "files",
    "fix",
    "patch",
    "edit",
    "implement",
    "refactor",
    "test",
    "bug",
    "script",
    "commit",
    "git",
    "frontend",
    "backend",
)

_RESEARCH_CUES = (
    "research",
    "verify",
    "check",
    "look up",
    "search",
    "compare",
    "what do others",
    "latest",
    "news",
    "source",
    "citation",
    "ground",
    "evidence",
)

_INCIDENT_CUES = (
    "incident",
    "outage",
    "degraded",
    "workflow failed",
    "scheduled workflow",
    "run failed",
    "stale run state",
    "stuck run state",
    "coding run failed",
    "broken build",
    "ci failed",
    "browser stuck",
    "download stuck",
    "session stuck",
    "adapter failed",
    "delivery failed",
    "rollback",
    "contain",
    "remediate",
    "recover",
    "escalate",
)

_LEGACY_OPERATOR_CUES = (
    "browser",
    "click",
    "open app",
    "wechat",
    "telegram",
    "email",
    "applescript",
    "screenshot",
    "desktop",
    "mac",
)


def build_dan_conversation_worker(
    *,
    worker_id: str,
    model: str | None,
) -> WorkerDefinition:
    return build_specialized_agent_worker(
        worker_id=worker_id,
        role="dan_conversation_controller",
        instruction=(
            "You are the DAN-v2 conversation controller on top of the universal-agent substrate. "
            "For each user turn, either answer directly, ask one clarifying question, or delegate "
            "to exactly one worker lane. Use respond for architecture discussion, status, planning, "
            "roadmap, meta conversation, or any answerable question that does not need execution. "
            "Use clarify when critical details are missing. Use selected_lane=code for concrete repo, "
            "file, test, fix, build, or implementation work. Use selected_lane=research for grounded "
            "investigation, comparison, verification, or evidence gathering. Use selected_lane=incident "
            "for operational failures that need triage, diagnosis, bounded remediation, verification, "
            "and an explicit terminal state. Use selected_lane=legacy for general operator tasks outside "
            "code/research/incident, such as browser control, app control, or broad desktop automation. "
            "Respect the operator_use_case_pack, operator_safety_envelope, operator_supervision_policy, "
            "operator_stop_conditions, operator_execution_target, "
            "operator_deterministic_capability_sets, operator_deterministic_adapters, "
            "operator_shared_control_membrane, and operator_non_goals facts when deciding whether "
            "the next step can keep going, must stop for approval, stay inside bounded operator lanes, "
            "or needs a sharper target. "
            "When delegating, define the smallest meaningful delta, why it matters now, concrete success "
            "criteria, what to avoid, and when the worker should ask back."
        ),
        model=model,
        specialization=SpecializedAgentKind.CONTROLLER,
        contract_name="dan_conversation_controller",
        recurrent_loop="user_turn -> lane_decision -> brief -> worker_report -> review_decision",
        typed_action_contract="conversation_turn_decision",
        deterministic_guardrails=default_controller_guardrails(
            safety_envelope="lane_routing",
            notes=[
                "controller decisions must preserve lane, approval, and audit context",
                "controller may delegate but does not execute workspace actions directly",
            ],
        ),
        metadata={
            "lane_family": ["code", "research", "incident", "legacy"],
        },
    )


class DANConversationMessage(BaseModel):
    role: str
    text: str
    kind: str = "message"


class DANConversationFacts(BaseModel):
    workflow_id: str
    thread_id: str = ""
    session_id: str = ""
    surface: str = ""
    workspace_root: str = ""
    platform: str = ""
    approval_mode: str = ""
    active_model: str = ""
    requested_mode: str = ""
    normalized_mode: str = ""
    operator_use_case_pack: str = ""
    operator_safety_envelope: str = ""
    operator_supervision_policy: str = ""
    operator_stop_conditions: list[str] = Field(default_factory=list)
    operator_execution_target: str = ""
    operator_deterministic_capability_sets: list[str] = Field(default_factory=list)
    operator_deterministic_adapters: list[str] = Field(default_factory=list)
    operator_shared_control_membrane: str = ""
    operator_non_goals: list[str] = Field(default_factory=list)
    available_organisms: list[str] = Field(default_factory=list)
    available_adapters: list[str] = Field(default_factory=list)
    available_tool_families: list[str] = Field(default_factory=list)
    pending_clarification: str | None = None
    recent_lane: str = ""
    recent_status: str = ""
    recent_objective: str = ""
    current_timestamp: str = ""
    current_date: str = ""
    timezone: str = ""


class SupervisorBrief(BaseModel):
    lane: Literal["code", "research", "legacy", "incident"]
    why_now: str = ""
    desired_delta: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    stop_and_ask_when: list[str] = Field(default_factory=list)

    def render_prompt_context(self) -> str:
        lines = [
            "DAN-v2 supervisor brief:",
            f"- Lane: {self.lane}",
            f"- Why now: {self.why_now or 'This is the highest-value next step for the user turn.'}",
            f"- Desired delta: {self.desired_delta or 'Produce the smallest concrete step that moves the task forward.'}",
        ]
        if self.success_criteria:
            lines.append("- Success criteria: " + "; ".join(self.success_criteria))
        if self.avoid:
            lines.append("- Avoid: " + "; ".join(self.avoid))
        if self.stop_and_ask_when:
            lines.append("- Stop and ask when: " + "; ".join(self.stop_and_ask_when))
        lines.append(
            "- Treat this as internal steering. Do the work, and only expose the brief if it materially helps the user."
        )
        return "\n".join(lines)


class WorkerReport(BaseModel):
    lane: str = ""
    status: Literal["responded", "clarify", "ready", "handoff"] = "responded"
    summary: str = ""
    objective: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    what_changed: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    best_next_question: str = ""


class ReviewDecision(BaseModel):
    action: Literal["stop", "continue", "sharpen", "redirect", "escalate"] = "stop"
    public_response: str = ""
    reason: str = ""
    next_lane: str = ""
    next_delta: str = ""


class DANConversationTurnDecision(BaseModel):
    action: Literal["respond", "clarify", "delegate"] = "respond"
    public_response: str = ""
    clarifying_question: str = ""
    selected_lane: Literal["", "code", "research", "legacy", "incident"] = ""
    why_now: str = ""
    desired_delta: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    stop_and_ask_when: list[str] = Field(default_factory=list)


class DANConversationContext(BaseModel):
    workflow_id: str
    model: str
    requested_mode: str
    normalized_mode: str
    pending_clarification: str | None = None
    facts: DANConversationFacts
    recent_conversation: list[DANConversationMessage] = Field(default_factory=list)
    recent_worker_reports: list[WorkerReport] = Field(default_factory=list)


def _conversation_turn_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["respond", "clarify", "delegate"],
            },
            "public_response": {"type": "string"},
            "clarifying_question": {"type": "string"},
            "selected_lane": {
                "type": "string",
                "enum": ["", "code", "research", "legacy", "incident"],
            },
            "why_now": {"type": "string"},
            "desired_delta": {"type": "string"},
            "success_criteria": {
                "type": "array",
                "items": {"type": "string"},
            },
            "avoid": {
                "type": "array",
                "items": {"type": "string"},
            },
            "stop_and_ask_when": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["action", "public_response"],
    }


def _conversation_turn_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Return one top-level DAN-v2 conversation decision. Keep action=respond for "
            "planning, architecture, status, roadmap, meta questions, or other turns that "
            "do not need execution. Use action=clarify when one concrete missing detail blocks "
            "execution. Use action=delegate only when the user is clearly asking for action "
            "and you can choose exactly one lane: code, research, incident, or legacy."
        ),
        expected_return_shape=json.dumps(
            {
                "action": "respond|clarify|delegate",
                "public_response": "<required>",
                "clarifying_question": "<optional>",
                "selected_lane": "code|research|legacy|incident",
                "why_now": "<optional>",
                "desired_delta": "<optional>",
                "success_criteria": ["<optional>"],
                "avoid": ["<optional>"],
                "stop_and_ask_when": ["<optional>"],
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
    return {}


def _looks_like_code_request(message: str) -> bool:
    lowered = _clean_text(message).lower()
    return any(cue in lowered for cue in _CODE_CUES)


def _looks_like_research_request(message: str) -> bool:
    lowered = _clean_text(message).lower()
    return any(cue in lowered for cue in _RESEARCH_CUES)


def _looks_like_incident_request(message: str) -> bool:
    lowered = _clean_text(message).lower()
    return any(cue in lowered for cue in _INCIDENT_CUES)


def _looks_like_legacy_operator_request(message: str) -> bool:
    lowered = _clean_text(message).lower()
    return any(cue in lowered for cue in _LEGACY_OPERATOR_CUES)


def _fallback_turn_decision(
    *,
    user_message: str,
    pending_clarification: str | None,
    context: DANConversationContext,
) -> DANConversationTurnDecision:
    if pending_clarification:
        return DANConversationTurnDecision(
            action="clarify",
            public_response=(
                "I need one concrete detail before I should keep going."
            ),
            clarifying_question=pending_clarification,
        )
    cleaned = _clean_text(user_message)
    if not cleaned:
        return DANConversationTurnDecision(
            action="respond",
            public_response=(
                "I’m ready. Give me a concrete coding task, research question, incident, or operator action."
            ),
        )
    if _looks_like_incident_request(cleaned):
        return DANConversationTurnDecision(
            action="delegate",
            public_response="I’ll route this through Incident Commander.",
            selected_lane="incident",
            why_now="The user is reporting an operational failure that needs bounded incident handling.",
            desired_delta=cleaned,
            success_criteria=[
                "Classify the incident, choose one bounded next action, verify the result, and stop with an explicit terminal state."
            ],
            avoid=[
                "Do not take rollback, destructive, browser input, desktop input, or outbound messaging actions without approval."
            ],
            stop_and_ask_when=[
                "The failing workflow, run, session, adapter, or approval boundary is ambiguous."
            ],
        )
    if _looks_like_code_request(cleaned):
        return DANConversationTurnDecision(
            action="delegate",
            public_response="I’ll route this through the code lane.",
            selected_lane="code",
            why_now="The user is asking for concrete repo or implementation work.",
            desired_delta=cleaned,
            success_criteria=["Produce the smallest concrete code delta that advances the request."],
            avoid=["Do not broaden scope beyond the stated task."],
            stop_and_ask_when=["A missing repo detail blocks a concrete edit or validation step."],
        )
    if _looks_like_research_request(cleaned):
        return DANConversationTurnDecision(
            action="delegate",
            public_response="I’ll route this through the research lane.",
            selected_lane="research",
            why_now="The user is asking for grounded information or verification.",
            desired_delta=cleaned,
            success_criteria=["Return evidence strong enough to answer the concrete question."],
            avoid=["Do not wander into unrelated background research."],
            stop_and_ask_when=["The scope or comparison target is still ambiguous."],
        )
    if _looks_like_legacy_operator_request(cleaned):
        return DANConversationTurnDecision(
            action="delegate",
            public_response="I’ll route this through the general operator lane.",
            selected_lane="legacy",
            why_now="The request involves broad desktop or browser operation outside code/research.",
            desired_delta=cleaned,
            success_criteria=["Finish one concrete operator action and report the result."],
            avoid=["Do not take risky actions without surfacing the need clearly."],
            stop_and_ask_when=["An approval or missing target blocks the next step."],
        )
    if context.facts.recent_lane:
        return DANConversationTurnDecision(
            action="respond",
            public_response=(
                "I can route this into code, research, incident handling, or general operator work. "
                "Tell me the concrete action you want next."
            ),
        )
    return DANConversationTurnDecision(
        action="respond",
        public_response=(
            "I can answer directly, route work into DAN Code, launch research, handle an incident, or hand a task to the operator lane."
        ),
    )


def _normalize_turn_decision(
    payload: dict[str, Any],
    *,
    user_message: str,
    pending_clarification: str | None,
    context: DANConversationContext,
) -> DANConversationTurnDecision:
    normalized_payload = dict(_parse_payload(payload or {}))
    try:
        decision = DANConversationTurnDecision.model_validate(normalized_payload)
    except Exception:
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification,
            context=context,
        )

    if decision.action == "clarify" and not _clean_text(decision.clarifying_question):
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification or "What exact result do you want next?",
            context=context,
        )
    if decision.action == "delegate" and decision.selected_lane not in {"code", "research", "legacy", "incident"}:
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification,
            context=context,
        )
    return decision.model_copy(
        update={
            "public_response": _clean_text(decision.public_response)
            or (
                "I’ll route this through the selected worker lane."
                if decision.action == "delegate"
                else "Here’s the next step."
            ),
            "clarifying_question": _clean_text(decision.clarifying_question),
            "why_now": _clean_text(decision.why_now),
            "desired_delta": _clean_text(decision.desired_delta) or _clean_text(user_message),
            "success_criteria": _dedupe(list(decision.success_criteria)),
            "avoid": _dedupe(list(decision.avoid)),
            "stop_and_ask_when": _dedupe(list(decision.stop_and_ask_when)),
        }
    )


class DANConversationController:
    """Durable top-level DAN-v2 controller for the app chat plane."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        stream_text_responses: bool = False,
        provider_request_overrides: dict[str, Any] | None = None,
        event_callback=None,
    ) -> None:
        self._worker = build_dan_conversation_worker(
            worker_id="dan.control-plane.conversation",
            model=str(model),
        )
        self._completion_adapter = ProviderCompletionAdapter(
            provider=provider,
            default_model=str(model),
            stream_text_responses=stream_text_responses,
            provider_request_overrides=provider_request_overrides,
            event_callback=event_callback,
        )
        self._runner = DurableAgentRunner(
            completion_provider=self._completion_adapter,
            event_callback=event_callback,
        )

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

    def set_event_callback(self, event_callback) -> None:
        self._completion_adapter.set_event_callback(event_callback)
        self._runner.set_event_callback(event_callback)

    async def decide_user_turn(
        self,
        *,
        session: DurableAgentSessionState | None,
        user_message: str,
        pending_clarification: str | None,
        context: DANConversationContext,
    ) -> tuple[DANConversationTurnDecision, DurableAgentSessionState]:
        durable_session = session or self.create_session(
            metadata={"surface": "dan", "kind": "conversation-controller"}
        )
        request = ExecutionRequest.from_harness(
            task=(
                "Handle the next DAN-v2 user turn. Choose whether to respond directly, "
                "ask one clarifying question, or delegate to exactly one worker lane."
            ),
            acquisition=AcquisitionRequest(
                policy=AcquisitionPolicy(reuse_continuation=False)
            ),
            output_contract=_conversation_turn_contract(),
            input_payload={
                "mode": "user_turn",
                "user_message": user_message,
                "pending_clarification": pending_clarification or "",
                "context": context.model_dump(mode="json"),
            },
            metadata={"surface": "dan", "turn_kind": "user_turn"},
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
        decision: DANConversationTurnDecision,
    ) -> SupervisorBrief | None:
        if decision.action != "delegate" or decision.selected_lane not in {"code", "research", "legacy", "incident"}:
            return None
        return SupervisorBrief(
            lane=decision.selected_lane,
            why_now=decision.why_now,
            desired_delta=decision.desired_delta,
            success_criteria=list(decision.success_criteria),
            avoid=list(decision.avoid),
            stop_and_ask_when=list(decision.stop_and_ask_when),
        )


__all__ = [
    "DANConversationContext",
    "DANConversationController",
    "DANConversationFacts",
    "DANConversationMessage",
    "DANConversationTurnDecision",
    "ReviewDecision",
    "SupervisorBrief",
    "WorkerReport",
    "build_dan_conversation_worker",
]
