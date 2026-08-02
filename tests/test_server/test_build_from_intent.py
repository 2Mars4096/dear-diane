"""Tests for build-from-intent mode (Plan 10-9 Task 6-1).

Covers: system prompt selection, empty-graph bootstrap, template registry,
mode parameter in API schema, empty-graph revision injection, and end-to-end
intent→plan→apply→validate integration (mocked LLM).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import LLMOperator
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult
from dan.server.app import ChatMessageRequest
from dan.server.chat_manager import (
    EMPTY_GRAPH_SUMMARY_PLACEHOLDER,
    WORKFLOW_TEMPLATES,
    ChatManager,
    _coerce_strict_edges,
    _normalize_generated_mutation_ops,
    build_graph_summary,
    compute_graph_revision,
)
from dan.server.graph_mutator import GraphMutator, MutationPlan


def _make_manager() -> ChatManager:
    return ChatManager(
        provider_registry=MagicMock(),
        graph_store=MagicMock(),
    )


def _make_empty_graph() -> Graph:
    return Graph(metadata=GraphMetadata(name="empty"))


def _make_nonempty_graph() -> Graph:
    return Graph(
        metadata=GraphMetadata(name="test-wf"),
        nodes=[
            LLMOperator(
                id="n1",
                name="Node 1",
                model="gpt-4o",
                prompt_template="Do: {input}",
                input_ports=[InputPort(name="input")],
                output_ports=[OutputPort(name="text")],
            ),
        ],
        entry_points=["n1"],
        exit_points=["n1"],
    )


def _node_system_prompt(node: dict[str, object]) -> str:
    llm_hints = node.get("llm_hints")
    if isinstance(llm_hints, dict):
        return str(llm_hints.get("system_prompt", "") or "")
    return str(node.get("system_prompt", "") or "")


# ── 1. Build-mode system prompt selection ────────────────────────────


class TestBuildModePromptSelection:
    @pytest.mark.asyncio
    async def test_build_mode_uses_build_from_intent_prompt(self):
        """LLM-first build mode uses the unified prompt and includes workflow context."""
        mgr = _make_manager()
        graph = _make_nonempty_graph()
        summary = build_graph_summary(graph, "wf-1")

        msgs = await mgr._build_messages(summary, "rebuild this", [], mode="build")
        system = msgs[0]["content"]
        assert "personal ai assistant with full tool access" in system.lower()
        assert "## Current Workflow" in system
        assert "n1 [llm_operator]" in system

    @pytest.mark.asyncio
    async def test_mutate_mode_uses_system_prompt(self):
        """LLM-first mutate mode uses the same unified prompt and workflow block."""
        mgr = _make_manager()
        graph = _make_nonempty_graph()
        summary = build_graph_summary(graph, "wf-1")

        msgs = await mgr._build_messages(summary, "add a node", [], mode="mutate")
        system = msgs[0]["content"]
        assert "personal ai assistant with full tool access" in system.lower()
        assert "## Current Workflow" in system
        assert "n1 [llm_operator]" in system

    @pytest.mark.asyncio
    async def test_empty_graph_always_uses_build_prompt_regardless_of_mode(self):
        """Empty graphs still use the unified prompt with empty workflow section."""
        mgr = _make_manager()
        summary = build_graph_summary(_make_empty_graph(), "wf-1")

        msgs = await mgr._build_messages(summary, "create something", [], mode="mutate")
        system = msgs[0]["content"]
        assert "personal ai assistant with full tool access" in system.lower()
        assert "## Current Workflow" in system
        assert "empty" in system.lower() or "0 nodes" in system

    @pytest.mark.asyncio
    async def test_build_prompt_contains_pattern_library(self):
        mgr = _make_manager()
        summary = build_graph_summary(_make_empty_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "build", [], mode="build")
        system = msgs[0]["content"]
        assert "## Rules" in system

    @pytest.mark.asyncio
    async def test_build_prompt_contains_template_names(self):
        mgr = _make_manager()
        summary = build_graph_summary(_make_empty_graph(), "wf-1")
        msgs = await mgr._build_messages(summary, "build", [], mode="build")
        system = msgs[0]["content"]
        assert "NEVER fabricate live data" in system
        assert "NEVER summarize a file you haven't read" in system

    @pytest.mark.asyncio
    async def test_build_prompt_nonempty_graph_includes_summary(self):
        """When mode='build' on non-empty graph, graph summary is serialized (not placeholder)."""
        mgr = _make_manager()
        graph = _make_nonempty_graph()
        summary = build_graph_summary(graph, "wf-1")

        msgs = await mgr._build_messages(summary, "rebuild", [], mode="build")
        system = msgs[0]["content"]
        assert EMPTY_GRAPH_SUMMARY_PLACEHOLDER not in system
        assert "Node 1" in system or "n1" in system


# ── 2. Empty-graph bootstrap ─────────────────────────────────────────


class TestEmptyGraphBootstrap:
    def test_empty_graph_summary_valid(self):
        summary = build_graph_summary(_make_empty_graph(), "wf-empty")
        assert summary.node_count == 0
        assert summary.edge_count == 0
        assert summary.workflow_id == "wf-empty"
        assert summary.name == "empty"
        assert summary.nodes == []
        assert summary.edges == []

    def test_empty_graph_has_nonempty_revision(self):
        summary = build_graph_summary(_make_empty_graph(), "wf-empty")
        assert summary.revision
        assert len(summary.revision) == 16
        assert all(c in "0123456789abcdef" for c in summary.revision)

    def test_empty_graph_revision_is_deterministic(self):
        s1 = build_graph_summary(_make_empty_graph(), "wf-1")
        s2 = build_graph_summary(_make_empty_graph(), "wf-2")
        assert s1.revision == s2.revision

    def test_empty_graph_revision_differs_from_nonempty(self):
        empty_rev = build_graph_summary(_make_empty_graph(), "wf").revision
        nonempty_rev = build_graph_summary(_make_nonempty_graph(), "wf").revision
        assert empty_rev != nonempty_rev


# ── 3. Template registry ─────────────────────────────────────────────


class TestTemplateRegistry:
    def test_expected_keys_present(self):
        assert "paper_writing" in WORKFLOW_TEMPLATES
        assert "rag_qa" in WORKFLOW_TEMPLATES
        assert "chain_3" in WORKFLOW_TEMPLATES

    def test_each_template_is_list_of_dicts(self):
        for key, ops in WORKFLOW_TEMPLATES.items():
            assert isinstance(ops, list), f"{key}: expected list"
            assert len(ops) > 0, f"{key}: empty template"
            for op in ops:
                assert isinstance(op, dict), f"{key}: op is not a dict"

    def test_each_template_has_valid_op_field(self):
        valid_ops = {
            "add_node", "remove_node", "edit_node", "add_edge",
            "remove_edge", "set_position", "expand_pattern", "apply_skill",
        }
        for key, ops in WORKFLOW_TEMPLATES.items():
            for op in ops:
                assert "op" in op, f"{key}: missing 'op' field"
                assert op["op"] in valid_ops, f"{key}: unknown op '{op['op']}'"

    def test_paper_writing_uses_review_loop_and_chain(self):
        ops = WORKFLOW_TEMPLATES["paper_writing"]
        patterns = [op.get("pattern") for op in ops if op.get("op") == "expand_pattern"]
        assert "review_loop" in patterns
        assert "chain" in patterns

    def test_rag_qa_uses_rag_qa_pattern(self):
        ops = WORKFLOW_TEMPLATES["rag_qa"]
        patterns = [op.get("pattern") for op in ops if op.get("op") == "expand_pattern"]
        assert "rag_qa" in patterns

    def test_chain_3_uses_chain_pattern(self):
        ops = WORKFLOW_TEMPLATES["chain_3"]
        patterns = [op.get("pattern") for op in ops if op.get("op") == "expand_pattern"]
        assert "chain" in patterns


# ── 4. Mode parameter in app ─────────────────────────────────────────


class TestChatMessageRequestMode:
    def test_default_mode_is_auto(self):
        req = ChatMessageRequest(workflow_id="wf", message="hi")
        assert req.mode == "auto"

    def test_accepts_build_mode(self):
        req = ChatMessageRequest(workflow_id="wf", message="hi", mode="build")
        assert req.mode == "build"

    def test_accepts_mutate_mode(self):
        req = ChatMessageRequest(workflow_id="wf", message="hi", mode="mutate")
        assert req.mode == "mutate"

    def test_all_fields_populated(self):
        req = ChatMessageRequest(
            workflow_id="wf-1",
            message="build a chain",
            mode="build",
            thread_id="t1",
            history=[{"role": "user", "content": "prev"}],
            client_graph_revision="abc123",
        )
        assert req.workflow_id == "wf-1"
        assert req.message == "build a chain"
        assert req.mode == "build"
        assert req.thread_id == "t1"
        assert len(req.history) == 1
        assert req.client_graph_revision == "abc123"


# ── 5. Empty-graph revision injection ────────────────────────────────


class TestEmptyGraphRevisionInjection:
    def test_compute_graph_revision_on_empty_graph(self):
        empty = {"nodes": [], "edges": []}
        rev = compute_graph_revision(empty)
        assert isinstance(rev, str)
        assert len(rev) == 16
        assert all(c in "0123456789abcdef" for c in rev)

    def test_empty_graph_revision_stable(self):
        empty = {"nodes": [], "edges": []}
        assert compute_graph_revision(empty) == compute_graph_revision(empty)

    def test_empty_graph_revision_matches_model(self):
        """compute_graph_revision on raw dict matches build_graph_summary revision."""
        graph = _make_empty_graph()
        summary = build_graph_summary(graph, "wf")
        import json
        raw = json.loads(graph.model_dump_json())
        assert compute_graph_revision(raw) == summary.revision

    def test_minimal_empty_dict_still_works(self):
        rev = compute_graph_revision({})
        assert isinstance(rev, str)
        assert len(rev) == 16

    def test_graph_revision_ignores_save_timestamps(self):
        graph = {
            "version": "dan_graph_v1",
            "metadata": {
                "name": "wf",
                "created_at": "2026-03-20T00:00:00Z",
                "updated_at": "2026-03-20T00:00:00Z",
            },
            "nodes": [],
            "edges": [],
            "sub_graphs": {},
            "entry_points": [],
            "exit_points": [],
            "shared_context": [],
            "artifact_refs": [],
            "hyperedges": [],
        }
        newer = dict(graph)
        newer["metadata"] = dict(graph["metadata"])
        newer["metadata"]["updated_at"] = "2026-03-20T00:00:30Z"
        assert compute_graph_revision(graph) == compute_graph_revision(newer)


# ── 6. _coerce_strict_edges helper ───────────────────────────────────


class TestCoerceStrictEdges:
    def test_forces_strict_on_add_edge_ops(self):
        ops = [
            {"op": "add_node", "node_type": "llm_operator", "name": "Writer"},
            {"op": "add_edge", "source_id": "a", "source_port": "text",
             "target_id": "b", "target_port": "input"},
        ]
        result = _coerce_strict_edges(ops)
        add_node = result[0]
        add_edge = result[1]
        assert "strict" not in add_node
        assert add_edge["strict"] is True

    def test_does_not_override_explicit_strict_false(self):
        """If an op carries strict=False, preserve it."""
        ops = [
            {"op": "add_edge", "source_id": "a", "source_port": "text",
             "target_id": "b", "target_port": "input", "strict": False},
        ]
        result = _coerce_strict_edges(ops)
        assert result[0]["strict"] is False

    def test_preserves_existing_strict_true(self):
        ops = [
            {"op": "add_edge", "source_id": "a", "source_port": "text",
             "target_id": "b", "target_port": "input", "strict": True},
        ]
        result = _coerce_strict_edges(ops)
        assert result[0]["strict"] is True

    def test_non_add_edge_ops_unchanged(self):
        ops = [{"op": "add_node", "node_type": "llm_operator", "name": "X"}]
        result = _coerce_strict_edges(ops)
        assert result == ops

    def test_empty_ops_returns_empty(self):
        assert _coerce_strict_edges([]) == []

    def test_original_list_not_mutated(self):
        op = {"op": "add_edge", "source_id": "a", "source_port": "out",
              "target_id": "b", "target_port": "in"}
        ops = [op]
        _coerce_strict_edges(ops)
        assert "strict" not in op  # original dict untouched (shallow copy)


class TestNormalizeGeneratedMutationOps:
    def test_parallel_subagents_dict_shapes_are_normalized(self):
        ops = [{
            "op": "add_node",
            "node_type": "parallel_subagents",
            "name": "Parallel Research Agents",
            "config": {
                "branch_graphs": {
                    "analyst_reports": {"description": "Reports branch"},
                    "financial_data": {"description": "Financial branch"},
                },
                "branch_inputs": {
                    "analyst_reports": "input",
                    "financial_data": {"ticker": "input.ticker"},
                },
            },
        }]
        out = _normalize_generated_mutation_ops(ops)
        cfg = out[0]["config"]
        assert cfg["branch_graphs"] == ["analyst_reports", "financial_data"]
        assert cfg["branch_inputs"]["analyst_reports"] == {"input": "input"}
        assert cfg["branch_inputs"]["financial_data"] == {"ticker": "input.ticker"}

    def test_validator_required_field_rule_is_normalized(self):
        ops = [{
            "op": "add_node",
            "node_type": "validator",
            "name": "Validation Gate",
            "config": {
                "validation_rules": [
                    {"rule_type": "required_field", "config": {"field": "summary"}},
                    {"rule_type": "required_fields", "config": {"fields": ["score"]}},
                ],
            },
        }]
        out = _normalize_generated_mutation_ops(ops)
        rules = out[0]["config"]["validation_rules"]
        assert rules[0]["rule_type"] == "required_keys"
        assert rules[0]["config"]["keys"] == ["summary"]
        assert rules[1]["rule_type"] == "required_keys"
        assert rules[1]["config"]["keys"] == ["score"]

    def test_validator_on_failure_alias_retry_is_normalized(self):
        ops = [{
            "op": "add_node",
            "node_type": "validator",
            "name": "Validation Gate",
            "config": {"on_failure": "retry"},
        }]
        out = _normalize_generated_mutation_ops(ops)
        assert out[0]["config"]["on_failure"] == "route"

    def test_add_edge_context_type_is_preserved(self):
        ops = [{
            "op": "add_edge",
            "edge_type": "context",
            "source_id": "a",
            "source_port": "out",
            "target_id": "b",
            "target_port": "input",
        }]
        out = _normalize_generated_mutation_ops(ops)
        assert out[0]["edge_type"] == "context"


# ── 7. End-to-end intent → plan → apply → validate (mocked LLM) ─────


def _make_graph_store(graph: Graph) -> MagicMock:
    """Graph store that returns a serialised graph dict."""
    store = MagicMock()
    store.get_graph.return_value = json.loads(graph.model_dump_json())
    return store


def _make_provider_registry(tool_call_args: dict) -> MagicMock:
    """Provider registry whose model returns a single tool call."""
    completion = CompletionResult(
        text="",
        usage={"prompt_tokens": 100, "completion_tokens": 50},
        tool_calls=[
            {
                "function": {
                    "name": "plan_graph_mutations",
                    "arguments": json.dumps(tool_call_args),
                }
            }
        ],
    )
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=completion)
    registry = MagicMock()
    registry.resolve.return_value = provider
    return registry


class TestBuildIntentEndToEnd:
    """Full flow: mocked LLM returns plan → dry_run succeeds → ChatMutationEvent emitted."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_chain_3_intent_produces_mutation_event(self):
        """Intent 'create a 3-node chain' → LLM returns chain plan → dry_run succeeds."""
        graph = _make_empty_graph()
        store = _make_graph_store(graph)
        revision = build_graph_summary(graph, "wf").revision

        plan_args = {
            "description": "3-node sequential chain",
            "operations": [
                {"op": "expand_pattern", "pattern": "chain", "params": {
                    "count": 3,
                    "names": ["Step 1", "Step 2", "Step 3"],
                }},
            ],
        }
        registry = _make_provider_registry(plan_args)
        mgr = ChatManager(provider_registry=registry, graph_store=store)

        from dan.server import chat_manager as chat_module
        with patch.object(chat_module, "_DAN_USE_CODEGEN_BUILD", "0"):
            events = self._run(_collect(
                mgr.send_message_with_tools("wf", "create a 3-node chain", [], mode="build")
            ))

        mutation_events = [e for e in events if e.type == "chat_mutation"]
        assert len(mutation_events) == 1
        evt = mutation_events[0]
        assert evt.dry_run_result["success"] is True
        assert len(evt.mutation_plan["operations"]) == 1

    def test_rag_qa_intent_produces_mutation_event(self):
        """Intent 'build a RAG QA workflow' → rag_qa pattern → dry_run succeeds."""
        graph = _make_empty_graph()
        store = _make_graph_store(graph)

        plan_args = {
            "description": "RAG QA workflow",
            "operations": [
                {"op": "expand_pattern", "pattern": "rag_qa", "params": {
                    "rag_name": "Knowledge Base",
                    "answer_name": "Answer Generator",
                    "top_k": 5,
                    "answer_prompt": "Answer based on context: {input}",
                }},
            ],
        }
        registry = _make_provider_registry(plan_args)
        mgr = ChatManager(provider_registry=registry, graph_store=store)

        from dan.server import chat_manager as chat_module
        with patch.object(chat_module, "_DAN_USE_CODEGEN_BUILD", "0"):
            events = self._run(_collect(
                mgr.send_message_with_tools("wf", "build a RAG QA workflow", [], mode="build")
            ))

        mutation_events = [e for e in events if e.type == "chat_mutation"]
        assert len(mutation_events) == 1
        assert mutation_events[0].dry_run_result["success"] is True

    def test_build_mode_forces_strict_on_add_edge(self):
        """In build mode (empty graph), add_edge ops get strict=True by default."""
        graph = _make_empty_graph()
        store = _make_graph_store(graph)
        revision = build_graph_summary(graph, "wf").revision

        plan_args = {
            "description": "Two nodes wired together",
            "operations": [
                {"op": "add_node", "node_type": "llm_operator", "name": "Writer",
                 "config": {"prompt_template": "Write: {input}"}},
                {"op": "add_node", "node_type": "llm_operator", "name": "Reviewer",
                 "config": {"prompt_template": "Review: {input}"}},
                {"op": "add_edge", "source_id": "writer", "source_port": "text",
                 "target_id": "reviewer", "target_port": "input"},
            ],
        }
        registry = _make_provider_registry(plan_args)
        mgr = ChatManager(provider_registry=registry, graph_store=store)

        from dan.server import chat_manager as chat_module
        with patch.object(chat_module, "_DAN_USE_CODEGEN_BUILD", "0"):
            events = self._run(_collect(
                mgr.send_message_with_tools("wf", "wire two nodes", [], mode="build")
            ))

        mutation_events = [e for e in events if e.type == "chat_mutation"]
        assert len(mutation_events) == 1
        ops = mutation_events[0].mutation_plan["operations"]
        edge_ops = [op for op in ops if op.get("op") == "add_edge"]
        assert all(op.get("strict") is True for op in edge_ops), \
            "Build mode should force strict=True on generated edges"

    def test_auto_retry_repairs_failed_dry_run_before_emitting_mutation(self):
        """When first plan fails dry-run, auto-retry should emit repaired mutation."""
        graph = _make_empty_graph()
        store = _make_graph_store(graph)

        bad_plan_args = {
            "description": "Bad first attempt",
            "operations": [
                {"op": "add_node", "node_type": "llm_operator", "name": "Writer"},
                {"op": "add_node", "node_type": "llm_operator", "name": "Reviewer"},
                {
                    "op": "add_edge",
                    "source_id": "writer",
                    "source_port": "text",
                    "target_id": "reviewer",
                    "target_port": "missing_port",
                    "strict": True,
                },
            ],
        }
        fixed_plan_args = {
            "description": "Fixed retry attempt",
            "operations": [
                {"op": "add_node", "node_type": "llm_operator", "name": "Writer"},
                {"op": "add_node", "node_type": "llm_operator", "name": "Reviewer"},
                {
                    "op": "add_edge",
                    "source_id": "writer",
                    "source_port": "text",
                    "target_id": "reviewer",
                    "target_port": "input",
                },
            ],
        }

        bad_completion = CompletionResult(
            text="First attempt",
            usage={"prompt_tokens": 100, "completion_tokens": 20},
            tool_calls=[{
                "function": {
                    "name": "plan_graph_mutations",
                    "arguments": json.dumps(bad_plan_args),
                },
            }],
        )

        fixed_completion = CompletionResult(
            text="Second attempt",
            usage={"prompt_tokens": 80, "completion_tokens": 16},
            tool_calls=[{
                "function": {
                    "name": "plan_graph_mutations",
                    "arguments": json.dumps(fixed_plan_args),
                },
            }],
        )

        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=[bad_completion, fixed_completion])
        registry = MagicMock()
        registry.resolve.return_value = provider
        mgr = ChatManager(provider_registry=registry, graph_store=store)

        from dan.server import chat_manager as chat_module
        with (
            patch.object(chat_module, "_MUTATION_AUTO_RETRY", True),
            patch.object(chat_module, "_MUTATION_AUTO_RETRY_MAX", 2),
            patch.object(chat_module, "_DAN_USE_CODEGEN_BUILD", "0"),
        ):
            events = self._run(_collect(
                mgr.send_message_with_tools("wf", "wire two nodes", [], mode="build")
            ))

        mutation_events = [e for e in events if e.type == "chat_mutation"]
        assert len(mutation_events) == 1
        evt = mutation_events[0]
        assert evt.dry_run_result["success"] is True
        edge_ops = [op for op in evt.mutation_plan["operations"] if op.get("op") == "add_edge"]
        assert len(edge_ops) == 1
        assert edge_ops[0]["target_port"] == "input"
        assert provider.complete.await_count == 2

    def test_chain_3_template_intent(self):
        """chain_3 template applied directly to empty graph → dry_run succeeds."""
        graph = _make_empty_graph()
        store = _make_graph_store(graph)

        plan_args = {
            "description": "3-node chain workflow",
            "operations": WORKFLOW_TEMPLATES["chain_3"],
        }
        registry = _make_provider_registry(plan_args)
        mgr = ChatManager(provider_registry=registry, graph_store=store)

        from dan.server import chat_manager as chat_module
        with patch.object(chat_module, "_DAN_USE_CODEGEN_BUILD", "0"):
            events = self._run(_collect(
                mgr.send_message_with_tools(
                    "wf", "create a 3-step chain workflow", [], mode="build"
                )
            ))

        mutation_events = [e for e in events if e.type == "chat_mutation"]
        assert len(mutation_events) == 1
        assert mutation_events[0].dry_run_result["success"] is True

    def test_build_mode_replace_body_graph_without_entry_exit_succeeds(self):
        """Chat-mode build should tolerate missing body entry/exit IDs."""
        graph = _make_empty_graph()
        store = _make_graph_store(graph)

        plan_args = {
            "description": "Build a for-each workflow with inferred body boundaries",
            "operations": [
                {
                    "op": "add_node",
                    "id": "process-items",
                    "node_type": "for_each",
                    "name": "Process Items",
                },
                {
                    "op": "replace_body_graph",
                    "node_id": "process-items",
                    "operations": [
                        {
                            "op": "add_node",
                            "node_type": "llm_operator",
                            "name": "Summarize Item",
                            "config": {"prompt_template": "Summarize {item}"},
                        },
                    ],
                },
            ],
        }
        registry = _make_provider_registry(plan_args)
        mgr = ChatManager(provider_registry=registry, graph_store=store)

        from dan.server import chat_manager as chat_module
        with patch.object(chat_module, "_DAN_USE_CODEGEN_BUILD", "0"):
            events = self._run(_collect(
                mgr.send_message_with_tools(
                    "wf", "build a for-each workflow", [], mode="build"
                )
            ))

        mutation_events = [e for e in events if e.type == "chat_mutation"]
        assert len(mutation_events) == 1
        evt = mutation_events[0]
        assert evt.dry_run_result["success"] is True
        dry_graph = evt.dry_run_result["new_graph"]
        body_graph = dry_graph["sub_graphs"]["process-items__body"]
        assert body_graph["entry_points"] == ["summarize-item"]
        assert body_graph["exit_points"] == ["summarize-item"]

    def test_review_loop_template_standalone(self):
        """review_loop pattern alone → Writer is correctly identified as entry point."""
        graph = _make_empty_graph()
        graph_dict = json.loads(graph.model_dump_json())
        revision = compute_graph_revision(graph_dict)

        plan = MutationPlan.model_validate({
            "description": "review_loop",
            "operations": [WORKFLOW_TEMPLATES["paper_writing"][0]],
            "base_graph_revision": revision,
        })
        result = GraphMutator().dry_run(graph_dict, plan, current_revision=revision)
        assert result.success, f"review_loop dry_run failed: {result.errors}"
        assert result.new_graph is not None
        entry_points = result.new_graph.get("entry_points", [])
        assert len(entry_points) == 1
        assert entry_points[0] == "drafter"

    def test_empty_graph_revision_injected_correctly(self):
        """Revision injected from empty graph; plan's base_graph_revision matches."""
        graph = _make_empty_graph()
        store = _make_graph_store(graph)
        expected_revision = build_graph_summary(graph, "wf").revision

        plan_args = {
            "description": "chain",
            "operations": [
                {"op": "expand_pattern", "pattern": "chain", "params": {"count": 2}},
            ],
        }
        registry = _make_provider_registry(plan_args)
        mgr = ChatManager(provider_registry=registry, graph_store=store)

        from dan.server import chat_manager as chat_module
        with patch.object(chat_module, "_DAN_USE_CODEGEN_BUILD", "0"):
            events = self._run(_collect(
                mgr.send_message_with_tools("wf", "build a chain", [], mode="build")
            ))

        mutation_events = [e for e in events if e.type == "chat_mutation"]
        assert len(mutation_events) == 1
        assert mutation_events[0].mutation_plan["base_graph_revision"] == expected_revision


