from __future__ import annotations

import asyncio
import logging

import pytest

from dan.agent_runtime.completion import (
    GuardedCompletionForwardedStep,
    GuardedCompletionInterrupted,
    GuardedCompletionOutcome,
    GuardedCompletionProgress,
    forward_guarded_steps,
    iter_guarded_completion,
    relay_guarded_steps,
)
from dan.chat_events import ChatInterruptedEvent, ChatNoticeEvent
from dan.agent_runtime.recovery import (
    PostToolFollowupResolution,
    recover_text_completion,
    resolve_post_tool_followup_failure,
    run_post_tool_followup_flow,
)
from dan.providers import CompletionResult


@pytest.mark.asyncio
async def test_iter_guarded_completion_emits_progress_before_result() -> None:
    async def _run() -> CompletionResult:
        await asyncio.sleep(0.03)
        return CompletionResult(text="done")

    events = [
        event
        async for event in iter_guarded_completion(
            run=_run,
            timeout_seconds=0.2,
            poll_interval_seconds=0.01,
            log_emit_progress_ack=True,
        )
    ]

    assert any(isinstance(event, GuardedCompletionProgress) for event in events[:-1])
    assert isinstance(events[-1], CompletionResult)
    assert events[-1].text == "done"


@pytest.mark.asyncio
async def test_iter_guarded_completion_emits_interrupted_marker_when_cancelled() -> None:
    cancel_event = asyncio.Event()

    async def _run() -> CompletionResult:
        await asyncio.sleep(0.2)
        return CompletionResult(text="done")

    events = []
    async for event in iter_guarded_completion(
        run=_run,
        cancel_event=cancel_event,
        timeout_seconds=0.5,
        poll_interval_seconds=0.01,
        interrupted_content=lambda: "partial",
        log_emit_progress_ack=True,
    ):
        events.append(event)
        if isinstance(event, GuardedCompletionProgress):
            cancel_event.set()

    assert any(isinstance(event, GuardedCompletionProgress) for event in events)
    assert isinstance(events[-1], GuardedCompletionInterrupted)
    assert events[-1].content == "partial"


