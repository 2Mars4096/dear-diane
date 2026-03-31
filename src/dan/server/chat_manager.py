"""Graph-aware chat manager — LLM conversations with workflow context.

This module is a compatibility facade. The support code (events, prompts,
tokens, graph summary, mutation parsing, helpers) now lives in the
``dan.server.chat`` package.  All public symbols are re-exported here so
that existing ``from dan.server.chat_manager import X`` imports continue
to work.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
import logging
import os
import pathlib
import re
import time
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from dan.agent_runtime import AgentEvent, AgentRequest, AgentResult, AgentRuntime

try:
    import tiktoken
    _tiktoken_available = True
except ImportError:
    _tiktoken_available = False

from dan.models.graph import Graph
from dan.llm_surface import (
    complete_chat_surface,
    complete_tool_chat_surface,
    resolve_llm_provider,
    resolve_tool_capable_provider,
    stream_chat_surface,
    wrap_provider_for_pii,
)
from dan.providers import (
    CompletionResult,
    StreamChunk,
    get_model_behavior,
)
from dan.providers.registry import ProviderRegistry
from dan.providers.costs import estimate_cost
from dan.server.capability_registry import CapabilityResult
from dan.server.graph_mutator import (
    GraphMutator,
    PATTERN_LIBRARY,
    _default_node_config,
    _default_ports,
)
from dan.server.graph_store import GraphStore
from dan.server.mutation_metrics import mutation_metrics
from dan.server.agent_runtime.workflow_outcomes import (
    build_codegen_saved_message,
    build_persisted_workflow_reply,
    build_structural_macro_message,
    prepare_validated_workflow_save,
)
from dan.server.agent_runtime.workflow_generation_helpers import (
    exec_deterministic_builder_code as _exec_deterministic_builder_code_impl,
    extract_code_from_response as _extract_code_from_response_impl,
    parse_intent_from_result as _parse_intent_from_result_impl,
    sandbox_exec_builder_code as _sandbox_exec_builder_code_impl,
)
from dan.server.agent_runtime.workflow_generation_stats import (
    get_generation_stats_hint as _get_generation_stats_hint_impl,
    record_generation_outcome as _record_generation_outcome_impl,
)
from dan.server.agent_runtime.workflow_handoff import (
    WorkflowGenerationAttemptOutcome,
    iter_workflow_generation_attempt,
    should_use_workflow_generation_fast_path,
)

# ---------------------------------------------------------------------------
# Re-exports from the ``dan.server.chat`` package
# ---------------------------------------------------------------------------

from dan.server.chat.events import (  # noqa: F401
    NodeSummary,
    EdgeSummary,
    GraphSummary,
    ChatTokenEvent,
    ChatCompleteEvent,
    ChatNoticeEvent,
    ChatErrorEvent,
    ChatMutationEvent,
    ChatInterruptedEvent,
    ChatToolCallStartEvent,
    ChatToolCallResultEvent,
    ChatIntentExtractedEvent,
    ChatCodeGeneratedEvent,
    ChatValidationResultEvent,
    ChatGraphCreatedEvent,
    ChatGraphQualityEvent,
    ChatQueuedEvent,
    ChatFileAttachmentEvent,
    ChatPollRequestEvent,
    ChatMultiPartEvent,
    ChatGenerationSummaryEvent,
    ChatInjectedMessageEvent,
    ChatStreamEvent,
)
from dan.server.chat.helpers import (  # noqa: F401
    _ACTION_HINT_TOOL_MAP,
    _MISSING_TARGET_PROBE_TOOLS,
    _RETRYABLE_CAPABILITY_MAX_RETRIES,
    _MISSING_TARGET_ERROR_TYPES,
    _MISSING_TARGET_ERROR_MARKERS,
    _PARALLEL_TOOL_FAMILY_MAP,
    _dedupe_action_hints,
    _missing_action_hints,
    _preferred_workflow_edit_tool,
    _tool_choice_for_action_hints,
    _tool_retry_prompt_for_missing_actions,
    _tool_schema_name,
    _parallel_tool_family,
    _force_single_tool_request,
    _looks_like_missing_target_error,
    _write_file_escalation_prompt,
    _is_tool_choice_incompatible_error,
    _is_transient_llm_error,
    _classify_llm_error_kind,
    _build_tool_followup_recovery_prompt,
    _build_tool_followup_error_intro,
    _post_tool_followup_retry_delay_seconds,
    _clean_tool_result,
    _summarize_tool_result,
    _extract_cited_sources,
    build_citation_records,
    should_require_web_grounding,
    verify_response_citations,
    _URL_RE,
    CHAT_MODE_ALIASES,
    normalize_chat_mode,
    recent_run_failed_for_workflow,
    detect_chat_mode,
    build_debug_context,
)
from dan.server.search_models import parse_search_result_set
from dan.chat_prompts import (  # noqa: F401
    DEFAULT_PROMPT_MODULE_RESOLVER,
    NODE_TYPES,
    EDGE_TYPES,
    _build_mutation_tool_schema,
    MUTATION_TOOL_SCHEMA,
    _build_node_type_reference,
    NODE_TYPE_REFERENCE,
    SYSTEM_PROMPT_TEMPLATE,
    BUILD_FROM_INTENT_PROMPT,
    _CATEGORY_ORDER,
    generate_capability_reference,
    invalidate_capability_cache,
    ToolReferenceEntry,
    _WHATSAPP_SURFACE_HINTS,
    _RESEARCH_REPORT_PROMPT_HINT,
    _RESEARCH_HINT_CLASSIFIER_SYSTEM_PROMPT,
    _classify_research_prompt_signal,
    _EXPLORATION_HINT_CLASSIFIER_SYSTEM_PROMPT,
    _classify_exploration_prompt_signal,
    PromptContext,
    SURFACE_HINTS,
    _looks_like_research_report_request,
    UNIFIED_SYSTEM_PROMPT,
    EMPTY_GRAPH_SUMMARY_PLACEHOLDER,
    WORKFLOW_TEMPLATES,
    resolve_surface_hints,
)

# Tests and older call sites expect this private alias.
_resolve_surface_hints = resolve_surface_hints

from dan.agent_runtime.tokens import (  # noqa: F401
    MODEL_CONTEXT_WINDOWS,
    _DEFAULT_CONTEXT_WINDOW,
    _NORMALIZED_CONTEXT_WINDOWS,
    _get_context_window,
    _completion_max_tokens,
    estimate_tokens,
    _estimate_messages_tokens,
    _TRUNCATE_RE,
    _truncate_assistant_message,
    _TOOL_SCHEMA_OVERHEAD_TOKENS,
    _compact_context,
    context_pressure_hint,
    compact_history,
)
from dan.agent_runtime.completion import (
    GuardedCompletionForwardedStep,
    GuardedCompletionInterrupted,
    GuardedCompletionProgress,
    forward_guarded_steps,
    iter_guarded_completion,
)
from dan.agent_runtime.recovery import (
    PostToolFollowupResolution,
    recover_text_completion,
    run_post_tool_followup_flow,
)
from dan.agent_runtime.messages import (
    BuiltPromptMessages,
    MentionResolution,
    PromptModuleResolution,
    build_runtime_messages,
    compose_mcp_tools_block,
    compose_memory_kernel_context,
    compose_recent_context_message,
    compose_user_context_block,
    format_surface_context,
    sanitize_history_messages as _sanitize_history_messages,
)
from dan.agent_runtime.profiles import (
    resolve_agent_profile,
)
from dan.agent_runtime.graph_summary import (  # noqa: F401
    compute_graph_revision,
    build_graph_summary,
    _format_node_line,
    serialize_for_prompt,
)
from dan.agent_runtime.followup import (
    build_assistant_followup_message,
    build_post_tool_failure_content,
    prepare_tool_followup_request,
)
from dan.agent_runtime.tool_loop import (
    NoToolTurnResolution,
    resolve_no_tool_turn,
)
from dan.agent_runtime.mutation_preview import (
    _coerce_strict_edges,
    _normalize_generated_mutation_ops,
    build_auto_apply_followup_messages,
    build_mutation_repair_messages,
    build_stale_replan_messages,
    compile_mutation_preview,
    format_mutation_preview_content as _format_mutation_preview_content,
    normalize_mutation_ops_for_chat,
    prepare_mutation_auto_apply,
    resolve_mutation_auto_apply_requested,
    workflow_contract_errors as _workflow_contract_errors,
)
from dan.meta.workflow_contract import (
    classify_run_readiness_issues as _classify_run_readiness_issues,
    workflow_build_provenance as _workflow_build_provenance,
)
from dan.agent_runtime.capability_calls import (
    annotate_capability_call_plan,
    build_pending_capability_calls,
    capability_cache_key,
    copy_capability_result,
    execute_capability_call,
    execute_capability_plan,
    extract_raw_capability_tool_calls,
    split_inventory_then_delete_batch,
)
from dan.server.chat.mutation_parser import (  # noqa: F401
    _normalize_usage,
    _merge_usage_totals,
    _JSON_BLOCK_RE,
    _try_parse_mutation_json,
    extract_mutation_from_result,
    _build_args_preview,
    _build_dry_run_preview,
    _try_persist_audit,
)
from dan.workflow_generation_guidance import (
    coerce_workflow_generation_contract_override,
    render_workflow_clarification_guidance,
    render_workflow_mutation_tool_guidance,
    workflow_generation_contract_enabled,
    workflow_generation_contract_override,
)

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------

_DAN_USE_CODEGEN_BUILD = os.environ.get("DAN_USE_CODEGEN_BUILD", "1")
_MUTATION_AUTO_RETRY = os.environ.get("DAN_MUTATION_AUTO_RETRY", "true").lower() == "true"
try:
    _MUTATION_AUTO_RETRY_MAX = max(0, int(os.environ.get("DAN_MUTATION_AUTO_RETRY_MAX", "2")))
except ValueError:
    _MUTATION_AUTO_RETRY_MAX = 2
try:
    _MUTATION_REPAIR_LLM_TIMEOUT_SECONDS = max(
        1.0,
        float(os.environ.get("DAN_MUTATION_REPAIR_LLM_TIMEOUT", "45")),
    )
except ValueError:
    _MUTATION_REPAIR_LLM_TIMEOUT_SECONDS = 45.0
_MAX_CONTEXT_RATIO = float(os.environ.get("DAN_CHAT_MAX_CONTEXT_RATIO", "0.8"))
_LLM_CALL_TIMEOUT_SECONDS = float(os.environ.get("DAN_LLM_CALL_TIMEOUT", "120"))
_WORKFLOW_GENERATION_PROGRESS_TIMEOUT_SECONDS = float(
    os.environ.get("DAN_WORKFLOW_GENERATION_PROGRESS_TIMEOUT_SECONDS", "30.0")
)
_POST_TOOL_FOLLOWUP_MAX_RETRIES = 2


def _friendly_chat_error(exc: Exception) -> str:
    """Map raw exceptions to actionable user-facing messages."""
    msg = str(exc)
    if isinstance(exc, KeyError):
        return (
            "Model or provider configuration error — check your "
            f"DAN_LLM_* environment variables. (Missing: {msg})"
        )
    lower = msg.lower()
    if "401" in msg or "unauthorized" in lower or "authentication" in lower:
        return "Authentication failed — check your API key and provider configuration."
    if "429" in msg or "rate limit" in lower or "too many requests" in lower:
        return "Rate limited by the LLM provider — please retry shortly."
    if "timeout" in lower or "timed out" in lower:
        return "Request timed out — the LLM provider may be overloaded. Try again."
    if "connection" in lower and ("refused" in lower or "error" in lower or "reset" in lower):
        return "Could not connect to the LLM provider — check your network and DAN_LLM_BASE_URL."
    if any(code in msg for code in ("500", "502", "503")) or "internal server error" in lower:
        return "The LLM provider returned a server error — please retry or check provider status."
    if len(msg) > 200:
        msg = msg[:200] + "…"
    return f"An error occurred: {msg}"


def _workflow_generation_contract_override_from_surface_context(
    surface_context: dict[str, Any] | None,
) -> bool | None:
    if not isinstance(surface_context, dict):
        return None
    return coerce_workflow_generation_contract_override(
        surface_context.get("workflow_generation_contract_enabled"),
    )

__all__ = [
    "NodeSummary",
    "EdgeSummary",
    "GraphSummary",
    "ChatTokenEvent",
    "ChatCompleteEvent",
    "ChatNoticeEvent",
    "ChatErrorEvent",
    "ChatMutationEvent",
    "ChatInterruptedEvent",
    "ChatToolCallStartEvent",
    "ChatToolCallResultEvent",
    "ChatIntentExtractedEvent",
    "ChatCodeGeneratedEvent",
    "ChatValidationResultEvent",
    "ChatGraphCreatedEvent",
    "ChatGraphQualityEvent",
    "ChatGenerationSummaryEvent",
    "ChatInjectedMessageEvent",
    "ChatStreamEvent",
    "MUTATION_TOOL_SCHEMA",
    "ChatManager",
    "build_graph_summary",
    "serialize_for_prompt",
    "compute_graph_revision",
    "BUILD_FROM_INTENT_PROMPT",
    "EMPTY_GRAPH_SUMMARY_PLACEHOLDER",
    "WORKFLOW_TEMPLATES",
    "normalize_chat_mode",
    "recent_run_failed_for_workflow",
    "build_debug_context",
    "_coerce_strict_edges",
    "_normalize_generated_mutation_ops",
    "estimate_tokens",
    "compact_history",
    "MODEL_CONTEXT_WINDOWS",
]

logger = logging.getLogger(__name__)

pii_session_var: ContextVar["Any"] = ContextVar("pii_session", default=None)

# Tests and older call sites expect this private alias.
_build_assistant_followup_message = build_assistant_followup_message


def _disable_tool_access_in_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a shallow copy of *messages* with a system-level tool-disable note."""
    TOOL_DISABLE_NOTE = (
        "\n\nTool calling is disabled for this response. "
        "Answer directly in natural language. Do not reference any tools."
    )
    messages = list(messages)
    if messages and messages[0].get("role") == "system":
        messages[0] = {
            **messages[0],
            "content": (messages[0].get("content") or "") + TOOL_DISABLE_NOTE,
        }
    else:
        messages.insert(0, {"role": "system", "content": TOOL_DISABLE_NOTE.strip()})
    return messages


def _load_recent_search_urls(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
) -> list[str]:
    if chat_store is None or not thread_id:
        return []
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
    except Exception:
        return []
    urls = meta.get("recent_search_urls") if isinstance(meta, dict) else []
    if not isinstance(urls, list):
        return []
    return [str(url or "").strip() for url in urls if str(url or "").strip()][:50]


def _persist_recent_search_urls(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
    urls: list[str],
) -> None:
    if chat_store is None or not thread_id:
        return
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
        if not isinstance(meta, dict):
            meta = {}
        meta["recent_search_urls"] = list(dict.fromkeys(urls))[-50:]
        chat_store.set_thread_meta(workflow_id, thread_id, meta)
    except Exception:
        logger.debug("Failed to persist recent search URLs", exc_info=True)