async def _collect(ait) -> list:
    """Drain an async iterator into a list."""
    results = []
    async for item in ait:
        results.append(item)
    return results


def _empty_graph():
    return {
        "metadata": {"name": "test", "description": "", "version": "1"},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
    }


# ── 8. Data-ingest pattern ───────────────────────────────────────────


class TestDataIngestPattern:
    def test_data_ingest_produces_three_nodes(self):
        """data_ingest pattern creates 3 nodes: Input, Index, RAG."""
        from dan.server.graph_mutator import _pattern_data_ingest
        ops = _pattern_data_ingest({"collection": "lit"})
        add_nodes = [o for o in ops if o["op"] == "add_node"]
        assert len(add_nodes) == 3
        types = [n["node_type"] for n in add_nodes]
        assert "input" in types
        assert "tool_operator" in types
        assert "rag_operator" in types

    def test_data_ingest_expand_succeeds(self):
        """Expanding data_ingest on empty graph succeeds."""
        from dan.server.graph_mutator import GraphMutator, MutationPlan, ExpandPattern
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            ExpandPattern(pattern="data_ingest", params={"collection": "test_col"}),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        assert len(r.new_graph["nodes"]) == 3
        assert len(r.new_graph["edges"]) == 3

    def test_data_ingest_custom_input_var(self):
        """data_ingest respects custom input_var for the InputNode variable."""
        from dan.server.graph_mutator import GraphMutator, MutationPlan, ExpandPattern
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            ExpandPattern(pattern="data_ingest", params={"input_var": "paper_dir", "collection": "c"}),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        inp = next(n for n in r.new_graph["nodes"] if n["id"] == "pdf-input")
        port_names = [p["name"] for p in inp["output_ports"]]
        assert "paper_dir" in port_names
        assert "topic" in port_names


