from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from dan.server.capabilities.misc import handle_load_prompt_detail
from dan.providers.registry import ProviderRegistry
from dan.server.capability_registry import (
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
    build_tool_schema,
)
from dan.chat_prompts import (
    BUILD_FROM_INTENT_PROMPT,
    DEFAULT_PROMPT_MODULE_RESOLVER,
    MUTATION_TOOL_SCHEMA,
    PromptContext,
    ToolReferenceEntry,
    generate_capability_reference,
)
from dan.server.chat_manager import ChatManager
from dan.server.concierge.autonomy import AutonomyResolution
from dan.workflow_generation_guidance import WORKFLOW_GENERATION_CONTRACT_DETAIL_ID


def test_legacy_server_prompt_module_aliases_canonical_module() -> None:
    canonical = importlib.import_module("dan.chat_prompts")
    legacy = importlib.import_module("dan.server.chat.prompts")

    assert legacy is canonical
    assert legacy.UNIFIED_SYSTEM_PROMPT is canonical.UNIFIED_SYSTEM_PROMPT
    assert legacy.get_prompt_detail_body is canonical.get_prompt_detail_body


@pytest.mark.asyncio
async def test_prompt_module_resolver_orders_layers_stably() -> None:
    modules, details = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
        PromptContext(
            mode="agent",
            surface="cli",
            model="test-model",
            user_message="Research the topic and summarize the evidence.",
            workflow_id="wf-1",
            autonomy_resolution=AutonomyResolution(
                preferred_level="auto",
                effective_level="balanced",
                source="fallback",
                reason="weak or mixed signals",
            ),
            tools_available=True,
            precomputed_hint_flags={
                "research_specializer": True,
                "exploration_specializer": True,
            },
        ),
    )

    assert [module.module_id for module in modules] == [
        "interaction_policy",
        "surface_presentation",
        "research_specializer",
        "exploration_specializer",
    ]
    assert "prompt:research_specializer:full" in details
    assert "prompt:exploration_specializer:full" in details


@pytest.mark.asyncio
async def test_research_module_does_not_suggest_load_prompt_detail_without_tools() -> None:
    modules, _ = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
        PromptContext(
            mode="agent",
            surface="cli",
            model="test-model",
            user_message="Research the topic and summarize the evidence.",
            workflow_id="wf-1",
            autonomy_resolution=AutonomyResolution(
                preferred_level="auto",
                effective_level="balanced",
                source="fallback",
                reason="weak or mixed signals",
            ),
            tools_available=False,
            precomputed_hint_flags={"research_specializer": True},
        ),
    )

    research_module = next(
        module for module in modules if module.module_id == "research_specializer"
    )
    assert "load_prompt_detail" not in research_module.content


@pytest.mark.asyncio
async def test_exploration_module_does_not_suggest_load_prompt_detail_without_tools() -> None:
    modules, _ = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
        PromptContext(
            mode="agent",
            surface="cli",
            model="test-model",
            user_message="Help me understand how this subsystem works.",
            workflow_id="wf-1",
            autonomy_resolution=AutonomyResolution(
                preferred_level="auto",
                effective_level="balanced",
                source="fallback",
                reason="weak or mixed signals",
            ),
            tools_available=False,
            precomputed_hint_flags={"exploration_specializer": True},
        ),
    )

    exploration_module = next(
        module for module in modules if module.module_id == "exploration_specializer"
    )
    assert "load_prompt_detail" not in exploration_module.content


@pytest.mark.asyncio
async def test_balanced_interaction_policy_preserves_agent_defaults() -> None:
    modules, _ = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
        PromptContext(
            mode="auto",
            surface="cli",
            model="test-model",
            user_message="Please help with the task.",
            workflow_id="wf-1",
            autonomy_resolution=AutonomyResolution(
                preferred_level="auto",
                effective_level="balanced",
                source="fallback",
                reason="weak or mixed signals",
            ),
            tools_available=True,
            precomputed_hint_flags={},
        ),
    )

    interaction_module = next(
        module for module in modules if module.module_id == "interaction_policy"
    )
    assert "## Mode Behavior: Agent" in interaction_module.content
    assert "Apply the self-management loop above after each action." in interaction_module.content
    assert "brief numbered plan" in interaction_module.content
    assert "## Autonomy Behavior: Balanced" in interaction_module.content
    assert "Match the current default DAN operating style." in interaction_module.content


@pytest.mark.asyncio
async def test_balanced_surface_presentation_preserves_telegram_bot_hints() -> None:
    modules, _ = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
        PromptContext(
            mode="agent",
            surface="telegram:dan-bot",
            model="test-model",
            user_message="Summarize the update.",
            workflow_id="wf-1",
            autonomy_resolution=AutonomyResolution(
                preferred_level="auto",
                effective_level="balanced",
                source="fallback",
                reason="weak or mixed signals",
            ),
            tools_available=True,
            precomputed_hint_flags={},
        ),
    )

    surface_module = next(
        module for module in modules if module.module_id == "surface_presentation"
    )
    assert "## Surface: Telegram" in surface_module.content
    assert "- Current model: test-model" in surface_module.content
    assert "Telegram supports Markdown" in surface_module.content
    assert "You are speaking as the Telegram bot `dan-bot`" in surface_module.content


