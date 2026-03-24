"""Text-stream fallback runtime for mutation-aware chat responses."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncIterator, Callable

from dan.agent_runtime.mutation_parsing import (
    _try_parse_mutation_json,
)
from dan.agent_runtime.streaming import StreamChunkCollector
from dan.agent_runtime.tokens import _get_context_window
from dan.chat_events import (
    ChatCompleteEvent,
    ChatGraphQualityEvent,
    ChatInterruptedEvent,
    ChatMutationEvent,
    ChatStreamEvent,
    ChatTokenEvent,
)
from dan.graph_mutator import GraphMutator, MutationPlan
from dan.providers.costs import estimate_cost

logger = logging.getLogger(__name__)


async def stream_json_fallback_response(
    *,
    provider: Any,
    messages: list[dict[str, str]],
    message_id: str,
    revision: str,
    revision_mismatch: bool,
    graph_dict: dict[str, Any],
    workflow_id: str,
    thread_id: str | None,
    user_message: str,
    chat_store: Any,
    cancel_event: asyncio.Event | None = None,
    mode: str = "agent",
    allow_mutation_tool: bool = True,
    effective_model: str,
    record_conversation_summary: Callable[..., None],
    persist_latest_mutation_preview: Callable[..., None],
    normalize_mutation_ops: Callable[
        [dict[str, Any], list[dict[str, Any]], bool],
        tuple[list[dict[str, Any]], list[str]],
    ],
    assess_graph_quality: Callable[[dict[str, Any], str], tuple[int, list[str]] | None]
    | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """Stream a plain-text fallback and promote mutation JSON when present."""

    final_content = ""
    token_usage: dict[str, int] = {}
    interrupted = False

    collector = StreamChunkCollector(
        provider=provider,
        messages=messages,
        model=effective_model,
        temperature=0.7,
        cancel_event=cancel_event,
    )
    async for chunk in collector:
        yield ChatTokenEvent(
            delta=chunk.delta,
            accumulated=chunk.accumulated,
        )
    final_content = collector.state.final_content
    token_usage = collector.state.token_usage
    interrupted = collector.state.interrupted

    if interrupted:
        yield ChatInterruptedEvent(
            message_id=message_id,
            content=final_content,
            token_usage=token_usage,
        )
        return

    mutation_data = (
        _try_parse_mutation_json(final_content) if allow_mutation_tool else None
    )
    if mutation_data is not None:
        try:
            try:
                is_empty_fb = not bool(graph_dict.get("nodes"))
                ops, mechanical_repairs = normalize_mutation_ops(
                    graph_dict,
                    mutation_data.get("operations", []),
                    is_empty_fb,
                )
                for line in mechanical_repairs:
                    logger.info("%s", line)
                plan = MutationPlan.model_validate(
                    {
                        "operations": ops,
                        "description": mutation_data.get("description", ""),
                        "reasoning": mutation_data.get("reasoning", ""),
                        "base_graph_revision": revision,
                    }
                )
            except Exception as plan_exc:
                logger.debug("Fallback mutation plan validation failed: %s", plan_exc)
                plan = MutationPlan(
                    operations=[],
                    description=mutation_data.get("description", ""),
                    reasoning=mutation_data.get("reasoning", ""),
                    base_graph_revision=revision,
                )
            dry_result = GraphMutator().dry_run(
                graph_dict,
                plan,
                current_revision=revision,
            )
            if not dry_result.success and not dry_result.stale_plan:
                error_summary = "; ".join(e.message for e in dry_result.errors)
                logger.info(
                    "Dry-run failed in fallback path for plan %s "
                    "(no auto-retry in fallback): %s",
                    plan.plan_id,
                    error_summary,
                )
            if (
                dry_result.success
                and dry_result.new_graph is not None
                and assess_graph_quality is not None
            ):
                quality_result = assess_graph_quality(dry_result.new_graph, user_message)
                if quality_result is not None:
                    score, concerns = quality_result
                    yield ChatGraphQualityEvent(
                        score=score,
                        concerns=concerns,
                    )
            plan_dump = plan.model_dump()
            if mode == "debug":
                plan_dump.setdefault("metadata", {})["source"] = "debug-fix"
            record_conversation_summary(
                workflow_id=workflow_id,
                user_message=user_message,
                assistant_message=mutation_data.get("reasoning", ""),
            )
            persist_latest_mutation_preview(
                chat_store,
                workflow_id,
                thread_id,
                message_id=message_id,
                mutation_plan=plan_dump,
                dry_run_result=dry_result.model_dump(),
            )
            yield ChatMutationEvent(
                message_id=message_id,
                content=mutation_data.get("reasoning", ""),
                mutation_plan=plan_dump,
                dry_run_result=dry_result.model_dump(),
                token_usage=token_usage,
                context_window=_get_context_window(effective_model),
                graph_revision=revision,
                revision_mismatch=revision_mismatch,
            )
            return
        except Exception as exc:
            logger.debug("JSON fallback mutation parse failed: %s", exc)

    record_conversation_summary(
        workflow_id=workflow_id,
        user_message=user_message,
        assistant_message=final_content,
    )

    cost = estimate_cost(
        effective_model,
        token_usage.get("prompt_tokens", 0),
        token_usage.get("completion_tokens", 0),
    )
    if os.environ.get("DAN_SHOW_COST", "1") == "1" and cost is not None and cost > 0:
        final_content += f"\n\n[~${cost:.4f}]"

    yield ChatCompleteEvent(
        message_id=message_id,
        content=final_content,
        token_usage=token_usage,
        estimated_cost=cost,
        context_window=_get_context_window(effective_model),
        graph_revision=revision,
        revision_mismatch=revision_mismatch,
    )