@pytest.mark.asyncio
async def test_iter_guarded_completion_raises_runtime_error_on_unexpected_cancel(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _run() -> CompletionResult:
        raise asyncio.CancelledError()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="Guarded completion cancelled unexpectedly"):
            async for _event in iter_guarded_completion(
                run=_run,
                timeout_seconds=0.2,
                poll_interval_seconds=0.01,
                log_model="test-model",
                log_emit_progress_ack=True,
                log_message_count=2,
                log_tool_names=("tool_a",),
            ):
                pass

    assert any(
        "Guarded completion cancelled unexpectedly" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_forward_guarded_steps_retains_outcome_and_labels() -> None:
    async def _steps():
        yield ChatNoticeEvent(content="working")
        yield CompletionResult(text="done")

    relay = forward_guarded_steps(
        _steps(),
        step_labeler=lambda event: getattr(event, "type", ""),
    )

    forwarded = [step async for step in relay]

    assert len(forwarded) == 1
    assert forwarded[0].label == "chat_notice"
    assert isinstance(forwarded[0].event, ChatNoticeEvent)
    assert relay.outcome == GuardedCompletionOutcome(
        result=CompletionResult(text="done"),
        step_labels=("chat_notice",),
        interrupted=False,
    )


@pytest.mark.asyncio
async def test_forward_guarded_steps_marks_interruption_and_yields_event() -> None:
    interrupted = ChatInterruptedEvent(message_id="m1", content="partial", token_usage={})

    async def _steps():
        yield interrupted

    relay = forward_guarded_steps(_steps())

    forwarded = [step async for step in relay]

    assert [step.event for step in forwarded] == [interrupted]
    assert relay.outcome == GuardedCompletionOutcome(
        result=None,
        step_labels=(),
        interrupted=True,
    )


@pytest.mark.asyncio
async def test_recover_text_completion_returns_primary_result_when_successful() -> None:
    result, used_fallback = await recover_text_completion(
        primary=lambda: asyncio.sleep(
            0,
            result=CompletionResult(text="Recovered on primary"),
        ),
    )

    assert result is not None
    assert result.text == "Recovered on primary"
    assert used_fallback is False


@pytest.mark.asyncio
async def test_recover_text_completion_uses_fallback_after_primary_failure() -> None:
    async def _primary() -> CompletionResult:
        raise RuntimeError("primary failed")

    result, used_fallback = await recover_text_completion(
        primary=_primary,
        fallback=lambda: asyncio.sleep(
            0,
            result=CompletionResult(text="Recovered on fallback"),
        ),
    )

    assert result is not None
    assert result.text == "Recovered on fallback"
    assert used_fallback is True


@pytest.mark.asyncio
async def test_recover_text_completion_returns_none_when_attempts_fail() -> None:
    async def _primary() -> CompletionResult:
        raise RuntimeError("primary failed")

    async def _fallback() -> CompletionResult:
        raise RuntimeError("fallback failed")

    result, used_fallback = await recover_text_completion(
        primary=_primary,
        fallback=_fallback,
    )

    assert result is None
    assert used_fallback is False


@pytest.mark.asyncio
async def test_run_post_tool_followup_flow_returns_initial_result() -> None:
    async def _initial_steps():
        yield ChatNoticeEvent(content="working")
        yield CompletionResult(text="done")

    items = [
        item
        async for item in run_post_tool_followup_flow(
            run_initial_steps=_initial_steps,
            max_retries=1,
            is_transient_error=lambda exc: True,
            retry_delay_seconds=lambda exc, retry_index: 0.0,
            retry_steps=lambda retry_index: _initial_steps(),
            synthesize=lambda exc: asyncio.sleep(0, result=(None, False)),
            step_labeler=lambda event: getattr(event, "type", ""),
        )
    ]

    assert isinstance(items[0], GuardedCompletionForwardedStep)
    assert items[0].label == "chat_notice"
    assert isinstance(items[1], PostToolFollowupResolution)
    assert items[1].result is not None
    assert items[1].result.text == "done"
    assert items[1].recovered_by == "initial"
    assert items[1].step_labels == ("chat_notice",)


@pytest.mark.asyncio
async def test_run_post_tool_followup_flow_retries_after_initial_failure() -> None:
    async def _initial_steps():
        raise RuntimeError("transient")
        yield

    async def _retry_steps(_retry_index: int):
        yield ChatNoticeEvent(content="retry")
        yield CompletionResult(text="retried")

    items = [
        item
        async for item in run_post_tool_followup_flow(
            run_initial_steps=_initial_steps,
            max_retries=1,
            is_transient_error=lambda exc: True,
            retry_delay_seconds=lambda exc, retry_index: 0.0,
            retry_steps=_retry_steps,
            synthesize=lambda exc: asyncio.sleep(0, result=(None, False)),
            step_labeler=lambda event: getattr(event, "type", ""),
        )
    ]

    assert isinstance(items[0], GuardedCompletionForwardedStep)
    assert items[0].label == "chat_notice"
    assert isinstance(items[1], PostToolFollowupResolution)
    assert items[1].result is not None
    assert items[1].result.text == "retried"
    assert items[1].recovered_by == "retry"
    assert str(items[1].error) == "transient"
    assert items[1].step_labels == ("chat_notice",)


@pytest.mark.asyncio
async def test_resolve_post_tool_followup_failure_retries_and_returns_retry_result() -> None:
    sleep_delays: list[float] = []
    retry_attempts: list[int] = []

    async def _sleep(delay: float) -> None:
        sleep_delays.append(delay)

    async def _retry_steps(retry_index: int):
        retry_attempts.append(retry_index)
        yield ChatNoticeEvent(content=f"retry {retry_index}")
        yield CompletionResult(text="retried")

    async def _synthesize(_exc: Exception) -> tuple[CompletionResult | None, bool]:
        raise AssertionError("synthesis should not run after retry success")

    events = [
        item
        async for item in resolve_post_tool_followup_failure(
            initial_error=RuntimeError("transient"),
            max_retries=2,
            is_transient_error=lambda exc: True,
            retry_delay_seconds=lambda exc, retry_index: float(retry_index),
            retry_steps=_retry_steps,
            synthesize=_synthesize,
            step_labeler=lambda event: getattr(event, "type", ""),
            sleep=_sleep,
        )
    ]

    assert sleep_delays == [1.0]
    assert retry_attempts == [1]
    assert isinstance(events[0], GuardedCompletionForwardedStep)
    assert events[0].label == "chat_notice"
    assert isinstance(events[1], PostToolFollowupResolution)
    assert events[1].result is not None
    assert events[1].result.text == "retried"
    assert events[1].recovered_by == "retry"
    assert events[1].interrupted is False


@pytest.mark.asyncio
async def test_resolve_post_tool_followup_failure_marks_interruption() -> None:
    async def _retry_steps(_retry_index: int):
        yield ChatInterruptedEvent(message_id="m1", content="", token_usage={})

    async def _synthesize(_exc: Exception) -> tuple[CompletionResult | None, bool]:
        raise AssertionError("synthesis should not run after interruption")

    events = [
        item
        async for item in resolve_post_tool_followup_failure(
            initial_error=RuntimeError("transient"),
            max_retries=1,
            is_transient_error=lambda exc: True,
            retry_delay_seconds=lambda exc, retry_index: 0.0,
            retry_steps=_retry_steps,
            synthesize=_synthesize,
        )
    ]

    assert isinstance(events[0], GuardedCompletionForwardedStep)
    assert isinstance(events[0].event, ChatInterruptedEvent)
    assert isinstance(events[1], PostToolFollowupResolution)
    assert events[1].result is None
    assert events[1].interrupted is True
    assert events[1].recovered_by == "none"


@pytest.mark.asyncio
async def test_resolve_post_tool_followup_failure_uses_fallback_synthesis_after_retry_failures() -> None:
    async def _retry_steps(retry_index: int):
        raise RuntimeError(f"retry {retry_index} failed")
        yield

    async def _synthesize(exc: Exception) -> tuple[CompletionResult | None, bool]:
        assert str(exc) == "retry 2 failed"
        return CompletionResult(text="fallback synthesis"), True

    events = [
        item
        async for item in resolve_post_tool_followup_failure(
            initial_error=RuntimeError("transient"),
            max_retries=2,
            is_transient_error=lambda exc: True,
            retry_delay_seconds=lambda exc, retry_index: 0.0,
            retry_steps=_retry_steps,
            synthesize=_synthesize,
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], PostToolFollowupResolution)
    assert events[0].result is not None
    assert events[0].result.text == "fallback synthesis"
    assert events[0].recovered_by == "fallback"
    assert str(events[0].error) == "retry 2 failed"


@pytest.mark.asyncio
async def test_resolve_post_tool_followup_failure_turns_missing_retry_result_into_contextual_error() -> None:
    async def _retry_steps(_retry_index: int):
        yield ChatNoticeEvent(content="working")

    async def _synthesize(exc: Exception) -> tuple[CompletionResult | None, bool]:
        assert str(exc) == "Tool-loop follow-up retry produced no result"
        return None, False

    events = [
        item
        async for item in resolve_post_tool_followup_failure(
            initial_error=RuntimeError("transient"),
            max_retries=1,
            is_transient_error=lambda exc: True,
            retry_delay_seconds=lambda exc, retry_index: 0.0,
            retry_steps=_retry_steps,
            synthesize=_synthesize,
            step_labeler=lambda event: getattr(event, "type", ""),
        )
    ]

    assert isinstance(events[0], GuardedCompletionForwardedStep)
    assert events[0].label == "chat_notice"
    assert isinstance(events[1], PostToolFollowupResolution)
    assert events[1].result is None
    assert events[1].recovered_by == "none"
    assert str(events[1].error) == "Tool-loop follow-up retry produced no result"


@pytest.mark.asyncio
async def test_relay_guarded_steps_collects_labels_and_result() -> None:
    async def _steps():
        yield ChatNoticeEvent(content="Working")
        yield CompletionResult(text="done")

    forwarded = [
        item
        async for item in relay_guarded_steps(
            _steps(),
            step_labeler=lambda event: getattr(event, "type", ""),
        )
    ]

    assert isinstance(forwarded[0], GuardedCompletionForwardedStep)
    assert forwarded[0].label == "chat_notice"
    assert isinstance(forwarded[1], GuardedCompletionOutcome)
    assert forwarded[1].result is not None
    assert forwarded[1].result.text == "done"
    assert forwarded[1].step_labels == ("chat_notice",)
    assert forwarded[1].interrupted is False


@pytest.mark.asyncio
async def test_relay_guarded_steps_marks_interruption() -> None:
    async def _steps():
        yield ChatInterruptedEvent(message_id="m1", content="", token_usage={})

    forwarded = [item async for item in relay_guarded_steps(_steps())]

    assert isinstance(forwarded[0], GuardedCompletionForwardedStep)
    assert isinstance(forwarded[1], GuardedCompletionOutcome)
    assert forwarded[1].result is None
    assert forwarded[1].interrupted is True
