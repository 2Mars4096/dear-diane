"""Durable conversation controller for the DAN Code orchestrator."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.providers import LLMProvider
from dan.worker.core.contracts import ExecutionRequest, OutputContract
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.core.model import WorkerDefinition
from dan.worker.runner import DurableAgentRunner, DurableAgentSessionState


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


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {"result": text}
    return dict(parsed) if isinstance(parsed, dict) else {"result": parsed}


def _looks_like_conversational_turn(user_message: str) -> bool:
    normalized = _clean_text(user_message).lower()
    return normalized in {
        "",
        "hi",
        "hello",
        "hey",
        "yo",
        "sup",
        "thanks",
        "thank you",
        "can we chat",
        "are you like claude code",
        "is this like claude code",
        "what can you do",
        "who are you",
        "what do you do",
        "how should i use this",
        "how do i use this",
        "how does this work",
    }


class ProviderCompletionAdapter:
    """Minimal completion adapter for the durable orchestrator agent."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        default_model: str,
        provider_request_overrides: dict[str, Any] | None = None,
        event_callback=None,
    ) -> None:
        self._provider = provider
        self._default_model = str(default_model or "").strip()
        self._provider_request_overrides = dict(provider_request_overrides or {})
        self._event_callback = event_callback

    def _emit(self, event: str, **payload: Any) -> None:
        if self._event_callback is None:
            return
        self._event_callback({"event": event, **payload})

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = str(request.model or self._default_model or "").strip()
        if not model:
            raise ValueError("Coding conversation controller requires a concrete model")
        worker_id = str(request.metadata.get("worker_id") or "").strip() or None
        self._emit(
            "model.requested",
            model=model,
            round=1,
            tool_count=0,
            worker_id=worker_id,
        )
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})
        result = await self._provider.complete(
            messages=messages,
            model=model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            **self._provider_request_overrides,
        )
        self._emit(
            "model.responded",
            model=result.model or model,
            round=1,
            tool_calls=[],
            finish_reason=getattr(result, "finish_reason", None),
            text=(result.text or "")[:400],
            worker_id=worker_id,
        )
        self._emit(
            "completion.completed",
            model=result.model or model,
            stop_reason="completed",
            tool_calls_executed=0,
            worker_id=worker_id,
        )
        return CompletionResponse(
            text=result.text or "",
            raw={
                "provider_result": {
                    "model": result.model,
                    "finish_reason": result.finish_reason,
                    "usage": result.usage,
                    "provider_metadata": result.provider_metadata,
                },
                "raw_assistant_message": result.raw_assistant_message,
            },
        )


class CodingConversationTurnDecision(BaseModel):
    """Orchestrator decision for one user-authored shell turn."""

    action: Literal["respond", "clarify", "code"] = "code"
    public_response: str = ""
    clarifying_question: str = ""
    coding_objective: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    research_findings: list[str] = Field(default_factory=list)
    repair_brief: str = ""


class CodingConversationReviewDecision(BaseModel):
    """Orchestrator decision after one bounded coding run finishes."""

    action: Literal["done", "continue", "clarify"] = "done"
    public_response: str = ""
    clarifying_question: str = ""
    next_objective: str = ""
    research_findings: list[str] = Field(default_factory=list)
    repair_brief: str = ""


class CodingConversationMessage(BaseModel):
    """Minimal conversation packet shared between the CLI shell and orchestrator."""

    role: str
    text: str
    kind: str = "message"


class CodingConversationReportSummary(BaseModel):
    """Compact bounded-run summary routed back into the orchestrator."""

    status: str
    task_id: str
    objective: str
    candidate_id: str | None = None
    change_summary: str = ""
    target_files: list[str] = Field(default_factory=list)
    test_plan: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    error: str | None = None


class CodingConversationContext(BaseModel):
    """Shared shell/runtime context for one orchestrator decision.

    Keep this contract explicit so the CLI, durable orchestrator, and bounded
    coding organism remain stackable modules with a narrow, diagrammable seam.
    """

    workspace_root: str
    model: str
    thinking_mode: str
    tool_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    pending_clarification: str | None = None
    recent_conversation: list[CodingConversationMessage] = Field(default_factory=list)
    recent_reports: list[CodingConversationReportSummary] = Field(default_factory=list)