@pytest.mark.asyncio
async def test_balanced_research_specializer_preserves_core_guidance() -> None:
    modules, _ = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
        PromptContext(
            mode="agent",
            surface="cli",
            model="test-model",
            user_message="Research the topic and summarize the evidence.",
            workflow_id="wf-1",
            autonomy_resolution=AutonomyResolution(
                preferred_level="auto",
                effective_level="balanced",
                source="fallback",
                reason="weak or mixed signals",
            ),
            tools_available=True,
            precomputed_hint_flags={"research_specializer": True},
        ),
    )

    research_module = next(
        module for module in modules if module.module_id == "research_specializer"
    )
    assert "## Research & Report Behavior" in research_module.content
    assert "Ground factual claims in tool results" in research_module.content
    assert "Search multiple angles" in research_module.content
    assert "Outline longer deliverables before writing." in research_module.content
    assert "load_prompt_detail" in research_module.content


@pytest.mark.asyncio
async def test_exploration_specializer_preserves_core_guidance() -> None:
    modules, _ = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
        PromptContext(
            mode="agent",
            surface="cli",
            model="test-model",
            user_message="Help me understand how this subsystem works.",
            workflow_id="wf-1",
            autonomy_resolution=AutonomyResolution(
                preferred_level="auto",
                effective_level="balanced",
                source="fallback",
                reason="weak or mixed signals",
            ),
            tools_available=True,
            precomputed_hint_flags={"exploration_specializer": True},
        ),
    )

    exploration_module = next(
        module for module in modules if module.module_id == "exploration_specializer"
    )
    assert "## Exploration & Understanding Behavior" in exploration_module.content
    assert "Start by mapping the relevant area before proposing changes." in exploration_module.content
    assert "key files, symbols, or execution flow" in exploration_module.content
    assert "load_prompt_detail" in exploration_module.content


@pytest.mark.asyncio
async def test_workflow_generation_module_resolves_for_authoring_turns() -> None:
    modules, details = await DEFAULT_PROMPT_MODULE_RESOLVER.resolve(
        PromptContext(
            mode="agent",
            surface="cli",
            model="test-model",
            user_message="Build a workflow that fetches reports and writes a summary.",
            workflow_id="wf-1",
            autonomy_resolution=AutonomyResolution(
                preferred_level="auto",
                effective_level="balanced",
                source="fallback",
                reason="weak or mixed signals",
            ),
            tools_available=True,
            allow_mutation_tool=True,
            required_action_hints=("workflow_edit",),
            graph_is_empty=True,
            precomputed_hint_flags={},
        ),
    )

    workflow_module = next(
        module for module in modules if module.module_id == "workflow_generation_contract"
    )
    assert workflow_module.layer == "task_specializer"
    assert "replace_body_graph" in workflow_module.content
    assert "items` and `results" in workflow_module.content
    assert WORKFLOW_GENERATION_CONTRACT_DETAIL_ID in details


@pytest.mark.asyncio
async def test_load_prompt_detail_returns_registered_detail() -> None:
    result = await handle_load_prompt_detail(
        {"detail_id": "prompt:research_specializer:full"},
        CapabilityContext(workflow_id="wf-1", chat_manager=SimpleNamespace()),
    )

    assert result.success is True
    assert "Phase 1 — Research" in result.message
    assert "Incremental writing" in result.message


@pytest.mark.asyncio
async def test_load_prompt_detail_returns_exploration_detail() -> None:
    result = await handle_load_prompt_detail(
        {"detail_id": "prompt:exploration_specializer:full"},
        CapabilityContext(workflow_id="wf-1", chat_manager=SimpleNamespace()),
    )

    assert result.success is True
    assert "### Exploration workflow" in result.message
    assert "Start broad enough to find the right area" in result.message


@pytest.mark.asyncio
async def test_load_prompt_detail_returns_workflow_generation_contract_detail() -> None:
    result = await handle_load_prompt_detail(
        {"detail_id": WORKFLOW_GENERATION_CONTRACT_DETAIL_ID},
        CapabilityContext(workflow_id="wf-1", chat_manager=SimpleNamespace()),
    )

    assert result.success is True
    assert "Workflow generation contract" in result.message
    assert "replace_body_graph" in result.message
    assert "result = ..." in result.message


@pytest.mark.asyncio
async def test_load_prompt_detail_fails_cleanly_for_unknown_id() -> None:
    chat_manager = SimpleNamespace(get_prompt_detail=lambda workflow_id, detail_id: None)
    result = await handle_load_prompt_detail(
        {"detail_id": "missing"},
        CapabilityContext(workflow_id="wf-1", chat_manager=chat_manager),
    )

    assert result.success is False
    assert "Unknown prompt detail" in result.message


