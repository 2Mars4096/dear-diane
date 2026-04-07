"""Text-only chat runtime helper extracted from ``ChatManager``."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, AsyncIterator, Callable

from dan.agent_runtime.graph_summary import build_graph_summary
from dan.agent_runtime.streaming import StreamChunkCollector
from dan.agent_runtime.tokens import _get_context_window
from dan.chat_events import (
    ChatCompleteEvent,
    ChatErrorEvent,
    ChatInterruptedEvent,
    ChatStreamEvent,
    ChatTokenEvent,
)
from dan.llm_surface import stream_chat_surface
from dan.models.graph import Graph
from dan.prompt_contracts import PromptEnvelope
from dan.providers.costs import estimate_cost

logger = logging.getLogger(__name__)


async def stream_text_response(
    *,
    manager: Any,
    workflow_id: str,
    message: str,
    history: list[dict[str, str]],
    thread_id: str | None = None,
    client_graph_revision: str | None = None,
    mode: str = "agent",
    cancel_event: asyncio.Event | None = None,
    prompt_envelope: PromptEnvelope | None = None,
    debug_context: str = "",
    prompt_context: str = "",
    mentions: list[Any] | None = None,
    surface_context: dict[str, Any] | None = None,
    surface: str | None = None,
    extra_system_instructions: str = "",
    memory_project_id: str | None = None,
    include_memory_kernel_context: bool = True,
    model_override: str | None = None,
    autonomy_resolution: Any | None = None,
    record_summary: bool = True,
    persist_audit: Callable[..., None],
    friendly_chat_error: Callable[[Exception], str],
) -> AsyncIterator[ChatStreamEvent]:
    """Stream a text-only LLM response using the manager's existing internals."""

    try:
        prompt_metadata: dict[str, Any] = {}
        effective_model = model_override or manager._chat_model
        graph_dict = manager._graph_store.get_graph(workflow_id)
        if graph_dict is None:
            persist_audit(
                workflow_id=workflow_id,
                message_id=uuid.uuid4().hex[:12],
                user_message=message,
                assistant_message="",
                mode=mode,
                model=effective_model,
                audit_tool_records=[],
                prompt_messages=[],
                surface=surface,
                error=f"Workflow '{workflow_id}' not found",
                audit_metadata=prompt_metadata,
            )
            yield ChatErrorEvent(error=f"Workflow '{workflow_id}' not found")
            return

        graph = Graph.model_validate(graph_dict)
        summary = build_graph_summary(graph, workflow_id)

        revision_mismatch = (
            client_graph_revision is not None
            and client_graph_revision != summary.revision
        )
        if revision_mismatch:
            logger.warning(
                "Graph revision mismatch for %s: client=%s current=%s",
                workflow_id,
                client_graph_revision,
                summary.revision,
            )

        text_required_action_hints = (
            ["workflow_edit"] if mode in {"build", "mutate"} else None
        )
        messages = await manager._build_messages(
            summary,
            message,
            history,
            mode=mode,
            prompt_envelope=prompt_envelope,
            debug_context=debug_context,
            prompt_context=prompt_context,
            mentions=mentions,
            workflow_id=workflow_id,
            graph_dict=graph_dict,
            surface_context=surface_context,
            surface=surface,
            extra_system_instructions=extra_system_instructions,
            memory_project_id=memory_project_id,
            include_memory_kernel_context=include_memory_kernel_context,
            tools_available=False,
            required_action_hints=text_required_action_hints,
            prompt_metadata_sink=prompt_metadata,
            model=effective_model,
            autonomy_resolution=autonomy_resolution,
        )

        message_id = uuid.uuid4().hex[:12]
        final_content = ""
        token_usage: dict[str, int] = {}
        interrupted = False
        tracker = getattr(manager, "_resource_tracker", None)
        if tracker is not None:
            await tracker.wait_acquire("llm")
        try:
            collector = StreamChunkCollector(
                cancel_event=cancel_event,
                stream_factory=lambda: stream_chat_surface(
                    manager,
                    messages=messages,
                    model=effective_model,
                    temperature=0.7,
                    pii_session_key=thread_id or workflow_id,
                ),
            )
            async for chunk in collector:
                yield ChatTokenEvent(
                    delta=chunk.delta,
                    accumulated=chunk.accumulated,
                )
            final_content = collector.state.final_content
            token_usage = collector.state.token_usage
            interrupted = collector.state.interrupted
        finally:
            if tracker is not None:
                await tracker.release("llm")

        if interrupted:
            persist_audit(
                workflow_id=workflow_id,
                message_id=message_id,
                user_message=message,
                assistant_message=final_content,
                mode=mode,
                model=effective_model,
                audit_tool_records=[],
                prompt_messages=messages,
                surface=surface,
                error="interrupted",
                audit_metadata=prompt_metadata,
            )
            yield ChatInterruptedEvent(
                message_id=message_id,
                content=final_content,
                token_usage=token_usage,
            )
            return

        if record_summary:
            manager._record_conversation_summary(
                workflow_id=workflow_id,
                user_message=message,
                assistant_message=final_content,
            )

        cost = estimate_cost(
            effective_model,
            token_usage.get("prompt_tokens", 0),
            token_usage.get("completion_tokens", 0),
        )
        persist_audit(
            workflow_id=workflow_id,
            message_id=message_id,
            user_message=message,
            assistant_message=final_content,
            mode=mode,
            model=effective_model,
            audit_tool_records=[],
            prompt_messages=messages,
            surface=surface,
            audit_metadata=prompt_metadata,
        )
        if os.environ.get("DAN_SHOW_COST", "1") == "1" and cost is not None and cost > 0:
            final_content += f"\n\n[~${cost:.4f}]"

        yield ChatCompleteEvent(
            message_id=message_id,
            content=final_content,
            token_usage=token_usage,
            estimated_cost=cost,
            context_window=_get_context_window(effective_model),
            graph_revision=summary.revision,
            revision_mismatch=revision_mismatch,
        )

    except KeyError as exc:
        logger.error("Provider resolution failed: %s", exc)
        persist_audit(
            workflow_id=workflow_id,
            message_id=locals().get("message_id", uuid.uuid4().hex[:12]),
            user_message=message,
            assistant_message="",
            mode=mode,
            model=locals().get("effective_model", manager._chat_model),
            audit_tool_records=[],
            prompt_messages=[],
            surface=surface,
            error=f"Provider error: {exc}",
            audit_metadata=prompt_metadata if "prompt_metadata" in locals() else {},
        )
        yield ChatErrorEvent(error=friendly_chat_error(exc))
    except Exception as exc:
        logger.exception("Chat error for workflow %s", workflow_id)
        persist_audit(
            workflow_id=workflow_id,
            message_id=locals().get("message_id", uuid.uuid4().hex[:12]),
            user_message=message,
            assistant_message="",
            mode=mode,
            model=locals().get("effective_model", manager._chat_model),
            audit_tool_records=[],
            prompt_messages=locals().get("messages", []),
            surface=surface,
            error=str(exc),
            audit_metadata=prompt_metadata if "prompt_metadata" in locals() else {},
        )
        yield ChatErrorEvent(error=friendly_chat_error(exc))