# ── 9. Data-analysis pattern ─────────────────────────────────────────


class TestDataAnalysisPattern:
    def test_data_analysis_produces_four_nodes(self):
        from dan.server.graph_mutator import _pattern_data_analysis
        ops = _pattern_data_analysis({"input_var": "data_path"})
        add_nodes = [o for o in ops if o["op"] == "add_node"]
        assert len(add_nodes) == 4
        types = [n["node_type"] for n in add_nodes]
        assert "input" in types
        assert "tool_operator" in types
        assert "code_operator" in types
        assert "llm_operator" in types

    def test_data_analysis_expand_succeeds(self):
        from dan.server.graph_mutator import GraphMutator, MutationPlan, ExpandPattern
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            ExpandPattern(pattern="data_analysis", params={"input_var": "my_data"}),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        assert len(r.new_graph["nodes"]) == 4
        assert len(r.new_graph["edges"]) == 3


# ── 10. ApplySkill operation ─────────────────────────────────────────


class TestApplySkill:
    def test_apply_skill_by_tag(self):
        """apply_skill injects skill text into nodes matching target_tag."""
        from dan.server.graph_mutator import (
            GraphMutator, MutationPlan, AddNode, ApplySkill,
        )
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(
                node_type="llm_operator",
                name="Writer",
                config={"system_prompt": "original", "metadata": {"tags": ["writing"]}},
            ),
            AddNode(
                node_type="llm_operator",
                name="Other",
                config={"system_prompt": "untouched"},
            ),
            ApplySkill(skill="management_science_writing", target_tag="writing"),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        writer = [n for n in r.new_graph["nodes"] if n["name"] == "Writer"][0]
        other = [n for n in r.new_graph["nodes"] if n["name"] == "Other"][0]
        assert "Management Science" in _node_system_prompt(writer)
        assert _node_system_prompt(other) == "untouched"

    def test_apply_skill_by_node_ids(self):
        from dan.server.graph_mutator import (
            GraphMutator, MutationPlan, AddNode, ApplySkill,
        )
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Target Node", config={"system_prompt": ""}),
            ApplySkill(skill="informs_latex_style", target_nodes=["target-node"]),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        node = r.new_graph["nodes"][0]
        assert "INFORMS" in _node_system_prompt(node)

    def test_apply_skill_unknown_fails(self):
        from dan.server.graph_mutator import (
            GraphMutator, MutationPlan, AddNode, ApplySkill,
        )
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="N", config={"metadata": {"tags": ["x"]}}),
            ApplySkill(skill="nonexistent_skill", target_tag="x"),
        ])
        r = GraphMutator().apply(empty, plan)
        assert not r.success

    def test_apply_skill_idempotent(self):
        """Applying the same skill twice doesn't double-inject."""
        from dan.server.graph_mutator import (
            GraphMutator, MutationPlan, AddNode, ApplySkill,
        )
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(
                node_type="llm_operator",
                name="W",
                config={"system_prompt": "", "metadata": {"tags": ["writing"]}},
            ),
            ApplySkill(skill="management_science_writing", target_tag="writing"),
            ApplySkill(skill="management_science_writing", target_tag="writing"),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        node = r.new_graph["nodes"][0]
        count = _node_system_prompt(node).count("Management Science")
        assert count == 1


