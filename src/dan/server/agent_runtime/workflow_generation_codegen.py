"""Shared codegen request helpers for workflow generation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkflowCodegenRequestResult:
    """Builder-code request outcome for one codegen attempt chain."""

    builder_code: str = ""
    codegen_retries: int = 0
    terminal_failure: str | None = None
    terminal_message: str = ""


async def request_builder_code(
    *,
    provider: Any,
    model: str,
    user_message: str,
    intent_goal: str | None,
    gen_stats_hint: str,
    detected_domain: str | None,
    complexity_tier: str,
    min_nodes: int,
    max_nodes: int,
    extract_code_from_response: Callable[[str], str],
    retries_used: dict[str, int],
    record_gen_outcome: Callable[..., None],
    pattern: str,
    is_transient_llm_error: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    log: logging.Logger | None = None,
) -> WorkflowCodegenRequestResult:
    """Request builder code from the LLM with the existing retry rules."""

    from dan.meta.planner import CodegenPromptBuilder

    active_log = log or logger
    codegen_builder = CodegenPromptBuilder()
    error_ctx = f"Intent extraction produced: {intent_goal}" if intent_goal else None
    if gen_stats_hint:
        error_ctx = f"{error_ctx}\n\n{gen_stats_hint}" if error_ctx else gen_stats_hint

    complexity_hint = (
        f"This is a {complexity_tier} prompt. "
        f"The generated graph should have {min_nodes}-{max_nodes} nodes."
    )
    if min_nodes >= 3:
        complexity_hint += " A 1-node or 2-node graph is likely underspecified."

    system_prompt, user_prompt = codegen_builder.build_full_prompt(
        goal=user_message,
        error_context=error_ctx,
        domain=detected_domain,
        complexity_hint=complexity_hint,
    )

    builder_code = ""
    codegen_retries = 0
    max_codegen_retries = 2
    backoff = [2.0, 4.0]
    terminal_codegen_failure: str | None = None
    terminal_codegen_message = ""
    last_codegen_error_type: str | None = None
    consecutive_same_error = 0

    for cg_attempt in range(max_codegen_retries + 1):
        try:
            codegen_result = await provider.complete(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                model=model,
                temperature=0.3,
            )
            raw_text = codegen_result.text or ""
            if not raw_text.strip():
                current_error = "empty_response"
                if current_error == last_codegen_error_type:
                    consecutive_same_error += 1
                else:
                    consecutive_same_error = 1
                last_codegen_error_type = current_error
                if consecutive_same_error >= 2:
                    terminal_codegen_failure = "no_output"
                    terminal_codegen_message = (
                        "Same error (empty response) 2 times in a row — failing fast"
                    )
                    active_log.info(
                        "Codegen early termination: %s",
                        terminal_codegen_message,
                    )
                    break
                if cg_attempt < max_codegen_retries:
                    codegen_retries += 1
                    retries_used["codegen"] += 1
                    active_log.warning(
                        "Codegen empty response, retrying (attempt %d, delay %.0fs)",
                        cg_attempt + 1,
                        backoff[cg_attempt],
                    )
                    await sleep(backoff[cg_attempt])
                    continue
                terminal_codegen_failure = "no_output"
                terminal_codegen_message = (
                    "Codegen returned empty builder code after retries"
                )
            builder_code = extract_code_from_response(raw_text)
            if not builder_code or not builder_code.strip():
                current_error = "empty_code"
                if current_error == last_codegen_error_type:
                    consecutive_same_error += 1
                else:
                    consecutive_same_error = 1
                last_codegen_error_type = current_error
                if consecutive_same_error >= 2:
                    terminal_codegen_failure = "no_output"
                    terminal_codegen_message = (
                        "Same error (empty code) 2 times in a row — failing fast"
                    )
                    active_log.info(
                        "Codegen early termination: %s",
                        terminal_codegen_message,
                    )
                    break
                if cg_attempt < max_codegen_retries:
                    codegen_retries += 1
                    retries_used["codegen"] += 1
                    active_log.warning(
                        "Codegen produced empty/whitespace code, retrying (attempt %d)",
                        cg_attempt + 1,
                    )
                    await sleep(backoff[cg_attempt])
                    continue
                terminal_codegen_failure = "no_output"
                terminal_codegen_message = (
                    "Codegen returned empty builder code after retries"
                )
            last_codegen_error_type = None
            consecutive_same_error = 0
            break
        except Exception as exc:
            current_error = type(exc).__name__
            if current_error == last_codegen_error_type:
                consecutive_same_error += 1
            else:
                consecutive_same_error = 1
            last_codegen_error_type = current_error
            if consecutive_same_error >= 2:
                terminal_codegen_failure = "llm_error"
                terminal_codegen_message = (
                    f"Same error type ({current_error}) 2 times in a row — failing fast: {exc}"
                )
                active_log.info(
                    "Codegen early termination: %s",
                    terminal_codegen_message,
                )
                record_gen_outcome(
                    "codegen",
                    success=False,
                    error_type="llm_error",
                    pattern=pattern,
                )
                break
            if cg_attempt < max_codegen_retries and is_transient_llm_error(exc):
                codegen_retries += 1
                retries_used["codegen"] += 1
                active_log.warning(
                    "Codegen transient error (attempt %d): %s",
                    cg_attempt + 1,
                    exc,
                )
                await sleep(backoff[cg_attempt])
                continue
            active_log.debug("Codegen LLM call failed: %s", exc)
            record_gen_outcome(
                "codegen",
                success=False,
                error_type="llm_error",
                pattern=pattern,
            )
            terminal_codegen_failure = "llm_error"
            terminal_codegen_message = str(exc)
            break

    return WorkflowCodegenRequestResult(
        builder_code=builder_code,
        codegen_retries=codegen_retries,
        terminal_failure=terminal_codegen_failure,
        terminal_message=terminal_codegen_message,
    )


__all__ = [
    "WorkflowCodegenRequestResult",
    "request_builder_code",
]