def _conversation_turn_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Decide whether to respond conversationally, ask one clarifying question, or "
            "launch one bounded coding run. Return action, public_response, optional "
            "clarifying_question, and the bounded coding objective if action=code."
        ),
        expected_return_shape=json.dumps(
            {
                "action": "respond|clarify|code",
                "public_response": "<required>",
                "clarifying_question": "<optional>",
                "coding_objective": "<optional>",
                "acceptance_criteria": ["<optional>"],
                "research_findings": ["<optional>"],
                "repair_brief": "<optional>",
            },
            sort_keys=True,
        ),
    )


def _conversation_review_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Review the bounded coding run and decide whether to declare done, continue "
            "with one more bounded coding pass, or ask one clarifying question."
        ),
        expected_return_shape=json.dumps(
            {
                "action": "done|continue|clarify",
                "public_response": "<required>",
                "clarifying_question": "<optional>",
                "next_objective": "<optional>",
                "research_findings": ["<optional>"],
                "repair_brief": "<optional>",
            },
            sort_keys=True,
        ),
    )


def _orchestrator_worker(model: str) -> WorkerDefinition:
    return WorkerDefinition(
        id="dan-code.orchestrator",
        role="coding_orchestrator",
        instruction=(
            "You are the durable orchestrator for DAN Code. Treat the user as another "
            "agent in the system and respond directly, concretely, and briefly. Decide "
            "whether to answer conversationally, ask one clarifying question, or launch "
            "one bounded coding run. When reviewing coding results, the validator is "
            "evidence only; you decide whether to continue, clarify, or stop."
        ),
        model=model,
    )


def _fallback_turn_decision(
    *,
    user_message: str,
    pending_clarification: str | None,
) -> CodingConversationTurnDecision:
    normalized = _clean_text(user_message).lower()
    if not normalized:
        return CodingConversationTurnDecision(
            action="respond",
            public_response="I’m ready. Give me a coding task or ask a repo question.",
        )
    if normalized.endswith("?") and len(normalized.split()) <= 12:
        return CodingConversationTurnDecision(
            action="respond",
            public_response=(
                "I can help with the repo and with coding work. Ask a concrete codebase "
                "question or give me a bounded implementation task."
            ),
        )
    if pending_clarification and len(normalized.split()) <= 3:
        return CodingConversationTurnDecision(
            action="clarify",
            public_response="I still need one concrete clarification before I should start coding.",
            clarifying_question=pending_clarification,
        )
    return CodingConversationTurnDecision(
        action="code",
        public_response="I’m treating this as a coding request and starting from the current workspace state.",
        coding_objective=_clean_text(user_message),
    )


def _fallback_review_decision(
    *,
    objective: str,
    report_summary: dict[str, Any],
) -> CodingConversationReviewDecision:
    if str(report_summary.get("status") or "").strip() == "completed":
        return CodingConversationReviewDecision(
            action="done",
            public_response="This bounded pass looks good enough to stop here.",
        )
    return CodingConversationReviewDecision(
        action="clarify",
        public_response="I hit a concrete failure and need one clarification before I should keep pushing.",
        clarifying_question=(
            f"Should I keep pushing on this exact objective: {_clean_text(objective)}?"
        ),
    )


def _normalize_turn_decision(
    payload: dict[str, Any],
    *,
    user_message: str,
    pending_clarification: str | None,
) -> CodingConversationTurnDecision:
    try:
        decision = CodingConversationTurnDecision.model_validate(payload)
    except Exception:
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification,
        )
    decision = decision.model_copy(
        update={
            "public_response": _clean_text(decision.public_response),
            "clarifying_question": _clean_text(decision.clarifying_question),
            "coding_objective": _clean_text(decision.coding_objective),
            "acceptance_criteria": _dedupe(list(decision.acceptance_criteria)),
            "research_findings": _dedupe(list(decision.research_findings)),
            "repair_brief": _clean_text(decision.repair_brief),
        }
    )
    if decision.action == "code" and _looks_like_conversational_turn(user_message):
        return decision.model_copy(
            update={
                "action": "respond",
                "coding_objective": "",
                "repair_brief": "",
                "public_response": (
                    decision.public_response
                    or "I can chat about the repo and also launch bounded coding runs when you ask for concrete work."
                ),
            }
        )
    if decision.action == "clarify":
        if not decision.clarifying_question:
            return _fallback_turn_decision(
                user_message=user_message,
                pending_clarification=pending_clarification,
            )
        if not decision.public_response:
            return decision.model_copy(
                update={"public_response": decision.clarifying_question}
            )
        return decision
    if decision.action == "code":
        coding_objective = decision.coding_objective or _clean_text(user_message)
        public_response = (
            decision.public_response
            or "I’m starting one bounded coding run for this request."
        )
        return decision.model_copy(
            update={
                "coding_objective": coding_objective,
                "public_response": public_response,
            }
        )
    return decision.model_copy(
        update={
            "public_response": (
                decision.public_response
                or "I can help with the repo. Give me a coding task or ask a concrete question."
            )
        }
    )


