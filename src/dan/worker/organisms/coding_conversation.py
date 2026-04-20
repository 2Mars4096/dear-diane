"""Durable conversation controller for the DAN Code orchestrator."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Literal
from urllib.parse import urlparse

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
from dan.worker.core.structured_output import (
    has_structured_output_schema,
    validate_structured_output,
)
from dan.worker.organism_log import organism_event_context, stable_output_contract_id
from dan.worker.organisms.coding_execution import build_coding_orchestrator_worker
from dan.worker.runner import DurableAgentRunner, DurableAgentSessionState
from dan.worker.structured_payload import parse_jsonish_payload

DEFAULT_CONTROL_HEDGE_MAX_ATTEMPTS = 2
DEFAULT_CONTROL_HEDGE_DELAY_SECONDS = 2.0
_PROJECT_MILESTONE_STATUSES = frozenset(
    {"pending", "active", "completed", "blocked"}
)


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


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _resolve_control_hedge_max_attempts(value: int | None) -> int:
    if value is not None:
        return _positive_int(value, default=DEFAULT_CONTROL_HEDGE_MAX_ATTEMPTS)
    return _positive_int(
        os.environ.get("DAN_CODE_CONTROL_HEDGE_MAX_ATTEMPTS"),
        default=DEFAULT_CONTROL_HEDGE_MAX_ATTEMPTS,
    )


def _resolve_control_hedge_delay_seconds(value: float | None) -> float:
    if value is not None:
        return _positive_float(value, default=DEFAULT_CONTROL_HEDGE_DELAY_SECONDS)
    return _positive_float(
        os.environ.get("DAN_CODE_CONTROL_HEDGE_DELAY_SECONDS"),
        default=DEFAULT_CONTROL_HEDGE_DELAY_SECONDS,
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


def _report_is_failed_no_output(report_summary: "CodingConversationReportSummary") -> bool:
    return (
        _clean_text(report_summary.status).lower() != "completed"
        and not _report_has_material_output(report_summary)
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


def _latest_resumable_report_summary(
    context: "CodingConversationContext",
) -> "CodingConversationReportSummary | None":
    for report in reversed(context.recent_reports):
        if _report_is_failed_no_output(report):
            continue
        if _clean_text(report.objective):
            return report
    return None


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


def _benchmark_review_should_stop(
    report_summary: "CodingConversationReportSummary",
    *,
    benchmark_mode: bool,
) -> bool:
    _ = report_summary
    if not benchmark_mode:
        return False
    # Benchmark mode must not turn "a patch exists" into permission to export.
    # The review model has to explicitly stop; otherwise the CLI should keep
    # iterating or return an incomplete result without writing predictions.
    return False


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


def build_coding_project_planner_worker(
    *,
    worker_id: str,
    model: str | None,
) -> WorkerDefinition:
    """Shared DAN Code project planner on the universal-agent substrate."""

    return WorkerDefinition(
        id=worker_id,
        role="coding_project_planner",
        instruction=(
            "You are the DAN Code project planner on top of the universal worker substrate. "
            "Given the user's coding request, recent conversation/report context, and any "
            "existing milestone plan, produce a short rolling milestone plan and choose the "
            "next concrete bounded slice to execute now. Keep plans compact, practical, and "
            "execution-first: usually 1-5 milestones. Reuse and update an existing plan when "
            "it still fits instead of rebuilding it from scratch. Mark already-finished "
            "milestones as completed when the supplied recent reports justify that conclusion. "
            "The active objective must be one concrete bounded milestone, not the whole project."
        ),
        model=model,
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
        hedge_max_attempts: int = DEFAULT_CONTROL_HEDGE_MAX_ATTEMPTS,
        hedge_delay_seconds: float = DEFAULT_CONTROL_HEDGE_DELAY_SECONDS,
        event_callback=None,
    ) -> None:
        self._provider = provider
        self._default_model = str(default_model or "").strip()
        self._stream_text_responses = bool(stream_text_responses)
        self._provider_request_overrides = dict(provider_request_overrides or {})
        self._hedge_max_attempts = max(1, int(hedge_max_attempts))
        self._hedge_delay_seconds = max(0.0, float(hedge_delay_seconds))
        self._event_callback = event_callback
        self._model_call_counter = 0

    def _emit(self, event: str, **payload: Any) -> None:
        if self._event_callback is None:
            return
        self._event_callback({"event": event, **payload})

    def set_event_callback(self, event_callback) -> None:
        self._event_callback = event_callback

    def _next_model_call_id(self) -> str:
        self._model_call_counter += 1
        return f"model-call:{self._model_call_counter:04d}"

    @staticmethod
    def _stringify_message_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return json.dumps(content, ensure_ascii=False, default=str)
        if content is None:
            return ""
        return str(content)

    @classmethod
    def _message_stats(cls, messages: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {
            "system": 0,
            "user": 0,
            "assistant": 0,
            "tool": 0,
            "other": 0,
        }
        char_counts = {key: 0 for key in counts}
        for message in messages:
            role = str(message.get("role") or "other").strip().lower() or "other"
            if role not in counts:
                role = "other"
            text = cls._stringify_message_content(message.get("content"))
            counts[role] += 1
            char_counts[role] += len(text)
        return {
            "message_count": sum(counts.values()),
            "system_message_count": counts["system"],
            "user_message_count": counts["user"],
            "assistant_message_count": counts["assistant"],
            "tool_message_count": counts["tool"],
            "other_message_count": counts["other"],
            "total_input_chars": sum(char_counts.values()),
            "system_chars": char_counts["system"],
            "user_chars": char_counts["user"],
            "assistant_chars": char_counts["assistant"],
            "tool_chars": char_counts["tool"],
            "other_chars": char_counts["other"],
        }

    def _provider_identity_stats(self) -> dict[str, Any]:
        provider_name = type(self._provider).__name__
        timeout_seconds = getattr(self._provider, "_timeout_seconds", None)
        base_url_host: str | None = None
        client = getattr(self._provider, "_client", None)
        base_url = getattr(client, "base_url", None)
        if base_url is not None:
            parsed = urlparse(str(base_url))
            base_url_host = parsed.netloc or None
        return {
            "provider_name": provider_name,
            "provider_base_url_host": base_url_host,
            "request_timeout_seconds": timeout_seconds,
        }

    def _override_stats(self) -> dict[str, Any]:
        extra_body = dict(self._provider_request_overrides.get("extra_body") or {})
        thinking = self._provider_request_overrides.get("thinking", extra_body.get("thinking"))
        reasoning = self._provider_request_overrides.get("reasoning", extra_body.get("reasoning"))
        payload: dict[str, Any] = {}
        if isinstance(thinking, dict):
            thinking_type = str(thinking.get("type") or "").strip()
            if thinking_type:
                payload["override_thinking_type"] = thinking_type
        elif thinking is not None:
            payload["override_thinking_type"] = str(thinking)
        if isinstance(reasoning, dict):
            if "enabled" in reasoning:
                payload["override_reasoning_enabled"] = bool(reasoning.get("enabled"))
            reasoning_type = str(reasoning.get("type") or "").strip()
            if reasoning_type:
                payload["override_reasoning_type"] = reasoning_type
        elif reasoning is not None:
            payload["override_reasoning_enabled"] = bool(reasoning)
        if extra_body:
            payload["override_extra_body_keys"] = sorted(str(key) for key in extra_body)
        return payload

    def _request_event_stats(
        self,
        *,
        messages: list[dict[str, Any]],
        request: CompletionRequest,
        request_mode: str,
    ) -> dict[str, Any]:
        return {
            **self._message_stats(messages),
            **self._provider_identity_stats(),
            **self._override_stats(),
            "request_mode": request_mode,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

    @staticmethod
    def _result_usage_stats(usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            return {}
        return {
            "usage_prompt_tokens": usage.get("prompt_tokens"),
            "usage_completion_tokens": usage.get("completion_tokens"),
            "usage_total_tokens": usage.get("total_tokens"),
            "usage_cached_input_tokens": usage.get("cached_input_tokens"),
        }

    @staticmethod
    def _provider_metadata_stats(provider_metadata: Any) -> dict[str, Any]:
        if not isinstance(provider_metadata, dict):
            return {}
        payload: dict[str, Any] = {
            "provider_metadata_keys": sorted(str(key) for key in provider_metadata),
        }
        request_details = provider_metadata.get("request_details")
        if isinstance(request_details, dict):
            for key in (
                "provider_name",
                "provider_base_url_host",
                "request_timeout_seconds",
                "request_mode",
                "effective_temperature",
                "effective_max_tokens",
                "tool_schema_count",
                "reasoning_enabled",
                "thinking_type",
            ):
                if key in request_details:
                    payload[key] = request_details.get(key)
        family = provider_metadata.get("family")
        if family is not None:
            payload["provider_family"] = family
        return payload

    @classmethod
    def _response_event_stats(cls, result: Any) -> dict[str, Any]:
        return {
            "text_chars": len(str(getattr(result, "text", "") or "")),
            "tool_call_count": len(list(getattr(result, "tool_calls", None) or [])),
            **cls._result_usage_stats(getattr(result, "usage", None)),
            **cls._provider_metadata_stats(getattr(result, "provider_metadata", None)),
        }

    @staticmethod
    def _event_context(
        request: CompletionRequest,
        *,
        worker_id: str | None,
    ) -> dict[str, Any]:
        return {
            **organism_event_context(
                metadata=request.metadata,
                output_contract=request.output_contract,
                worker_id=worker_id,
            ),
            "worker_id": str(worker_id or "").strip() or None,
            "contract_id": stable_output_contract_id(request.output_contract),
        }

    def _is_acceptable_response(
        self,
        *,
        response: CompletionResponse,
        request: CompletionRequest,
    ) -> bool:
        if has_structured_output_schema(request.output_contract):
            return validate_structured_output(
                response.text,
                request.output_contract,
            ).valid
        return bool(_clean_text(response.text))

    async def _complete_attempt(
        self,
        *,
        request: CompletionRequest,
        model: str,
        worker_id: str | None,
        messages: list[dict[str, Any]],
        attempt_index: int,
        model_call_id: str,
        event_context: dict[str, Any],
    ) -> CompletionResponse:
        self._emit(
            "model.requested",
            model=model,
            round=attempt_index,
            tool_count=0,
            model_call_id=model_call_id,
            hedged=attempt_index > 1,
            **self._request_event_stats(
                messages=messages,
                request=request,
                request_mode="complete",
            ),
            **event_context,
        )
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
            round=attempt_index,
            model_call_id=model_call_id,
            tool_calls=[],
            finish_reason=getattr(result, "finish_reason", None),
            text=(result.text or "")[:400],
            streamed=False,
            hedged=attempt_index > 1,
            **self._response_event_stats(result),
            **event_context,
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

    async def _cancel_pending_attempts(
        self,
        tasks: dict[int, asyncio.Task[CompletionResponse]],
        *,
        worker_id: str | None,
        winner_attempt: int,
        event_context: dict[str, Any],
    ) -> None:
        pending_attempts = sorted(
            attempt_index
            for attempt_index, task in tasks.items()
            if not task.done() and attempt_index != winner_attempt
        )
        if pending_attempts:
            self._emit(
                "model.hedge.cancelled",
                cancelled_attempts=pending_attempts,
                winner_attempt=winner_attempt,
                **event_context,
            )
        for attempt_index, task in tasks.items():
            if attempt_index == winner_attempt or task.done():
                continue
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)

    async def _hedged_complete(
        self,
        *,
        request: CompletionRequest,
        model: str,
        worker_id: str | None,
        messages: list[dict[str, Any]],
        event_context: dict[str, Any],
    ) -> CompletionResponse:
        active_tasks: dict[int, asyncio.Task[CompletionResponse]] = {}
        launched_attempts = 0
        fallback_response: CompletionResponse | None = None
        first_exception: BaseException | None = None
        next_launch_deadline: float | None = None
        loop = asyncio.get_running_loop()

        def _launch_attempt(attempt_index: int) -> None:
            nonlocal launched_attempts, next_launch_deadline
            launched_attempts = max(launched_attempts, attempt_index)
            model_call_id = self._next_model_call_id()
            if attempt_index > 1:
                self._emit(
                    "model.hedge.launched",
                    model=model,
                    attempt=attempt_index,
                    launched_attempts=launched_attempts,
                    hedge_delay_seconds=self._hedge_delay_seconds,
                    model_call_id=model_call_id,
                    **event_context,
                )
            active_tasks[attempt_index] = asyncio.create_task(
                self._complete_attempt(
                    request=request,
                    model=model,
                    worker_id=worker_id,
                    messages=messages,
                    attempt_index=attempt_index,
                    model_call_id=model_call_id,
                    event_context=event_context,
                )
            )
            next_launch_deadline = (
                loop.time() + self._hedge_delay_seconds
                if launched_attempts < self._hedge_max_attempts
                else None
            )

        _launch_attempt(1)

        while active_tasks:
            timeout: float | None = None
            if next_launch_deadline is not None:
                timeout = max(0.0, next_launch_deadline - loop.time())
            done, _pending = await asyncio.wait(
                set(active_tasks.values()),
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                if launched_attempts < self._hedge_max_attempts:
                    _launch_attempt(launched_attempts + 1)
                    continue
                done, _pending = await asyncio.wait(
                    set(active_tasks.values()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            for task in done:
                attempt_index = next(
                    key for key, value in active_tasks.items() if value is task
                )
                active_tasks.pop(attempt_index, None)
                try:
                    response = task.result()
                except Exception as exc:
                    if first_exception is None:
                        first_exception = exc
                    continue
                if fallback_response is None:
                    fallback_response = response
                if self._is_acceptable_response(response=response, request=request):
                    self._emit(
                        "model.hedge.accepted",
                        model=model,
                        accepted_attempt=attempt_index,
                        launched_attempts=launched_attempts,
                        used_fallback=False,
                        **event_context,
                    )
                    await self._cancel_pending_attempts(
                        active_tasks,
                        worker_id=worker_id,
                        winner_attempt=attempt_index,
                        event_context=event_context,
                    )
                    return response
            if not active_tasks and launched_attempts < self._hedge_max_attempts:
                _launch_attempt(launched_attempts + 1)

        if fallback_response is not None:
            self._emit(
                "model.hedge.accepted",
                model=model,
                accepted_attempt=None,
                launched_attempts=launched_attempts,
                used_fallback=True,
                **event_context,
            )
            return fallback_response
        if first_exception is not None:
            raise first_exception
        raise RuntimeError("Hedged DAN Code controller completion produced no response")

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        model = str(request.model or self._default_model or "").strip()
        if not model:
            raise ValueError("Coding conversation controller requires a concrete model")
        worker_id = str(request.metadata.get("worker_id") or "").strip() or None
        event_context = self._event_context(request, worker_id=worker_id)
        messages: list[dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.user_prompt})
        stream_method = getattr(self._provider, "stream", None)
        if self._stream_text_responses and callable(stream_method):
            model_call_id = self._next_model_call_id()
            self._emit(
                "model.requested",
                model=model,
                round=1,
                tool_count=0,
                model_call_id=model_call_id,
                **self._request_event_stats(
                    messages=messages,
                    request=request,
                    request_mode="stream",
                ),
                **event_context,
            )
            accumulated = ""
            usage: dict[str, Any] | None = None
            emitted_delta = False
            self._emit(
                "model.stream.started",
                model=model,
                round=1,
                model_call_id=model_call_id,
                **event_context,
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
                            model_call_id=model_call_id,
                            delta=delta,
                            accumulated=accumulated,
                            **event_context,
                        )
                self._emit(
                    "model.stream.completed",
                    model=model,
                    round=1,
                    model_call_id=model_call_id,
                    usage=usage,
                    **self._result_usage_stats(usage),
                    **event_context,
                )
                self._emit(
                    "model.responded",
                    model=model,
                    round=1,
                    model_call_id=model_call_id,
                    tool_calls=[],
                    finish_reason="stream",
                    text=accumulated[:400],
                    streamed=True,
                    text_chars=len(accumulated),
                    tool_call_count=0,
                    **self._result_usage_stats(usage),
                    **event_context,
                )
                self._emit(
                    "completion.completed",
                    model=model,
                    model_call_id=model_call_id,
                    stop_reason="completed",
                    tool_calls_executed=0,
                    **event_context,
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

        completion_model_call_id: str | None = None
        if self._hedge_max_attempts > 1 and self._hedge_delay_seconds >= 0:
            response = await self._hedged_complete(
                request=request,
                model=model,
                worker_id=worker_id,
                messages=messages,
                event_context=event_context,
            )
        else:
            model_call_id = self._next_model_call_id()
            completion_model_call_id = model_call_id
            response = await self._complete_attempt(
                request=request,
                model=model,
                worker_id=worker_id,
                messages=messages,
                attempt_index=1,
                model_call_id=model_call_id,
                event_context=event_context,
            )
        self._emit(
            "completion.completed",
            model=model,
            model_call_id=completion_model_call_id,
            stop_reason="completed",
            tool_calls_executed=0,
            **event_context,
        )
        return response


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


class CodingProjectMilestone(BaseModel):
    """One project-level milestone for DAN Code."""

    milestone_id: str = ""
    title: str = ""
    objective: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: str = "pending"


class CodingProjectPlan(BaseModel):
    """Rolling project plan persisted by DAN Code."""

    project_goal: str = ""
    plan_summary: str = ""
    milestones: list[CodingProjectMilestone] = Field(default_factory=list)
    active_milestone_id: str | None = None


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
    benchmark_mode: bool = False
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


class CodingProjectPlannerContext(BaseModel):
    """Shared shell/runtime context for the project planner."""

    workspace_root: str
    model: str
    thinking_mode: str
    tool_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    pending_clarification: str | None = None
    facts: CodingConversationFacts
    recent_conversation: list[CodingConversationMessage] = Field(default_factory=list)
    recent_reports: list[CodingConversationReportSummary] = Field(default_factory=list)
    existing_plan: CodingProjectPlan | None = None


class CodingProjectPlannerDecision(BaseModel):
    """Project-planner output for the next DAN Code coding slice."""

    public_response: str = ""
    project_goal: str = ""
    plan_summary: str = ""
    milestones: list[CodingProjectMilestone] = Field(default_factory=list)
    active_milestone_id: str | None = None
    active_objective: str = ""
    active_acceptance_criteria: list[str] = Field(default_factory=list)


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
            "build-style tasks failed to produce concrete material output such as files. "
            "If report_summary already shows a completed run with concrete material output "
            "and no blocking error, choose action=done rather than reopening the loop. "
            "When context.facts.benchmark_mode=true, prefer action=done once the run is "
            "completed, produced a concrete candidate/material code change, and you "
            "cannot name a specific unmet contract or blocking validation failure from "
            "report_summary or context. Do not continue only for optional extra "
            "validation, broader exploration, or environment/package cleanup."
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


def _project_planner_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "public_response": {"type": "string"},
            "project_goal": {"type": "string"},
            "plan_summary": {"type": "string"},
            "milestones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "milestone_id": {"type": "string"},
                        "title": {"type": "string"},
                        "objective": {"type": "string"},
                        "acceptance_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "status": {"type": "string"},
                    },
                    "required": ["objective"],
                },
            },
            "active_milestone_id": {"type": "string"},
            "active_objective": {"type": "string"},
            "active_acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["public_response", "milestones"],
    }


def _project_planner_contract() -> OutputContract:
    return OutputContract(
        definition_of_done=(
            "Given the raw user turn, the requested coding objective chosen by the "
            "conversation orchestrator, recent context, and any existing plan, return a "
            "short rolling milestone plan plus the next bounded milestone to execute now. "
            "For small tasks, a single milestone is enough. Reuse and update the existing "
            "plan when it still applies."
        ),
        expected_return_shape=json.dumps(
            {
                "public_response": "<required>",
                "project_goal": "<optional>",
                "plan_summary": "<optional>",
                "milestones": [
                    {
                        "milestone_id": "<optional>",
                        "title": "<optional>",
                        "objective": "<required>",
                        "acceptance_criteria": ["<optional>"],
                        "status": "pending|active|completed|blocked",
                    }
                ],
                "active_milestone_id": "<optional>",
                "active_objective": "<optional>",
                "active_acceptance_criteria": ["<optional>"],
            },
            sort_keys=True,
        ),
        output_schema=_project_planner_schema(),
    )


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _dedupe([value])
    if isinstance(value, (list, tuple, set)):
        return _dedupe([str(item) for item in value])
    return _dedupe([str(value)])


def _normalize_project_plan(
    *,
    project_goal: str,
    plan_summary: str,
    milestones_payload: Any,
    active_milestone_id: str | None,
    fallback_objective: str,
    fallback_acceptance_criteria: list[str],
) -> CodingProjectPlan:
    normalized_milestones: list[CodingProjectMilestone] = []
    active_id = _clean_text(active_milestone_id)
    raw_items = list(milestones_payload) if isinstance(milestones_payload, list) else []
    for index, raw_item in enumerate(raw_items[:5], start=1):
        if not isinstance(raw_item, dict):
            continue
        objective = _clean_text(
            raw_item.get("objective")
            or raw_item.get("brief")
            or raw_item.get("title")
        )
        if not objective:
            continue
        milestone_id = _clean_text(raw_item.get("milestone_id") or raw_item.get("id"))
        if not milestone_id:
            milestone_id = f"m{index}"
        status = _clean_text(raw_item.get("status")).lower() or "pending"
        if status not in _PROJECT_MILESTONE_STATUSES:
            status = "pending"
        normalized_milestones.append(
            CodingProjectMilestone(
                milestone_id=milestone_id,
                title=_clean_text(raw_item.get("title")),
                objective=objective,
                acceptance_criteria=_coerce_text_list(raw_item.get("acceptance_criteria")),
                status=status,
            )
        )

    fallback_goal = _clean_text(project_goal) or fallback_objective
    if not normalized_milestones and fallback_objective:
        normalized_milestones = [
            CodingProjectMilestone(
                milestone_id="m1",
                title="Current slice",
                objective=fallback_objective,
                acceptance_criteria=list(fallback_acceptance_criteria),
                status="active",
            )
        ]
        active_id = "m1"

    if normalized_milestones:
        milestone_ids = {item.milestone_id for item in normalized_milestones}
        if active_id not in milestone_ids:
            preferred = next(
                (
                    item.milestone_id
                    for item in normalized_milestones
                    if item.status == "active"
                ),
                None,
            )
            if preferred is None:
                preferred = next(
                    (
                        item.milestone_id
                        for item in normalized_milestones
                        if item.status not in {"completed", "blocked"}
                    ),
                    normalized_milestones[0].milestone_id,
                )
            active_id = preferred

        normalized_statuses: list[CodingProjectMilestone] = []
        for item in normalized_milestones:
            status = item.status
            if item.milestone_id == active_id and status != "completed":
                status = "active"
            elif status == "active":
                status = "pending"
            normalized_statuses.append(item.model_copy(update={"status": status}))
        normalized_milestones = normalized_statuses

    return CodingProjectPlan(
        project_goal=fallback_goal,
        plan_summary=_clean_text(plan_summary),
        milestones=normalized_milestones,
        active_milestone_id=active_id or None,
    )


def _active_milestone(plan: CodingProjectPlan | None) -> CodingProjectMilestone | None:
    if plan is None or not plan.milestones:
        return None
    active_id = _clean_text(plan.active_milestone_id)
    if active_id:
        for milestone in plan.milestones:
            if (
                milestone.milestone_id == active_id
                and milestone.status not in {"completed", "blocked"}
            ):
                return milestone
    for milestone in plan.milestones:
        if milestone.status == "active":
            return milestone
    for milestone in plan.milestones:
        if milestone.status not in {"completed", "blocked"}:
            return milestone
    return plan.milestones[-1]


def _fallback_project_planner_decision(
    *,
    user_message: str,
    requested_objective: str,
    requested_acceptance_criteria: list[str],
    existing_plan: CodingProjectPlan | None,
) -> CodingProjectPlannerDecision:
    plan = existing_plan
    active = _active_milestone(plan)
    if active is None:
        fallback_objective = _clean_text(requested_objective) or _clean_text(user_message)
        plan = _normalize_project_plan(
            project_goal=fallback_objective,
            plan_summary="Treat this as one bounded milestone for now.",
            milestones_payload=[],
            active_milestone_id=None,
            fallback_objective=fallback_objective,
            fallback_acceptance_criteria=list(requested_acceptance_criteria),
        )
        active = _active_milestone(plan)
    assert plan is not None
    assert active is not None
    milestone_count = len(plan.milestones)
    response = (
        "I’ll continue with the next milestone from the saved project plan."
        if existing_plan is not None and milestone_count > 1
        else "I’ll treat this as one bounded milestone for now."
    )
    return CodingProjectPlannerDecision(
        public_response=response,
        project_goal=plan.project_goal,
        plan_summary=plan.plan_summary,
        milestones=list(plan.milestones),
        active_milestone_id=active.milestone_id,
        active_objective=active.objective,
        active_acceptance_criteria=list(active.acceptance_criteria or requested_acceptance_criteria),
    )


def _normalize_project_planner_decision(
    payload: dict[str, Any],
    *,
    user_message: str,
    requested_objective: str,
    requested_acceptance_criteria: list[str],
    existing_plan: CodingProjectPlan | None,
) -> CodingProjectPlannerDecision:
    normalized_payload = dict(payload or {})
    fallback_objective = _clean_text(requested_objective) or _clean_text(user_message)
    try:
        validated = CodingProjectPlannerDecision.model_validate(normalized_payload)
    except Exception:
        return _fallback_project_planner_decision(
            user_message=user_message,
            requested_objective=requested_objective,
            requested_acceptance_criteria=requested_acceptance_criteria,
            existing_plan=existing_plan,
        )

    plan = _normalize_project_plan(
        project_goal=_clean_text(validated.project_goal) or fallback_objective,
        plan_summary=_clean_text(validated.plan_summary),
        milestones_payload=[item.model_dump(mode="json") for item in validated.milestones],
        active_milestone_id=validated.active_milestone_id,
        fallback_objective=fallback_objective,
        fallback_acceptance_criteria=list(requested_acceptance_criteria),
    )
    active = _active_milestone(plan)
    if active is None:
        return _fallback_project_planner_decision(
            user_message=user_message,
            requested_objective=requested_objective,
            requested_acceptance_criteria=requested_acceptance_criteria,
            existing_plan=existing_plan,
        )

    active_objective = _clean_text(validated.active_objective) or active.objective
    active_acceptance_criteria = _dedupe(
        [
            *list(requested_acceptance_criteria),
            *_coerce_text_list(validated.active_acceptance_criteria),
            *list(active.acceptance_criteria),
        ]
    )
    public_response = (
        _clean_text(validated.public_response)
        or (
            "I split this into milestones and I’m starting with the next bounded slice."
            if len(plan.milestones) > 1
            else "I’ll start with one bounded milestone."
        )
    )
    return CodingProjectPlannerDecision(
        public_response=public_response,
        project_goal=plan.project_goal,
        plan_summary=plan.plan_summary,
        milestones=list(plan.milestones),
        active_milestone_id=active.milestone_id,
        active_objective=active_objective,
        active_acceptance_criteria=active_acceptance_criteria,
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
    latest_report = _latest_resumable_report_summary(context)
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
    benchmark_mode: bool = False,
) -> CodingConversationReviewDecision:
    if _benchmark_review_should_stop(
        report_summary,
        benchmark_mode=benchmark_mode,
    ):
        return CodingConversationReviewDecision(
            action="done",
            public_response=(
                "This bounded coding pass is done and the benchmark artifacts can be "
                "exported now."
            ),
        )
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
    benchmark_mode: bool = False,
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
        return _fallback_review_decision(
            objective=objective,
            report_summary=report_summary,
            benchmark_mode=benchmark_mode,
        )
    decision = decision.model_copy(
        update={
            "public_response": _clean_text(decision.public_response),
            "clarifying_question": _clean_text(decision.clarifying_question),
            "next_objective": _clean_text(decision.next_objective),
            "research_findings": _dedupe(list(decision.research_findings)),
            "repair_brief": _clean_text(decision.repair_brief),
        }
    )
    if (
        decision.action == "continue"
        and _benchmark_review_should_stop(
            report_summary,
            benchmark_mode=benchmark_mode,
        )
    ):
        return CodingConversationReviewDecision(
            action="done",
            public_response=(
                "This bounded coding pass is done and the benchmark artifacts can be "
                "exported now."
            ),
            research_findings=_dedupe(list(decision.research_findings)),
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
    if not _review_requires_more_work(objective, report_summary) and decision.action == "continue":
        return CodingConversationReviewDecision(
            action="done",
            public_response=(
                "This bounded coding pass is done and the benchmark artifacts can be "
                "exported now."
                if benchmark_mode
                else "This bounded coding pass is done."
            ),
            research_findings=_dedupe(list(decision.research_findings)),
        )
    if decision.action == "clarify":
        if not decision.clarifying_question:
            return _fallback_review_decision(
                objective=objective,
                report_summary=report_summary,
                benchmark_mode=benchmark_mode,
            )
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
        hedge_max_attempts: int | None = None,
        hedge_delay_seconds: float | None = None,
        event_callback=None,
    ) -> None:
        self._worker = build_coding_orchestrator_worker(
            worker_id="dan-code.orchestrator",
            model=str(model),
        )
        self._completion_adapter = ProviderCompletionAdapter(
            provider=provider,
            default_model=str(model),
            stream_text_responses=stream_text_responses,
            provider_request_overrides=provider_request_overrides,
            hedge_max_attempts=_resolve_control_hedge_max_attempts(
                hedge_max_attempts
            ),
            hedge_delay_seconds=_resolve_control_hedge_delay_seconds(
                hedge_delay_seconds
            ),
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
                benchmark_mode=bool(context.facts.benchmark_mode),
            ),
            session,
        )


class CodingProjectPlannerController:
    """Durable milestone planner for DAN Code project-sized tasks."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        stream_text_responses: bool = False,
        provider_request_overrides: dict[str, Any] | None = None,
        hedge_max_attempts: int | None = None,
        hedge_delay_seconds: float | None = None,
        event_callback=None,
    ) -> None:
        self._worker = build_coding_project_planner_worker(
            worker_id="dan-code.project-planner",
            model=str(model),
        )
        self._completion_adapter = ProviderCompletionAdapter(
            provider=provider,
            default_model=str(model),
            stream_text_responses=stream_text_responses,
            provider_request_overrides=provider_request_overrides,
            hedge_max_attempts=_resolve_control_hedge_max_attempts(
                hedge_max_attempts
            ),
            hedge_delay_seconds=_resolve_control_hedge_delay_seconds(
                hedge_delay_seconds
            ),
            event_callback=event_callback,
        )
        self._runner = DurableAgentRunner(
            completion_provider=self._completion_adapter,
            event_callback=event_callback,
        )

    def create_session(
        self,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> DurableAgentSessionState:
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

    async def plan_project(
        self,
        *,
        session: DurableAgentSessionState | None,
        user_message: str,
        requested_objective: str,
        requested_acceptance_criteria: list[str],
        context: CodingProjectPlannerContext,
    ) -> tuple[CodingProjectPlannerDecision, DurableAgentSessionState]:
        durable_session = session or self.create_session(
            metadata={"surface": "dan-code", "kind": "project-planner"}
        )
        if bool(context.facts.benchmark_mode):
            return (
                _fallback_project_planner_decision(
                    user_message=user_message,
                    requested_objective=requested_objective,
                    requested_acceptance_criteria=requested_acceptance_criteria,
                    existing_plan=context.existing_plan,
                ),
                durable_session,
            )
        request = ExecutionRequest.from_harness(
            task=(
                "Plan the next DAN Code milestone. Return a short rolling project plan "
                "and the next bounded milestone objective to execute now."
            ),
            acquisition=AcquisitionRequest(
                policy=AcquisitionPolicy(reuse_continuation=False)
            ),
            output_contract=_project_planner_contract(),
            input_payload={
                "mode": "project_planning",
                "user_message": user_message,
                "requested_objective": requested_objective,
                "requested_acceptance_criteria": list(requested_acceptance_criteria),
                "context": context.model_dump(mode="json"),
            },
            metadata={"surface": "dan-code", "turn_kind": "project_planning"},
        )
        self._runner.enqueue_message(durable_session, request)
        result = await self._runner.process_next(self._worker, durable_session)
        payload = _parse_payload(result.outputs if result is not None else {})
        return (
            _normalize_project_planner_decision(
                payload,
                user_message=user_message,
                requested_objective=requested_objective,
                requested_acceptance_criteria=requested_acceptance_criteria,
                existing_plan=context.existing_plan,
            ),
            durable_session,
        )


__all__ = [
    "CodingConversationFacts",
    "CodingConversationContext",
    "CodingConversationController",
    "CodingConversationMessage",
    "CodingProjectMilestone",
    "CodingProjectPlan",
    "CodingProjectPlannerContext",
    "CodingProjectPlannerController",
    "CodingProjectPlannerDecision",
    "CodingConversationReportSummary",
    "CodingConversationReviewDecision",
    "CodingConversationTurnDecision",
]
