"""Tests for dan.server.chat_manager — graph-aware chat utilities."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dan.models.edges import DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import CodeOperator, LLMOperator
from dan.models.ports import InputPort, OutputPort
from dan.server.chat_manager import (
    EMPTY_GRAPH_SUMMARY_PLACEHOLDER,
    MUTATION_TOOL_SCHEMA,
    NODE_TYPE_REFERENCE,
    NODE_TYPES,
    ChatManager,
    _completion_max_tokens,
    _get_context_window,
    build_graph_summary,
    compute_graph_revision,
    detect_chat_mode,
    serialize_for_prompt,
)
from dan.engine.user_profile import UserProfile


def _make_test_graph() -> Graph:
    return Graph(
        metadata=GraphMetadata(name="test-workflow"),
        nodes=[
            LLMOperator(
                id="writer",
                name="Writer",
                model="claude-sonnet-4-6",
                prompt_template="Write about {topic}",
                input_ports=[InputPort(name="topic")],
                output_ports=[OutputPort(name="text")],
                position={"x": 0, "y": 0},
            ),
            CodeOperator(
                id="formatter",
                name="Formatter",
                code="print(x)",
                language="python",
                input_ports=[InputPort(name="input")],
                output_ports=[OutputPort(name="result")],
                position={"x": 200, "y": 0},
            ),
        ],
        edges=[
            DataEdge(
                id="e1",
                source_node_id="writer",
                source_port="text",
                target_node_id="formatter",
                target_port="input",
            ),
        ],
        entry_points=["writer"],
        exit_points=["formatter"],
    )


# ── compute_graph_revision ─────────────────────────────────────────


class TestComputeGraphRevision:
    def test_same_dict_same_hash(self):
        d = {"nodes": [{"id": "a"}], "edges": []}
        assert compute_graph_revision(d) == compute_graph_revision(d)

    def test_different_dicts_different_hash(self):
        d1 = {"nodes": [{"id": "a"}]}
        d2 = {"nodes": [{"id": "b"}]}
        assert compute_graph_revision(d1) != compute_graph_revision(d2)

    def test_key_order_irrelevant(self):
        d1 = {"b": 2, "a": 1}
        d2 = {"a": 1, "b": 2}
        assert compute_graph_revision(d1) == compute_graph_revision(d2)

    def test_hash_length(self):
        d = {"nodes": []}
        h = compute_graph_revision(d)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


class TestModelBudgetHelpers:
    def test_get_context_window_supports_newer_model_aliases(self):
        assert _get_context_window("claude-opus-4.6") == 200_000
        assert _get_context_window("gpt5.4") == 1_000_000
        assert _get_context_window("MiniMax-M2.5-highspeed") == 204_800
        assert _get_context_window("glm5") == 200_000
        assert _get_context_window("kimi-k2.5") == 256_000
        assert _get_context_window("kimi-k2.6") == 256_000

    def test_completion_max_tokens_is_more_generous_for_large_context_models(self):
        assert _completion_max_tokens("claude-sonnet-4.6") == 48_000
        assert _completion_max_tokens("gpt5.4") == 64_000
        assert _completion_max_tokens("test-model") == 32_000


# ── build_graph_summary ────────────────────────────────────────────


class TestBuildGraphSummary:
    def test_empty_graph(self):
        graph = Graph(metadata=GraphMetadata(name="empty"))
        summary = build_graph_summary(graph, "wf-1")
        assert summary.node_count == 0
        assert summary.edge_count == 0
        assert summary.name == "empty"
        assert summary.workflow_id == "wf-1"

    def test_llm_node_extracts_model_and_prompt(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        llm_node = next(n for n in summary.nodes if n.id == "writer")
        assert llm_node.model == "claude-sonnet-4-6"
        assert llm_node.prompt_snippet == "Write about {topic}"

    def test_mixed_node_types(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        assert summary.node_count == 2
        assert summary.edge_count == 1
        types = {n.node_type for n in summary.nodes}
        assert types == {"llm_operator", "code_operator"}

    def test_code_node_has_no_model(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        code_node = next(n for n in summary.nodes if n.id == "formatter")
        assert code_node.model is None
        assert code_node.prompt_snippet is None

    def test_revision_computed_from_content(self):
        graph = _make_test_graph()
        s1 = build_graph_summary(graph, "wf-1")
        s2 = build_graph_summary(graph, "wf-1")
        assert s1.revision == s2.revision
        assert len(s1.revision) == 16

    def test_entry_exit_points(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        assert summary.entry_points == ["writer"]
        assert summary.exit_points == ["formatter"]

    def test_edge_summary_fields(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        assert len(summary.edges) == 1
        e = summary.edges[0]
        assert e.source_node_id == "writer"
        assert e.source_port == "text"
        assert e.target_node_id == "formatter"
        assert e.target_port == "input"
        assert e.edge_type == "data"

    def test_port_names_extracted(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        llm_node = next(n for n in summary.nodes if n.id == "writer")
        assert llm_node.input_ports == ["topic"]
        assert llm_node.output_ports == ["text"]

    def test_long_prompt_truncated_to_200(self):
        long_prompt = "x" * 500
        graph = Graph(
            metadata=GraphMetadata(name="long"),
            nodes=[
                LLMOperator(
                    id="n1",
                    name="Verbose",
                    model="gpt-4o",
                    prompt_template=long_prompt,
                    input_ports=[InputPort(name="in")],
                    output_ports=[OutputPort(name="out")],
                ),
            ],
        )
        summary = build_graph_summary(graph, "wf-1")
        assert len(summary.nodes[0].prompt_snippet) == 200


# ── serialize_for_prompt ──────────────────────────────────────────


class TestSerializeForPrompt:
    def test_small_graph_serializes_completely(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        text = serialize_for_prompt(summary)
        assert "test-workflow" in text
        assert "writer" in text
        assert "formatter" in text
        assert "llm_operator" in text
        assert "code_operator" in text

    def test_contains_edge_info(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        text = serialize_for_prompt(summary)
        assert "writer.text" in text
        assert "formatter.input" in text

    def test_contains_entry_exit(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        text = serialize_for_prompt(summary)
        assert "Entry: writer" in text
        assert "Exit: formatter" in text

    def test_empty_graph_produces_none_markers(self):
        graph = Graph(metadata=GraphMetadata(name="empty"))
        summary = build_graph_summary(graph, "wf-1")
        text = serialize_for_prompt(summary)
        assert "Nodes: (none)" in text
        assert "Edges: (none)" in text

    def test_truncation_respects_max_tokens(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        full = serialize_for_prompt(summary, max_tokens=10_000)
        truncated = serialize_for_prompt(summary, max_tokens=1)
        assert len(truncated) <= 4
        assert len(truncated) <= len(full)

    def test_model_shown_for_llm_node(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        text = serialize_for_prompt(summary)
        assert "model=claude-sonnet-4-6" in text

    def test_node_count_in_header(self):
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        text = serialize_for_prompt(summary)
        assert "2 nodes" in text
        assert "1 edges" in text


# ── ChatManager._build_messages ───────────────────────────────────


class TestBuildMessages:
    @staticmethod
    def _make_manager() -> ChatManager:
        return ChatManager(
            provider_registry=MagicMock(),
            graph_store=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_system_prompt_includes_graph_summary(self):
        mgr = self._make_manager()
        graph = _make_test_graph()
        summary = build_graph_summary(graph, "wf-1")
        msgs = await mgr._build_messages(summary, "hello", [])
        assert msgs[0]["role"] == "system"
        assert "test-workflow" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_history_included_in_order(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        history = [
            {"role": "user", "content": "prev question"},
            {"role": "assistant", "content": "prev answer"},
        ]
        msgs = await mgr._build_messages(summary, "new question", history)
        assert msgs[1] == {"role": "user", "content": "prev question"}
        assert msgs[2] == {"role": "assistant", "content": "prev answer"}

    @pytest.mark.asyncio
    async def test_user_message_appended_last(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "what does this do?", [])
        assert msgs[-1] == {"role": "user", "content": "what does this do?"}

    @pytest.mark.asyncio
    async def test_system_prompt_lists_node_types(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "hi", [])
        system = msgs[0]["content"]
        assert "llm_operator" in system
        assert "code_operator" in system

    @pytest.mark.asyncio
    async def test_message_count_with_history(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        history = [{"role": "user", "content": "q"}]
        msgs = await mgr._build_messages(summary, "follow-up", history)
        assert len(msgs) == 3

    @pytest.mark.asyncio
    async def test_empty_history_gives_two_messages(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "hi", [])
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_empty_graph_uses_unified_prompt_with_empty_workflow_placeholder(self):
        """Empty workflows still use the unified prompt and advertise the placeholder."""
        mgr = self._make_manager()
        empty_graph = Graph()
        summary = build_graph_summary(empty_graph, "wf-1")
        assert summary.node_count == 0
        assert summary.edge_count == 0

        msgs = await mgr._build_messages(summary, "create a paper writing workflow", [])
        system = msgs[0]["content"]
        assert system.startswith("You are DAN, a personal AI assistant with full tool access.")
        assert EMPTY_GRAPH_SUMMARY_PLACEHOLDER in system
        assert "workflow building" in system
        assert "Build from scratch." in system

    @pytest.mark.asyncio
    async def test_system_prompt_includes_profile_and_recent_context(self):
        profile = UserProfile(
            preferred_models={"drafting": "claude-sonnet-4-6"},
            preferred_output_format="markdown",
            common_domains=["equity_research"],
            search_dirs=["/tmp/papers"],
        )
        memory = SimpleNamespace(
            format_context_block=lambda n=3: "Recent conversation context:\n- Discussed factor model setup.",
        )
        mgr = ChatManager(
            provider_registry=MagicMock(),
            graph_store=MagicMock(),
            user_profile=profile,
            conversation_memory=memory,
        )
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "hi", [])
        system = msgs[0]["content"]
        assert "User preference hints:" in system
        assert "drafting -> claude-sonnet-4-6" in system
        assert "Preferred output format: markdown" in system
        assert "Common domains: equity research" in system
        assert "Frequent directories: /tmp/papers" in system
        assert "Recent conversation context:" not in system
        assert msgs[1]["role"] == "assistant"
        assert "Historical context from prior sessions" in msgs[1]["content"]
        assert "Discussed factor model setup." in msgs[1]["content"]

    @pytest.mark.asyncio
    async def test_empty_profile_does_not_add_user_preference_hints(self):
        mgr = ChatManager(
            provider_registry=MagicMock(),
            graph_store=MagicMock(),
            user_profile=UserProfile(),
        )
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "hi", [])
        assert "User preference hints:" not in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_build_messages_can_skip_duplicate_kernel_memory_context(self):
        kernel = MagicMock()
        kernel.retrieve_by_task.side_effect = AssertionError("kernel lookup should be skipped")
        mgr = ChatManager(
            provider_registry=MagicMock(),
            graph_store=MagicMock(),
            memory_kernel=kernel,
        )
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(
            summary,
            "where are my paper directories?",
            [],
            prompt_context="Relevant memory:\n- papers directory: /tmp/papers",
            include_memory_kernel_context=False,
        )
        assert "papers directory: /tmp/papers" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_text_only_build_messages_adds_no_tools_override(self):
        mgr = ChatManager(
            provider_registry=MagicMock(),
            graph_store=MagicMock(),
            capability_registry=MagicMock(),
        )
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(
            summary,
            "where is the report?",
            [],
            mode="ask",
            tools_available=False,
        )
        system = msgs[0]["content"]
        assert "responding without tool access for this response" in system
        assert "Tool calling is disabled for this response." in system
        assert "Do not mention or attempt to use tools." in system

    @pytest.mark.asyncio
    async def test_agent_mode_prompt_includes_self_management_loop(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "fix the workflow", [], mode="agent")
        system = msgs[0]["content"]
        assert "Did the last step succeed or fail?" in system
        assert "Do not retry the identical action without changing something." in system
        assert "## Mode Behavior: Agent" in system
        assert "start with a brief numbered plan" in system

    @pytest.mark.asyncio
    async def test_plan_mode_prompt_requires_approval_before_execution(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(
            summary,
            "plan a fix",
            [],
            mode="plan",
            tools_available=False,
        )
        system = msgs[0]["content"]
        assert "## Mode Behavior: Plan" in system
        assert "Do not call tools, mutate workflows, or change files until the user approves." in system

    @pytest.mark.asyncio
    async def test_ask_mode_prompt_is_read_only(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(
            summary,
            "what does this workflow do?",
            [],
            mode="ask",
            tools_available=False,
        )
        system = msgs[0]["content"]
        assert "## Mode Behavior: Ask" in system
        assert "Do not modify files, workflows, or other state in this mode." in system

    @pytest.mark.asyncio
    async def test_debug_mode_prompt_requests_diagnostic_hypothesis(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(
            summary,
            "fix the failure",
            [],
            mode="debug",
            debug_context="Traceback: boom",
        )
        system = msgs[0]["content"]
        assert "## Recent failures\nTraceback: boom" in system
        assert "## Mode Behavior: Debug" in system
        assert "State the leading diagnostic hypothesis before proposing a fix." in system

    @pytest.mark.asyncio
    async def test_legacy_build_mode_uses_agent_mode_hints(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "build a workflow", [], mode="build")
        assert "## Mode Behavior: Agent" in msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_extra_system_instructions_are_appended_to_system_prompt(self):
        mgr = self._make_manager()
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(
            summary,
            "hi",
            [],
            extra_system_instructions="## Surface identity\n- Reply as bot `scholar`",
        )
        system = msgs[0]["content"]
        assert "## Surface identity" in system
        assert "Reply as bot `scholar`" in system

    @pytest.mark.asyncio
    async def test_codegen_path_records_conversation_summary(self, monkeypatch):
        graph_store = MagicMock()
        graph_store.get_graph.return_value = {"nodes": [], "edges": []}
        graph_store.save_graph = MagicMock()
        mgr = ChatManager(
            provider_registry=MagicMock(),
            graph_store=graph_store,
        )

        async def _fake_codegen(**kwargs):
            _ = kwargs
            return ({"nodes": [], "edges": []}, [])

        monkeypatch.setattr(mgr, "_generate_workflow_from_intent", _fake_codegen)
        record = MagicMock()
        monkeypatch.setattr(mgr, "_record_conversation_summary", record)

        events = []
        async for evt in mgr.send_message_with_tools(
            workflow_id="wf-1",
            message="build me a workflow",
            history=[],
            mode="agent",
        ):
            events.append(evt)

        assert events[-1].type == "chat_complete"
        record.assert_called_once()


# ── Mutation tool schema (Task 4) ────────────────────────────────


class TestMutationToolSchema:
    def test_mutation_schema_has_required_fields(self):
        """Operation variants enforce correct required fields per operation."""
        items = MUTATION_TOOL_SCHEMA["function"]["parameters"]["properties"]["operations"]["items"]
        variants = items.get("oneOf") or items.get("anyOf")
        assert variants is not None

        schemas_by_op = {}
        for schema in variants:
            op_val = schema["properties"]["op"]["const"]
            schemas_by_op[op_val] = schema

        add_node = schemas_by_op["add_node"]
        assert "node_type" in add_node["required"]
        assert "name" in add_node["required"]

        add_edge = schemas_by_op["add_edge"]
        for field in ("source_id", "source_port", "target_id", "target_port"):
            assert field in add_edge["required"]

        remove_node = schemas_by_op["remove_node"]
        assert "node_id" in remove_node["required"]

        edit_node = schemas_by_op["edit_node"]
        assert "node_id" in edit_node["required"]
        assert "updates" in edit_node["required"]

        set_pos = schemas_by_op["set_position"]
        assert "node_id" in set_pos["required"]
        assert "x" in set_pos["required"]
        assert "y" in set_pos["required"]

    def test_schema_excludes_deprecated_types(self):
        assert "if_else" not in NODE_TYPES
        assert "while_loop" in NODE_TYPES

    def test_schema_add_node_enum_matches_node_types(self):
        schemas_by_op = {}
        items = MUTATION_TOOL_SCHEMA["function"]["parameters"]["properties"]["operations"]["items"]
        variants = items.get("oneOf") or items.get("anyOf") or []
        for schema in variants:
            schemas_by_op[schema["properties"]["op"]["const"]] = schema
        assert schemas_by_op["add_node"]["properties"]["node_type"]["enum"] == NODE_TYPES

    def test_schema_top_level_structure(self):
        assert MUTATION_TOOL_SCHEMA["type"] == "function"
        assert MUTATION_TOOL_SCHEMA["function"]["name"] == "plan_graph_mutations"
        params = MUTATION_TOOL_SCHEMA["function"]["parameters"]
        assert "description" in params["required"]
        assert "operations" in params["required"]


# ── Node type reference (Task 5) ─────────────────────────────────


class TestNodeTypeReference:
    def test_node_type_reference_includes_all_types(self):
        for nt in NODE_TYPES:
            assert nt in NODE_TYPE_REFERENCE, f"{nt} missing from NODE_TYPE_REFERENCE"

    def test_reference_has_port_info(self):
        assert "in=[" in NODE_TYPE_REFERENCE
        assert "out=[" in NODE_TYPE_REFERENCE

    def test_reference_has_config_for_llm(self):
        assert "config={" in NODE_TYPE_REFERENCE

    @pytest.mark.asyncio
    async def test_system_prompt_includes_reference_and_examples(self):
        mgr = ChatManager(
            provider_registry=MagicMock(),
            graph_store=MagicMock(),
        )
        summary = build_graph_summary(_make_test_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "hi", [])
        system = msgs[0]["content"]
        assert system.startswith("You are DAN, a personal AI assistant with full tool access.")
        assert "## Current Workflow" in system
        assert "writer [llm_operator]" in system
        assert "formatter [code_operator]" in system


class TestDetectChatMode:
    def test_live_market_news_query_uses_conversation_mode(self):
        msg = "What happened for ONDAS, what re the recent key milestone dates, what are the news worth noting"
        assert detect_chat_mode(msg) == "conversation"