# ── 11. Tool port manifests ──────────────────────────────────────────


class TestToolPortManifests:
    def test_tool_operator_gets_specific_ports(self):
        """tool_operator with known tool_id gets tool-specific ports."""
        from dan.server.graph_mutator import GraphMutator, MutationPlan, AddNode
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(node_type="tool_operator", name="Reader",
                    config={"tool_id": "file_read"}),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        node = r.new_graph["nodes"][0]
        in_names = [p["name"] for p in node["input_ports"]]
        out_names = [p["name"] for p in node["output_ports"]]
        assert "path" in in_names
        assert "content" in out_names

    def test_unknown_tool_gets_generic_ports(self):
        from dan.server.graph_mutator import GraphMutator, MutationPlan, AddNode
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(node_type="tool_operator", name="Custom",
                    config={"tool_id": "my_custom_tool"}),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        node = r.new_graph["nodes"][0]
        in_names = [p["name"] for p in node["input_ports"]]
        out_names = [p["name"] for p in node["output_ports"]]
        assert "input" in in_names
        assert "result" in out_names

    def test_compile_latex_has_title_port(self):
        """compile_latex manifest declares 'title' as required input alongside 'content'."""
        from dan.server.graph_mutator import GraphMutator, MutationPlan, AddNode
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(node_type="tool_operator", name="Compiler",
                    config={"tool_id": "compile_latex"}),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        node = r.new_graph["nodes"][0]
        in_names = [p["name"] for p in node["input_ports"]]
        out_names = [p["name"] for p in node["output_ports"]]
        assert "content" in in_names
        assert "title" in in_names
        assert "pdf_path" in out_names

    def test_save_paper_has_structured_ports(self):
        """save_paper manifest exposes content/title inputs and tex_path/bib_path outputs."""
        from dan.server.graph_mutator import GraphMutator, MutationPlan, AddNode
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(node_type="tool_operator", name="Saver",
                    config={"tool_id": "save_paper"}),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        node = r.new_graph["nodes"][0]
        in_names = [p["name"] for p in node["input_ports"]]
        out_names = [p["name"] for p in node["output_ports"]]
        assert "content" in in_names and "title" in in_names
        assert "tex_path" in out_names and "bib_path" in out_names


