"""Durable conversation controller for the DAN Code orchestrator."""

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
from dan.worker.core.interfaces import CompletionRequest, CompletionResponse
from dan.worker.core.model import WorkerDefinition
from dan.worker.runner import DurableAgentRunner, DurableAgentSessionState
from dan.worker.structured_payload import parse_jsonish_payload


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


_DECISION_KEYS = frozenset(
    {
        "action",
        "public_response",
        "clarifying_question",
        "coding_objective",
        "next_objective",
    }
)


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
        if any(
            key in payload
            for key in _DECISION_KEYS
        ):
            return payload
        nested = payload.get("result", payload.get("text"))
        if nested is not None and nested is not raw:
            return _parse_payload(nested)
        return payload
    parsed = parse_jsonish_payload(raw)
    if isinstance(parsed, dict):
        return dict(parsed)
    text = str(parsed or "").strip()
    if not text:
        return {}
    return {"public_response": text}


def _report_has_material_output(report_summary: "CodingConversationReportSummary") -> bool:
    return bool(
        report_summary.candidate_id
        or report_summary.target_files
        or _clean_text(report_summary.change_summary)
    )


def _looks_like_user_question(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    return "?" in cleaned


def _looks_like_continuation_request(text: str) -> bool:
    cleaned = _clean_text(text).lower()
    if not cleaned:
        return False
    cues = (
        "continue",
        "retry",
        "try again",
        "one more attempt",
        "another attempt",
        "resume",
        "make the fix",
        "fix it",
        "fix the",
        "please implement",
        "implement it",
        "go ahead",
        "proceed",
        "finish it",
        "complete it",
    )
    return any(cue in cleaned for cue in cues)


def _looks_like_resume_request(text: str) -> bool:
    cleaned = _clean_text(text).lower()
    if not cleaned:
        return False
    cues = (
        "continue",
        "retry",
        "try again",
        "one more attempt",
        "another attempt",
        "resume",
        "go ahead",
        "proceed",
        "finish it",
        "complete it",
        "keep working",
    )
    return any(cue in cleaned for cue in cues)


def _contains_code_reference(text: str) -> bool:
    cleaned = _clean_text(text).lower()
    if not cleaned:
        return False
    if "/" in cleaned or "\\" in cleaned:
        return True
    if any(
        ext in cleaned
        for ext in (
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".css",
            ".html",
            ".md",
            ".json",
            ".yaml",
            ".yml",
            ".sh",
        )
    ):
        return True
    code_cues = (
        " file ",
        " files ",
        " test ",
        " tests ",
        " bug ",
        " bugs ",
        " repo ",
        " code ",
        " function ",
        " class ",
        " module ",
        " component ",
        " page ",
        " website ",
        " navbar ",
        " validator ",
        " runtime ",
        " cli ",
        " orchestrator ",
        " agent ",
    )
    padded = f" {cleaned} "
    return any(cue in padded for cue in code_cues)


def _looks_like_explicit_coding_request(
    text: str,
    *,
    context: "CodingConversationContext",
) -> bool:
    cleaned = _clean_text(text).lower()
    if not cleaned:
        return False
    if _looks_like_user_question(cleaned):
        return False
    meta_cues = (
        "current status",
        "latest status",
        "show me the results",
        "tell me the results",
        "workspace root",
        "working directory",
        "what happened",
    )
    if any(cue in cleaned for cue in meta_cues):
        return False
    latest_report = _latest_report_summary(context)
    if _looks_like_resume_request(cleaned):
        return latest_report is not None and bool(_clean_text(latest_report.objective))
    action_cues = (
        "fix",
        "edit",
        "patch",
        "update",
        "change",
        "implement",
        "add",
        "remove",
        "rename",
        "refactor",
        "debug",
        "repair",
        "optimize",
        "speed up",
        "clean up",
    )
    if not any(cue in cleaned for cue in action_cues):
        return False
    return _contains_code_reference(cleaned)


def _latest_report_summary(
    context: "CodingConversationContext",
) -> "CodingConversationReportSummary | None":
    if context.recent_reports:
        return context.recent_reports[-1]
    if not _clean_text(context.facts.latest_report_objective):
        return None
    return CodingConversationReportSummary(
        status=_clean_text(context.facts.latest_report_status),
        task_id="",
        objective=_clean_text(context.facts.latest_report_objective),
        candidate_id=None,
        change_summary="",
        target_files=list(context.facts.latest_report_target_files),
        test_plan=[],
        risks=[],
        error=_clean_text(context.facts.latest_report_error),
    )


def _review_requires_more_work(
    objective: str,
    report_summary: "CodingConversationReportSummary",
) -> bool:
    _ = objective
    if _clean_text(report_summary.error):
        return True
    if _clean_text(report_summary.status).lower() != "completed":
        return True
    return not _report_has_material_output(report_summary)


def _continue_review_response(report_summary: "CodingConversationReportSummary") -> str:
    if _report_has_material_output(report_summary):
        return (
            "This bounded pass produced material output, but it is not validated enough "
            "to stop yet, so I need another bounded repair pass."
        )
    return (
        "This bounded pass did not produce a concrete validated result yet, "
        "so I need another bounded repair pass."
    )


def _fast_path_turn_decision(
    *,
    user_message: str,
    pending_clarification: str | None,
    context: "CodingConversationContext",
) -> "CodingConversationTurnDecision | None":
    if pending_clarification:
        return None
    if not _looks_like_explicit_coding_request(user_message, context=context):
        return None
    latest_report = _latest_report_summary(context)
    if latest_report is not None and _looks_like_resume_request(user_message):
        objective = _clean_text(latest_report.objective)
        if objective:
            return CodingConversationTurnDecision(
                action="code",
                public_response=(
                    "I’ll continue from the latest coding objective and start another "
                    "bounded pass."
                ),
                coding_objective=objective,
                repair_brief=_clean_text(latest_report.error),
            )
    objective = _clean_text(user_message)
    if not objective:
        return None
    return CodingConversationTurnDecision(
        action="code",
        public_response="I’m starting one bounded coding run for this request.",
        coding_objective=objective,
    )


def _fast_path_review_decision(
    *,
    objective: str,
    report_summary: "CodingConversationReportSummary",
) -> "CodingConversationReviewDecision | None":
    if _review_requires_more_work(objective, report_summary):
        return None
    return CodingConversationReviewDecision(
        action="done",
        public_response="This bounded coding pass is done.",
    )


class ProviderCompletionAdapter:
    """Minimal completion adapter for the durable orchestrator agent."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        default_model: str,
        stream_text_responses: bool = False,
        provider_request_overrides: dict[str, Any] | None = None,
        event_callback=None,
    ) -> None:
        self._provider = provider
        self._default_model = str(default_model or "").strip()
        self._stream_text_responses = bool(stream_text_responses)
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
        streamed = False
        stream_method = getattr(self._provider, "stream", None)
        if self._stream_text_responses and callable(stream_method):
            accumulated = ""
            usage: dict[str, Any] | None = None
            emitted_delta = False
            self._emit(
                "model.stream.started",
                model=model,
                round=1,
                worker_id=worker_id,
            )
            try:
                async for chunk in stream_method(
                    messages=messages,
                    model=model,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    **self._provider_request_overrides,
                ):
                    delta = str(getattr(chunk, "delta", "") or "")
                    accumulated = str(
                        getattr(chunk, "accumulated", accumulated + delta) or accumulated + delta
                    )
                    usage_candidate = getattr(chunk, "usage", None)
                    if usage_candidate:
                        usage = dict(usage_candidate)
                    if delta:
                        emitted_delta = True
                        self._emit(
                            "model.stream.delta",
                            model=model,
                            round=1,
                            delta=delta,
                            accumulated=accumulated,
                            worker_id=worker_id,
                        )
                self._emit(
                    "model.stream.completed",
                    model=model,
                    round=1,
                    usage=usage,
                    worker_id=worker_id,
                )
                streamed = True
                self._emit(
                    "model.responded",
                    model=model,
                    round=1,
                    tool_calls=[],
                    finish_reason="stream",
                    text=accumulated[:400],
                    streamed=True,
                    worker_id=worker_id,
                )
                self._emit(
                    "completion.completed",
                    model=model,
                    stop_reason="completed",
                    tool_calls_executed=0,
                    worker_id=worker_id,
                )
                return CompletionResponse(
                    text=accumulated,
                    raw={
                        "provider_result": {
                            "model": model,
                            "finish_reason": "stream",
                            "usage": usage,
                            "provider_metadata": {"streamed_response": True},
                        },
                        "raw_assistant_message": {
                            "role": "assistant",
                            "content": accumulated or None,
                        },
                    },
                )
            except Exception:
                if emitted_delta:
                    raise

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
            streamed=streamed,
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


class CodingConversationFacts(BaseModel):
    """Shared high-signal runtime/session facts for orchestrator reasoning."""

    product_name: str
    workspace_root: str
    effective_working_directory: str
    shell_process_directory: str
    session_id: str
    active_model: str = ""
    thinking_mode: str = ""
    approval_mode: str = ""
    enabled_tools: list[str] = Field(default_factory=list)
    coding_turn_count: int = 0
    conversation_message_count: int = 0
    pending_clarification: str | None = None
    latest_report_status: str = ""
    latest_report_objective: str = ""
    latest_report_target_files: list[str] = Field(default_factory=list)
    latest_report_error: str | None = None
    current_timestamp: str = ""
    current_date: str = ""
    timezone: str = ""


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
    facts: CodingConversationFacts
    recent_conversation: list[CodingConversationMessage] = Field(default_factory=list)
    recent_reports: list[CodingConversationReportSummary] = Field(default_factory=list)


def _conversation_turn_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["respond", "clarify", "code"],
            },
            "public_response": {"type": "string"},
            "clarifying_question": {"type": "string"},
            "coding_objective": {"type": "string"},
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
            },
            "research_findings": {
                "type": "array",
                "items": {"type": "string"},
            },
            "repair_brief": {"type": "string"},
        },
        "required": ["action", "public_response"],
    }


def _conversation_review_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["done", "continue", "clarify"],
            },
            "public_response": {"type": "string"},
            "clarifying_question": {"type": "string"},
            "next_objective": {"type": "string"},
            "research_findings": {
                "type": "array",
                "items": {"type": "string"},
            },
            "repair_brief": {"type": "string"},
        },
        "required": ["action", "public_response"],
    }


def _conversation_turn_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Decide whether to respond conversationally, ask one clarifying question, or "
            "launch one bounded coding run. Only choose action=code when the user has "
            "actually given a directly actionable repo/coding task. For social chatter, "
            "meta questions, or ambiguous non-task turns, prefer respond or clarify. "
            "If the user is asking about current status, current progress, what's next, "
            "workspace root, working directory, latest results, or what happened in the "
            "last run, answer from context.facts and context.recent_reports and keep "
            "action=respond. Do not launch coding just to inspect or restate facts already "
            "present in the supplied context. Resolve "
            "phrases like 'the website we talked about' against recent conversation/report "
            "context; if the referent is still under-specified, ask one clarifying question "
            "instead of launching coding. If you are asking the user to choose a next step "
            "or provide missing information before work can continue, that is action=clarify, "
            "and you must put the concrete user-facing question in clarifying_question "
            "instead of hiding it inside public_response. "
            "Return action, public_response, optional clarifying_question, and the "
            "bounded coding objective if action=code."
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
        output_schema=_conversation_turn_schema(),
    )


def _conversation_review_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Review the bounded coding run and decide whether to declare done, continue "
            "with one more bounded coding pass, or ask one clarifying question. Do not "
            "declare done when the run failed, produced no validated candidate, or for "
            "build-style tasks failed to produce concrete material output such as files."
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
        output_schema=_conversation_review_schema(),
    )


def _orchestrator_worker(model: str) -> WorkerDefinition:
    return WorkerDefinition(
        id="dan-code.orchestrator",
        role="coding_orchestrator",
        instruction=(
            "You are the durable orchestrator for DAN Code. Treat the user as another "
            "agent in the system and respond directly, concretely, and briefly. Decide "
            "whether to answer conversationally, ask one clarifying question, or launch "
            "one bounded coding run. Only launch coding when the user has given a direct, "
            "actionable repo or implementation task. For social chatter, acknowledgements, "
            "meta discussion, or ambiguous non-task turns, respond conversationally or ask "
            "for clarification instead of starting the worker pool. Use the provided "
            "runtime/session facts and recent reports when the user asks about status, "
            "progress, what's next, workspace, current working directory, latest results, "
            "or what happened in the last run. Those turns should normally stay in respond "
            "mode rather than launch coding. Resolve references against recent conversation and report context "
            "before coding; if a request still depends on an unresolved prior discussion, "
            "ask for clarification. If you ask the user to choose between next steps or give "
            "missing information, set action=clarify and put that concrete question in "
            "clarifying_question. When reviewing coding results, the "
            "validator is evidence only; you decide whether to continue, clarify, or stop. "
            "A build-style task with no concrete output is not done."
        ),
        model=model,
    )


def _fallback_turn_decision(
    *,
    user_message: str,
    pending_clarification: str | None,
    context: CodingConversationContext,
) -> CodingConversationTurnDecision:
    if not _clean_text(user_message):
        return CodingConversationTurnDecision(
            action="respond",
            public_response="I’m ready. Give me a coding task or ask a repo question.",
        )
    if pending_clarification:
        return CodingConversationTurnDecision(
            action="clarify",
            public_response="I still need one concrete clarification before I should start coding.",
            clarifying_question=pending_clarification,
        )
    latest_report = _latest_report_summary(context)
    if latest_report is not None and _looks_like_continuation_request(user_message):
        objective = _clean_text(latest_report.objective)
        if objective:
            return CodingConversationTurnDecision(
                action="code",
                public_response=(
                    "I’ll continue from the latest coding objective and try another "
                    "bounded repair pass."
                ),
                coding_objective=objective,
                repair_brief=_clean_text(latest_report.error),
            )
    return CodingConversationTurnDecision(
        action="respond",
        public_response=(
            "I can chat about the repo and I can also launch bounded coding work. "
            "Tell me what you want me to build, inspect, explain, or fix."
        ),
    )


def _fallback_review_decision(
    *,
    objective: str,
    report_summary: CodingConversationReportSummary,
) -> CodingConversationReviewDecision:
    if not _review_requires_more_work(objective, report_summary):
        return CodingConversationReviewDecision(
            action="done",
            public_response="This bounded pass looks good enough to stop here.",
        )
    return CodingConversationReviewDecision(
        action="continue",
        public_response=_continue_review_response(report_summary),
        next_objective=_clean_text(objective),
        repair_brief=_clean_text(report_summary.error)
        or "The last pass ended without concrete validated output.",
    )


def _normalize_turn_decision(
    payload: dict[str, Any],
    *,
    user_message: str,
    pending_clarification: str | None,
    context: CodingConversationContext,
) -> CodingConversationTurnDecision:
    normalized_payload = dict(payload or {})
    if "action" not in normalized_payload or not _clean_text(normalized_payload.get("action")):
        if _clean_text(normalized_payload.get("clarifying_question")):
            normalized_payload["action"] = "clarify"
        elif _clean_text(normalized_payload.get("coding_objective")):
            normalized_payload["action"] = "code"
        elif _clean_text(normalized_payload.get("public_response")):
            normalized_payload["action"] = "respond"
    try:
        decision = CodingConversationTurnDecision.model_validate(normalized_payload)
    except Exception:
        return _fallback_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification,
            context=context,
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
    if decision.action == "clarify":
        if not decision.clarifying_question:
            return _fallback_turn_decision(
                user_message=user_message,
                pending_clarification=pending_clarification,
                context=context,
            )
        if not decision.public_response:
            return decision.model_copy(
                update={"public_response": decision.clarifying_question}
            )
        return decision
    if decision.action == "respond" and not decision.clarifying_question and _looks_like_user_question(
        decision.public_response
    ):
        return decision.model_copy(
            update={
                "action": "clarify",
                "clarifying_question": decision.public_response,
            }
        )
    if decision.action == "code":
        coding_objective = decision.coding_objective or _clean_text(user_message)
        public_response = (
            decision.public_response
            or "I’m starting one bounded coding run for this request."
        )
        if not coding_objective:
            return _fallback_turn_decision(
                user_message=user_message,
                pending_clarification=pending_clarification,
                context=context,
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
    report_summary: CodingConversationReportSummary,
) -> CodingConversationReviewDecision:
    normalized_payload = dict(payload or {})
    if "action" not in normalized_payload or not _clean_text(normalized_payload.get("action")):
        if _clean_text(normalized_payload.get("clarifying_question")):
            normalized_payload["action"] = "clarify"
        elif _clean_text(normalized_payload.get("next_objective")) or _clean_text(
            normalized_payload.get("repair_brief")
        ):
            normalized_payload["action"] = "continue"
        elif _clean_text(normalized_payload.get("public_response")):
            normalized_payload["action"] = (
                "continue"
                if _review_requires_more_work(objective, report_summary)
                else "done"
            )
    try:
        decision = CodingConversationReviewDecision.model_validate(normalized_payload)
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
    if _review_requires_more_work(objective, report_summary) and decision.action == "done":
        return CodingConversationReviewDecision(
            action="continue",
            public_response=_continue_review_response(report_summary),
            next_objective=_clean_text(objective),
            repair_brief=(
                _clean_text(report_summary.error)
                or _clean_text(decision.repair_brief)
                or "The last pass ended without concrete validated output."
            ),
            research_findings=_dedupe(list(decision.research_findings)),
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
        stream_text_responses: bool = False,
        provider_request_overrides: dict[str, Any] | None = None,
        event_callback=None,
    ) -> None:
        self._worker = _orchestrator_worker(str(model))
        self._runner = DurableAgentRunner(
            completion_provider=ProviderCompletionAdapter(
                provider=provider,
                default_model=str(model),
                stream_text_responses=stream_text_responses,
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
        fast_path = _fast_path_turn_decision(
            user_message=user_message,
            pending_clarification=pending_clarification,
            context=context,
        )
        if fast_path is not None:
            return fast_path, durable_session
        request = ExecutionRequest.from_harness(
            task=(
                "Handle the next DAN Code user turn. Decide whether to respond "
                "conversationally, ask one clarifying question, or launch one bounded coding run."
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
                context=context,
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
        fast_path = _fast_path_review_decision(
            objective=objective,
            report_summary=report_summary,
        )
        if fast_path is not None:
            return fast_path, session
        request = ExecutionRequest.from_harness(
            task=(
                "Review the bounded coding run. Decide whether to stop, continue with "
                "one more bounded coding pass, or ask one clarifying question."
            ),
            acquisition=AcquisitionRequest(
                policy=AcquisitionPolicy(reuse_continuation=False)
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
    "CodingConversationFacts",
    "CodingConversationContext",
    "CodingConversationController",
    "CodingConversationMessage",
    "CodingConversationReportSummary",
    "CodingConversationReviewDecision",
    "CodingConversationTurnDecision",
]
