"""Focused runtime coverage for the no-tool tool-loop helper."""

from __future__ import annotations

from typing import Any

import pytest

from dan.agent_runtime.tool_loop import NoToolTurnResolution, resolve_no_tool_turn
from dan.providers import CompletionResult


def _continuation_result(
    *,
    text: str = "continuation final",
    usage: dict[str, int] | None = None,
    finish_reason: str = "stop",
) -> CompletionResult:
    return CompletionResult(
        text=text,
        usage=usage,
        tool_calls=[],
        finish_reason=finish_reason,
    )


async def _collect_items(stream: Any) -> list[Any]:
    items: list[Any] = []
    async for item in stream:
        items.append(item)
    return items


def _merge_usage_totals(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if current is None:
        return dict(incoming) if incoming is not None else None
    if incoming is None:
        return dict(current)
    merged = dict(current)
    for key, value in incoming.items():
        merged[key] = merged.get(key, 0) + value
    return merged


@pytest.mark.asyncio
async def test_truncated_continuation_success_returns_continue_and_preserves_prompting() -> None:
    captured: dict[str, Any] = {}
    continuation = _continuation_result(usage={"prompt_tokens": 2, "completion_tokens": 4})

    async def _run_continuation(payload: dict[str, Any], interrupted_content: Any):
        captured["payload"] = payload
        captured["interrupted_content"] = interrupted_content
        if False:
            yield continuation
        yield continuation

    result = CompletionResult(
        text="partial answer",
        usage={"prompt_tokens": 5, "completion_tokens": 1},
        finish_reason="length",
    )
    items = await _collect_items(
        resolve_no_tool_turn(
            result=result,
            messages=[{"role": "system", "content": "sys"}],
            combined_text_parts=["seed"],
            usage_totals={"prompt_tokens": 1, "completion_tokens": 1},
            completion_review_requested=False,
            turn_index=0,
            max_tool_turns=3,
            model="model-x",
            completion_max_tokens=128,
            force_file_write_next_turn=True,
            missing_action_hints=[],
            autonomy_level="off",
            tool_request_config=lambda force: ([{"type": "tool"}], "required"),
            run_continuation=_run_continuation,
            merge_usage_totals=_merge_usage_totals,
            normalize_usage=lambda usage: usage or {},
            retry_prompt_builder=lambda hints: f"retry: {', '.join(hints)}",
            interrupted_content_builder=lambda parts: " / ".join(parts),
            review_interrupted_content_builder=lambda parts: "review: " + " / ".join(parts),
            compact_context=lambda messages, model: messages,
        )
    )

    resolution = items[-1]
    assert isinstance(resolution, NoToolTurnResolution)
    assert resolution.action == "continue"
    assert resolution.result is continuation
    assert resolution.combined_text_parts == ("seed", "partial answer")
    assert resolution.usage_totals == {"prompt_tokens": 3, "completion_tokens": 5}
    assert captured["payload"]["tool_choice"] == "required"
    assert captured["payload"]["messages"][-1] == {
        "role": "user",
        "content": "Continue from where you left off. Keep using file_write to save your output.",
    }
    assert any(
        message == {"role": "assistant", "content": "partial answer"}
        for message in captured["payload"]["messages"]
    )


@pytest.mark.asyncio
async def test_missing_action_continuation_failure_falls_back_to_terminal_complete() -> None:
    async def _run_continuation(payload: dict[str, Any], interrupted_content: Any):
        if False:
            yield payload  # pragma: no cover - keeps this an async generator
        raise RuntimeError("continuation failed")

    result = CompletionResult(
        text="partial answer",
        usage={"prompt_tokens": 2, "completion_tokens": 1},
        finish_reason="stop",
    )
    items = await _collect_items(
        resolve_no_tool_turn(
            result=result,
            messages=[{"role": "user", "content": "start"}],
            combined_text_parts=["partial answer"],
            usage_totals=None,
            completion_review_requested=False,
            turn_index=0,
            max_tool_turns=2,
            model="model-x",
            completion_max_tokens=128,
            force_file_write_next_turn=False,
            missing_action_hints=["write the file"],
            autonomy_level="off",
            tool_request_config=lambda force: ([{"type": "tool"}], "auto"),
            run_continuation=_run_continuation,
            merge_usage_totals=_merge_usage_totals,
            normalize_usage=lambda usage: usage or {},
            retry_prompt_builder=lambda hints: f"retry: {', '.join(hints)}",
            interrupted_content_builder=lambda parts: " / ".join(parts),
            review_interrupted_content_builder=lambda parts: "review: " + " / ".join(parts),
            compact_context=lambda messages, model: messages,
        )
    )

    resolution = items[-1]
    assert isinstance(resolution, NoToolTurnResolution)
    assert resolution.action == "complete"
    assert resolution.result is result
    assert resolution.terminal_content == "partial answer\n\npartial answer"
    assert resolution.terminal_token_usage == {}
    assert resolution.use_terminal_reply_helper is False
    assert any(
        message == {"role": "user", "content": "retry: write the file"}
        for message in resolution.messages
    )


@pytest.mark.asyncio
async def test_autonomy_review_failure_falls_back_to_terminal_completion() -> None:
    async def _run_continuation(payload: dict[str, Any], interrupted_content: Any):
        if False:
            yield payload  # pragma: no cover - keeps this an async generator
        raise RuntimeError("review failed")

    result = CompletionResult(
        text="",
        usage={"prompt_tokens": 4, "completion_tokens": 2},
        finish_reason="stop",
    )
    items = await _collect_items(
        resolve_no_tool_turn(
            result=result,
            messages=[{"role": "user", "content": "start"}],
            combined_text_parts=["draft answer"],
            usage_totals=None,
            completion_review_requested=False,
            turn_index=0,
            max_tool_turns=3,
            model="model-x",
            completion_max_tokens=128,
            force_file_write_next_turn=False,
            missing_action_hints=[],
            autonomy_level="careful",
            tool_request_config=lambda force: ([{"type": "tool"}], "auto"),
            run_continuation=_run_continuation,
            merge_usage_totals=_merge_usage_totals,
            normalize_usage=lambda usage: usage or {},
            retry_prompt_builder=lambda hints: f"retry: {', '.join(hints)}",
            interrupted_content_builder=lambda parts: " / ".join(parts),
            review_interrupted_content_builder=lambda parts: "review: " + " / ".join(parts),
            compact_context=lambda messages, model: messages,
        )
    )

    resolution = items[-1]
    assert isinstance(resolution, NoToolTurnResolution)
    assert resolution.action == "complete"
    assert resolution.completion_review_requested is True
    assert resolution.terminal_content == "draft answer"
    assert resolution.use_terminal_reply_helper is True
    assert any(
        message == {
            "role": "user",
            "content": (
                "Before you stop, make sure the answer clearly summarizes what was done "
                "and surfaces any remaining uncertainties or approvals needed."
            ),
        }
        for message in resolution.messages
    )


@pytest.mark.asyncio
async def test_final_terminal_completion_joins_parts_and_normalizes_usage() -> None:
    normalized_inputs: list[dict[str, int] | None] = []

    result = CompletionResult(
        text="",
        usage={"prompt_tokens": 7, "completion_tokens": 3},
        finish_reason="stop",
        raw_assistant_message={"role": "assistant", "content": None},
    )
    items = await _collect_items(
        resolve_no_tool_turn(
            result=result,
            messages=[{"role": "user", "content": "start"}],
            combined_text_parts=["alpha", "beta"],
            usage_totals=None,
            completion_review_requested=False,
            turn_index=0,
            max_tool_turns=3,
            model="model-x",
            completion_max_tokens=128,
            force_file_write_next_turn=False,
            missing_action_hints=[],
            autonomy_level="off",
            tool_request_config=lambda force: ([{"type": "tool"}], "auto"),
            run_continuation=lambda payload, interrupted_content: None,  # pragma: no cover
            merge_usage_totals=_merge_usage_totals,
            normalize_usage=lambda usage: normalized_inputs.append(usage) or {"normalized": True, "usage": usage},
            retry_prompt_builder=lambda hints: f"retry: {', '.join(hints)}",
            interrupted_content_builder=lambda parts: " / ".join(parts),
            review_interrupted_content_builder=lambda parts: "review: " + " / ".join(parts),
            compact_context=lambda messages, model: messages,
        )
    )

    resolution = items[-1]
    assert isinstance(resolution, NoToolTurnResolution)
    assert resolution.action == "complete"
    assert resolution.terminal_content == "alpha\n\nbeta"
    assert resolution.terminal_token_usage == {
        "normalized": True,
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    assert resolution.terminal_raw_assistant_message == {
        "role": "assistant",
        "content": None,
    }
    assert resolution.use_terminal_reply_helper is True
    assert normalized_inputs == [{"prompt_tokens": 7, "completion_tokens": 3}]
