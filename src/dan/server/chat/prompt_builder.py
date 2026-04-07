"""Prompt/message assembly helpers for ChatManager."""

from __future__ import annotations

import logging
from typing import Any

from dan.agent_runtime.graph_summary import serialize_for_prompt
from dan.agent_runtime.messages import (
    MentionResolution,
    PromptModuleResolution,
    build_runtime_messages,
)
from dan.agent_runtime.profiles import resolve_agent_profile
from dan.agent_runtime.tokens import _get_context_window
from dan.chat_prompts import (
    DEFAULT_PROMPT_MODULE_RESOLVER,
    EMPTY_GRAPH_SUMMARY_PLACEHOLDER,
    PromptContext,
    ToolReferenceEntry,
    UNIFIED_SYSTEM_PROMPT,
    generate_capability_reference,
)
from dan.prompt_contracts import PromptEnvelope
from dan.server.chat.events import GraphSummary

logger = logging.getLogger(__name__)


async def build_chat_messages(
    *,
    manager: Any,
    summary: GraphSummary,
    user_message: str,
    history: list[dict[str, str]],
    mode: str = "agent",
    prompt_envelope: PromptEnvelope | None = None,
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
    max_context_ratio: float = 0.8,
) -> list[dict[str, str]]:
    effective_model = model or manager._chat_model
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
        research_hint_enabled = await manager._should_inject_research_prompt_hint(
            prompt_user_message,
            workflow_id=prompt_workflow_id,
            model=prompt_model,
        )
        exploration_hint_enabled = False
        if not research_hint_enabled:
            exploration_hint_enabled = await manager._should_inject_exploration_prompt_hint(
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
        if manager._capability_registry is not None:
            capability_entries = []
            for tool_meta in manager._capability_registry.describe_tools(profile.normalized_mode):
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
        if not (mentions and manager._mention_resolver and workflow_id):
            return MentionResolution()
        try:
            resolved_mentions = manager._mention_resolver.resolve_all(
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
        prompt_envelope=prompt_envelope,
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
        max_context_ratio=max_context_ratio,
        resolve_hint_flags=_resolve_hint_flags,
        resolve_prompt_modules=_resolve_prompt_modules,
        build_capability_reference=_build_capability_reference,
        resolve_behavior_prompt=manager._resolve_behavior_prompt,
        run_preflight_context=manager._run_preflight_hooks,
        build_user_context_block=manager._compose_user_context_block,
        build_mcp_tools_block=manager._compose_mcp_tools_block,
        build_memory_context=lambda prompt_user_message, project_id: manager._compose_memory_kernel_context(
            prompt_user_message,
            project_id=project_id,
        ),
        build_recent_context_message=manager._compose_recent_context_message,
        resolve_mentions=_resolve_mentions,
        unified_system_prompt=UNIFIED_SYSTEM_PROMPT,
        context_window=_get_context_window(effective_model),
        logger_override=logger,
    )
    if workflow_id and built_messages.prompt_details:
        manager.set_prompt_details(workflow_id, built_messages.prompt_details)
    if prompt_metadata_sink is not None:
        prompt_metadata_sink.update(built_messages.prompt_metadata)
    return built_messages.messages