# ── 12. Input node variable ports ────────────────────────────────────


class TestInputNodeVariablePorts:
    def test_input_node_with_variables_gets_named_ports(self):
        """InputNode with variables config creates output ports matching variable names."""
        from dan.server.graph_mutator import GraphMutator, MutationPlan, AddNode
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(node_type="input", name="Inputs",
                    config={"variables": [
                        {"name": "pdf_dir", "type": "string", "default": ""},
                        {"name": "data_path", "type": "string", "default": ""},
                    ]}),
        ])
        r = GraphMutator().apply(empty, plan)
        assert r.success
        node = r.new_graph["nodes"][0]
        out_names = [p["name"] for p in node["output_ports"]]
        assert "pdf_dir" in out_names
        assert "data_path" in out_names

    def test_input_node_missing_named_source_port_is_auto_repaired(self, monkeypatch):
        """InputNode edges can infer a missing named variable port when unambiguous."""
        from dan.server.graph_mutator import AddEdge, GraphMutator, MutationPlan, AddNode

        monkeypatch.setenv("DAN_WORKER_BUILDER", "disabled")
        empty = _empty_graph()
        plan = MutationPlan(operations=[
            AddNode(node_type="input", name="Receive Watchlist"),
            AddNode(
                node_type="tool_operator",
                name="Load Watchlist",
                config={"tool_id": "file_read"},
            ),
            AddEdge(
                source_id="receive-watchlist",
                source_port="watchlist_path",
                target_id="load-watchlist",
                target_port="path",
            ),
        ])

        r = GraphMutator().apply(empty, plan)
        assert r.success, r.errors
        node = next(node for node in r.new_graph["nodes"] if node["id"] == "receive-watchlist")
        out_names = [p["name"] for p in node["output_ports"]]
        assert out_names == ["input", "watchlist_path"]
        assert node["variables"] == [{"name": "watchlist_path", "type": "string", "default": ""}]
        assert any(
            "Auto-added input variable/output port 'watchlist_path'" in message
            for message in r.diagnostics
        )