def _persist_citation_summary(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
    verifications: list[Any],
) -> None:
    if chat_store is None or not thread_id:
        return
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
        if not isinstance(meta, dict):
            meta = {}
        verified = sum(1 for item in verifications if getattr(item, "verified", False))
        unverified = sum(1 for item in verifications if not getattr(item, "verified", False))
        meta["latest_citation_summary"] = {
            "verified": verified,
            "unverified": unverified,
            "ran": bool(verifications),
        }
        chat_store.set_thread_meta(workflow_id, thread_id, meta)
    except Exception:
        logger.debug("Failed to persist citation summary", exc_info=True)


def _persist_latest_mutation_preview(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
    *,
    message_id: str,
    mutation_plan: dict[str, Any],
    dry_run_result: dict[str, Any],
) -> None:
    if chat_store is None or not thread_id:
        return
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
        if not isinstance(meta, dict):
            meta = {}
        meta["latest_mutation_preview"] = {
            "message_id": message_id,
            "mutation_plan": mutation_plan,
            "dry_run_result": dry_run_result,
        }
        chat_store.set_thread_meta(workflow_id, thread_id, meta)
    except Exception:
        logger.debug("Failed to persist latest mutation preview", exc_info=True)


def _clear_latest_mutation_preview(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
) -> None:
    if chat_store is None or not thread_id:
        return
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
        if not isinstance(meta, dict) or "latest_mutation_preview" not in meta:
            return
        meta.pop("latest_mutation_preview", None)
        chat_store.set_thread_meta(workflow_id, thread_id, meta)
    except Exception:
        logger.debug("Failed to clear latest mutation preview", exc_info=True)


def _has_latest_mutation_preview(
    chat_store: Any,
    workflow_id: str,
    thread_id: str | None,
) -> bool:
    if chat_store is None or not thread_id:
        return False
    try:
        thread = chat_store.get_thread(workflow_id, thread_id)
    except Exception:
        thread = None
    if thread is not None:
        for msg in reversed(getattr(thread, "messages", []) or []):
            mutation_plan = getattr(msg, "mutation_plan", None)
            if not isinstance(mutation_plan, dict) or not mutation_plan:
                continue
            if getattr(msg, "mutation_status", None) in (None, "proposed"):
                return True
    try:
        meta = chat_store.get_thread_meta(workflow_id, thread_id)
    except Exception:
        meta = {}
    preview = meta.get("latest_mutation_preview") if isinstance(meta, dict) else None
    return isinstance(preview, dict) and isinstance(preview.get("mutation_plan"), dict)


def _citation_warning_text(verifications: list[Any]) -> str | None:
    flagged = [item for item in verifications if not getattr(item, "verified", True)]
    if not flagged:
        return None
    return (
        "Some cited claims could not be verified against the retrieved source content. "
        "Please double-check the cited source before relying on those figures."
    )


def _progress_ack_event(
    *,
    message_id: str,
    label: str,
    context_window: int,
    graph_revision: str,
    revision_mismatch: bool,
) -> ChatCompleteEvent:
    phase_label = str(label or "").strip()
    return ChatCompleteEvent(
        message_id=message_id,
        content=phase_label,
        token_usage={},
        context_window=context_window,
        graph_revision=graph_revision,
        revision_mismatch=revision_mismatch,
        detected_mode="progress_ack",
        phase_label=phase_label or None,
    )
def _capability_registry_mode(mode: str) -> str:
    """Resolve user-facing chat modes to capability-registry buckets."""
    normalized = normalize_chat_mode(mode)
    return "agent" if normalized == "auto" else normalized