def test_generate_capability_reference_uses_request_catalog_and_mutation_guidance() -> None:
    reference = generate_capability_reference(
        [
            ToolReferenceEntry(name="list_graphs", category="graph"),
            ToolReferenceEntry(name="start_run", category="run"),
            ToolReferenceEntry(name="get_workflow_details", category="experience"),
        ],
        include_mutation_tool=True,
    )

    assert "**Workflow:** list_graphs" in reference
    assert "**Run:** start_run" in reference
    assert "**History:** get_workflow_details" in reference
    assert "plan_graph_mutations" in reference
    assert "create_node" in reference
    assert "proposed preview/diff" in reference
    assert "delete_graph" in reference or "workflow deletion" in reference
    assert "use `start_run`" in reference
    assert "Do NOT use `http_request` or Furnace endpoints" in reference


def test_mutation_tool_schema_exposes_replace_body_graph() -> None:
    operations = MUTATION_TOOL_SCHEMA["function"]["parameters"]["properties"]["operations"]
    op_variants = operations["items"]["anyOf"]
    op_names = {variant["properties"]["op"]["const"] for variant in op_variants}

    assert "replace_body_graph" in op_names


@pytest.mark.asyncio
async def test_build_messages_advertises_request_time_workflow_tools_only() -> None:
    async def _stub_capability(
        _args: dict[str, Any],
        _context: CapabilityContext,
    ) -> CapabilityResult:
        return CapabilityResult(success=True, message="ok")

    capability_registry = ChatCapabilityRegistry()
    capability_registry.register(
        "list_graphs",
        build_tool_schema("list_graphs", "List workflows.", {"type": "object", "properties": {}}),
        _stub_capability,
        modes=["agent"],
        category="graph",
    )
    capability_registry.register(
        "start_run",
        build_tool_schema("start_run", "Start a run.", {"type": "object", "properties": {}}),
        _stub_capability,
        modes=["agent"],
        category="run",
    )
    capability_registry.register(
        "load_prompt_detail",
        build_tool_schema(
            "load_prompt_detail",
            "Load extra prompt detail.",
            {"type": "object", "properties": {"detail_id": {"type": "string"}}},
        ),
        _stub_capability,
        modes=["agent"],
        category="system",
    )

    manager = ChatManager(
        ProviderRegistry(),
        graph_store=SimpleNamespace(),
        capability_registry=capability_registry,
    )

    async def _false_hint(*_args: Any, **_kwargs: Any) -> bool:
        return False

    manager._should_inject_research_prompt_hint = _false_hint  # type: ignore[method-assign]
    manager._should_inject_exploration_prompt_hint = _false_hint  # type: ignore[method-assign]

    messages = await manager._build_messages(
        SimpleNamespace(node_count=0, edge_count=0, revision="rev-1"),
        "Build the workflow and test it.",
        [],
        mode="agent",
        workflow_id="wf-1",
        graph_dict={"nodes": [], "edges": []},
        tools_available=True,
        allow_mutation_tool=True,
    )

    system_content = messages[0]["content"]
    assert "list_graphs" in system_content
    assert "start_run" in system_content
    assert "load_prompt_detail" in system_content
    assert "plan_graph_mutations" in system_content
    assert "replace_body_graph" in system_content
    assert "workflow-building/editing tool for the current workflow" in system_content
    assert "Workflow Generation Contract" in system_content
    assert "items` and `results" in system_content
    assert "Do not introduce new `{{variable}}` placeholders" in system_content
    assert "proposed, applied, tested" in system_content
    assert "Say whether the workflow is only proposed" in system_content
    assert "Do not batch speculative `delete_graph` calls" in system_content
    assert "use `start_run`" in system_content
    assert "Do NOT use `http_request` or Furnace endpoints" in system_content
    assert WORKFLOW_GENERATION_CONTRACT_DETAIL_ID in system_content


@pytest.mark.asyncio
async def test_build_messages_does_not_leak_workflow_contract_into_non_workflow_turns() -> None:
    manager = ChatManager(ProviderRegistry(), graph_store=SimpleNamespace())

    async def _false_hint(*_args: Any, **_kwargs: Any) -> bool:
        return False

    manager._should_inject_research_prompt_hint = _false_hint  # type: ignore[method-assign]
    manager._should_inject_exploration_prompt_hint = _false_hint  # type: ignore[method-assign]

    messages = await manager._build_messages(
        SimpleNamespace(node_count=0, edge_count=0, revision="rev-1"),
        "Read /tmp/notes.txt and summarize the key points.",
        [],
        mode="ask",
        workflow_id="wf-1",
        graph_dict={"nodes": [], "edges": []},
        tools_available=False,
        allow_mutation_tool=False,
    )

    system_content = messages[0]["content"]
    assert "Workflow Generation Contract" not in system_content
    assert "plan_graph_mutations" not in system_content
    assert "replace_body_graph" not in system_content
    assert WORKFLOW_GENERATION_CONTRACT_DETAIL_ID not in system_content
    assert "proposed, applied, tested" not in system_content


def test_build_from_intent_prompt_distinguishes_proposed_vs_applied_states() -> None:
    prompt = BUILD_FROM_INTENT_PROMPT.format(
        node_type_reference="node-ref",
        graph_summary="empty",
    )

    assert "prepares a proposed workflow preview/diff" in prompt
    assert "Do not say the workflow is built, applied, or tested" in prompt