# ── 13. INFORMS template ────────────────────────────────────────────


class TestINFORMSTemplate:
    def test_informs_template_applies_successfully(self):
        """Full INFORMS template creates a valid connected graph."""
        from dan.server.graph_mutator import (
            GraphMutator, MutationPlan, ExpandPattern, AddNode, AddEdge, ApplySkill,
        )
        from dan.server.chat_manager import WORKFLOW_TEMPLATES

        _OP_MAP = {
            "expand_pattern": ExpandPattern,
            "add_node": AddNode,
            "add_edge": AddEdge,
            "apply_skill": ApplySkill,
        }
        tpl = WORKFLOW_TEMPLATES["informs_paper_writing"]
        ops = [_OP_MAP[raw["op"]](**raw) for raw in tpl]
        empty = _empty_graph()
        plan = MutationPlan(operations=ops)
        r = GraphMutator().apply(empty, plan)
        assert r.success
        assert len(r.new_graph["nodes"]) == 19
        assert len(r.new_graph["edges"]) >= 20

    def test_informs_template_has_skill_injection(self):
        """INFORMS template injects skill prompts into tagged nodes."""
        from dan.server.graph_mutator import (
            GraphMutator, MutationPlan, ExpandPattern, AddNode, AddEdge, ApplySkill,
        )
        from dan.server.chat_manager import WORKFLOW_TEMPLATES

        _OP_MAP = {
            "expand_pattern": ExpandPattern,
            "add_node": AddNode,
            "add_edge": AddEdge,
            "apply_skill": ApplySkill,
        }
        tpl = WORKFLOW_TEMPLATES["informs_paper_writing"]
        ops = [_OP_MAP[raw["op"]](**raw) for raw in tpl]
        empty = _empty_graph()
        r = GraphMutator().apply(empty, MutationPlan(operations=ops))
        assert r.success
        latex_assembler = [n for n in r.new_graph["nodes"] if n["name"] == "LaTeX Assembler"][0]
        assert "INFORMS" in _node_system_prompt(latex_assembler)


