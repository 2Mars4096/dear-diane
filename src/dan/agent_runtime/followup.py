"""Canonical helpers for post-tool follow-up request assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Sequence

from dan.agent_runtime.completion import (
    GuardedCompletionForwardedStep,
    forward_guarded_steps,
)
from dan.chat_events import ChatStreamEvent
from dan.providers import CompletionResult

_GROUNDING_REQUIREMENT_PROMPT = (
    "Grounding requirement: for live or current claims, answer ONLY from the web/tool "
    "evidence already retrieved in this conversation. If the evidence is only snippets "
    "or does not support a claim, say you could not verify it yet. Prefer fetched "
    "page content over snippets. When results are numbered, cite them inline as [1], [2] "
    "and include markdown links to the source URLs when helpful."
)


@dataclass(frozen=True)
class PreparedToolFollowupRequest:
    """Prepared provider-facing request state for the next post-tool turn."""

    messages: list[dict[str, Any]]
    followup_tools: list[dict[str, Any]]
    followup_tool_choice: str | dict[str, Any]
    missing_action_hints: tuple[str, ...]
    force_file_write_next_turn: bool


@dataclass(frozen=True)
class ToolFollowupAttemptResolution:
    """Collected state after one guarded post-tool follow-up attempt."""

    result: CompletionResult | None
    step_labels: tuple[str, ...] = ()
    interrupted: bool = False


def build_assistant_followup_message(
    *,
    text: str,
    tool_calls: list[dict[str, Any]],
    raw_assistant_message: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize an assistant-turn message for provider replay."""
    if raw_assistant_message:
        msg = dict(raw_assistant_message)
        if msg.get("content") is not None or msg.get("tool_calls"):
            msg.setdefault("role", "assistant")
            return msg

    assistant_text = text or ""
    if not tool_calls and not assistant_text.strip():
        return None
    assistant_message: dict[str, Any] = {"role": "assistant"}
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls
    if assistant_text.strip():
        assistant_message["content"] = assistant_text
    else:
        assistant_message["content"] = None
    return assistant_message


def prepare_tool_followup_request(
    *,
    messages: list[dict[str, Any]],
    assistant_text: str,
    executed_raw_tool_calls: Sequence[dict[str, Any]],
    raw_assistant_message: dict[str, Any] | None,
    tool_result_messages: Sequence[dict[str, Any]],
    deferred_capability_prompt: str | None,
    pending_run_status_check_id: str | None,
    include_grounding_requirement: bool,
    pending_write_file: bool,
    missing_target_detected: bool,
    force_file_write_next_turn: bool,
    followup_missing_action_hints: Sequence[str],
    model: str,
    tool_request_config: Callable[[bool], tuple[list[dict[str, Any]], str | dict[str, Any]]],
    compact_context: Callable[[list[dict[str, Any]], str], list[dict[str, Any]]],
    pressure_hint: Callable[[list[dict[str, Any]], str], str],
    retry_prompt_builder: Callable[[list[str]], str],
    write_prompt_builder: Callable[..., str],
) -> PreparedToolFollowupRequest:
    """Build the next provider request after one or more tool results."""
    prepared_messages = list(messages)
    assistant_tool_message = build_assistant_followup_message(
        text=assistant_text,
        tool_calls=list(executed_raw_tool_calls),
        raw_assistant_message=raw_assistant_message,
    )
    if assistant_tool_message is not None:
        prepared_messages.append(assistant_tool_message)
    prepared_messages.extend(tool_result_messages)

    if deferred_capability_prompt:
        prepared_messages.append({
            "role": "user",
            "content": deferred_capability_prompt,
        })
    if pending_run_status_check_id:
        prepared_messages.append({
            "role": "user",
            "content": (
                f"The workflow run `{pending_run_status_check_id}` has only been started so far. "
                "Before giving a final answer, call `get_run_status` for that run_id and report the "
                "actual current status. Do not describe the run as completed, successful, or failed "
                "unless `get_run_status` confirms that terminal status."
            ),
        })
    if include_grounding_requirement:
        prepared_messages.append({
            "role": "user",
            "content": _GROUNDING_REQUIREMENT_PROMPT,
        })

    if pending_write_file and missing_target_detected:
        force_file_write_next_turn = True
        prepared_messages.append({
            "role": "user",
            "content": write_prompt_builder(missing_target=True),
        })
    elif not pending_write_file:
        force_file_write_next_turn = False

    followup_tools, followup_tool_choice = tool_request_config(force_file_write_next_turn)
    if followup_missing_action_hints and followup_tool_choice == "auto":
        prepared_messages.append({
            "role": "user",
            "content": retry_prompt_builder(list(followup_missing_action_hints)),
        })

    prepared_messages = compact_context(prepared_messages, model)
    pressure = pressure_hint(prepared_messages, model)
    if pressure:
        prepared_messages.append({"role": "system", "content": pressure})

    return PreparedToolFollowupRequest(
        messages=prepared_messages,
        followup_tools=followup_tools,
        followup_tool_choice=followup_tool_choice,
        missing_action_hints=tuple(str(item) for item in followup_missing_action_hints if str(item)),
        force_file_write_next_turn=force_file_write_next_turn,
    )


async def run_tool_followup_attempt(
    *,
    run_steps: Callable[[], AsyncIterator[ChatStreamEvent | CompletionResult]],
    step_labeler: Callable[[ChatStreamEvent], str] | None = None,
    empty_result_error: str = "Tool-loop follow-up produced no result",
) -> AsyncIterator[GuardedCompletionForwardedStep | ToolFollowupAttemptResolution]:
    """Drain one guarded post-tool follow-up attempt into a reusable resolution."""
    relay = forward_guarded_steps(
        run_steps(),
        step_labeler=step_labeler,
    )
    async for step in relay:
        yield step

    outcome = relay.outcome_or_empty()
    if outcome.interrupted:
        yield ToolFollowupAttemptResolution(
            result=None,
            step_labels=tuple(outcome.step_labels),
            interrupted=True,
        )
        return
    if outcome.result is None:
        raise RuntimeError(empty_result_error)
    yield ToolFollowupAttemptResolution(
        result=outcome.result,
        step_labels=tuple(outcome.step_labels),
        interrupted=False,
    )


def build_post_tool_failure_content(
    *,
    error_intro: str,
    tool_summary_lines: Sequence[str],
    continue_prompt: str = "Please ask me to continue or summarize.",
) -> str:
    """Render the fallback terminal text after post-tool follow-up recovery fails."""
    progress_note = ""
    summary_lines = [str(line).strip() for line in tool_summary_lines if str(line).strip()]
    if summary_lines:
        progress_note = "Here's what I completed before the interruption:\n" + "\n".join(
            f"- {line}" for line in summary_lines
        )
    return f"{error_intro}\n\n{progress_note}\n\n{continue_prompt}".strip()


__all__ = [
    "PreparedToolFollowupRequest",
    "ToolFollowupAttemptResolution",
    "build_assistant_followup_message",
    "build_post_tool_failure_content",
    "prepare_tool_followup_request",
    "run_tool_followup_attempt",
]
