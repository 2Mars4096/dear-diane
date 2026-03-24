"""Canonical guarded-completion helper for long-running chat turns."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from dan.chat_events import ChatInterruptedEvent, ChatStreamEvent
from dan.providers import CompletionResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardedCompletionProgress:
    """Marker emitted when a guarded completion is still in flight."""


@dataclass(frozen=True)
class GuardedCompletionInterrupted:
    """Marker emitted when cancellation interrupts the guarded completion."""

    content: str


@dataclass(frozen=True)
class GuardedCompletionForwardedStep:
    """Forwarded non-result event plus its optional step label."""

    event: ChatStreamEvent
    label: str = ""


@dataclass(frozen=True)
class GuardedCompletionOutcome:
    """Final collected state after consuming a guarded completion stream."""

    result: CompletionResult | None
    step_labels: tuple[str, ...] = ()
    interrupted: bool = False


class GuardedCompletionRelay:
    """Async iterator over forwarded steps with the final outcome retained."""

    def __init__(
        self,
        steps: AsyncIterator[ChatStreamEvent | CompletionResult],
        *,
        step_labeler: Callable[[ChatStreamEvent], str] | None = None,
    ) -> None:
        self._steps = steps
        self._step_labeler = step_labeler
        self._outcome: GuardedCompletionOutcome | None = None
        self._consumed = False

    @property
    def outcome(self) -> GuardedCompletionOutcome | None:
        return self._outcome

    def outcome_or_empty(self) -> GuardedCompletionOutcome:
        return self._outcome or GuardedCompletionOutcome(result=None)

    def __aiter__(self) -> AsyncIterator[GuardedCompletionForwardedStep]:
        return self._forwarded_steps()

    async def _forwarded_steps(self) -> AsyncIterator[GuardedCompletionForwardedStep]:
        if self._consumed:
            raise RuntimeError("Guarded completion relay can only be consumed once")
        self._consumed = True
        labels: list[str] = []
        async for step in self._steps:
            if isinstance(step, CompletionResult):
                self._outcome = GuardedCompletionOutcome(
                    result=step,
                    step_labels=tuple(labels),
                    interrupted=False,
                )
                return
            label = self._step_labeler(step) if self._step_labeler is not None else ""
            if label:
                labels.append(label)
            yield GuardedCompletionForwardedStep(event=step, label=label)
            if isinstance(step, ChatInterruptedEvent):
                self._outcome = GuardedCompletionOutcome(
                    result=None,
                    step_labels=tuple(labels),
                    interrupted=True,
                )
                return
        self._outcome = GuardedCompletionOutcome(
            result=None,
            step_labels=tuple(labels),
            interrupted=False,
        )


async def iter_guarded_completion(
    *,
    run: Callable[[], Awaitable[CompletionResult]],
    cancel_event: asyncio.Event | None = None,
    timeout_seconds: float,
    poll_interval_seconds: float = 8.0,
    interrupted_content: str | Callable[[], str] | None = None,
    log: logging.Logger | None = None,
    log_model: str = "",
    log_emit_progress_ack: bool = False,
    log_message_count: int = 0,
    log_tool_names: tuple[str, ...] = (),
) -> AsyncIterator[GuardedCompletionProgress | GuardedCompletionInterrupted | CompletionResult]:
    """Run one completion call with cancellation and progress-ack handling."""
    complete_task: asyncio.Task[CompletionResult] | None = None
    diagnostic_logger = log or logger

    try:
        complete_task = asyncio.create_task(
            asyncio.wait_for(
                run(),
                timeout=timeout_seconds,
            )
        )
        while True:
            cancel_wait_task: asyncio.Task[bool] | None = None
            try:
                wait_set: set[asyncio.Task[Any]] = {complete_task}
                if cancel_event is not None:
                    cancel_wait_task = asyncio.create_task(cancel_event.wait())
                    wait_set.add(cancel_wait_task)
                done, pending = await asyncio.wait(
                    wait_set,
                    timeout=poll_interval_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if (
                    cancel_wait_task is not None
                    and cancel_wait_task in done
                    and cancel_event
                    and cancel_event.is_set()
                ):
                    complete_task.cancel()
                    try:
                        await complete_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    content = (
                        interrupted_content()
                        if callable(interrupted_content)
                        else (interrupted_content or "")
                    )
                    yield GuardedCompletionInterrupted(content=content)
                    return
                if complete_task in done:
                    for task in pending:
                        task.cancel()
                    break
                if log_emit_progress_ack:
                    yield GuardedCompletionProgress()
            finally:
                if cancel_wait_task is not None and not cancel_wait_task.done():
                    cancel_wait_task.cancel()
        yield await complete_task
    except asyncio.CancelledError as exc:
        if complete_task is not None and not complete_task.done():
            complete_task.cancel()
        current_task = asyncio.current_task()
        externally_cancelled = bool(
            (current_task is not None and current_task.cancelling())
            or (cancel_event is not None and cancel_event.is_set())
        )
        if externally_cancelled:
            raise

        diagnostic_logger.warning(
            "Guarded completion cancelled unexpectedly: model=%s, emit_progress_ack=%s, "
            "messages=%d, tools=%s",
            log_model,
            log_emit_progress_ack,
            log_message_count,
            ",".join(log_tool_names) or "none",
        )
        raise RuntimeError("Guarded completion cancelled unexpectedly") from exc
    except Exception:
        if complete_task is not None and not complete_task.done():
            complete_task.cancel()
        raise


async def relay_guarded_steps(
    steps: AsyncIterator[ChatStreamEvent | CompletionResult],
    *,
    step_labeler: Callable[[ChatStreamEvent], str] | None = None,
) -> AsyncIterator[GuardedCompletionForwardedStep | GuardedCompletionOutcome]:
    """Forward non-result events while collecting result/label outcome state."""
    relay = forward_guarded_steps(steps, step_labeler=step_labeler)
    async for step in relay:
        yield step
    yield relay.outcome_or_empty()


def forward_guarded_steps(
    steps: AsyncIterator[ChatStreamEvent | CompletionResult],
    *,
    step_labeler: Callable[[ChatStreamEvent], str] | None = None,
) -> GuardedCompletionRelay:
    """Create a forwarded-step relay that retains the final outcome for later inspection."""
    return GuardedCompletionRelay(steps, step_labeler=step_labeler)


__all__ = [
    "GuardedCompletionRelay",
    "GuardedCompletionForwardedStep",
    "GuardedCompletionInterrupted",
    "GuardedCompletionOutcome",
    "GuardedCompletionProgress",
    "forward_guarded_steps",
    "iter_guarded_completion",
    "relay_guarded_steps",
]
