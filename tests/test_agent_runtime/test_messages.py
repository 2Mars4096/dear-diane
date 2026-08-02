from __future__ import annotations

from types import SimpleNamespace

import pytest

from dan.agent_runtime.messages import (
    BuiltPromptMessages,
    MentionResolution,
    PromptModuleResolution,
    build_context_block,
    build_history_with_context,
    build_prompt_messages,
    build_runtime_messages,
    build_messages_without_mentions,
    build_system_content,
    build_workflow_block,
    compose_memory_kernel_context,
    compose_recent_context_message,
    compose_user_context_block,
    format_surface_context,
    sanitize_history_messages,
)
from dan.agent_runtime.profiles import resolve_agent_profile


def test_sanitize_history_messages_filters_to_non_empty_user_and_assistant_turns() -> None:
    history = [
        {"role": "system", "content": "ignore"},
        {"role": "assistant", "content": 123},
        {"role": "user", "content": " keep me "},
        {"role": "tool", "content": "ignore"},
        {"role": "assistant", "content": "   "},
        {"role": "user", "content": None},
        "not-a-message",
    ]

    assert sanitize_history_messages(history) == [
        {"role": "assistant", "content": "123"},
        {"role": "user", "content": " keep me "},
    ]


def test_build_context_block_adds_debug_fallback_and_workflow_block() -> None:
    context_block = build_context_block(
        prompt_context="Project facts",
        surface_context_block="## Surface\neditor",
        mode="debug",
        debug_context="",
    )

    assert "## Context\nProject facts" in context_block
    assert "## Surface\neditor" in context_block
    assert "## Recent failures\nNo recent run failures found." in context_block
    assert build_workflow_block(graph_text="graph summary") == "## Current Workflow\ngraph summary"


def test_build_system_content_rewrites_tool_access_for_text_only_turns() -> None:
    system_content = build_system_content(
        prompt_template=(
            "You are DAN, a personal AI assistant with full tool access. "
            "You help with anything: research, file operations, web search, "
            "computation, communication, workflow building.\n\n"
            "{current_date}\n{capability_reference}\n{module_hints}\n"
            "{context_block}\n{workflow_block}"
        ),
        preflight_context="Today is Monday.",
        capability_reference="Capabilities",
        module_hints="Hints",
        context_block="Context",
        workflow_block="Workflow",
        tools_available=False,
        user_context_block="## User\nprofile",
        mcp_block="server-a",
        memory_context="## Memory\nremember this",
        extra_system_instructions="Be terse.",
    )

    assert "responding without tool access for this response" in system_content
    assert "## Tool access for this response" in system_content
    assert "## Connected MCP servers\nserver-a" in system_content
    assert "## Memory\nremember this" in system_content
    assert system_content.rstrip().endswith("Be terse.")


def test_build_messages_without_mentions_injects_recent_context_before_history() -> None:
    messages = build_messages_without_mentions(
        system_content="system prompt",
        history=[
            {"role": "assistant", "content": "prior answer"},
            {"role": "tool", "content": "ignore"},
            {"role": "user", "content": "prior question"},
        ],
        recent_context_message="Recent context",
        user_message="Current question",
        context_window=128_000,
        model="gpt-4o-mini",
        max_context_ratio=0.8,
    )

    assert messages[:4] == [
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": "Recent context"},
        {"role": "assistant", "content": "prior answer"},
        {"role": "user", "content": "prior question"},
    ]
    assert messages[-1] == {"role": "user", "content": "Current question"}


def test_build_prompt_messages_returns_structured_metadata_and_flag() -> None:
    built = build_prompt_messages(
        system_content="system prompt",
        history=[{"role": "assistant", "content": "prior answer"}],
        recent_context_message="Recent context",
        user_message="Current question",
        context_window=128_000,
        model="gpt-4o-mini",
        max_context_ratio=0.8,
        prompt_metadata={"active_prompt_key": "prompts/runtime.unified_system"},
        supports_load_prompt_detail=True,
    )

    assert isinstance(built, BuiltPromptMessages)
    assert built.messages[0] == {"role": "system", "content": "system prompt"}
    assert built.messages[-1] == {"role": "user", "content": "Current question"}
    assert built.prompt_metadata["active_prompt_key"] == "prompts/runtime.unified_system"
    assert built.prompt_metadata["supports_load_prompt_detail"] is True
    assert built.supports_load_prompt_detail is True


