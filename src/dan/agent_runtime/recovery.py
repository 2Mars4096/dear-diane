"""Canonical helpers for post-failure completion recovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import AsyncIterator, Awaitable, Callable, Literal

from dan.agent_runtime.completion import (
    GuardedCompletionForwardedStep,
)
from dan.agent_runtime.followup import run_tool_followup_attempt
from dan.chat_events import ChatStreamEvent
from dan.providers import CompletionResult

logger = logging.getLogger(__name__)


async def recover_text_completion(
    *,
    primary: Callable[[], Awaitable[CompletionResult]],
    fallback: Callable[[], Awaitable[CompletionResult]] | None = None,
    logger_override: logging.Logger | None = None,
    fallback_label: str = "",
) -> tuple[CompletionResult | None, bool]:
    """Attempt a no-tools recovery completion, optionally with a fallback."""
    log = logger_override or logger

    try:
        primary_result = await primary()
        if primary_result and (primary_result.text or "").strip():
            log.info("Post-tool recovery synthesis succeeded")
            return primary_result, False
    except Exception as exc:
        log.debug("Post-tool recovery synthesis failed: %s", exc)

    if fallback is None:
        return None, False

    try:
        fallback_result = await fallback()
        if fallback_result and (fallback_result.text or "").strip():
            if fallback_label:
                log.info(
                    "Post-tool recovery synthesis succeeded with fallback model %s",
                    fallback_label,
                )
            else:
                log.info("Post-tool recovery synthesis succeeded with fallback model")
            return fallback_result, True
    except Exception as exc:
        log.debug("Fallback model synthesis also failed: %s", exc)

    return None, False


@dataclass(frozen=True)
class PostToolFollowupResolution:
    """Outcome of the post-tool follow-up failure policy."""

    result: CompletionResult | None
    interrupted: bool = False
    recovered_by: Literal["initial", "retry", "synthesis", "fallback", "none"] = "none"
    error: Exception | None = None
    step_labels: tuple[str, ...] = ()


async def run_post_tool_followup_flow(
    *,
    run_initial_steps: Callable[[], AsyncIterator[ChatStreamEvent | CompletionResult]],
    max_retries: int,
    is_transient_error: Callable[[Exception], bool],
    retry_delay_seconds: Callable[[Exception, int], float],
    retry_steps: Callable[[int], AsyncIterator[ChatStreamEvent | CompletionResult]],
    synthesize: Callable[[Exception], Awaitable[tuple[CompletionResult | None, bool]]],
    step_labeler: Callable[[ChatStreamEvent], str] | None = None,
    initial_empty_result_error: str = "Tool-loop follow-up produced no result",
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    logger_override: logging.Logger | None = None,
) -> AsyncIterator[GuardedCompletionForwardedStep | PostToolFollowupResolution]:
    """Run the full post-tool follow-up flow, including retry/synthesis recovery."""
    try:
        initial_resolution = None
        async for item in run_tool_followup_attempt(
            run_steps=run_initial_steps,
            step_labeler=step_labeler,
            empty_result_error=initial_empty_result_error,
        ):
            if isinstance(item, GuardedCompletionForwardedStep):
                yield item
                continue
            initial_resolution = item
        if initial_resolution is None:
            raise RuntimeError("Tool-loop follow-up attempt produced no resolution")
        if initial_resolution.interrupted:
            yield PostToolFollowupResolution(
                result=None,
                interrupted=True,
                step_labels=initial_resolution.step_labels,
            )
            return
        yield PostToolFollowupResolution(
            result=initial_resolution.result,
            recovered_by="initial",
            step_labels=initial_resolution.step_labels,
        )
        return
    except Exception as exc:
        async for item in resolve_post_tool_followup_failure(
            initial_error=exc,
            max_retries=max_retries,
            is_transient_error=is_transient_error,
            retry_delay_seconds=retry_delay_seconds,
            retry_steps=retry_steps,
            synthesize=synthesize,
            step_labeler=step_labeler,
            sleep=sleep,
            logger_override=logger_override,
        ):
            yield item


async def resolve_post_tool_followup_failure(
    *,
    initial_error: Exception,
    max_retries: int,
    is_transient_error: Callable[[Exception], bool],
    retry_delay_seconds: Callable[[Exception, int], float],
    retry_steps: Callable[[int], AsyncIterator[ChatStreamEvent | CompletionResult]],
    synthesize: Callable[[Exception], Awaitable[tuple[CompletionResult | None, bool]]],
    step_labeler: Callable[[ChatStreamEvent], str] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    logger_override: logging.Logger | None = None,
) -> AsyncIterator[GuardedCompletionForwardedStep | PostToolFollowupResolution]:
    """Retry a failed post-tool follow-up before falling back to no-tools synthesis."""
    log = logger_override or logger
    followup_error = initial_error

    if is_transient_error(followup_error) and max_retries > 0:
        for retry_index in range(1, max_retries + 1):
            retry_delay = retry_delay_seconds(followup_error, retry_index)
            log.info(
                "Retrying follow-up call %d/%d after transient error (backoff %.1fs)",
                retry_index,
                max_retries,
                retry_delay,
            )
            await sleep(retry_delay)
            try:
                retry_resolution: PostToolFollowupResolution | None = None
                async for item in run_tool_followup_attempt(
                    run_steps=lambda: retry_steps(retry_index),
                    step_labeler=step_labeler,
                    empty_result_error="Tool-loop follow-up retry produced no result",
                ):
                    if isinstance(item, GuardedCompletionForwardedStep):
                        yield item
                        continue
                    if item.interrupted:
                        yield PostToolFollowupResolution(
                            result=None,
                            interrupted=True,
                            error=followup_error,
                            step_labels=item.step_labels,
                        )
                        return
                    retry_resolution = PostToolFollowupResolution(
                        result=item.result,
                        recovered_by="retry",
                        error=followup_error,
                        step_labels=item.step_labels,
                    )
                if retry_resolution is None or retry_resolution.result is None:
                    raise RuntimeError("Tool-loop follow-up retry produced no result")
                log.info(
                    "Follow-up retry %d/%d succeeded",
                    retry_index,
                    max_retries,
                )
                yield retry_resolution
                return
            except Exception as retry_exc:
                followup_error = retry_exc
                log.info(
                    "Follow-up retry %d/%d failed: %s",
                    retry_index,
                    max_retries,
                    retry_exc,
                )

    synthesis_result, used_fallback = await synthesize(followup_error)
    yield PostToolFollowupResolution(
        result=synthesis_result,
        recovered_by="fallback" if used_fallback else ("synthesis" if synthesis_result is not None else "none"),
        error=followup_error,
    )


__all__ = [
    "PostToolFollowupResolution",
    "recover_text_completion",
    "run_post_tool_followup_flow",
    "resolve_post_tool_followup_failure",
]
