"""Canonical helpers for no-tool tool-loop continuation and terminal decisions."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, AsyncIterator, Callable, Literal, Sequence

from dan.agent_runtime.completion import (
    GuardedCompletionForwardedStep,
    forward_guarded_steps,
)
from dan.chat_events import ChatStreamEvent
from dan.providers import CompletionResult

logger = logging.getLogger(__name__)

_TRUNCATION_CONTINUE_PROMPT = (
    "Continue from where you left off. Keep using file_write to save your output."
)
_TRUNCATION_FAILURE_TEMPLATE = (
    "Response was truncated and continuation failed ({error_type}). Please try again."
)
_MISSING_ACTION_FAILURE_TEXT = (
    "I could not complete the required tool action. Please try again or narrow the request."
)
_EMPTY_RESPONSE_TEXT = (
    "I wasn't able to generate a response. Please try rephrasing your request."
)

ToolRequestConfig = Callable[[bool], tuple[list[dict[str, Any]], str | dict[str, Any]]]
ContinuationRunner = Callable[
    [dict[str, Any], str | Callable[[], str] | None],
    AsyncIterator[ChatStreamEvent | CompletionResult],
]
InterruptedContentBuilder = Callable[[Sequence[str]], str]
UsageMerger = Callable[[dict[str, Any] | None, dict[str, Any] | None], dict[str, Any] | None]
UsageNormalizer = Callable[[dict[str, Any] | None], dict[str, Any]]


@dataclass(frozen=True)
class NoToolTurnResolution:
    """Resolved state after handling a tool-loop turn with no tool or mutation calls."""

    action: Literal["continue", "interrupt", "complete"]
    result: CompletionResult | None
    messages: list[dict[str, Any]]
    combined_text_parts: tuple[str, ...]
    usage_totals: dict[str, Any] | None
    completion_review_requested: bool
    terminal_content: str = ""
    terminal_token_usage: dict[str, Any] | None = None
    terminal_raw_assistant_message: dict[str, Any] | None = None
    use_terminal_reply_helper: bool = False


def _append_partial_text(
    *,
    messages: list[dict[str, Any]],
    combined_text_parts: list[str],
    partial: str,
    add_assistant_message: bool,
) -> None:
    if partial:
        combined_text_parts.append(partial)
    if add_assistant_message and partial.strip():
        messages.append({"role": "assistant", "content": partial})


def _resolution(
    *,
    action: Literal["continue", "interrupt", "complete"],
    result: CompletionResult | None,
    messages: list[dict[str, Any]],
    combined_text_parts: list[str],
    usage_totals: dict[str, Any] | None,
    completion_review_requested: bool,
    terminal_content: str = "",
    terminal_token_usage: dict[str, Any] | None = None,
    terminal_raw_assistant_message: dict[str, Any] | None = None,
    use_terminal_reply_helper: bool = False,
) -> NoToolTurnResolution:
    return NoToolTurnResolution(
        action=action,
        result=result,
        messages=list(messages),
        combined_text_parts=tuple(combined_text_parts),
        usage_totals=usage_totals,
        completion_review_requested=completion_review_requested,
        terminal_content=terminal_content,
        terminal_token_usage=terminal_token_usage,
        terminal_raw_assistant_message=terminal_raw_assistant_message,
        use_terminal_reply_helper=use_terminal_reply_helper,
    )


async def _run_continuation(
    *,
    messages: list[dict[str, Any]],
    combined_text_parts: list[str],
    usage_totals: dict[str, Any] | None,
    completion_review_requested: bool,
    turn_index: int,
    model: str,
    completion_max_tokens: int,
    force_file_write_next_turn: bool,
    tool_request_config: ToolRequestConfig,
    run_continuation: ContinuationRunner,
    merge_usage_totals: UsageMerger,
    interrupted_content: str | Callable[[], str] | None,
    empty_result_error: str,
    success_log_prefix: str = "",
    logger_override: logging.Logger | None = None,
) -> AsyncIterator[GuardedCompletionForwardedStep | NoToolTurnResolution]:
    log = logger_override or logger
    continuation_tools, continuation_tool_choice = tool_request_config(force_file_write_next_turn)
    relay = forward_guarded_steps(
        run_continuation(
            {
                "messages": messages,
                "model": model,
                "temperature": 0.7,
                "max_tokens": completion_max_tokens,
                "tools": continuation_tools,
                "tool_choice": continuation_tool_choice,
            },
            interrupted_content,
        ),
    )
    async for step in relay:
        yield step

    continuation_outcome = relay.outcome_or_empty()
    if continuation_outcome.interrupted:
        yield _resolution(
            action="interrupt",
            result=None,
            messages=messages,
            combined_text_parts=combined_text_parts,
            usage_totals=usage_totals,
            completion_review_requested=completion_review_requested,
        )
        return

    continuation_result = continuation_outcome.result
    if continuation_result is None:
        raise RuntimeError(empty_result_error)

    usage_totals = merge_usage_totals(usage_totals, continuation_result.usage)
    if success_log_prefix:
        continuation_finish_reason = getattr(continuation_result, "finish_reason", "") or ""
        continuation_usage = continuation_result.usage or {}
        log.info(
            "%s turn %d: finish_reason=%s, has_text=%s, has_tools=%s, "
            "prompt_tokens=%s, completion_tokens=%s",
            success_log_prefix,
            turn_index,
            continuation_finish_reason or "n/a",
            bool((continuation_result.text or "").strip()),
            bool(continuation_result.tool_calls),
            continuation_usage.get("prompt_tokens", "?"),
            continuation_usage.get("completion_tokens", "?"),
        )

    yield _resolution(
        action="continue",
        result=continuation_result,
        messages=messages,
        combined_text_parts=combined_text_parts,
        usage_totals=usage_totals,
        completion_review_requested=completion_review_requested,
    )


async def resolve_no_tool_turn(
    *,
    result: CompletionResult,
    messages: list[dict[str, Any]],
    combined_text_parts: list[str],
    usage_totals: dict[str, Any] | None,
    completion_review_requested: bool,
    turn_index: int,
    max_tool_turns: int,
    model: str,
    completion_max_tokens: int,
    force_file_write_next_turn: bool,
    missing_action_hints: Sequence[str],
    autonomy_level: str,
    tool_request_config: ToolRequestConfig,
    run_continuation: ContinuationRunner,
    merge_usage_totals: UsageMerger,
    normalize_usage: UsageNormalizer,
    retry_prompt_builder: Callable[[list[str]], str],
    interrupted_content_builder: InterruptedContentBuilder,
    review_interrupted_content_builder: InterruptedContentBuilder,
    compact_context: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]],
    logger_override: logging.Logger | None = None,
) -> AsyncIterator[GuardedCompletionForwardedStep | NoToolTurnResolution]:
    """Resolve the no-tool branch of a tool loop, including continuations."""
    log = logger_override or logger
    working_messages = list(messages)
    working_text_parts = list(combined_text_parts)
    review_requested = completion_review_requested
    finish_reason = getattr(result, "finish_reason", "") or ""
    was_truncated = finish_reason in ("length", "max_tokens")

    if was_truncated and turn_index < max_tool_turns - 1:
        log.info(
            "Turn %d: finish_reason=%s, output truncated — injecting continuation",
            turn_index,
            finish_reason,
        )
        partial = result.text or ""
        _append_partial_text(
            messages=working_messages,
            combined_text_parts=working_text_parts,
            partial=partial,
            add_assistant_message=True,
        )
        working_messages.append({"role": "user", "content": _TRUNCATION_CONTINUE_PROMPT})
        working_messages = compact_context(working_messages, model)
        try:
            async for item in _run_continuation(
                messages=working_messages,
                combined_text_parts=working_text_parts,
                usage_totals=usage_totals,
                completion_review_requested=review_requested,
                turn_index=turn_index,
                model=model,
                completion_max_tokens=completion_max_tokens,
                force_file_write_next_turn=force_file_write_next_turn,
                tool_request_config=tool_request_config,
                run_continuation=run_continuation,
                merge_usage_totals=merge_usage_totals,
                interrupted_content=lambda: interrupted_content_builder(working_text_parts),
                empty_result_error="Continuation completion produced no result",
                success_log_prefix="Continuation",
                logger_override=log,
            ):
                yield item
            return
        except Exception as exc:
            log.warning("Continuation call failed: %s", exc)
            content = "\n\n".join(working_text_parts) if working_text_parts else ""
            if not content.strip():
                content = _TRUNCATION_FAILURE_TEMPLATE.format(
                    error_type=type(exc).__name__,
                )
            yield _resolution(
                action="complete",
                result=result,
                messages=working_messages,
                combined_text_parts=working_text_parts,
                usage_totals=usage_totals,
                completion_review_requested=review_requested,
                terminal_content=content,
                terminal_token_usage={},
            )
            return

    if missing_action_hints:
        if turn_index < max_tool_turns - 1:
            log.info(
                "Turn %d: required actions still missing (%s) — requesting another tool call",
                turn_index,
                ", ".join(missing_action_hints),
            )
            partial = result.text or ""
            _append_partial_text(
                messages=working_messages,
                combined_text_parts=working_text_parts,
                partial=partial,
                add_assistant_message=True,
            )
            working_messages.append({
                "role": "user",
                "content": retry_prompt_builder(list(missing_action_hints)),
            })
            working_messages = compact_context(working_messages, model)
            try:
                async for item in _run_continuation(
                    messages=working_messages,
                    combined_text_parts=working_text_parts,
                    usage_totals=usage_totals,
                    completion_review_requested=review_requested,
                    turn_index=turn_index,
                    model=model,
                    completion_max_tokens=completion_max_tokens,
                    force_file_write_next_turn=force_file_write_next_turn,
                    tool_request_config=tool_request_config,
                    run_continuation=run_continuation,
                    merge_usage_totals=merge_usage_totals,
                    interrupted_content=lambda: interrupted_content_builder(working_text_parts),
                    empty_result_error="Required-action continuation produced no result",
                    success_log_prefix="Required-action continuation",
                    logger_override=log,
                ):
                    yield item
                return
            except Exception as exc:
                log.warning("Required-action continuation failed: %s", exc)
                content = "\n\n".join(working_text_parts) if working_text_parts else ""
                if not content.strip():
                    content = _MISSING_ACTION_FAILURE_TEXT
                yield _resolution(
                    action="complete",
                    result=result,
                    messages=working_messages,
                    combined_text_parts=working_text_parts,
                    usage_totals=usage_totals,
                    completion_review_requested=review_requested,
                    terminal_content=content,
                    terminal_token_usage={},
                )
                return

        partial = result.text or ""
        if partial:
            working_text_parts.append(partial)
        content = "\n\n".join(part for part in working_text_parts if part).strip()
        note = (
            "I could not complete all required tool steps before responding. "
            + retry_prompt_builder(list(missing_action_hints))
        )
        content = f"{content}\n\n{note}".strip() if content else note
        yield _resolution(
            action="complete",
            result=result,
            messages=working_messages,
            combined_text_parts=working_text_parts,
            usage_totals=usage_totals,
            completion_review_requested=review_requested,
            terminal_content=content,
            terminal_token_usage={},
        )
        return

    normalized_autonomy_level = str(autonomy_level or "").strip().lower()
    if (
        normalized_autonomy_level in {"careful", "aggressive"}
        and not review_requested
        and turn_index < max_tool_turns - 1
    ):
        partial = result.text or ""
        if partial:
            working_text_parts.append(partial)
            working_messages.append({"role": "assistant", "content": partial})
        if normalized_autonomy_level == "aggressive":
            review_prompt = (
                "Before you stop, check whether you fully addressed the goal. "
                "If verification, testing, or one obvious next step should be done now, do it. "
                "If the answer is already complete, return the final answer."
            )
        else:
            review_prompt = (
                "Before you stop, make sure the answer clearly summarizes what was done "
                "and surfaces any remaining uncertainties or approvals needed."
            )
        working_messages.append({"role": "user", "content": review_prompt})
        working_messages = compact_context(working_messages, model)
        review_requested = True
        try:
            async for item in _run_continuation(
                messages=working_messages,
                combined_text_parts=working_text_parts,
                usage_totals=usage_totals,
                completion_review_requested=review_requested,
                turn_index=turn_index,
                model=model,
                completion_max_tokens=completion_max_tokens,
                force_file_write_next_turn=force_file_write_next_turn,
                tool_request_config=tool_request_config,
                run_continuation=run_continuation,
                merge_usage_totals=merge_usage_totals,
                interrupted_content=lambda: review_interrupted_content_builder(working_text_parts),
                empty_result_error="Completion review produced no result",
                logger_override=log,
            ):
                yield item
            return
        except Exception as exc:
            log.warning("Completion review continuation failed: %s", exc)

    content = result.text or ""
    if not content.strip() and working_text_parts:
        content = "\n\n".join(working_text_parts)
    if not content.strip():
        content = _EMPTY_RESPONSE_TEXT
    yield _resolution(
        action="complete",
        result=result,
        messages=working_messages,
        combined_text_parts=working_text_parts,
        usage_totals=usage_totals,
        completion_review_requested=review_requested,
        terminal_content=content,
        terminal_token_usage=normalize_usage(usage_totals or result.usage),
        terminal_raw_assistant_message=result.raw_assistant_message,
        use_terminal_reply_helper=True,
    )


__all__ = [
    "NoToolTurnResolution",
    "resolve_no_tool_turn",
]