def _normalize_review_decision(
    payload: dict[str, Any],
    *,
    objective: str,
    report_summary: dict[str, Any],
) -> CodingConversationReviewDecision:
    try:
        decision = CodingConversationReviewDecision.model_validate(payload)
    except Exception:
        return _fallback_review_decision(objective=objective, report_summary=report_summary)
    decision = decision.model_copy(
        update={
            "public_response": _clean_text(decision.public_response),
            "clarifying_question": _clean_text(decision.clarifying_question),
            "next_objective": _clean_text(decision.next_objective),
            "research_findings": _dedupe(list(decision.research_findings)),
            "repair_brief": _clean_text(decision.repair_brief),
        }
    )
    if decision.action == "clarify":
        if not decision.clarifying_question:
            return _fallback_review_decision(objective=objective, report_summary=report_summary)
        if not decision.public_response:
            return decision.model_copy(
                update={"public_response": decision.clarifying_question}
            )
        return decision
    if decision.action == "continue":
        next_objective = decision.next_objective or _clean_text(objective)
        public_response = (
            decision.public_response
            or "I see one more bounded repair pass to make before I should stop."
        )
        return decision.model_copy(
            update={
                "next_objective": next_objective,
                "public_response": public_response,
            }
        )
    return decision.model_copy(
        update={
            "public_response": (
                decision.public_response
                or "This bounded coding pass is done."
            )
        }
    )


class CodingConversationController:
    """Durable orchestrator controller for the DAN Code shell."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        provider_request_overrides: dict[str, Any] | None = None,
        event_callback=None,
    ) -> None:
        self._worker = _orchestrator_worker(str(model))
        self._runner = DurableAgentRunner(
            completion_provider=ProviderCompletionAdapter(
                provider=provider,
                default_model=str(model),
                provider_request_overrides=provider_request_overrides,
                event_callback=event_callback,
            )
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

    async def decide_user_turn(
        self,
        *,
        session: DurableAgentSessionState | None,
        user_message: str,
        pending_clarification: str | None,
        context: CodingConversationContext,
    ) -> tuple[CodingConversationTurnDecision, DurableAgentSessionState]:
        durable_session = session or self.create_session(
            metadata={"surface": "dan-code", "kind": "conversation"}
        )
        request = ExecutionRequest.from_harness(
            task=(
                "Handle the next DAN Code user turn. Decide whether to respond "
                "conversationally, ask one clarifying question, or launch one bounded coding run."
            ),
            output_contract=_conversation_turn_contract(),
            input_payload={
                "mode": "user_turn",
                "user_message": user_message,
                "pending_clarification": pending_clarification or "",
                "context": context.model_dump(mode="json"),
            },
            metadata={"surface": "dan-code", "turn_kind": "user_turn"},
        )
        self._runner.enqueue_message(durable_session, request)
        result = await self._runner.process_next(self._worker, durable_session)
        payload = _parse_payload(result.outputs if result is not None else {})
        return (
            _normalize_turn_decision(
                payload,
                user_message=user_message,
                pending_clarification=pending_clarification,
            ),
            durable_session,
        )

    async def review_coding_result(
        self,
        *,
        session: DurableAgentSessionState,
        objective: str,
        report_summary: CodingConversationReportSummary,
        context: CodingConversationContext,
    ) -> tuple[CodingConversationReviewDecision, DurableAgentSessionState]:
        request = ExecutionRequest.from_harness(
            task=(
                "Review the bounded coding run. Decide whether to stop, continue with "
                "one more bounded coding pass, or ask one clarifying question."
            ),
            output_contract=_conversation_review_contract(),
            input_payload={
                "mode": "post_run_review",
                "objective": objective,
                "report_summary": report_summary.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
            },
            metadata={"surface": "dan-code", "turn_kind": "post_run_review"},
        )
        self._runner.enqueue_message(session, request)
        result = await self._runner.process_next(self._worker, session)
        payload = _parse_payload(result.outputs if result is not None else {})
        return (
            _normalize_review_decision(
                payload,
                objective=objective,
                report_summary=report_summary,
            ),
            session,
        )


__all__ = [
    "CodingConversationContext",
    "CodingConversationController",
    "CodingConversationMessage",
    "CodingConversationReportSummary",
    "CodingConversationReviewDecision",
    "CodingConversationTurnDecision",
]