# ── 14. Skill library ───────────────────────────────────────────────


class TestSkillLibrary:
    def test_skill_library_has_required_skills(self):
        from dan.server.skill_library import SKILL_LIBRARY
        assert "management_science_writing" in SKILL_LIBRARY
        assert "informs_latex_style" in SKILL_LIBRARY
        assert "skill_creation" in SKILL_LIBRARY

    def test_skill_entries_have_required_fields(self):
        from dan.server.skill_library import SKILL_LIBRARY
        for name, skill in SKILL_LIBRARY.items():
            assert "text" in skill, f"{name} missing 'text'"
            assert "inject_as" in skill, f"{name} missing 'inject_as'"
            assert "tags" in skill, f"{name} missing 'tags'"
            assert len(skill["text"]) > 50, f"{name} text too short"


# ── 15. Mutation schema updates ──────────────────────────────────────


class TestMutationSchemaUpdate:
    def test_schema_includes_apply_skill(self):
        from dan.server.chat_manager import MUTATION_TOOL_SCHEMA
        items_schema = MUTATION_TOOL_SCHEMA["function"]["parameters"]["properties"]["operations"]["items"]
        ops_variants = items_schema.get("anyOf") or items_schema.get("oneOf") or []
        op_types = [o["properties"]["op"].get("const") for o in ops_variants]
        assert "apply_skill" in op_types

    def test_schema_includes_new_patterns(self):
        from dan.server.chat_manager import MUTATION_TOOL_SCHEMA
        items_schema = MUTATION_TOOL_SCHEMA["function"]["parameters"]["properties"]["operations"]["items"]
        ops_variants = items_schema.get("anyOf") or items_schema.get("oneOf") or []
        expand_schema = [o for o in ops_variants if o["properties"]["op"].get("const") == "expand_pattern"][0]
        pattern_enum = expand_schema["properties"]["pattern"]["enum"]
        assert "data_ingest" in pattern_enum
        assert "data_analysis" in pattern_enum