def test_resolve_agent_profile_promotes_build_and_debug_modes_explicitly() -> None:
    build_profile = resolve_agent_profile(
        mode="build",
        allow_mutation_tool=True,
        graph_is_empty=True,
        required_action_hints=["workflow_edit"],
    )
    debug_profile = resolve_agent_profile(mode="debug")

    assert build_profile.profile.value == "build"
    assert build_profile.requested_mode == "build"
    assert build_profile.normalized_mode == "agent"
    assert debug_profile.profile.value == "debug"


@pytest.mark.asyncio
async def test_build_runtime_messages_returns_profile_metadata_and_prompt_details() -> None:
    profile = resolve_agent_profile(
        mode="build",
        allow_mutation_tool=True,
        graph_is_empty=True,
        required_action_hints=["workflow_edit"],
    )

    async def _resolve_hint_flags(
        user_message: str,
        workflow_id: str,
        model: str,
    ) -> dict[str, bool]:
        assert user_message == "Build the workflow."
        assert workflow_id == "wf-1"
        assert model == "test-model"
        return {
            "research_specializer": False,
            "exploration_specializer": True,
        }

    async def _resolve_prompt_modules(
        resolved_profile,
        hint_flags: dict[str, bool],
        graph_is_empty: bool,
        user_message: str,
        workflow_id: str,
        tools_available: bool,
        allow_mutation_tool: bool,
        required_action_hints: tuple[str, ...],
        memory_project_id: str | None,
        autonomy_resolution,
    ) -> PromptModuleResolution:
        assert resolved_profile == profile
        assert hint_flags["exploration_specializer"] is True
        assert graph_is_empty is True
        assert user_message == "Build the workflow."
        assert workflow_id == "wf-1"
        assert tools_available is True
        assert allow_mutation_tool is True
        assert required_action_hints == ("workflow_edit",)
        assert memory_project_id == "project-1"
        assert autonomy_resolution == {"mode": "balanced"}
        return PromptModuleResolution(
            module_ids=("workflow_generation_contract",),
            module_hints="## Module hints\nUse the build path.",
            workflow_guidance_surface="build",
            supports_load_prompt_detail=True,
            prompt_details={"workflow_generation_contract": "detail text"},
        )

    built = await build_runtime_messages(
        graph_text="empty graph",
        graph_is_empty=True,
        user_message="Build the workflow.",
        history=[],
        prompt_profile=profile,
        prompt_context="Project facts",
        debug_context="",
        surface_context={"mode": "editor"},
        workflow_id="wf-1",
        surface="server",
        extra_system_instructions="Be precise.",
        memory_project_id="project-1",
        include_memory_kernel_context=True,
        tools_available=True,
        allow_mutation_tool=True,
        required_action_hints=["workflow_edit"],
        model="test-model",
        autonomy_resolution={"mode": "balanced"},
        max_context_ratio=0.8,
        resolve_hint_flags=_resolve_hint_flags,
        resolve_prompt_modules=_resolve_prompt_modules,
        build_capability_reference=lambda supports_detail, resolved_profile: (
            f"tools:{supports_detail}:{resolved_profile.profile.value}"
        ),
        resolve_behavior_prompt=lambda key, default: (
            default,
            "v1",
        ),
        run_preflight_context=lambda _message: _async_result("Today is Friday, 2026-03-13."),
        build_user_context_block=lambda: "## User\nlikes markdown",
        build_mcp_tools_block=lambda: "mcp tools",
        build_memory_context=lambda _message, _project_id: "## Memory\nremember this",
        build_recent_context_message=lambda _message: "Recent context",
        resolve_mentions=lambda _model: MentionResolution(),
        unified_system_prompt=(
            "You are DAN.\n{current_date}\n{capability_reference}\n{module_hints}\n"
            "{context_block}\n{workflow_block}"
        ),
        context_window=128_000,
    )

    assert built.prompt_metadata["prompt_module_ids"] == ["workflow_generation_contract"]
    assert built.prompt_metadata["workflow_guidance_surface"] == "build"
    assert built.prompt_metadata["agent_profile"] == "build"
    assert built.prompt_metadata["requested_mode"] == "build"
    assert built.prompt_metadata["normalized_mode"] == "agent"
    assert built.prompt_metadata["supports_load_prompt_detail"] is True
    assert built.prompt_details == {"workflow_generation_contract": "detail text"}
    assert "Use the build path." in built.messages[0]["content"]
    assert "tools:True:build" in built.messages[0]["content"]
    assert "## Memory\nremember this" in built.messages[0]["content"]
    assert built.messages[-1] == {"role": "user", "content": "Build the workflow."}


