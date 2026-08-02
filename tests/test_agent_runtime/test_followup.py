from __future__ import annotations

from typing import Any

import pytest

from dan.agent_runtime.completion import GuardedCompletionForwardedStep
from dan.agent_runtime.followup import (
    ToolFollowupAttemptResolution,
    build_assistant_followup_message,
    build_post_tool_failure_content,
    prepare_tool_followup_request,
    run_tool_followup_attempt,
)
from dan.chat_events import ChatCompleteEvent, ChatInterruptedEvent
from dan.providers import CompletionResult


async def _collect_items(stream: Any) -> list[Any]:
    items: list[Any] = []
    async for item in stream:
        items.append(item)
    return items


def test_build_assistant_followup_message_prefers_raw_provider_message() -> None:
    raw_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1"}],
        "reasoning_content": "kept",
    }

    message = build_assistant_followup_message(
        text="ignored",
        tool_calls=[{"id": "call_1"}],
        raw_assistant_message=raw_message,
    )

    assert message == raw_message


def test_prepare_tool_followup_request_adds_prompts_and_compacts() -> None:
    request_force_flags: list[bool] = []
    compacted_messages: list[list[dict[str, Any]]] = []

    def _tool_request_config(force_file_write_now: bool) -> tuple[list[dict[str, Any]], str]:
        request_force_flags.append(force_file_write_now)
        return [{"function": {"name": "search_web"}}], "auto"

    def _compact_context(messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
        compacted_messages.append(list(messages))
        return list(messages)

    prepared = prepare_tool_followup_request(
        messages=[{"role": "system", "content": "sys"}],
        assistant_text="assistant body",
        executed_raw_tool_calls=[{"id": "call_1"}],
        raw_assistant_message={
            "role": "assistant",
            "content": "assistant body",
            "tool_calls": [{"id": "call_1"}],
            "reasoning_content": "kept",
        },
        tool_result_messages=[{"role": "tool", "tool_call_id": "call_1", "content": "done"}],
        deferred_capability_prompt="extra prompt",
        pending_run_status_check_id="run-123",
        include_grounding_requirement=True,
        pending_write_file=False,
        missing_target_detected=False,
        force_file_write_next_turn=True,
        followup_missing_action_hints=["write_file"],
        model="test-model",
        tool_request_config=_tool_request_config,
        compact_context=_compact_context,
        pressure_hint=lambda messages, model: "pressure hint",
        retry_prompt_builder=lambda hints: f"retry: {', '.join(hints)}",
        write_prompt_builder=lambda **kwargs: "write escalation",
    )

    assert request_force_flags == [False]
    assert len(compacted_messages) == 1
    assert prepared.followup_tools == [{"function": {"name": "search_web"}}]
    assert prepared.followup_tool_choice == "auto"
    assert prepared.missing_action_hints == ("write_file",)
    assert prepared.force_file_write_next_turn is False

    messages = prepared.messages
    assert messages[1]["reasoning_content"] == "kept"
    assert any(msg.get("content") == "extra prompt" for msg in messages)
    assert any("run `run-123`" in str(msg.get("content") or "") for msg in messages)
    assert any("Grounding requirement:" in str(msg.get("content") or "") for msg in messages)
    assert any(msg.get("content") == "retry: write_file" for msg in messages)
    assert messages[-1] == {"role": "system", "content": "pressure hint"}


def test_prepare_tool_followup_request_escalates_write_file_when_target_missing() -> None:
    request_force_flags: list[bool] = []

    prepared = prepare_tool_followup_request(
        messages=[{"role": "system", "content": "sys"}],
        assistant_text="assistant body",
        executed_raw_tool_calls=[],
        raw_assistant_message=None,
        tool_result_messages=[],
        deferred_capability_prompt=None,
        pending_run_status_check_id=None,
        include_grounding_requirement=False,
        pending_write_file=True,
        missing_target_detected=True,
        force_file_write_next_turn=False,
        followup_missing_action_hints=[],
        model="test-model",
        tool_request_config=lambda force_file_write_now: (
            request_force_flags.append(force_file_write_now) or [],
            "required",
        ),
        compact_context=lambda messages, model: list(messages),
        pressure_hint=lambda messages, model: "",
        retry_prompt_builder=lambda hints: "retry",
        write_prompt_builder=lambda **kwargs: "write escalation",
    )

    assert request_force_flags == [True]
    assert prepared.force_file_write_next_turn is True
    assert any(msg.get("content") == "write escalation" for msg in prepared.messages)


def test_build_post_tool_failure_content_includes_progress_lines() -> None:
    content = build_post_tool_failure_content(
        error_intro="Timed out while finishing the answer.",
        tool_summary_lines=["Searched the web", "Read README.md"],
    )

    assert "Timed out while finishing the answer." in content
    assert "Here's what I completed before the interruption:" in content
    assert "- Searched the web" in content
    assert "- Read README.md" in content
    assert content.endswith("Please ask me to continue or summarize.")


@pytest.mark.asyncio
async def test_run_tool_followup_attempt_forwards_steps_and_returns_result() -> None:
    result = CompletionResult(text="done", finish_reason="stop")

    async def _steps():
        yield ChatCompleteEvent(
            message_id="progress",
            content="working",
            graph_revision="",
            detected_mode="progress_ack",
        )
        yield result

    items = await _collect_items(
        run_tool_followup_attempt(
            run_steps=_steps,
            step_labeler=lambda event: getattr(event, "detected_mode", "") or "event",
        )
    )

    assert isinstance(items[0], GuardedCompletionForwardedStep)
    assert items[0].label == "progress_ack"
    resolution = items[-1]
    assert isinstance(resolution, ToolFollowupAttemptResolution)
    assert resolution.result is result
    assert resolution.step_labels == ("progress_ack",)
    assert resolution.interrupted is False


@pytest.mark.asyncio
async def test_run_tool_followup_attempt_marks_interruption() -> None:
    async def _steps():
        yield ChatInterruptedEvent(
            message_id="interrupted",
            content="stopped",
            token_usage={},
        )

    items = await _collect_items(
        run_tool_followup_attempt(
            run_steps=_steps,
            step_labeler=lambda event: "interrupted",
        )
    )

    assert isinstance(items[0], GuardedCompletionForwardedStep)
    resolution = items[-1]
    assert isinstance(resolution, ToolFollowupAttemptResolution)
    assert resolution.result is None
    assert resolution.step_labels == ("interrupted",)
    assert resolution.interrupted is True


@pytest.mark.asyncio
async def test_run_tool_followup_attempt_raises_when_no_result_is_produced() -> None:
    async def _steps():
        yield ChatCompleteEvent(
            message_id="progress",
            content="working",
            graph_revision="",
        )

    with pytest.raises(RuntimeError, match="no follow-up result"):
        async for _item in run_tool_followup_attempt(
            run_steps=_steps,
            empty_result_error="no follow-up result",
        ):
            pass