class ChatManager:
    def __init__(
        self,
        provider_registry: ProviderRegistry,
        graph_store: GraphStore,
        mention_resolver: Any | None = None,
        chat_store: Any | None = None,
        capability_registry: Any | None = None,
        capability_context: Any | None = None,
        user_profile: Any | None = None,
        conversation_memory: Any | None = None,
        memory_kernel: Any | None = None,
        telemetry_store: Any | None = None,
        agent_runtime: "AgentRuntime | None" = None,
    ) -> None:
        self._providers = provider_registry
        self._graph_store = graph_store
        self._mention_resolver = mention_resolver
        self._chat_store = chat_store
        self._capability_registry = capability_registry
        self._capability_context = capability_context
        self._user_profile = user_profile
        self._conversation_memory = conversation_memory
        self._memory_kernel = memory_kernel
        self._telemetry_store = telemetry_store
        self._agent_runtime = agent_runtime
        self._behavior_store: Any | None = None
        self._chat_model = os.environ.get(
            "DAN_CHAT_MODEL",
            os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        )
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._injection_queues: dict[str, asyncio.Queue[dict[str, str]]] = {}
        self._prompt_details_by_workflow: dict[str, dict[str, str]] = {}
        self._last_built_prompt_messages: BuiltPromptMessages | None = None
        from dan.server.agent_runtime import WorkflowGenerationRuntime
        self._workflow_generation_runtime = WorkflowGenerationRuntime()

    def _resolve_agent_runtime(self) -> "AgentRuntime":
        if self._agent_runtime is not None:
            return self._agent_runtime

        from dan.agent_runtime import BaseAgentRuntime
        from dan.server.llm_gateway import resolve_model_gateway

        return BaseAgentRuntime(gateway=resolve_model_gateway(self))

    async def run_agent_turn(self, request: "AgentRequest") -> "AgentResult":
        """Delegate a generic agent turn to the extracted runtime seam."""
        return await self._resolve_agent_runtime().run_turn(request)

    async def stream_agent_turn(
        self,
        request: "AgentRequest",
    ) -> AsyncIterator["AgentEvent"]:
        """Stream a generic agent turn through the extracted runtime seam."""
        async for event in self._resolve_agent_runtime().stream_turn(request):
            yield event

    def register_stream(self, channel_id: str) -> asyncio.Event:
        """Register a cancellation event for an active stream."""
        evt = asyncio.Event()
        self._cancel_events[channel_id] = evt
        self._injection_queues[channel_id] = asyncio.Queue()
        return evt

    def cancel_stream(self, channel_id: str) -> bool:
        """Signal a running stream to stop. Returns True if stream was found."""
        evt = self._cancel_events.get(channel_id)
        if evt is None:
            return False
        evt.set()
        return True

    def unregister_stream(self, channel_id: str) -> None:
        """Clean up a finished stream's cancellation event."""
        self._cancel_events.pop(channel_id, None)
        self._injection_queues.pop(channel_id, None)

    def inject_message(self, channel_id: str, content: str, inject_id: str) -> bool:
        """Inject a user message into an active stream's tool loop.

        Returns True if the stream was found and the message was queued.
        """
        q = self._injection_queues.get(channel_id)
        if q is None:
            return False
        q.put_nowait({"content": content, "inject_id": inject_id})
        return True

    def _drain_injections(self, channel_id: str) -> list[dict[str, str]]:
        """Drain all pending injected messages for a stream (non-blocking)."""
        q = self._injection_queues.get(channel_id)
        if q is None:
            return []
        items: list[dict[str, str]] = []
        while not q.empty():
            try:
                items.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items

    def set_prompt_details(self, workflow_id: str, details: dict[str, str]) -> None:
        workflow_key = str(workflow_id or "").strip()
        if not workflow_key:
            return
        if details:
            self._prompt_details_by_workflow[workflow_key] = dict(details)
        else:
            self._prompt_details_by_workflow.pop(workflow_key, None)

    def clear_prompt_details(self, workflow_id: str) -> None:
        workflow_key = str(workflow_id or "").strip()
        if workflow_key:
            self._prompt_details_by_workflow.pop(workflow_key, None)

    def get_prompt_detail(self, workflow_id: str, detail_id: str) -> str | None:
        workflow_key = str(workflow_id or "").strip()
        detail_key = str(detail_id or "").strip()
        if not workflow_key or not detail_key:
            return None
        return (self._prompt_details_by_workflow.get(workflow_key) or {}).get(detail_key)

    def set_behavior_store(self, store: Any) -> None:
        """Inject a BehaviorStore for domain detection and parameter resolution."""
        self._behavior_store = store
        if store is None:
            return
        try:
            store.register_seed("prompts/runtime.unified_system", UNIFIED_SYSTEM_PROMPT)
        except Exception:
            logger.debug("Failed to register runtime prompt seeds", exc_info=True)

    def _resolve_behavior_prompt(
        self,
        key: str,
        default: str,
    ) -> tuple[str, int | None]:
        store = self._behavior_store
        if store is None:
            return default, None
        try:
            value = store.get(key, default)
            artifact = store.get_artifact(key)
            version = artifact.version if artifact is not None else None
            if isinstance(value, str) and value.strip():
                return value, version
        except Exception:
            logger.debug("Failed to resolve prompt override for %s", key, exc_info=True)
        return default, None

    def _emit_intent_extraction_telemetry(
        self,
        workflow_id: str,
        extracted: bool,
        fully_covered: bool,
        recommendation: str | None,
        stage_count: int,
        patterns: list[str] | None,
    ) -> None:
        """Fire-and-forget: record intent extraction outcome (33-6)."""
        store = getattr(self, "_telemetry_store", None)
        if store is None:
            return
        try:
            from dan.server.telemetry import TelemetryEvent

            ev = TelemetryEvent(
                event_type="intent_extraction",
                graph_id=workflow_id,
                metadata={
                    "extracted": extracted,
                    "fully_covered": fully_covered,
                    "recommendation": recommendation or "",
                    "stage_count": stage_count,
                    "patterns": patterns or [],
                },
            )
            asyncio.create_task(store.record(ev))
        except Exception:
            logger.debug("Intent extraction telemetry failed", exc_info=True)

    # -- Preflight tool hooks -----------------------------------------------

    _preflight_cache: dict[str, tuple[Any, dict]] | None = None

    @classmethod
    def _get_preflight_tools(cls) -> dict[str, tuple[Any, dict]]:
        if cls._preflight_cache is None:
            try:
                from dan.tools import get_preflight_tools
                cls._preflight_cache = get_preflight_tools()
            except Exception:
                logger.debug("Preflight tool discovery failed", exc_info=True)
                cls._preflight_cache = {}
        return cls._preflight_cache

    async def _run_preflight_hooks(self, user_message: str) -> str:
        """Execute preflight tool hooks and return formatted context lines.

        Tools declare a ``preflight`` block in their TOOL_METADATA:
          trigger: "always" — run on every message
          trigger: "pattern" + patterns: [...] — run when any regex matches
          format: template string populated from the tool's return dict
          args: default kwargs passed to the tool function

        Returns a combined string (one line per hook result) suitable for
        injection into the system prompt.  Empty string if no hooks fire.
        """
        hooks = self._get_preflight_tools()
        if not hooks:
            return ""

        import re as _re

        lines: list[str] = []
        for tool_id, (fn, meta) in hooks.items():
            pf = meta["preflight"]
            trigger = pf.get("trigger", "pattern")

            should_run = False
            if trigger == "always":
                should_run = True
            elif trigger == "pattern":
                patterns = pf.get("patterns") or []
                should_run = any(
                    _re.search(pat, user_message, _re.IGNORECASE)
                    for pat in patterns
                )

            if not should_run:
                continue

            try:
                args = dict(pf.get("args") or {})
                result = await fn(**args)
                fmt = pf.get("format")
                if fmt and isinstance(result, dict):
                    lines.append(fmt.format(**result))
                elif isinstance(result, dict):
                    lines.append(f"[{tool_id}] {result}")
                else:
                    lines.append(f"[{tool_id}] {result}")
            except Exception:
                logger.debug("Preflight hook %s failed", tool_id, exc_info=True)

        return "\n".join(lines)

    def _resolve_provider(
        self,
        *,
        pii_session_key: str | None = None,
        model: str | None = None,
    ) -> Any:
        """Resolve the active provider and apply shared PII wrapping."""
        return resolve_llm_provider(
            self,
            model=model or self._chat_model,
            pii_session_key=pii_session_key,
        )

    @property
    def provider_registry(self) -> ProviderRegistry:
        """Expose the provider registry through a public runtime seam."""

        return self._providers

    @property
    def default_llm_model(self) -> str:
        """Expose the default chat model through a public runtime seam."""

        return self._chat_model

    def resolve_llm_provider(
        self,
        *,
        model: str | None = None,
        pii_session_key: str | None = None,
    ) -> Any:
        """Public wrapper around provider resolution for orchestration layers."""

        return self._resolve_provider(
            pii_session_key=pii_session_key,
            model=model,
        )

    def _wrap_provider_for_pii(
        self,
        provider: Any,
        *,
        pii_session_key: str | None = None,
    ) -> Any:
        """Wrap a resolved provider with request-scoped PII protection if enabled."""
        return wrap_provider_for_pii(
            provider,
            pii_session_key=pii_session_key,
        )

    def _compose_user_context_block(self) -> str:
        """Compatibility wrapper for the canonical agent-runtime helper."""
        return compose_user_context_block(self._user_profile)

    def _compose_mcp_tools_block(self) -> str:
        """Compatibility wrapper for the canonical agent-runtime helper."""
        return compose_mcp_tools_block(self._capability_context)

    def _compose_recent_context_message(self, user_message: str = "") -> str:
        """Compatibility wrapper for the canonical agent-runtime helper."""
        return compose_recent_context_message(
            self._conversation_memory,
            user_message=user_message,
        )

    def _compose_memory_kernel_context(
        self,
        user_message: str,
        *,
        project_id: str | None = None,
    ) -> str:
        """Compatibility wrapper for the canonical agent-runtime helper."""
        from dan.engine.memory_kernel import MemoryType, classify_task_type

        return compose_memory_kernel_context(
            self._memory_kernel,
            user_message,
            project_id=project_id,
            classify_task_type=classify_task_type,
            working_state_tag=MemoryType.WORKING_STATE.value,
            logger=logger,
        )

    def _record_to_memory_kernel(
        self,
        *,
        user_message: str,
        assistant_message: str,
        workflow_id: str = "",
    ) -> None:
        """Store interaction summary as an EPISODE in the memory kernel."""
        kernel = self._memory_kernel
        if kernel is None:
            return
        try:
            user_text = " ".join(user_message.split()).strip()[:150]
            assistant_text = " ".join(assistant_message.split()).strip()[:200]
            if not user_text:
                return
            summary = f"User: {user_text}. Assistant: {assistant_text}"
            kernel.store_episode(summary, tags=[workflow_id] if workflow_id else [])
        except Exception:
            logger.debug("Failed to store to memory kernel", exc_info=True)

    def _record_conversation_summary(
        self,
        *,
        workflow_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Persist a short exchange summary for cross-session recall."""
        self._record_to_memory_kernel(
            user_message=user_message,
            assistant_message=assistant_message,
            workflow_id=workflow_id,
        )
        if self._conversation_memory is None:
            return
        user_text = " ".join(user_message.split()).strip()
        assistant_text = " ".join(assistant_message.split()).strip()
        if not user_text or not assistant_text:
            return

        summary = (
            f"User asked: {user_text[:120]}. "
            f"Assistant replied: {assistant_text[:180]}"
        )
        topic_tags: list[str] = []
        profile = self._user_profile
        if profile is not None:
            domains = getattr(profile, "common_domains", []) or []
            lower_user = user_text.lower()
            for domain in domains[:5]:
                if isinstance(domain, str) and domain and domain.lower() in lower_user:
                    topic_tags.append(domain)

        try:
            self._conversation_memory.add_summary(
                summary=summary,
                workflow_id=workflow_id,
                topic_tags=topic_tags,
            )
        except Exception:
            logger.debug("Failed to write conversation summary", exc_info=True)

    # ------------------------------------------------------------------
    # Text-only streaming path (original)
    # ------------------------------------------------------------------

    async def send_message(
        self,
        workflow_id: str,
        message: str,
        history: list[dict[str, str]],
        thread_id: str | None = None,
        client_graph_revision: str | None = None,
        mode: str = "agent",
        cancel_event: asyncio.Event | None = None,
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
    ) -> AsyncIterator[ChatStreamEvent]:
        """Stream a text-only LLM response (no function calling)."""
        contract_override = _workflow_generation_contract_override_from_surface_context(
            surface_context,
        )
        with workflow_generation_contract_override(contract_override):
            from dan.agent_runtime.text_runtime import stream_text_response

            async for event in stream_text_response(
                manager=self,
                workflow_id=workflow_id,
                message=message,
                history=history,
                thread_id=thread_id,
                client_graph_revision=client_graph_revision,
                mode=mode,
                cancel_event=cancel_event,
                debug_context=debug_context,
                prompt_context=prompt_context,
                mentions=mentions,
                surface_context=surface_context,
                surface=surface,
                extra_system_instructions=extra_system_instructions,
                memory_project_id=memory_project_id,
                include_memory_kernel_context=include_memory_kernel_context,
                model_override=model_override,
                autonomy_resolution=autonomy_resolution,
                record_summary=record_summary,
                persist_audit=_try_persist_audit,
                friendly_chat_error=_friendly_chat_error,
            ):
                yield event

    # ------------------------------------------------------------------
    # Function-calling path (mutations via tool use)
    # ------------------------------------------------------------------

    async def send_message_with_tools(
        self,
        workflow_id: str,
        message: str,
        history: list[dict[str, str]],
        thread_id: str | None = None,
        client_graph_revision: str | None = None,
        mode: str = "agent",
        cancel_event: asyncio.Event | None = None,
        debug_context: str = "",
        prompt_context: str = "",
        mentions: list[Any] | None = None,
        surface_context: dict[str, Any] | None = None,
        max_tool_turns: int = 24,
        allow_mutation_tool: bool = True,
        surface: str | None = None,
        audit_metadata: dict[str, Any] | None = None,
        extra_system_instructions: str = "",
        required_action_hints: list[str] | None = None,
        stream_channel_id: str | None = None,
        memory_project_id: str | None = None,
        include_memory_kernel_context: bool = True,
        model_override: str | None = None,
        autonomy_resolution: Any | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Process a user message using LLM function calling for graph mutations.

        Falls back to the text-streaming path when the provider does not
        support the ``tools`` parameter.
        """
        contract_override = _workflow_generation_contract_override_from_surface_context(
            surface_context,
        )
        contract_override_ctx = workflow_generation_contract_override(
            contract_override,
        )
        contract_override_ctx.__enter__()
        try:
            effective_model = model_override or self._chat_model
            required_action_hints = _dedupe_action_hints(required_action_hints)
            audit_metadata = dict(audit_metadata or {})
            capability_mode = _capability_registry_mode(mode)
            grounding_required = should_require_web_grounding(
                message,
                mode=capability_mode,
                required_action_hints=required_action_hints,
                mentions=mentions,
            )
            if grounding_required and "search_web" not in required_action_hints:
                required_action_hints = [*required_action_hints, "search_web"]
            graph_dict = self._graph_store.get_graph(workflow_id)
            if graph_dict is None:
                _try_persist_audit(
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
                    audit_metadata=audit_metadata,
                )
                yield ChatErrorEvent(error=f"Workflow '{workflow_id}' not found")
                return

            graph = Graph.model_validate(graph_dict)
            summary = build_graph_summary(graph, workflow_id)
            revision = summary.revision
            is_empty_graph = summary.node_count == 0 and summary.edge_count == 0

            revision_mismatch = (
                client_graph_revision is not None
                and client_graph_revision != revision
            )
            if revision_mismatch:
                logger.warning(
                    "Graph revision mismatch for %s: client=%s current=%s",
                    workflow_id,
                    client_graph_revision,
                    revision,
                )

            # -- Codegen / intent-compiler fast path ----------------------
            use_codegen = should_use_workflow_generation_fast_path(
                is_empty_graph=is_empty_graph,
                allow_mutation_tool=allow_mutation_tool,
                mode=mode,
                codegen_enabled=_DAN_USE_CODEGEN_BUILD == "1",
            )
            workflow_generation_fallback_active = False
            if use_codegen:
                message_id = uuid.uuid4().hex[:12]
                handoff_outcome: WorkflowGenerationAttemptOutcome | None = None
                async for workflow_event in iter_workflow_generation_attempt(
                    generate_workflow=self._generate_workflow_from_intent(
                        user_message=message,
                        workflow_id=workflow_id,
                        channel_id=thread_id or workflow_id,
                        effective_model=effective_model,
                    ),
                    progress_event_factory=lambda: _progress_ack_event(
                        message_id=message_id,
                        label="",
                        context_window=_get_context_window(effective_model),
                        graph_revision=revision,
                        revision_mismatch=revision_mismatch,
                    ),
                    message_id=message_id,
                    progress_timeout_seconds=_WORKFLOW_GENERATION_PROGRESS_TIMEOUT_SECONDS,
                ):
                    if isinstance(workflow_event, WorkflowGenerationAttemptOutcome):
                        handoff_outcome = workflow_event
                    else:
                        yield workflow_event

                assert handoff_outcome is not None
                graph_result = handoff_outcome.graph_result
                codegen_events = handoff_outcome.runtime_events

                if graph_result is not None:
                    saved_graph = self._graph_store.save_graph(workflow_id, graph_result)
                    saved_graph_dict = (
                        saved_graph if isinstance(saved_graph, dict) else graph_result
                    )
                    # A.1-2: enrich response text with path info for non-trivial paths
                    generation_summary_event = next(
                        (e for e in codegen_events if isinstance(e, ChatGenerationSummaryEvent)),
                        None,
                    )
                    new_graph = Graph.model_validate(saved_graph_dict)
                    new_summary = build_graph_summary(new_graph, workflow_id)
                    workflow_name = str(new_summary.name or "").strip()
                    node_preview_items = [
                        str(getattr(node, "name", "") or getattr(node, "id", "")).strip()
                        for node in new_graph.nodes[:5]
                    ]
                    summary_message = build_codegen_saved_message(
                        workflow_id=workflow_id,
                        workflow_name=workflow_name,
                        node_count=new_summary.node_count,
                        edge_count=new_summary.edge_count,
                        node_preview_items=node_preview_items,
                        generation_summary_event=generation_summary_event,
                        build_summary=getattr(generation_summary_event, "build_summary", None),
                    )
                    terminal_reply = build_persisted_workflow_reply(
                        workflow_id=workflow_id,
                        saved_graph=saved_graph_dict,
                        message_id=message_id,
                        assistant_message=summary_message,
                        context_window=_get_context_window(effective_model),
                        detected_mode="agent",
                    )
                    self._record_conversation_summary(
                        workflow_id=workflow_id,
                        user_message=message,
                        assistant_message=terminal_reply.assistant_message,
                    )
                    yield terminal_reply.graph_created_event
                    yield terminal_reply.complete_event
                    return
                else:
                    logger.info(
                        "Codegen path failed for %s, falling back to mutation path",
                        workflow_id,
                    )
                    workflow_generation_fallback_active = True

            # -- 32-4: Structural mutation macro fast path ------------------
            if (
                not is_empty_graph
                and allow_mutation_tool
                and mode in ("agent", "build", "mutate")
                and graph_dict is not None
            ):
                try:
                    from dan.meta.structural_mutations import dispatch_compound_mutations

                    structural_graph = copy.deepcopy(graph_dict)
                    dispatch = dispatch_compound_mutations(structural_graph, message)
                    macro_fast_path_matched = bool(
                        dispatch.matched
                        and (
                            (dispatch.results and all(r.success for r in dispatch.results))
                            or (dispatch.result and dispatch.result.success)
                        )
                    )
                    if macro_fast_path_matched:
                        from dan.meta.workflow_contract import validate_workflow_build_contract

                        prepared_save = prepare_validated_workflow_save(
                            structural_graph,
                            validate_graph=lambda candidate_graph: validate_workflow_build_contract(
                                candidate_graph,
                                workflow_id=workflow_id,
                                apply_repairs=True,
                            ),
                            collect_contract_errors=_workflow_contract_errors,
                            blocked_default_message="Structural macro changes were not run-ready.",
                        )
                        if prepared_save.validation_errors:
                            errors = list(prepared_save.validation_errors)
                            provenance = _workflow_build_provenance(prepared_save.contract_report)
                            yield ChatValidationResultEvent(
                                success=False,
                                error_count=len(errors),
                                errors=errors,
                                failure_mode=_classify_run_readiness_issues(
                                    list(getattr(prepared_save.contract_report, "run_readiness_issues", []) or [])
                                ),
                                **provenance,
                            )
                        elif prepared_save.graph_to_save is not None:
                            provenance = _workflow_build_provenance(prepared_save.contract_report)
                            yield ChatValidationResultEvent(
                                success=True,
                                error_count=0,
                                errors=[],
                                **provenance,
                            )
                            graph_dict = self._graph_store.save_graph(
                                workflow_id,
                                prepared_save.graph_to_save,
                            )
                            try:
                                from dan.meta.graph_quality import compute_quality_report
                                report = compute_quality_report(graph_dict, message, tier=None)
                                yield ChatGraphQualityEvent(
                                    score=report.overall_score,
                                    concerns=report.concerns,
                                )
                            except Exception:
                                pass
                            macro_msg = build_structural_macro_message(
                                dispatch,
                                build_summary=provenance.get("build_summary"),
                            )
                            message_id = uuid.uuid4().hex[:12]
                            terminal_reply = build_persisted_workflow_reply(
                                workflow_id=workflow_id,
                                saved_graph=graph_dict,
                                message_id=message_id,
                                assistant_message=macro_msg,
                                context_window=_get_context_window(effective_model),
                                detected_mode=mode,
                            )
                            self._record_conversation_summary(
                                workflow_id=workflow_id,
                                user_message=message,
                                assistant_message=terminal_reply.assistant_message,
                            )
                            yield terminal_reply.graph_created_event
                            yield terminal_reply.complete_event
                            return
                except Exception:
                    logger.debug("Structural mutation dispatch failed, continuing to mutation path", exc_info=True)

            # -- Mutation path (extended with capability tools) -------------
            prompt_metadata: dict[str, Any] = {}
            messages = await self._build_messages(
                summary, message, history, mode=mode, debug_context=debug_context,
                prompt_context=prompt_context,
                mentions=mentions, workflow_id=workflow_id, graph_dict=graph_dict,
                surface_context=surface_context,
                surface=surface,
                extra_system_instructions=extra_system_instructions,
                memory_project_id=memory_project_id,
                include_memory_kernel_context=include_memory_kernel_context,
                allow_mutation_tool=allow_mutation_tool,
                required_action_hints=required_action_hints,
                prompt_metadata_sink=prompt_metadata,
                model=effective_model,
                autonomy_resolution=autonomy_resolution,
            )
            audit_metadata.update(prompt_metadata)
            provider = resolve_tool_capable_provider(
                self,
                model=effective_model,
                pii_session_key=thread_id or workflow_id,
                logger_override=logger,
            )
            model_behavior = get_model_behavior(provider, effective_model)
            exact_tool_choice_supported = model_behavior.supports_exact_tool_choice
            required_tool_choice_supported = model_behavior.supports_required_tool_choice
            allow_exact_tool_choice = exact_tool_choice_supported
            allow_required_tool_choice = required_tool_choice_supported
            replay_raw_assistant_messages = (
                model_behavior.assistant_replay_mode == "raw"
            )
            message_id = uuid.uuid4().hex[:12]
            usage_totals: dict[str, int] = {}
            completion_max_tokens = _completion_max_tokens(effective_model)

            from dan.server.capability_registry import READ_ONLY_MODES

            all_tools: list[dict[str, Any]] = []
            if self._capability_registry is not None:
                all_tools = list(self._capability_registry.get_tools(capability_mode))
            prompt_supports_load_prompt_detail = bool(
                prompt_metadata.get("supports_load_prompt_detail")
            )
            if "supports_load_prompt_detail" not in prompt_metadata:
                prompt_supports_load_prompt_detail = any(
                    str(message_obj.get("role") or "") == "system"
                    and "load_prompt_detail" in str(message_obj.get("content") or "")
                    for message_obj in messages
                    if isinstance(message_obj, dict)
                )
            if not prompt_supports_load_prompt_detail:
                all_tools = [
                    tool for tool in all_tools
                    if str(((tool.get("function") or {}).get("name")) or "") != "load_prompt_detail"
                ]
            if allow_mutation_tool and capability_mode not in READ_ONLY_MODES:
                all_tools.append(MUTATION_TOOL_SCHEMA)
            available_tool_names_for_turn = {
                _tool_schema_name(tool)
                for tool in all_tools
                if isinstance(tool, dict)
            }
            satisfied_tool_names: set[str] = set()
            successful_tool_results: list[dict[str, Any]] = []
            force_file_write_next_turn = False
            pending_run_status_check_id: str | None = None
            latest_mutation_preview_available = _has_latest_mutation_preview(
                self._chat_store,
                workflow_id,
                thread_id,
            )
            preferred_workflow_edit_tool = _preferred_workflow_edit_tool(
                required_action_hints,
                message,
                preview_available=latest_mutation_preview_available,
                allow_plan_graph_mutations=(
                    allow_mutation_tool and capability_mode not in READ_ONLY_MODES
                ),
                allow_apply_last_mutation=(
                    self._capability_registry is not None
                    and self._capability_registry.is_available(
                        "apply_last_mutation",
                        capability_mode,
                    )
                ),
                allow_delete_graph=(
                    self._capability_registry is not None
                    and self._capability_registry.is_available(
                        "delete_graph",
                        capability_mode,
                    )
                ),
            )

            def _retry_prompt_for_available_tools(missing_action_hints: list[str]) -> str:
                return _tool_retry_prompt_for_missing_actions(
                    missing_action_hints,
                    available_tool_names=available_tool_names_for_turn,
                )

            def _tool_request_config(
                *,
                force_file_write_now: bool = False,
            ) -> tuple[list[dict[str, Any]], str | dict[str, Any]]:
                if pending_run_status_check_id:
                    return _force_single_tool_request(
                        all_tools,
                        "get_run_status",
                        allow_exact_tool_choice=allow_exact_tool_choice,
                        allow_required_tool_choice=allow_required_tool_choice,
                    )
                should_force_file_write = (
                    force_file_write_now
                    and "write_file" in required_action_hints
                    and "file_write" not in satisfied_tool_names
                )
                if should_force_file_write:
                    return _force_single_tool_request(
                        all_tools,
                        "file_write",
                        allow_exact_tool_choice=allow_exact_tool_choice,
                        allow_required_tool_choice=allow_required_tool_choice,
                    )
                if (
                    preferred_workflow_edit_tool
                    and preferred_workflow_edit_tool in available_tool_names_for_turn
                    and preferred_workflow_edit_tool not in satisfied_tool_names
                ):
                    return _force_single_tool_request(
                        all_tools,
                        preferred_workflow_edit_tool,
                        allow_exact_tool_choice=allow_exact_tool_choice,
                        allow_required_tool_choice=allow_required_tool_choice,
                    )
                return (
                    all_tools,
                    _tool_choice_for_action_hints(
                        required_action_hints,
                        satisfied_tool_names,
                        tool_results=successful_tool_results,
                        allow_exact_tool_choice=allow_exact_tool_choice,
                        allow_required_tool_choice=allow_required_tool_choice,
                    ),
                )

            def _initial_tool_request_max_tokens(
                request_tools: list[dict[str, Any]],
                request_tool_choice: str | dict[str, Any],
            ) -> int:
                if (
                    isinstance(request_tools, list)
                    and len(request_tools) == 1
                    and request_tool_choice != "auto"
                ):
                    return min(completion_max_tokens, 4096)
                return completion_max_tokens

            async def _iter_guarded_complete(
                *,
                request_kwargs: dict[str, Any],
                interrupted_content: str | Callable[[], str] | None = None,
                emit_progress_ack: bool = False,
                timeout_seconds: float | None = None,
            ) -> AsyncIterator[ChatStreamEvent | CompletionResult]:
                async def _run_complete_request() -> CompletionResult:
                    tracker = getattr(self, "_resource_tracker", None)
                    if tracker is not None:
                        await tracker.wait_acquire("llm")
                    try:
                        request_tools = request_kwargs.get("tools") or []
                        if request_tools:
                            return await complete_tool_chat_surface(
                                self,
                                pii_session_key=thread_id or workflow_id,
                                logger_override=logger,
                                **request_kwargs,
                            )
                        return await complete_chat_surface(
                            self,
                            pii_session_key=thread_id or workflow_id,
                            **request_kwargs,
                        )
                    finally:
                        if tracker is not None:
                            await tracker.release("llm")

                async for step in iter_guarded_completion(
                    run=_run_complete_request,
                    cancel_event=cancel_event,
                    timeout_seconds=(
                        timeout_seconds
                        if timeout_seconds is not None
                        else _LLM_CALL_TIMEOUT_SECONDS
                    ),
                    poll_interval_seconds=8.0,
                    interrupted_content=interrupted_content,
                    log=logger,
                    log_model=str(request_kwargs.get("model", effective_model) or ""),
                    log_emit_progress_ack=emit_progress_ack,
                    log_message_count=len(request_kwargs.get("messages") or []),
                    log_tool_names=tuple(
                        str((tool.get("function") or {}).get("name") or "").strip()
                        for tool in (request_kwargs.get("tools") or [])
                        if isinstance(tool, dict)
                        and isinstance(tool.get("function"), dict)
                        and str((tool.get("function") or {}).get("name") or "").strip()
                    ),
                ):
                    if isinstance(step, GuardedCompletionInterrupted):
                        yield ChatInterruptedEvent(
                            message_id=message_id,
                            content=step.content,
                            token_usage={},
                        )
                        return
                    if isinstance(step, GuardedCompletionProgress):
                        if emit_progress_ack:
                            yield ChatCompleteEvent(
                                message_id=message_id,
                                content="",
                                token_usage={},
                                context_window=_get_context_window(effective_model),
                                graph_revision=revision,
                                revision_mismatch=revision_mismatch,
                                detected_mode="progress_ack",
                            )
                        continue
                    yield step

            def _compile_preview(
                mutation_payload: dict[str, Any],
                *,
                current_graph: dict[str, Any],
                current_revision: str,
            ) -> Any:
                return compile_mutation_preview(
                    mutation_payload=mutation_payload,
                    graph_snapshot=current_graph,
                    base_revision=current_revision,
                    is_empty_graph=is_empty_graph,
                    normalize_mutation_ops=normalize_mutation_ops_for_chat,
                    logger_override=logger,
                )

            try:
                # Retry loop for transient errors
                attempt = 0
                tool_choice_compat_fallback_used = False
                while attempt < 2:
                    try:
                        request_tools, request_tool_choice = _tool_request_config()
                        result: CompletionResult | None = None
                        outcome_relay = forward_guarded_steps(
                            _iter_guarded_complete(
                                request_kwargs={
                                    "messages": messages,
                                    "model": effective_model,
                                    "temperature": 0.7,
                                    "max_tokens": _initial_tool_request_max_tokens(
                                        request_tools,
                                        request_tool_choice,
                                    ),
                                    "tools": request_tools,
                                    "tool_choice": request_tool_choice,
                                },
                                interrupted_content="",
                                emit_progress_ack=True,
                            ),
                        )
                        async for step in outcome_relay:
                            yield step.event
                        outcome = outcome_relay.outcome_or_empty()
                        if outcome.interrupted:
                            return
                        result = outcome.result
                        if result is None:
                            raise RuntimeError("Initial tool completion produced no result")
                        usage_totals = _merge_usage_totals(usage_totals, result.usage)
                        _init_fr = getattr(result, "finish_reason", "") or ""
                        _init_usage = result.usage or {}
                        logger.info(
                            "Initial LLM call: finish_reason=%s, has_text=%s, has_tools=%s, "
                            "prompt_tokens=%s, completion_tokens=%s",
                            _init_fr or "n/a",
                            bool((result.text or "").strip()),
                            bool(result.tool_calls),
                            _init_usage.get("prompt_tokens", "?"),
                            _init_usage.get("completion_tokens", "?"),
                        )
                        break
                    except Exception as e:
                        if (
                            not tool_choice_compat_fallback_used
                            and _is_tool_choice_incompatible_error(e)
                            and (allow_exact_tool_choice or allow_required_tool_choice)
                        ):
                            logger.warning(
                                "Provider rejected explicit tool_choice for model %s; retrying with auto tool choice",
                                effective_model,
                            )
                            allow_exact_tool_choice = False
                            allow_required_tool_choice = False
                            tool_choice_compat_fallback_used = True
                            continue
                        _is_transient = (
                            isinstance(e, (asyncio.TimeoutError, TimeoutError))
                            or "timeout" in str(e).lower()
                            or "rate" in str(e).lower()
                            or "connection" in str(e).lower()
                        )
                        if attempt < 1 and _is_transient:
                            logger.warning("Transient error in LLM call (attempt %d), retrying: %s", attempt, e)
                            await asyncio.sleep(2 * (attempt + 1))
                            attempt += 1
                            continue
                        raise
                else:
                    raise RuntimeError("Initial tool completion retries exhausted")
            except Exception as exc:
                logger.warning(
                    "Tool-calling complete() failed (%s), falling back to text-only stream",
                    exc,
                )
                try:
                    fallback_messages = await self._build_messages(
                        summary,
                        message,
                        history,
                        mode=mode,
                        debug_context=debug_context,
                        prompt_context=prompt_context,
                        mentions=mentions,
                        surface_context=surface_context,
                        workflow_id=workflow_id,
                        graph_dict=graph_dict,
                        surface=surface,
                        extra_system_instructions=extra_system_instructions,
                        memory_project_id=memory_project_id,
                        include_memory_kernel_context=include_memory_kernel_context,
                        tools_available=False,
                        allow_mutation_tool=allow_mutation_tool,
                        required_action_hints=required_action_hints,
                        prompt_metadata_sink=prompt_metadata,
                        model=effective_model,
                        autonomy_resolution=autonomy_resolution,
                    )
                    async for event in self._stream_with_json_fallback(
                        provider, fallback_messages, message_id,
                        revision, revision_mismatch, graph_dict,
                        workflow_id=workflow_id,
                        thread_id=thread_id,
                        user_message=message,
                        cancel_event=cancel_event,
                        mode=mode,
                        allow_mutation_tool=allow_mutation_tool,
                        effective_model=effective_model,
                    ):
                        yield event
                except Exception as fallback_exc:
                    logger.error(
                        "Text-only fallback also failed: %s", fallback_exc,
                    )
                    yield ChatCompleteEvent(
                        message_id=message_id,
                        content=f"Both tool-calling and text-only paths failed. Error: {fallback_exc}",
                        token_usage={},
                        context_window=_get_context_window(effective_model),
                        graph_revision=revision,
                        revision_mismatch=revision_mismatch,
                    )
                return

            # -- Multi-turn tool loop ------------------------------------
            combined_text_parts: list[str] = []
            last_stream_channel_id: str | None = None
            audit_tool_records: list[dict[str, Any]] = []
            emitted_attachment_paths: set[str] = set()
            tool_result_cache: dict[str, CapabilityResult] = {}
            file_read_cache: dict[str, list[tuple[int, float, CapabilityResult]]] = {}
            completion_review_requested = False
            search_state = {
                "turn_seen_urls": [],
                "session_seen_order": _load_recent_search_urls(
                    self._chat_store,
                    workflow_id,
                    thread_id,
                ),
            }
            web_budget_state = {
                "web_search_calls_made": 0,
                "web_fetch_attempts_made": 0,
                "max_web_search_calls_per_turn": int(
                    os.environ.get("DAN_MAX_WEB_SEARCH_CALLS_PER_TURN", "4")
                ),
                "max_web_fetch_attempts_per_turn": int(
                    os.environ.get("DAN_MAX_WEB_FETCH_ATTEMPTS_PER_TURN", "8")
                ),
            }

            def _finalize_search_audit_metadata(
                assistant_text: str,
                *,
                raw_assistant_message: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                search_results = [
                    search_result
                    for record in audit_tool_records
                    for search_result in (record.get("search_results") or [])
                ]
                verifications = []
                if os.environ.get("DAN_VERIFY_CITATIONS", "0").strip() == "1" and search_results:
                    verifications = verify_response_citations(
                        assistant_text,
                        search_results,
                        raw_assistant_message=raw_assistant_message,
                    )
                citations = build_citation_records(
                    assistant_text,
                    search_results,
                    verifications=verifications,
                    raw_assistant_message=raw_assistant_message,
                )
                for record in audit_tool_records:
                    if record.get("tool_name") != "web_search":
                        continue
                    record["citations"] = citations
                    record["citation_verifications"] = verifications
                _persist_recent_search_urls(
                    self._chat_store,
                    workflow_id,
                    thread_id,
                    list(search_state.get("session_seen_order") or []),
                )
                _persist_citation_summary(
                    self._chat_store,
                    workflow_id,
                    thread_id,
                    verifications,
                )
                citation_warning = _citation_warning_text(verifications)
                return {
                    "citations": citations,
                    "citation_verifications": verifications,
                    "citation_warning": citation_warning,
                }

            async def _emit_terminal_tool_loop_reply(
                *,
                assistant_content: str,
                token_usage: dict[str, Any],
                raw_assistant_message: dict[str, Any] | None = None,
                display_content: str | None = None,
                estimated_cost: float | None = None,
            ) -> AsyncIterator[ChatStreamEvent]:
                final_content = display_content if display_content is not None else assistant_content
                self._record_conversation_summary(
                    workflow_id=workflow_id,
                    user_message=message,
                    assistant_message=assistant_content,
                )
                search_audit_metadata = _finalize_search_audit_metadata(
                    assistant_content,
                    raw_assistant_message=raw_assistant_message,
                )
                _try_persist_audit(
                    workflow_id=workflow_id,
                    message_id=message_id,
                    user_message=message,
                    assistant_message=assistant_content,
                    mode=mode,
                    model=effective_model,
                    audit_tool_records=audit_tool_records,
                    prompt_messages=messages,
                    surface=surface,
                    audit_metadata=audit_metadata,
                )
                citation_warning = str(search_audit_metadata.get("citation_warning") or "").strip()
                if citation_warning:
                    yield ChatNoticeEvent(
                        content=citation_warning,
                        level="warning",
                    )
                yield ChatCompleteEvent(
                    message_id=message_id,
                    content=final_content,
                    token_usage=token_usage,
                    estimated_cost=estimated_cost,
                    context_window=_get_context_window(effective_model),
                    graph_revision=revision,
                    revision_mismatch=revision_mismatch,
                    stream_channel_id=last_stream_channel_id,
                )

            def _stream_step_label(step: Any) -> str:
                step_type = str(getattr(step, "type", "") or "").strip()
                if step_type:
                    detected_mode = str(getattr(step, "detected_mode", "") or "").strip()
                    if detected_mode:
                        return f"{step_type}:{detected_mode}"
                    return step_type
                return type(step).__name__

            def _interrupted_tool_loop_content() -> str:
                # Once structured tool results exist in the UI, avoid copying the
                # one-line tool trace fallback into assistant text on stop.
                if audit_tool_records:
                    return ""
                return "\n\n".join(combined_text_parts) if combined_text_parts else ""

            async def _run_no_tool_continuation(
                request_kwargs: dict[str, Any],
                interrupted_content: str | Callable[[], str] | None,
            ) -> AsyncIterator[ChatStreamEvent | CompletionResult]:
                async for step in _iter_guarded_complete(
                    request_kwargs=request_kwargs,
                    interrupted_content=interrupted_content,
                    emit_progress_ack=True,
                ):
                    yield step

            for _turn in range(max_tool_turns):
                if cancel_event and cancel_event.is_set():
                    yield ChatInterruptedEvent(
                        message_id=message_id,
                        content=_interrupted_tool_loop_content(),
                        token_usage={},
                    )
                    return

                if stream_channel_id and _turn > 0:
                    injections = self._drain_injections(stream_channel_id)
                    for inj in injections:
                        messages.append({
                            "role": "user",
                            "content": inj["content"],
                        })
                        yield ChatInjectedMessageEvent(
                            content=inj["content"],
                            inject_id=inj["inject_id"],
                        )

                cap_calls = self._extract_all_capability_tool_calls(result, capability_mode)
                mutation_data = (
                    self._extract_mutation_from_result(result)
                    if allow_mutation_tool and not cap_calls
                    else None
                )

                # No tools called → check if response is truly complete
                if not cap_calls and mutation_data is None:
                    missing_action_hints = _missing_action_hints(
                        required_action_hints,
                        satisfied_tool_names,
                        tool_results=successful_tool_results,
                    )
                    no_tool_resolution: NoToolTurnResolution | None = None
                    async for item in resolve_no_tool_turn(
                        result=result,
                        messages=messages,
                        combined_text_parts=combined_text_parts,
                        usage_totals=usage_totals,
                        completion_review_requested=completion_review_requested,
                        turn_index=_turn,
                        max_tool_turns=max_tool_turns,
                        model=effective_model,
                        completion_max_tokens=completion_max_tokens,
                        force_file_write_next_turn=force_file_write_next_turn,
                        missing_action_hints=missing_action_hints,
                        autonomy_level=str(
                            getattr(autonomy_resolution, "effective_level", "") or "",
                        ),
                        tool_request_config=lambda next_force_write: _tool_request_config(
                            force_file_write_now=next_force_write,
                        ),
                        run_continuation=_run_no_tool_continuation,
                        merge_usage_totals=_merge_usage_totals,
                        normalize_usage=_normalize_usage,
                        retry_prompt_builder=_retry_prompt_for_available_tools,
                        interrupted_content_builder=lambda parts: (
                            ""
                            if audit_tool_records
                            else ("\n\n".join(parts) if parts else "")
                        ),
                        review_interrupted_content_builder=lambda parts: (
                            "\n\n".join(parts) if parts else ""
                        ),
                        compact_context=_compact_context,
                        logger_override=logger,
                    ):
                        if isinstance(item, GuardedCompletionForwardedStep):
                            yield item.event
                        else:
                            no_tool_resolution = item

                    if no_tool_resolution is None:
                        raise RuntimeError("No-tool turn resolution produced no outcome")

                    messages = no_tool_resolution.messages
                    combined_text_parts = list(no_tool_resolution.combined_text_parts)
                    usage_totals = no_tool_resolution.usage_totals
                    completion_review_requested = (
                        no_tool_resolution.completion_review_requested
                    )

                    if no_tool_resolution.action == "interrupt":
                        return
                    if no_tool_resolution.action == "continue":
                        if no_tool_resolution.result is None:
                            raise RuntimeError("No-tool continuation produced no result")
                        result = no_tool_resolution.result
                        continue

                    content = no_tool_resolution.terminal_content
                    normalized_usage = no_tool_resolution.terminal_token_usage or {}
                    if content:
                        yield ChatTokenEvent(delta=content, accumulated=content)
                    if not no_tool_resolution.use_terminal_reply_helper:
                        yield ChatCompleteEvent(
                            message_id=message_id,
                            content=content,
                            token_usage=normalized_usage,
                            context_window=_get_context_window(effective_model),
                            graph_revision=revision,
                            revision_mismatch=revision_mismatch,
                            stream_channel_id=last_stream_channel_id,
                        )
                        return

                    display_content = content
                    cost = estimate_cost(
                        effective_model,
                        normalized_usage.get("prompt_tokens", 0),
                        normalized_usage.get("completion_tokens", 0),
                    )
                    if os.environ.get("DAN_SHOW_COST", "1") == "1" and cost is not None and cost > 0:
                        display_content = f"{content}\n\n[~${cost:.4f}]"

                    async for event in _emit_terminal_tool_loop_reply(
                        assistant_content=content,
                        display_content=display_content,
                        token_usage=normalized_usage,
                        raw_assistant_message=no_tool_resolution.terminal_raw_assistant_message,
                        estimated_cost=cost,
                    ):
                        yield event
                    return

                # Mutation → handle as before, return
                if mutation_data is not None:
                    tool_call_id = f"tc_{uuid.uuid4().hex[:10]}"
                    tool_start_time = time.monotonic()

                    yield ChatToolCallStartEvent(
                        tool_call_id=tool_call_id,
                        tool_name="plan_graph_mutations",
                        args_preview=_build_args_preview(mutation_data),
                    )
                    yield _progress_ack_event(
                        message_id=message_id,
                        label="Preparing workflow change preview",
                        context_window=_get_context_window(effective_model),
                        graph_revision=revision,
                        revision_mismatch=revision_mismatch,
                    )

                    preview = _compile_preview(
                        mutation_data,
                        current_graph=graph_dict,
                        current_revision=revision,
                    )
                    plan_payload = preview.plan_payload
                    plan = preview.plan
                    dry_result = preview.dry_result

                    if (
                        not dry_result.success
                        and not dry_result.stale_plan
                        and _MUTATION_AUTO_RETRY
                        and _MUTATION_AUTO_RETRY_MAX > 0
                    ):
                        for attempt in range(_MUTATION_AUTO_RETRY_MAX):
                            mutation_metrics.record_retry()
                            error_summary = "; ".join(
                                error.message
                                for error in getattr(dry_result, "errors", []) or []
                            ) or "unknown compilation/validation error"
                            yield _progress_ack_event(
                                message_id=message_id,
                                label="Repairing workflow change preview",
                                context_window=_get_context_window(effective_model),
                                graph_revision=revision,
                                revision_mismatch=revision_mismatch,
                            )
                            logger.info(
                                "Dry-run failed for plan %s, attempting auto-retry %d/%d: %s",
                                plan.plan_id if plan is not None else "invalid-plan",
                                attempt + 1,
                                _MUTATION_AUTO_RETRY_MAX,
                                error_summary,
                            )
                            retry_messages = build_mutation_repair_messages(
                                user_message=message,
                                graph_summary=summary,
                                current_mutation=mutation_data,
                                current_plan_payload=plan_payload,
                                current_dry_result=dry_result,
                            )
                            try:
                                retry_result: CompletionResult | None = None
                                retry_relay = forward_guarded_steps(
                                    _iter_guarded_complete(
                                        request_kwargs={
                                            "messages": retry_messages,
                                            "model": effective_model,
                                            "temperature": 0.5,
                                            "max_tokens": completion_max_tokens,
                                            "tools": [MUTATION_TOOL_SCHEMA],
                                            "tool_choice": "auto",
                                        },
                                        interrupted_content=lambda: "\n\n".join(combined_text_parts)
                                        if combined_text_parts
                                        else "",
                                        emit_progress_ack=True,
                                        timeout_seconds=min(
                                            _LLM_CALL_TIMEOUT_SECONDS,
                                            _MUTATION_REPAIR_LLM_TIMEOUT_SECONDS,
                                        ),
                                    ),
                                )
                                async for step in retry_relay:
                                    yield step.event
                                retry_outcome = retry_relay.outcome_or_empty()
                                if retry_outcome.interrupted:
                                    return
                                retry_result = retry_outcome.result
                                if retry_result is None:
                                    raise RuntimeError("Auto-retry completion produced no result")
                            except Exception as retry_exc:
                                logger.debug("Auto-retry LLM call failed: %s", retry_exc)
                                break

                            retry_mutation = self._extract_mutation_from_result(retry_result)
                            if retry_mutation is None:
                                continue

                            retry_preview = _compile_preview(
                                retry_mutation,
                                current_graph=graph_dict,
                                current_revision=revision,
                            )

                            plan_payload = retry_preview.plan_payload
                            plan = retry_preview.plan
                            dry_result = retry_preview.dry_result
                            mutation_data = retry_mutation
                            result = retry_result

                            if plan is not None and dry_result.success:
                                logger.info(
                                    "Auto-retry succeeded for plan %s on attempt %d",
                                    plan.plan_id,
                                    attempt + 1,
                                )
                                break
                            if dry_result.stale_plan:
                                break

                    if dry_result.stale_plan:
                        mutation_metrics.record_stale_plan()
                        yield _progress_ack_event(
                            message_id=message_id,
                            label="Refreshing workflow change preview",
                            context_window=_get_context_window(effective_model),
                            graph_revision=revision,
                            revision_mismatch=revision_mismatch,
                        )
                        logger.info(
                            "Stale plan for %s, re-planning against current revision",
                            plan.plan_id if plan is not None else "invalid-plan",
                        )
                        graph_dict = self._graph_store.get_graph(workflow_id)
                        if graph_dict is not None:
                            graph = Graph.model_validate(graph_dict)
                            summary = build_graph_summary(graph, workflow_id)
                            revision = summary.revision
                            replan_messages = await self._build_messages(
                                summary,
                                message,
                                history,
                                mode=mode,
                                prompt_context=prompt_context,
                                surface_context=surface_context,
                                surface=surface,
                                memory_project_id=memory_project_id,
                                include_memory_kernel_context=include_memory_kernel_context,
                                allow_mutation_tool=True,
                                required_action_hints=required_action_hints,
                                model=effective_model,
                                autonomy_resolution=autonomy_resolution,
                            )
                            replan_messages = build_stale_replan_messages(
                                replan_messages,
                            )
                            try:
                                replan_result: CompletionResult | None = None
                                replan_relay = forward_guarded_steps(
                                    _iter_guarded_complete(
                                        request_kwargs={
                                            "messages": replan_messages,
                                            "model": effective_model,
                                            "temperature": 0.5,
                                            "max_tokens": completion_max_tokens,
                                            "tools": [MUTATION_TOOL_SCHEMA],
                                            "tool_choice": "auto",
                                        },
                                        interrupted_content=lambda: "\n\n".join(combined_text_parts)
                                        if combined_text_parts
                                        else "",
                                        emit_progress_ack=True,
                                        timeout_seconds=min(
                                            _LLM_CALL_TIMEOUT_SECONDS,
                                            _MUTATION_REPAIR_LLM_TIMEOUT_SECONDS,
                                        ),
                                    ),
                                )
                                async for step in replan_relay:
                                    yield step.event
                                replan_outcome = replan_relay.outcome_or_empty()
                                if replan_outcome.interrupted:
                                    return
                                replan_result = replan_outcome.result
                                if replan_result is None:
                                    raise RuntimeError("Replan completion produced no result")
                                replan_mutation = self._extract_mutation_from_result(
                                    replan_result,
                                )
                                if replan_mutation is not None:
                                    replan_preview = _compile_preview(
                                        replan_mutation,
                                        current_graph=graph_dict,
                                        current_revision=revision,
                                    )
                                    if replan_preview.plan is not None and replan_preview.dry_result.success:
                                        plan_payload = replan_preview.plan_payload
                                        plan = replan_preview.plan
                                        dry_result = replan_preview.dry_result
                                        mutation_data = replan_mutation
                                        logger.info("Stale-plan re-planning succeeded")
                            except Exception as replan_exc:
                                logger.debug(
                                    "Stale-plan re-planning failed: %s", replan_exc,
                                )

                    dr_status, dr_preview = _build_dry_run_preview(dry_result)
                    elapsed = int((time.monotonic() - tool_start_time) * 1000)
                    yield ChatToolCallResultEvent(
                        tool_call_id=tool_call_id,
                        tool_name="plan_graph_mutations",
                        status=dr_status,
                        output_preview=dr_preview,
                        duration_ms=elapsed,
                    )

                    if dry_result.success and dry_result.new_graph is not None:
                        from dan.meta.graph_quality import compute_quality_report, tier_quality_threshold

                        quality_report = compute_quality_report(
                            dry_result.new_graph,
                            message,
                            tier=None,
                        )
                        quality_threshold = tier_quality_threshold(None, message)
                        if quality_threshold > 0 and quality_report.overall_score < quality_threshold:
                            yield ChatGraphQualityEvent(
                                score=quality_report.overall_score,
                                concerns=quality_report.concerns,
                            )

                    normalized_usage = _normalize_usage(result.usage)
                    plan_dump = (
                        plan.model_dump()
                        if plan is not None
                        else dict(plan_payload)
                    )
                    _mr = (plan_payload or {}).get("mechanical_repairs") or []
                    if _mr:
                        plan_dump["mechanical_repairs"] = _mr
                    if mode == "debug":
                        plan_dump.setdefault("metadata", {})["source"] = "debug-fix"

                    auto_apply_requested = resolve_mutation_auto_apply_requested(
                        explicit_auto_apply=bool(mutation_data.get("auto_apply", False)),
                        is_empty_graph=is_empty_graph,
                        generation_fallback_active=workflow_generation_fallback_active,
                        dry_result=dry_result,
                        plan=plan,
                    )
                    did_apply = False
                    new_revision = revision

                    if (
                        auto_apply_requested
                        and dry_result.success
                        and dry_result.new_graph is not None
                        and self._graph_store is not None
                    ):
                        from dan.meta.workflow_contract import validate_workflow_build_contract

                        auto_apply_outcome = prepare_mutation_auto_apply(
                            auto_apply_requested=auto_apply_requested,
                            dry_result=dry_result,
                            graph_snapshot=graph_dict,
                            plan=plan,
                            current_revision=revision,
                            apply_mutation=lambda current_graph, current_plan, current_revision: (
                                GraphMutator().apply(
                                    current_graph,
                                    current_plan,
                                    current_revision=current_revision,
                                )
                            ),
                            validate_graph=lambda applied_graph: validate_workflow_build_contract(
                                applied_graph,
                                workflow_id=workflow_id,
                                apply_repairs=True,
                            ),
                            collect_contract_errors=_workflow_contract_errors,
                        )
                        if auto_apply_outcome.status in {"validation_error", "blocked"}:
                            errors = list(auto_apply_outcome.validation_errors)
                            provenance = _workflow_build_provenance(auto_apply_outcome.contract_report)
                            yield ChatValidationResultEvent(
                                success=False,
                                error_count=len(errors),
                                errors=errors,
                                failure_mode=_classify_run_readiness_issues(
                                    list(getattr(auto_apply_outcome.contract_report, "run_readiness_issues", []) or [])
                                ),
                                **provenance,
                            )
                            mutation_metrics.record_apply(False)
                        elif auto_apply_outcome.status == "ready_to_save":
                            provenance = _workflow_build_provenance(auto_apply_outcome.contract_report)
                            yield ChatValidationResultEvent(
                                success=True,
                                error_count=0,
                                errors=[],
                                **provenance,
                            )
                            graph_dict = self._graph_store.save_graph(
                                workflow_id,
                                auto_apply_outcome.graph_to_save,
                            )
                            new_revision = compute_graph_revision(graph_dict)
                            revision = new_revision
                            did_apply = True
                            logger.info(
                                "Auto-applied mutation plan %s for workflow %s (new rev %s)",
                                plan.plan_id, workflow_id, new_revision,
                            )
                            from dan.server.mutation_metrics import mutation_metrics as _apply_metrics
                            _apply_metrics.record_apply(True)

                    preview_content = _format_mutation_preview_content(
                        description=mutation_data.get("description", ""),
                        dry_result=dry_result,
                        is_empty_graph=is_empty_graph,
                        applied=did_apply,
                    )
                    if did_apply:
                        _clear_latest_mutation_preview(
                            self._chat_store,
                            workflow_id,
                            thread_id,
                        )
                    else:
                        _persist_latest_mutation_preview(
                            self._chat_store,
                            workflow_id,
                            thread_id,
                            message_id=message_id,
                            mutation_plan=plan_dump,
                            dry_run_result=dry_result.model_dump(),
                        )
                    self._record_conversation_summary(
                        workflow_id=workflow_id,
                        user_message=message,
                        assistant_message=preview_content,
                    )
                    yield ChatMutationEvent(
                        message_id=message_id,
                        content=preview_content,
                        mutation_plan=plan_dump,
                        dry_run_result=dry_result.model_dump(),
                        token_usage=normalized_usage,
                        context_window=_get_context_window(effective_model),
                        graph_revision=new_revision if did_apply else revision,
                        revision_mismatch=False if did_apply else revision_mismatch,
                        applied=did_apply,
                    )

                    if not did_apply:
                        return

                    satisfied_tool_names.add("plan_graph_mutations")

                    mutation_tc = None
                    for tc in (result.tool_calls or []):
                        if tc.get("function", {}).get("name") == "plan_graph_mutations":
                            mutation_tc = tc
                            break

                    apply_result_text = json.dumps({
                        "success": True,
                        "applied": True,
                        "graph_revision": new_revision,
                        "message": (
                            "Workflow built and applied successfully. "
                            "The workflow is saved, validated, and ready to run. "
                            "Call `start_run` to execute it."
                        ),
                    })

                    messages = build_auto_apply_followup_messages(
                        messages=messages,
                        assistant_text=result.text or "",
                        mutation_tool_call=mutation_tc,
                        tool_call_id=tool_call_id,
                        apply_result_text=apply_result_text,
                    )
                    yield _progress_ack_event(
                        message_id=message_id,
                        label="Workflow applied \u2014 proceeding to run",
                        context_window=_get_context_window(effective_model),
                        graph_revision=new_revision,
                        revision_mismatch=False,
                    )

                    messages = _compact_context(messages, effective_model)
                    aa_tools, aa_tool_choice = _tool_request_config()
                    aa_result: CompletionResult | None = None
                    aa_relay = forward_guarded_steps(
                        _iter_guarded_complete(
                            request_kwargs={
                                "messages": messages,
                                "model": effective_model,
                                "temperature": 0.7,
                                "max_tokens": completion_max_tokens,
                                "tools": aa_tools,
                                "tool_choice": aa_tool_choice,
                            },
                            interrupted_content=_interrupted_tool_loop_content,
                            emit_progress_ack=True,
                        ),
                    )
                    async for step in aa_relay:
                        yield step.event
                    aa_outcome = aa_relay.outcome_or_empty()
                    if aa_outcome.interrupted:
                        return
                    aa_result = aa_outcome.result
                    if aa_result is None:
                        raise RuntimeError("Auto-apply follow-up produced no result")
                    result = aa_result
                    usage_totals = _merge_usage_totals(usage_totals, result.usage)
                    continue

                # Capability tools → execute, build tool result messages, loop
                tool_result_messages: list[dict[str, Any]] = []
                raw_tool_calls = extract_raw_capability_tool_calls(
                    result.tool_calls or [],
                    is_capability_tool=lambda name: (
                        name != "plan_graph_mutations"
                        and self._capability_registry is not None
                        and self._capability_registry.is_available(name, capability_mode)
                    ),
                )

                pending_capabilities = build_pending_capability_calls(
                    cap_calls,
                    raw_tool_calls,
                    call_id_factory=lambda: f"tc_{uuid.uuid4().hex[:10]}",
                )
                deferred_capability_prompt: str | None = None
                pending_capabilities, deferred_capability_prompt = split_inventory_then_delete_batch(
                    pending_capabilities,
                )
                pending_capabilities = annotate_capability_call_plan(
                    pending_capabilities,
                    is_cacheable=lambda name: bool(
                        self._capability_registry is not None
                        and self._capability_registry.is_cacheable(name)
                    ),
                    cache_key_for=capability_cache_key,
                )

                for pending in pending_capabilities:
                    yield ChatToolCallStartEvent(
                        tool_call_id=pending.event_tool_call_id,
                        tool_name=pending.tool_name,
                        args_preview=pending.args_preview,
                    )

                async def _dispatch_capability(tool_name: str, args: Any) -> CapabilityResult:
                    ctx = self._capability_context
                    if ctx is None or self._capability_registry is None:
                        return CapabilityResult(
                            success=False,
                            message="Capability context not configured.",
                        )
                    ctx = dataclasses.replace(
                        ctx,
                        workflow_id=workflow_id,
                        grounding_required=grounding_required,
                        thread_id=thread_id,
                        web_budget_state=web_budget_state,
                        search_state=search_state,
                    )
                    return await self._capability_registry.execute(
                        tool_name,
                        args,
                        ctx,
                        mode=capability_mode,
                    )

                capability_results = await execute_capability_plan(
                    pending_capabilities,
                    execute_unique_call=lambda pending: execute_capability_call(
                        pending,
                        dispatch=_dispatch_capability,
                        make_error_result=lambda exc: CapabilityResult(
                            success=False,
                            message=f"Tool error: {exc}",
                        ),
                        tool_result_cache=tool_result_cache,
                        file_read_cache=file_read_cache,
                        max_retryable_retries=_RETRYABLE_CAPABILITY_MAX_RETRIES,
                        logger_override=logger,
                    ),
                    tool_family_for=_parallel_tool_family,
                    copy_result=copy_capability_result,
                )

                for outcome in capability_results:
                    pending = outcome.pending
                    cap_result = outcome.cap_result
                    cap_name = pending.tool_name
                    cap_args = pending.args
                    if outcome.status == "success":
                        satisfied_tool_names.add(cap_name)
                        successful_tool_results.append({
                            "tool_name": cap_name,
                            "status": outcome.status,
                            "cap_result": cap_result,
                        })
                        cap_data = cap_result.data if isinstance(cap_result.data, dict) else {}
                        if cap_name == "start_run":
                            started_run_id = str(cap_data.get("run_id") or "").strip()
                            if started_run_id:
                                pending_run_status_check_id = started_run_id
                        elif cap_name == "get_run_status":
                            observed_run_id = str(cap_data.get("run_id") or "").strip()
                            requested_run_id = (
                                str(cap_args.get("run_id") or "").strip()
                                if isinstance(cap_args, dict)
                                else ""
                            )
                            if pending_run_status_check_id and (
                                observed_run_id == pending_run_status_check_id
                                or requested_run_id in {"latest", pending_run_status_check_id}
                            ):
                                pending_run_status_check_id = None
                    yield ChatToolCallResultEvent(
                        tool_call_id=pending.event_tool_call_id,
                        tool_name=cap_name,
                        status=outcome.status,
                        output_preview=outcome.output_preview,
                        duration_ms=outcome.duration_ms,
                    )
                    
                    if outcome.status == "success" and cap_result.data and isinstance(cap_result.data, dict):
                        file_path = cap_result.data.get("path") or cap_result.data.get("file_path")
                        if not file_path and isinstance(cap_result.data.get("result"), dict):
                            file_path = cap_result.data["result"].get("path") or cap_result.data["result"].get("file_path")
                            
                        if (
                            file_path
                            and isinstance(file_path, str)
                            and os.path.isfile(file_path)
                            and file_path not in emitted_attachment_paths
                        ):
                            try:
                                stat = os.stat(file_path)
                                emitted_attachment_paths.add(file_path)
                                yield ChatFileAttachmentEvent(
                                    path=file_path,
                                    filename=os.path.basename(file_path),
                                    size=stat.st_size,
                                )
                            except Exception as e:
                                logger.debug("Failed to emit file attachment event for %s: %s", file_path, e)

                        if cap_result.data.get("poll_request"):
                            yield ChatPollRequestEvent(
                                question=cap_result.data.get("question", ""),
                                options=cap_result.data.get("options", []),
                                is_anonymous=cap_result.data.get("is_anonymous", False),
                                allows_multiple=cap_result.data.get("allows_multiple", False),
                            )

                    audit_tool_records.append({
                        "tool_name": cap_name,
                        "args": cap_args,
                        "args_preview": pending.args_preview,
                        "output_preview": outcome.output_preview,
                        "status": outcome.status,
                        "duration_ms": outcome.duration_ms,
                        "result_data": cap_result.data,
                        "source_urls": [
                            u for u in _URL_RE.findall(json.dumps(cap_result.data, default=str))
                        ] if cap_name in ("web_search", "web_fetch") and cap_result.data else [],
                        "source_files": [
                            str(cap_args.get("path") or cap_args.get("file_path") or cap_args.get("filepath") or "")
                        ] if cap_name in ("pdf_read", "file_read") and isinstance(cap_args, dict) else [],
                        "search_results": (
                            parse_search_result_set(cap_result.data).results
                            if parse_search_result_set(cap_result.data) is not None
                            else []
                        ),
                        "citations": [],
                        "citation_verifications": [],
                    })
                    combined_text_parts.append(
                        _summarize_tool_result(
                            cap_name,
                            cap_args if isinstance(cap_args, dict) else {},
                            cap_result.message,
                            cap_result.success,
                            cap_result.data,
                        )
                    )
                    if cap_result.stream_channel_id:
                        last_stream_channel_id = cap_result.stream_channel_id
                    tool_result_messages.append({
                        "role": "tool",
                        "tool_call_id": pending.raw_tool_call_id,
                        "content": _clean_tool_result(cap_name, cap_result.message),
                        "anthropic_tool_result_content": (
                            cap_result.data.get("anthropic_tool_result_content")
                            if isinstance(cap_result.data, dict)
                            else None
                        ),
                    })

                executed_raw_tool_calls = [
                    pending.raw_tool_call
                    for pending in pending_capabilities
                    if isinstance(pending.raw_tool_call, dict)
                ]
                can_replay_raw_assistant_message = (
                    replay_raw_assistant_messages
                    and len(executed_raw_tool_calls) == len(raw_tool_calls)
                )
                pending_write_file = (
                    "write_file" in required_action_hints
                    and "file_write" not in satisfied_tool_names
                )
                tool_names_this_turn = [
                    outcome.pending.tool_name for outcome in capability_results
                ]
                missing_target_detected = any(
                    _looks_like_missing_target_error(
                        outcome.pending.tool_name,
                        outcome.cap_result,
                    )
                    for outcome in capability_results
                )

                followup_missing_action_hints = _missing_action_hints(
                    required_action_hints,
                    satisfied_tool_names,
                    tool_results=successful_tool_results,
                )

                followup_tools: list[dict[str, Any]] = []
                followup_tool_choice: str | dict[str, Any] = "auto"
                try:
                    prepared_followup = prepare_tool_followup_request(
                        messages=messages,
                        assistant_text=result.text or "",
                        executed_raw_tool_calls=executed_raw_tool_calls,
                        raw_assistant_message=(
                            result.raw_assistant_message
                            if can_replay_raw_assistant_message
                            else None
                        ),
                        tool_result_messages=tool_result_messages,
                        deferred_capability_prompt=deferred_capability_prompt,
                        pending_run_status_check_id=pending_run_status_check_id,
                        include_grounding_requirement=any(
                            outcome.status == "success"
                            and outcome.pending.tool_name in ("web_search", "web_fetch", "http_request")
                            for outcome in capability_results
                        ),
                        pending_write_file=pending_write_file,
                        missing_target_detected=missing_target_detected,
                        force_file_write_next_turn=force_file_write_next_turn,
                        followup_missing_action_hints=followup_missing_action_hints,
                        model=effective_model,
                        tool_request_config=lambda next_force_write: _tool_request_config(
                            force_file_write_now=next_force_write,
                        ),
                        compact_context=_compact_context,
                        pressure_hint=context_pressure_hint,
                        retry_prompt_builder=_retry_prompt_for_available_tools,
                        write_prompt_builder=_write_file_escalation_prompt,
                    )
                    messages = prepared_followup.messages
                    followup_tools = prepared_followup.followup_tools
                    followup_tool_choice = prepared_followup.followup_tool_choice
                    followup_missing_action_hints = list(prepared_followup.missing_action_hints)
                    force_file_write_next_turn = prepared_followup.force_file_write_next_turn
                    logger.info(
                        "Tool-loop follow-up turn %d starting: tools=%s, missing_actions=%s, "
                        "force_file_write=%s, last_stream_channel_id=%s, context_msgs=%d",
                        _turn,
                        ",".join(tool_names_this_turn) or "none",
                        ",".join(followup_missing_action_hints) or "none",
                        force_file_write_next_turn,
                        last_stream_channel_id or "none",
                        len(messages),
                    )
                    followup_request_kwargs = {
                        "messages": messages,
                        "model": effective_model,
                        "temperature": 0.7,
                        "max_tokens": completion_max_tokens,
                        "tools": followup_tools,
                        "tool_choice": followup_tool_choice,
                    }
                    recovery_resolution: PostToolFollowupResolution | None = None

                    async def _run_followup_steps() -> AsyncIterator[ChatStreamEvent | CompletionResult]:
                        async for step in _iter_guarded_complete(
                            request_kwargs=followup_request_kwargs,
                            interrupted_content=_interrupted_tool_loop_content,
                            emit_progress_ack=True,
                        ):
                            yield step

                    async def _retry_followup_steps(
                        _retry_index: int,
                    ) -> AsyncIterator[ChatStreamEvent | CompletionResult]:
                        async for step in _iter_guarded_complete(
                            request_kwargs=followup_request_kwargs,
                            interrupted_content=_interrupted_tool_loop_content,
                            emit_progress_ack=True,
                        ):
                            yield step

                    async def _synthesize_followup(
                        failure: Exception,
                    ) -> tuple[CompletionResult | None, bool]:
                        synthesis_messages = list(messages)
                        synthesis_messages.append({
                            "role": "user",
                            "content": _build_tool_followup_recovery_prompt(failure),
                        })

                        fallback_complete = None
                        if effective_model != self._chat_model:
                            logger.info(
                                "Attempting synthesis fallback with model %s (was %s)",
                                self._chat_model,
                                effective_model,
                            )

                            async def _fallback_complete() -> CompletionResult:
                                return await asyncio.wait_for(
                                    complete_chat_surface(
                                        self,
                                        messages=synthesis_messages,
                                        model=self._chat_model,
                                        temperature=0.7,
                                        max_tokens=completion_max_tokens,
                                        pii_session_key=thread_id or workflow_id,
                                    ),
                                    timeout=min(_LLM_CALL_TIMEOUT_SECONDS, 60),
                                )

                            fallback_complete = _fallback_complete

                        return await recover_text_completion(
                            primary=lambda: asyncio.wait_for(
                                complete_chat_surface(
                                    self,
                                    messages=synthesis_messages,
                                    model=effective_model,
                                    temperature=0.7,
                                    max_tokens=completion_max_tokens,
                                    pii_session_key=thread_id or workflow_id,
                                ),
                                timeout=min(_LLM_CALL_TIMEOUT_SECONDS, 60),
                            ),
                            fallback=fallback_complete,
                            logger_override=logger,
                            fallback_label=self._chat_model if fallback_complete else "",
                        )

                    async for step in run_post_tool_followup_flow(
                        run_initial_steps=_run_followup_steps,
                        max_retries=_POST_TOOL_FOLLOWUP_MAX_RETRIES,
                        is_transient_error=_is_transient_llm_error,
                        retry_delay_seconds=_post_tool_followup_retry_delay_seconds,
                        retry_steps=_retry_followup_steps,
                        synthesize=_synthesize_followup,
                        step_labeler=_stream_step_label,
                        logger_override=logger,
                    ):
                        if isinstance(step, GuardedCompletionForwardedStep):
                            yield step.event
                        else:
                            recovery_resolution = step
                    if recovery_resolution is None:
                        raise RuntimeError("Tool-loop follow-up attempt produced no resolution")
                    if (
                        recovery_resolution.recovered_by != "initial"
                        and recovery_resolution.error is not None
                    ):
                        failure_kind = _classify_llm_error_kind(recovery_resolution.error)
                        missing_action_hints = followup_missing_action_hints
                        failure_label = {
                            "timeout": "timed out",
                            "tool_history_incompatible": "failed due to tool-history incompatibility",
                        }.get(failure_kind, "failed")
                        logger.warning(
                            "Multi-turn complete() %s at turn %d: %s (tools=%s, missing_actions=%s, force_file_write=%s)",
                            failure_label,
                            _turn,
                            recovery_resolution.error,
                            ",".join(tool_names_this_turn) or "none",
                            ",".join(missing_action_hints) or "none",
                            force_file_write_next_turn,
                        )
                    if recovery_resolution.interrupted:
                        logger.info(
                            "Tool-loop follow-up turn %d interrupted after steps=%s",
                            _turn,
                            list(recovery_resolution.step_labels) or ["none"],
                        )
                        return
                    if recovery_resolution.result is None:
                        exc = recovery_resolution.error or RuntimeError(
                            "Tool-loop follow-up produced no result",
                        )
                        combined_content = build_post_tool_failure_content(
                            error_intro=_build_tool_followup_error_intro(exc),
                            tool_summary_lines=[p for p in combined_text_parts if p.strip()],
                        )
                        async for event in _emit_terminal_tool_loop_reply(
                            assistant_content=combined_content,
                            token_usage={},
                        ):
                            yield event
                        return

                    result = recovery_resolution.result
                    if recovery_resolution.recovered_by in {"initial", "retry"}:
                        usage_totals = _merge_usage_totals(usage_totals, result.usage)
                        fr = getattr(result, "finish_reason", "") or ""
                        usage = result.usage or {}
                        has_tools = bool(result.tool_calls)
                        has_text = bool((result.text or "").strip())
                        logger.info(
                            "Tool loop turn %d: finish_reason=%s, has_text=%s, has_tools=%s, "
                            "prompt_tokens=%s, completion_tokens=%s, context_msgs=%d",
                            _turn, fr or "n/a", has_text, has_tools,
                            usage.get("prompt_tokens", "?"),
                            usage.get("completion_tokens", "?"),
                            len(messages),
                        )
                        if recovery_resolution.recovered_by == "retry":
                            logger.info("Follow-up retry recovery succeeded at turn %d", _turn)
                            continue
                    else:
                        content = result.text or ""
                        yield ChatTokenEvent(delta=content, accumulated=content)
                        normalized_usage = _normalize_usage(usage_totals or result.usage)
                        async for event in _emit_terminal_tool_loop_reply(
                            assistant_content=content,
                            token_usage=normalized_usage,
                            raw_assistant_message=result.raw_assistant_message,
                        ):
                            yield event
                        return
                except Exception:
                    raise

            # Turn cap reached — prefer one final no-tools synthesis if the
            # model is still requesting more tools, so the user gets the best
            # partial answer available instead of a hard stop note alone.
            final_cap_calls = self._extract_all_capability_tool_calls(result, capability_mode)
            missing_action_hints = _missing_action_hints(
                required_action_hints,
                satisfied_tool_names,
                tool_results=successful_tool_results,
            )
            turn_cap_note: str | None = None
            if final_cap_calls:
                turn_cap_note = (
                    f"I reached the tool-call limit ({max_tool_turns}) while still gathering data, "
                    "so this answer may be partial."
                )
                if missing_action_hints:
                    turn_cap_note = (
                        f"{turn_cap_note} Required steps are still incomplete: "
                        f"{', '.join(missing_action_hints)}."
                    )
                try:
                    synthesis_messages = _compact_context(
                        messages
                        + [
                            {
                                "role": "user",
                                "content": (
                                    "Stop gathering new data. Based only on the information already "
                                    "collected in this conversation, write the best partial answer "
                                    "you can. Do not call more tools. Make clear which key gaps or "
                                    "uncertainties remain because the tool-call limit was reached."
                                ),
                            }
                        ],
                        effective_model,
                    )
                    synthesis: CompletionResult | None = None
                    synthesis_relay = forward_guarded_steps(
                        _iter_guarded_complete(
                            request_kwargs={
                                "messages": synthesis_messages,
                                "model": effective_model,
                                "temperature": 0.7,
                                "max_tokens": completion_max_tokens,
                            },
                            interrupted_content=_interrupted_tool_loop_content,
                            emit_progress_ack=True,
                        ),
                    )
                    async for step in synthesis_relay:
                        yield step.event
                    synthesis_outcome = synthesis_relay.outcome_or_empty()
                    if synthesis_outcome.interrupted:
                        return
                    synthesis = synthesis_outcome.result
                    if synthesis is None:
                        raise RuntimeError("Tool-turn-cap synthesis produced no result")
                    usage_totals = _merge_usage_totals(usage_totals, synthesis.usage)
                    if not (synthesis.text or "").strip():
                        raise RuntimeError("Tool-turn-cap synthesis returned empty text")
                    result = synthesis
                    messages = synthesis_messages
                except Exception:
                    logger.debug("Synthesis call at tool-turn cap failed", exc_info=True)
                    final_content = "\n\n".join(combined_text_parts)
                    note = (
                        f"I reached the tool-call limit ({max_tool_turns}) while still gathering data. "
                        "Please ask me to continue, or narrow the task so I can finish in fewer steps."
                    )
                    if final_content.strip():
                        final_content = final_content + "\n\n" + note
                    else:
                        final_content = note
                    token_usage = _normalize_usage(usage_totals or result.usage)
                    yield ChatCompleteEvent(
                        message_id=message_id,
                        content=final_content,
                        token_usage=token_usage,
                        estimated_cost=estimate_cost(
                            effective_model,
                            token_usage.get("prompt", 0),
                            token_usage.get("completion", 0),
                        ),
                        context_window=_get_context_window(effective_model),
                        graph_revision=revision,
                        revision_mismatch=revision_mismatch,
                        stream_channel_id=last_stream_channel_id,
                    )
                    return

            # Turn cap reached — force a synthesis call if the last response was tool-only
            if not (result.text or "").strip() and combined_text_parts:
                try:
                    synthesis: CompletionResult | None = None
                    synthesis_relay = forward_guarded_steps(
                        _iter_guarded_complete(
                            request_kwargs={
                                "messages": messages,
                                "model": effective_model,
                                "temperature": 0.7,
                                "max_tokens": completion_max_tokens,
                            },
                            interrupted_content=_interrupted_tool_loop_content,
                            emit_progress_ack=True,
                        ),
                    )
                    async for step in synthesis_relay:
                        yield step.event
                    synthesis_outcome = synthesis_relay.outcome_or_empty()
                    if synthesis_outcome.interrupted:
                        return
                    synthesis = synthesis_outcome.result
                    if synthesis is None:
                        raise RuntimeError("Synthesis completion produced no result")
                    usage_totals = _merge_usage_totals(usage_totals, synthesis.usage)
                    if (synthesis.text or "").strip():
                        result = synthesis
                except Exception:
                    logger.debug("Synthesis call at max turns failed", exc_info=True)
            final_content = result.text or "\n\n".join(combined_text_parts)
            if not final_content.strip():
                final_content = "I completed all tool operations but couldn't generate a final summary. Please ask me to summarize the results."
            if turn_cap_note and turn_cap_note not in final_content:
                final_content = final_content.rstrip() + "\n\n" + turn_cap_note
            self._record_conversation_summary(
                workflow_id=workflow_id,
                user_message=message,
                assistant_message=final_content,
            )
            search_audit_metadata = _finalize_search_audit_metadata(
                final_content,
                raw_assistant_message=result.raw_assistant_message,
            )
            _try_persist_audit(
                workflow_id=workflow_id,
                message_id=message_id,
                user_message=message,
                assistant_message=final_content,
                mode=mode,
                model=effective_model,
                audit_tool_records=audit_tool_records,
                prompt_messages=messages,
                surface=surface,
                audit_metadata=audit_metadata,
            )
            token_usage = _normalize_usage(usage_totals or result.usage)
            cost = estimate_cost(effective_model, token_usage.get("prompt_tokens", 0), token_usage.get("completion_tokens", 0))
            if os.environ.get("DAN_SHOW_COST", "1") == "1" and cost is not None and cost > 0:
                final_content += f"\n\n[~${cost:.4f}]"

            citation_warning = str(search_audit_metadata.get("citation_warning") or "").strip()
            if citation_warning:
                yield ChatNoticeEvent(
                    content=citation_warning,
                    level="warning",
                )
            yield ChatCompleteEvent(
                message_id=message_id,
                content=final_content,
                token_usage=token_usage,
                estimated_cost=cost,
                context_window=_get_context_window(effective_model),
                graph_revision=revision,
                revision_mismatch=revision_mismatch,
                stream_channel_id=last_stream_channel_id,
            )

        except KeyError as exc:
            logger.error("Provider resolution failed: %s", exc)
            _try_persist_audit(
                workflow_id=workflow_id,
                message_id=locals().get("message_id", uuid.uuid4().hex[:12]),
                user_message=message,
                assistant_message="",
                mode=mode,
                model=locals().get("effective_model", self._chat_model),
                audit_tool_records=locals().get("audit_tool_records", []),
                prompt_messages=locals().get("messages", []),
                surface=surface,
                error=f"LLM provider error: {exc}",
                audit_metadata=audit_metadata,
            )
            yield ChatErrorEvent(error=_friendly_chat_error(exc))
        except Exception as exc:
            logger.exception("Chat error for workflow %s", workflow_id)
            _try_persist_audit(
                workflow_id=workflow_id,
                message_id=locals().get("message_id", uuid.uuid4().hex[:12]),
                user_message=message,
                assistant_message="",
                mode=mode,
                model=locals().get("effective_model", self._chat_model),
                audit_tool_records=locals().get("audit_tool_records", []),
                prompt_messages=locals().get("messages", []),
                surface=surface,
                error=str(exc),
                audit_metadata=audit_metadata,
            )
            yield ChatErrorEvent(error=_friendly_chat_error(exc))
        finally:
            contract_override_ctx.__exit__(None, None, None)

    # ------------------------------------------------------------------
    # Fallback: stream text, then try to parse JSON as mutation plan
    # ------------------------------------------------------------------

    async def _stream_with_json_fallback(
        self,
        provider: Any,
        messages: list[dict[str, str]],
        message_id: str,
        revision: str,
        revision_mismatch: bool,
        graph_dict: dict[str, Any],
        workflow_id: str,
        user_message: str,
        thread_id: str | None = None,
        cancel_event: asyncio.Event | None = None,
        mode: str = "agent",
        allow_mutation_tool: bool = True,
        effective_model: str | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        from dan.agent_runtime.mutation_fallback import stream_json_fallback_response
        from dan.meta.graph_quality import compute_quality_report, tier_quality_threshold

        def _normalize_ops(
            current_graph: dict[str, Any],
            operations: list[dict[str, Any]],
            is_empty_graph: bool,
        ) -> tuple[list[dict[str, Any]], list[str]]:
            return normalize_mutation_ops_for_chat(
                current_graph,
                operations,
                is_empty_graph=is_empty_graph,
            )

        def _assess_quality(
            new_graph: dict[str, Any],
            user_prompt: str,
        ) -> tuple[int, list[str]] | None:
            quality_report = compute_quality_report(
                new_graph,
                user_prompt,
                tier=None,
            )
            quality_threshold = tier_quality_threshold(None, user_prompt)
            if quality_threshold > 0 and quality_report.overall_score < quality_threshold:
                return quality_report.overall_score, quality_report.concerns
            return None

        async for event in stream_json_fallback_response(
            provider=provider,
            messages=messages,
            message_id=message_id,
            revision=revision,
            revision_mismatch=revision_mismatch,
            graph_dict=graph_dict,
            workflow_id=workflow_id,
            thread_id=thread_id,
            user_message=user_message,
            chat_store=self._chat_store,
            cancel_event=cancel_event,
            mode=mode,
            allow_mutation_tool=allow_mutation_tool,
            effective_model=effective_model or self._chat_model,
            record_conversation_summary=self._record_conversation_summary,
            persist_latest_mutation_preview=_persist_latest_mutation_preview,
            normalize_mutation_ops=_normalize_ops,
            assess_graph_quality=_assess_quality,
        ):
            yield event

    # ------------------------------------------------------------------
    # Mutation extraction helpers
    # ------------------------------------------------------------------

    def _extract_capability_tool_call(
        self,
        result: CompletionResult,
        mode: str,
    ) -> tuple[str, dict[str, Any]] | None:
        """Extract a non-mutation capability tool call from a CompletionResult."""
        calls = self._extract_all_capability_tool_calls(result, mode)
        return calls[0] if calls else None

    def _extract_all_capability_tool_calls(
        self,
        result: CompletionResult,
        mode: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Extract all non-mutation capability tool calls from a CompletionResult."""
        if not result.tool_calls or self._capability_registry is None:
            return []
        capability_mode = _capability_registry_mode(mode)
        out: list[tuple[str, dict[str, Any]]] = []
        for tc in result.tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            if name == "plan_graph_mutations":
                continue
            if self._capability_registry.is_available(name, capability_mode):
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                out.append((name, args))
        return out

    @staticmethod
    def _extract_mutation_from_result(
        result: CompletionResult,
    ) -> dict[str, Any] | None:
        """Compatibility shim for mutation extraction."""
        return extract_mutation_from_result(result)

    @staticmethod
    def _format_surface_context(surface_context: dict[str, Any] | None) -> str:
        return format_surface_context(surface_context)

    # ------------------------------------------------------------------
    # Message building
    # ------------------------------------------------------------------

    async def _build_messages(
        self,
        summary: GraphSummary,
        user_message: str,
        history: list[dict[str, str]],
        mode: str = "agent",
        debug_context: str = "",
        prompt_context: str = "",
        mentions: list[Any] | None = None,
        surface_context: dict[str, Any] | None = None,
        workflow_id: str = "",
        graph_dict: dict[str, Any] | None = None,
        surface: str = "server",
        extra_system_instructions: str = "",
        memory_project_id: str | None = None,
        include_memory_kernel_context: bool = True,
        tools_available: bool = True,
        allow_mutation_tool: bool = False,
        required_action_hints: list[str] | None = None,
        prompt_metadata_sink: dict[str, Any] | None = None,
        model: str | None = None,
        autonomy_resolution: Any | None = None,
    ) -> list[dict[str, str]]:
        effective_model = model or self._chat_model
        graph_is_empty = summary.node_count == 0 and summary.edge_count == 0
        prompt_profile = resolve_agent_profile(
            mode=mode,
            allow_mutation_tool=allow_mutation_tool,
            graph_is_empty=graph_is_empty,
            required_action_hints=required_action_hints,
        )
        graph_text = (
            EMPTY_GRAPH_SUMMARY_PLACEHOLDER
            if graph_is_empty
            else serialize_for_prompt(summary)
        )

        async def _resolve_hint_flags(
            prompt_user_message: str,
            prompt_workflow_id: str,
            prompt_model: str,
        ) -> dict[str, bool]:
            research_hint_enabled = await self._should_inject_research_prompt_hint(
                prompt_user_message,
                workflow_id=prompt_workflow_id,
                model=prompt_model,
            )
            exploration_hint_enabled = False
            if not research_hint_enabled:
                exploration_hint_enabled = await self._should_inject_exploration_prompt_hint(
                    prompt_user_message,
                    workflow_id=prompt_workflow_id,
                    model=prompt_model,
                )
            return {
                "research_specializer": research_hint_enabled,
                "exploration_specializer": exploration_hint_enabled,
            }

        async def _resolve_prompt_modules(
            profile: Any,
            hint_flags: dict[str, bool],
            prompt_graph_is_empty: bool,
            prompt_user_message: str,
            prompt_workflow_id: str,
            prompt_tools_available: bool,
            prompt_allow_mutation_tool: bool,
            prompt_required_action_hints: tuple[str, ...],
            prompt_memory_project_id: str | None,
            prompt_autonomy_resolution: Any | None,
        ) -> PromptModuleResolution:
            prompt_context_obj = PromptContext(
                mode=profile.normalized_mode,
                surface=surface or "server",
                model=effective_model,
                user_message=prompt_user_message,
                workflow_id=prompt_workflow_id,
                autonomy_resolution=prompt_autonomy_resolution,
                tools_available=prompt_tools_available,
                allow_mutation_tool=prompt_allow_mutation_tool,
                required_action_hints=prompt_required_action_hints,
                graph_is_empty=prompt_graph_is_empty,
                project_metadata={
                    "memory_project_id": prompt_memory_project_id,
                    "agent_profile": profile.profile.value,
                    "requested_mode": profile.requested_mode,
                },
                precomputed_hint_flags=hint_flags,
            )
            resolved_modules, prompt_details = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
                prompt_context_obj,
            )
            workflow_guidance_surface = next(
                (
                    str(module.metadata.get("workflow_guidance_surface") or "").strip()
                    for module in resolved_modules
                    if str(module.metadata.get("workflow_guidance_surface") or "").strip()
                ),
                "",
            )
            return PromptModuleResolution(
                module_ids=tuple(module.module_id for module in resolved_modules),
                module_hints="\n\n".join(
                    module.content.strip()
                    for module in resolved_modules
                    if module.content.strip()
                ),
                workflow_guidance_surface=workflow_guidance_surface,
                supports_load_prompt_detail=any(bool(module.detail_id) for module in resolved_modules),
                prompt_details=prompt_details,
            )

        def _build_capability_reference(
            prompt_supports_load_prompt_detail: bool,
            profile: Any,
        ) -> str:
            capability_entries: list[ToolReferenceEntry] | None = None
            if self._capability_registry is not None:
                capability_entries = []
                for tool_meta in self._capability_registry.describe_tools(profile.normalized_mode):
                    tool_name = str(tool_meta.get("name") or "").strip()
                    if not tool_name:
                        continue
                    if tool_name == "load_prompt_detail" and not prompt_supports_load_prompt_detail:
                        continue
                    capability_entries.append(
                        ToolReferenceEntry(
                            name=tool_name,
                            category=str(tool_meta.get("category") or "other"),
                        )
                    )
            return generate_capability_reference(
                capability_entries,
                include_mutation_tool=(
                    allow_mutation_tool
                    and profile.normalized_mode not in {"ask", "plan", "conversation"}
                ),
            )

        def _resolve_mentions(prompt_model: str) -> MentionResolution:
            if not (mentions and self._mention_resolver and workflow_id):
                return MentionResolution()
            try:
                resolved_mentions = self._mention_resolver.resolve_all(
                    mentions,
                    workflow_id,
                    graph_dict,
                    model=prompt_model,
                )
            except Exception as exc:
                logger.warning("Mention resolution failed: %s", exc)
                return MentionResolution()
            if not resolved_mentions:
                return MentionResolution()

            from dan.server.mention_resolver import pack_context

            return MentionResolution(
                resolved_mentions=resolved_mentions,
                mention_packer=pack_context,
            )

        built_messages = await build_runtime_messages(
            graph_text=graph_text,
            graph_is_empty=graph_is_empty,
            user_message=user_message,
            history=history,
            prompt_profile=prompt_profile,
            prompt_context=prompt_context,
            debug_context=debug_context,
            surface_context=surface_context,
            workflow_id=workflow_id,
            surface=surface,
            extra_system_instructions=extra_system_instructions,
            memory_project_id=memory_project_id,
            include_memory_kernel_context=include_memory_kernel_context,
            tools_available=tools_available,
            allow_mutation_tool=allow_mutation_tool,
            required_action_hints=required_action_hints,
            model=effective_model,
            autonomy_resolution=autonomy_resolution,
            max_context_ratio=_MAX_CONTEXT_RATIO,
            resolve_hint_flags=_resolve_hint_flags,
            resolve_prompt_modules=_resolve_prompt_modules,
            build_capability_reference=_build_capability_reference,
            resolve_behavior_prompt=self._resolve_behavior_prompt,
            run_preflight_context=self._run_preflight_hooks,
            build_user_context_block=self._compose_user_context_block,
            build_mcp_tools_block=self._compose_mcp_tools_block,
            build_memory_context=lambda prompt_user_message, project_id: self._compose_memory_kernel_context(
                prompt_user_message,
                project_id=project_id,
            ),
            build_recent_context_message=self._compose_recent_context_message,
            resolve_mentions=_resolve_mentions,
            unified_system_prompt=UNIFIED_SYSTEM_PROMPT,
            context_window=_get_context_window(effective_model),
            logger_override=logger,
        )
        if workflow_id and built_messages.prompt_details:
            self.set_prompt_details(workflow_id, built_messages.prompt_details)
        if prompt_metadata_sink is not None:
            prompt_metadata_sink.update(built_messages.prompt_metadata)
        return built_messages.messages

    async def _should_inject_research_prompt_hint(
        self,
        user_message: str,
        *,
        workflow_id: str = "",
        model: str | None = None,
    ) -> bool:
        heuristic = _classify_research_prompt_signal(user_message)
        if heuristic == "yes":
            return True
        if heuristic == "no":
            return False

        effective_model = model or self._chat_model
        pii_key = workflow_id or f"research-hint:{hashlib.sha256(user_message.encode('utf-8')).hexdigest()[:12]}"
        try:
            result = await asyncio.wait_for(
                complete_chat_surface(
                    self,
                    messages=[
                        {
                            "role": "system",
                            "content": _RESEARCH_HINT_CLASSIFIER_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": user_message},
                    ],
                    model=effective_model,
                    temperature=0.0,
                    max_tokens=3,
                    pii_session_key=pii_key,
                ),
                timeout=min(_LLM_CALL_TIMEOUT_SECONDS, 8.0),
            )
            answer = (result.text or "").strip().lower()
            return answer.startswith("yes")
        except Exception:
            logger.debug(
                "Research prompt classifier fallback failed; defaulting to no hint",
                exc_info=True,
            )
            return False

    async def _should_inject_exploration_prompt_hint(
        self,
        user_message: str,
        *,
        workflow_id: str = "",
        model: str | None = None,
    ) -> bool:
        heuristic = _classify_exploration_prompt_signal(user_message)
        if heuristic == "yes":
            return True
        if heuristic == "no":
            return False

        effective_model = model or self._chat_model
        pii_key = workflow_id or f"exploration-hint:{hashlib.sha256(user_message.encode('utf-8')).hexdigest()[:12]}"
        try:
            result = await asyncio.wait_for(
                complete_chat_surface(
                    self,
                    messages=[
                        {
                            "role": "system",
                            "content": _EXPLORATION_HINT_CLASSIFIER_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": user_message},
                    ],
                    model=effective_model,
                    temperature=0.0,
                    max_tokens=3,
                    pii_session_key=pii_key,
                ),
                timeout=min(_LLM_CALL_TIMEOUT_SECONDS, 8.0),
            )
            answer = (result.text or "").strip().lower()
            return answer.startswith("yes")
        except Exception:
            logger.debug(
                "Exploration prompt classifier fallback failed; defaulting to no hint",
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Multi-turn clarification
    # ------------------------------------------------------------------

    async def clarify_intent(
        self,
        workflow_id: str,
        message: str,
        history: list[dict[str, str]],
        cancel_event: asyncio.Event | None = None,
        model_override: str | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """Stream clarifying questions when build-mode intent is ambiguous."""
        _model = model_override or self._chat_model
        clarify_prompt = (
            "The user wants to create or modify a workflow, but their request is not specific enough "
            "to produce a reliable plan. Ask 1-2 focused clarifying questions to understand:\n"
            "1. What is the main goal?\n"
            "2. What inputs, systems, or resources are involved?\n"
            "3. What output or deliverable should the workflow produce?\n"
            "Be concise. Do not produce a mutation plan yet."
        )
        if workflow_generation_contract_enabled():
            clarify_prompt = (
                f"{clarify_prompt}\n\n"
                f"{render_workflow_clarification_guidance()}"
            )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": clarify_prompt},
        ]
        messages.extend(_sanitize_history_messages(history))
        messages.append({"role": "user", "content": message})

        try:
            stream = stream_chat_surface(
                self,
                messages=messages,
                model=_model,
                temperature=0.7,
                pii_session_key=workflow_id,
            )

            message_id = uuid.uuid4().hex[:12]
            final_content = ""
            token_usage: dict[str, int] = {}

            async for chunk in stream:
                yield ChatTokenEvent(
                    delta=chunk.delta,
                    accumulated=chunk.accumulated,
                )
                if chunk.done:
                    final_content = chunk.accumulated
                    token_usage = _normalize_usage(chunk.usage)

            cost = estimate_cost(_model, token_usage.get("prompt_tokens", 0), token_usage.get("completion_tokens", 0))
            if os.environ.get("DAN_SHOW_COST", "1") == "1" and cost is not None and cost > 0:
                final_content += f"\n\n[~${cost:.4f}]"

            yield ChatCompleteEvent(
                message_id=message_id,
                content=final_content,
                token_usage=token_usage,
                estimated_cost=cost,
                context_window=_get_context_window(_model),
                graph_revision="",
            )
        except Exception as exc:
            logger.exception("Clarify error")
            yield ChatErrorEvent(error=_friendly_chat_error(exc))

    # ------------------------------------------------------------------
    # Codegen / intent-compiler build path (Phase 24-1 / 24-2)
    # ------------------------------------------------------------------

    async def _generate_workflow_from_intent(
        self,
        user_message: str,
        workflow_id: str,
        channel_id: str,
        effective_model: str | None = None,
    ) -> tuple[dict | None, list[ChatStreamEvent]]:
        """Compatibility wrapper around the extracted workflow-generation runtime."""

        return await self._workflow_generation_runtime.generate(
            user_message=user_message,
            workflow_id=workflow_id,
            channel_id=channel_id,
            effective_model=effective_model,
            default_model=self._chat_model,
            behavior_store=self._behavior_store,
            resolve_provider=self._resolve_provider,
            emit_intent_extraction_telemetry=self._emit_intent_extraction_telemetry,
            record_gen_outcome=self._record_gen_outcome,
            get_generation_stats_hint=self._get_generation_stats_hint,
            parse_intent_from_result=self._parse_intent_from_result,
            exec_deterministic_builder_code=self._exec_deterministic_builder_code,
            sandbox_exec_builder_code=self._sandbox_exec_builder_code,
            extract_code_from_response=self._extract_code_from_response,
            run_candidate_smoke=self._run_candidate_execution_smoke,
        )

    async def _run_candidate_execution_smoke(
        self,
        graph_dict: dict[str, Any],
        *,
        workflow_id: str,
        inputs: dict[str, Any],
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        """Run a bounded one-shot smoke on a structured candidate when runtime support exists."""

        run_manager = getattr(self._capability_context, "run_manager", None)
        if run_manager is None:
            return {
                "success": False,
                "skipped": True,
                "reason": "RunManager is unavailable for structured execution smoke.",
            }

        try:
            graph = Graph.model_validate(graph_dict)
        except Exception as exc:
            return {
                "success": False,
                "errors": [f"Structured execution smoke could not load the candidate graph: {exc}"],
            }

        structured_generation = dict(
            ((graph_dict.get("metadata") or {}).get("structured_generation") or {})
        )
        candidate_workspace_id = str(
            structured_generation.get("candidate_workspace_id") or ""
        ).strip()
        smoke_graph_id = (
            f"{workflow_id}::candidate-smoke::{candidate_workspace_id}"
            if candidate_workspace_id
            else f"{workflow_id}::candidate-smoke"
        )
        record = await run_manager.start_run(
            graph,
            graph_id=smoke_graph_id,
            inputs=dict(inputs or {}),
            goal_context={
                "source": "structured_execution_smoke",
                "workflow_id": workflow_id,
                "candidate_workspace_id": candidate_workspace_id or None,
            },
            run_policy={
                "profile": "structured_smoke",
                "max_duration": max(float(timeout_seconds), 1.0),
            },
        )
        task = getattr(run_manager, "_tasks", {}).get(record.run_id)
        if task is None:
            return {
                "success": False,
                "run_id": record.run_id,
                "errors": ["Structured execution smoke started without a tracked task."],
            }

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=max(float(timeout_seconds), 1.0))
        except asyncio.TimeoutError:
            task.cancel()
            return {
                "success": False,
                "run_id": record.run_id,
                "errors": [
                    f"Structured execution smoke timed out after {timeout_seconds:.0f}s."
                ],
            }

        status = getattr(record.status, "value", str(record.status))
        result = getattr(record, "result", None)
        if status != "completed" or result is None or not getattr(result, "success", False):
            snapshot = record.snapshot()
            errors = [
                str(item).strip()
                for item in (
                    list((snapshot.get("errors") or {}).values())
                    if isinstance(snapshot.get("errors"), dict)
                    else [snapshot.get("error") or ""]
                )
                if str(item).strip()
            ] or [
                f"Structured execution smoke ended with status {status}."
            ]
            return {
                "success": False,
                "run_id": record.run_id,
                "status": status,
                "errors": errors,
            }

        return {
            "success": True,
            "run_id": record.run_id,
            "status": status,
            "outputs": dict(getattr(result, "outputs", {}) or {}),
        }

    # ------------------------------------------------------------------
    # Generation stats (29-6 §5)
    # ------------------------------------------------------------------

    def _record_gen_outcome(
        self,
        method: str,
        success: bool = True,
        error_type: str = "",
        fix_needed: bool = False,
        pattern: str = "",
    ) -> None:
        """Compatibility wrapper around the server-side generation-stats adapter."""
        mk = getattr(self, "_memory_kernel", None) or getattr(self, "memory_kernel", None)
        _record_generation_outcome_impl(
            mk,
            method=method,
            pattern=pattern,
            success=success,
            error_type=error_type,
            fix_needed=fix_needed,
            log=logger,
        )

    def _get_generation_stats_hint(self) -> str:
        """Compatibility wrapper around the server-side generation-stats adapter."""
        mk = getattr(self, "_memory_kernel", None) or getattr(self, "memory_kernel", None)
        return _get_generation_stats_hint_impl(mk)

    # ------------------------------------------------------------------
    # Codegen helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_intent_from_result(
        result: CompletionResult,
    ) -> "WorkflowIntent | None":
        """Compatibility wrapper around the canonical workflow-generation helper."""
        return _parse_intent_from_result_impl(result)

    @staticmethod
    def _exec_deterministic_builder_code(code: str) -> dict | None:
        """Compatibility wrapper around the canonical deterministic executor."""
        return _exec_deterministic_builder_code_impl(code, log=logger)

    @staticmethod
    async def _sandbox_exec_builder_code(code: str) -> tuple[dict | None, Any]:
        """Compatibility wrapper around the canonical sandbox executor."""
        return await _sandbox_exec_builder_code_impl(
            code,
            src_path=str(pathlib.Path(__file__).resolve().parents[2]),
            log=logger,
        )

    @staticmethod
    def _extract_code_from_response(text: str) -> str:
        """Compatibility wrapper around the canonical response-code extractor."""
        return _extract_code_from_response_impl(text)