def test_build_prompt_messages_uses_mention_packer_when_mentions_are_resolved() -> None:
    captured: dict[str, object] = {}

    def _pack_context(**kwargs):
        captured.update(kwargs)
        return [{"role": "system", "content": "packed"}]

    built = build_prompt_messages(
        system_content="system prompt",
        history=[{"role": "assistant", "content": "prior answer"}],
        recent_context_message="Recent context",
        user_message="Current question",
        context_window=128_000,
        model="gpt-4o-mini",
        max_context_ratio=0.8,
        resolved_mentions=[SimpleNamespace(identifier="README.md")],
        mention_packer=_pack_context,
    )

    assert built.messages == [{"role": "system", "content": "packed"}]
    assert captured["system_content"] == "system prompt"
    assert captured["user_message"] == "Current question"
    assert captured["history"] == [
        {"role": "assistant", "content": "Recent context"},
        {"role": "assistant", "content": "prior answer"},
    ]


def test_build_history_with_context_reuses_recent_context_injection() -> None:
    history_with_context = build_history_with_context(
        history=[
            {"role": "assistant", "content": "prior answer"},
            {"role": "tool", "content": "ignore"},
            {"role": "user", "content": "prior question"},
        ],
        recent_context_message="Recent context",
    )

    assert history_with_context == [
        {"role": "assistant", "content": "Recent context"},
        {"role": "assistant", "content": "prior answer"},
        {"role": "user", "content": "prior question"},
    ]


def test_compose_user_context_block_formats_profile_hints() -> None:
    profile = SimpleNamespace(
        preferred_models={"research": "o3-mini", "chat": "gpt-4o"},
        preferred_output_format="markdown",
        common_domains=["machine_learning", "finance"],
        search_dirs=["src", "tests", "docs"],
    )

    block = compose_user_context_block(profile)

    assert block.startswith("User preference hints:")
    assert "Preferred models: chat -> gpt-4o, research -> o3-mini" in block
    assert "Preferred output format: markdown" in block
    assert "Common domains:" in block
    assert "Frequent directories: src, tests, docs" in block


def test_compose_recent_context_message_quotes_memory_hits() -> None:
    memory = SimpleNamespace(
        search_by_keywords=lambda keywords, limit=3: [
            SimpleNamespace(summary="Discussed deployment strategy", workflow_id="wf-1"),
        ],
        format_context_block=lambda n=3: "",
    )

    message = compose_recent_context_message(memory, user_message="How should we deploy this service?")

    assert "Historical context from prior sessions" in message
    assert "> Recent conversation context:" in message
    assert "> - Discussed deployment strategy (workflow: wf-1)" in message


def test_compose_memory_kernel_context_skips_working_state_entries() -> None:
    class _Kernel:
        def retrieve_by_task(self, *_args, **_kwargs):
            return [
                SimpleNamespace(
                    item=SimpleNamespace(
                        memory_type=SimpleNamespace(value="fact"),
                        content="Remember to validate the schema before saving.",
                    )
                ),
                SimpleNamespace(
                    item=SimpleNamespace(
                        memory_type=SimpleNamespace(value="working_state"),
                        content="Temporary draft state",
                    )
                ),
            ]

    block = compose_memory_kernel_context(
        _Kernel(),
        "save the graph",
        classify_task_type=lambda _message: "workflow_build",
        working_state_tag="working_state",
    )

    assert "Relevant context from memory:" in block
    assert "[FACT] Remember to validate the schema before saving." in block
    assert "Temporary draft state" not in block


def test_format_surface_context_truncates_large_payloads() -> None:
    block = format_surface_context({
        "mode": "development",
        "workspace_id": "ws-1",
        "active_file": {
            "path": "src/big.ts",
            "language": "typescript",
            "content": "A" * 20_000,
        },
        "selection_text": "B" * 5_000,
        "mentioned_files": [
            {"path": "src/huge.ts", "content": "C" * 8_000, "lines": 500},
        ],
    })

    assert block.startswith("## Surface context\n")
    assert len(block) <= 10_000 + len("## Surface context\n")
    assert "truncated" in block.lower()


async def _async_result(value):
    return value
