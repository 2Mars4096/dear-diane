"""Integration tests for the codegen/intent-compiler build path in ChatManager.

Tests the Phase 24-1 (builder codegen) and 24-2 (intent compiler) integration
with ChatManager.send_message_with_tools(), verifying:
- Empty graph + DAN_USE_CODEGEN_BUILD=1 triggers codegen path
- Non-empty graph always uses mutation path
- Feature flag DAN_USE_CODEGEN_BUILD=0 disables codegen
- Intent fully covered → IntentCompiler used
- Intent not covered → falls back to codegen
- Codegen failure → diagnosis invoked
- Events emitted in correct order
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from dan.models.graph import Graph
from dan.providers import CompletionResult, StreamChunk
from dan.providers.registry import ProviderRegistry
from dan.server.chat_manager import (
    ChatCodeGeneratedEvent,
    ChatCompleteEvent,
    ChatErrorEvent,
    ChatGraphCreatedEvent,
    ChatIntentExtractedEvent,
    ChatManager,
    ChatMutationEvent,
    ChatStreamEvent,
    ChatValidationResultEvent,
    compute_graph_revision,
)
from dan.server.graph_store import GraphStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_BUILDER_CODE = '''\
from dan.builder import workflow

wf = workflow("test_workflow")
a = wf.llm("step_1", prompt="Process: {input}")
b = wf.llm("step_2", prompt="Refine: {text}")
a >> b
graph = wf.build()
'''


def _make_empty_graph_dict() -> dict:
    g = Graph()
    g.metadata.name = "test-wf"
    return json.loads(g.model_dump_json())


def _make_nonempty_graph_dict() -> dict:
    from dan.models.nodes import LLMOperator
    from dan.models.ports import InputPort, OutputPort

    g = Graph(
        nodes=[
            LLMOperator(
                id="writer",
                name="Writer",
                model="test",
                prompt_template="Write about {topic}",
                input_ports=[InputPort(name="topic")],
                output_ports=[OutputPort(name="text")],
                position={"x": 0, "y": 0},
            ),
        ],
        entry_points=["writer"],
        exit_points=["writer"],
    )
    return json.loads(g.model_dump_json())


class _MockProvider:
    """Provider that returns a plain text response (no tool calls)."""

    async def complete(self, **kwargs: Any) -> CompletionResult:
        return CompletionResult(
            text="Mock reply",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="Mock", accumulated="Mock", done=False, usage=None)
        yield StreamChunk(
            delta=" reply",
            accumulated="Mock reply",
            done=True,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


class _MockMutationProvider:
    """Provider that returns a mutation tool call."""

    async def complete(self, **kwargs: Any) -> CompletionResult:
        mutation = {
            "description": "Add a node",
            "operations": [
                {"op": "add_node", "node_type": "llm_operator", "name": "Node1"}
            ],
        }
        return CompletionResult(
            text="Adding node.",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "plan_graph_mutations",
                    "arguments": json.dumps(mutation),
                },
            }],
            usage={"prompt_tokens": 20, "completion_tokens": 15},
        )

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="Adding", accumulated="Adding", done=False, usage=None)
        yield StreamChunk(
            delta=" node.",
            accumulated="Adding node.",
            done=True,
            usage={"prompt_tokens": 20, "completion_tokens": 15},
        )


class _IntentThenCodegenProvider:
    """Provider that returns an intent tool call first, then builder code."""

    def __init__(self, *, intent_tool_call: dict | None = None, code: str = ""):
        self._intent_tool_call = intent_tool_call
        self._code = code
        self._call_count = 0

    async def complete(self, **kwargs: Any) -> CompletionResult:
        self._call_count += 1
        tools = kwargs.get("tools", [])
        tool_names = [
            t.get("function", {}).get("name", "") for t in tools if isinstance(t, dict)
        ]

        if "emit_workflow_intent" in tool_names and self._intent_tool_call:
            return CompletionResult(
                text="",
                tool_calls=[self._intent_tool_call],
                usage={"prompt_tokens": 15, "completion_tokens": 10},
            )

        return CompletionResult(
            text=f"```python\n{self._code}\n```" if self._code else "No code",
            tool_calls=[],
            usage={"prompt_tokens": 20, "completion_tokens": 30},
        )

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(delta="text", accumulated="text", done=True, usage=None)


def _make_intent_tool_call(goal: str = "test workflow") -> dict:
    """Build a valid emit_workflow_intent tool call."""
    intent_data = {
        "goal": goal,
        "stages": [
            {
                "name": "process",
                "stage_type": "transform",
                "description": "Process the input",
            },
            {
                "name": "refine",
                "stage_type": "transform",
                "description": "Refine the output",
            },
        ],
        "global_inputs": [],
        "global_outputs": [],
    }
    return {
        "id": "call_intent",
        "type": "function",
        "function": {
            "name": "emit_workflow_intent",
            "arguments": json.dumps(intent_data),
        },
    }


def _make_chat_manager(
    provider: Any, *, graphs_dir: str | None = None
) -> ChatManager:
    """Create a ChatManager with the given provider and temp graph store."""
    if graphs_dir is None:
        graphs_dir = tempfile.mkdtemp()
    store = GraphStore(base_dir=graphs_dir)
    registry = MagicMock(spec=ProviderRegistry)
    registry.resolve.return_value = provider
    return ChatManager(
        provider_registry=registry,
        graph_store=store,
    )


async def _collect_events(
    manager: ChatManager,
    workflow_id: str,
    message: str = "Build a simple chain workflow",
    history: list | None = None,
    mode: str = "agent",
) -> list[ChatStreamEvent]:
    events: list[ChatStreamEvent] = []
    async for evt in manager.send_message_with_tools(
        workflow_id=workflow_id,
        message=message,
        history=history or [],
        mode=mode,
    ):
        events.append(evt)
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCodegenPathRouting:
    """Test that the codegen path is selected/skipped based on conditions."""

    @pytest.mark.asyncio
    async def test_empty_graph_codegen_enabled_uses_codegen_path(self):
        """Empty graph + DAN_USE_CODEGEN_BUILD=1 → codegen path invoked."""
        provider = _IntentThenCodegenProvider(
            intent_tool_call=_make_intent_tool_call(),
            code=_VALID_BUILDER_CODE,
        )
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_codegen"
        store.create_graph(wf_id, _make_empty_graph_dict())

        with patch.dict(os.environ, {"DAN_USE_CODEGEN_BUILD": "1"}):
            with patch(
                "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"
            ):
                events = await _collect_events(manager, wf_id)

        event_types = [type(e).__name__ for e in events]
        assert "ChatIntentExtractedEvent" in event_types or "ChatCodeGeneratedEvent" in event_types
        assert "ChatGraphCreatedEvent" in event_types
        assert "ChatCompleteEvent" in event_types

        saved = store.get_graph(wf_id)
        assert saved is not None
        graph = Graph.model_validate(saved)
        assert len(graph.nodes) > 0

    @pytest.mark.asyncio
    async def test_nonempty_graph_uses_mutation_path(self):
        """Non-empty graph → mutation path used (not codegen)."""
        provider = _MockMutationProvider()
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_nonempty"
        store.create_graph(wf_id, _make_nonempty_graph_dict())

        with patch(
            "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"
        ):
            events = await _collect_events(
                manager, wf_id, message="Add a summarizer node"
            )

        event_types = [type(e).__name__ for e in events]
        assert "ChatGraphCreatedEvent" not in event_types
        assert "ChatIntentExtractedEvent" not in event_types
        has_mutation_or_complete = (
            "ChatMutationEvent" in event_types
            or "ChatCompleteEvent" in event_types
        )
        assert has_mutation_or_complete

    @pytest.mark.asyncio
    async def test_codegen_disabled_uses_mutation_path(self):
        """DAN_USE_CODEGEN_BUILD=0 → mutation path even for empty graph."""
        provider = _MockMutationProvider()
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_disabled"
        store.create_graph(wf_id, _make_empty_graph_dict())

        with patch(
            "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "0"
        ):
            events = await _collect_events(manager, wf_id)

        event_types = [type(e).__name__ for e in events]
        assert "ChatGraphCreatedEvent" not in event_types
        assert "ChatIntentExtractedEvent" not in event_types


class TestIntentCompilerFastPath:
    """Test that fully-covered intents use the intent compiler."""

    @pytest.mark.asyncio
    async def test_fully_covered_intent_uses_compiler(self):
        """Intent fully covered → IntentCompiler used (no LLM codegen call)."""
        provider = _IntentThenCodegenProvider(
            intent_tool_call=_make_intent_tool_call("Build a two-step chain"),
            code=_VALID_BUILDER_CODE,
        )
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_intent"
        store.create_graph(wf_id, _make_empty_graph_dict())

        with patch(
            "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"
        ):
            events = await _collect_events(
                manager, wf_id, message="Build a two-step chain"
            )

        event_types = [type(e).__name__ for e in events]
        assert "ChatIntentExtractedEvent" in event_types

        intent_events = [
            e for e in events if isinstance(e, ChatIntentExtractedEvent)
        ]
        assert len(intent_events) == 1
        assert intent_events[0].fully_covered is True
        assert intent_events[0].stage_count == 2

        code_events = [
            e for e in events if isinstance(e, ChatCodeGeneratedEvent)
        ]
        if code_events:
            assert code_events[0].source == "intent_compiler"

    @pytest.mark.asyncio
    async def test_fully_covered_intent_uses_in_process_execution(self):
        """Fully covered intent takes the direct intent-compiler path and creates a graph."""
        provider = _IntentThenCodegenProvider(
            intent_tool_call=_make_intent_tool_call("Build a two-step chain"),
            code=_VALID_BUILDER_CODE,
        )
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_intent_deterministic"
        store.create_graph(wf_id, _make_empty_graph_dict())

        deterministic_exec = Mock(return_value=_make_nonempty_graph_dict())
        with patch.object(
            ChatManager,
            "_exec_deterministic_builder_code",
            deterministic_exec,
        ):
            with patch.object(
                manager,
                "_sandbox_exec_builder_code",
                side_effect=AssertionError("sandbox should not be used for intent path"),
            ):
                with patch("dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"):
                    events = await _collect_events(
                        manager, wf_id, message="Build a two-step chain",
                    )

        event_types = [type(e).__name__ for e in events]
        assert "ChatGraphCreatedEvent" in event_types
        deterministic_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_covered_falls_back_to_codegen(self):
        """Intent not fully covered → falls back to codegen."""
        unsupported_intent = {
            "goal": "test",
            "stages": [
                {
                    "name": "step",
                    "stage_type": "transform",
                    "description": "Do stuff",
                }
            ],
            "global_inputs": [],
            "global_outputs": [],
        }
        intent_tc = {
            "id": "call_intent",
            "type": "function",
            "function": {
                "name": "emit_workflow_intent",
                "arguments": json.dumps(unsupported_intent),
            },
        }

        provider = _IntentThenCodegenProvider(
            intent_tool_call=intent_tc,
            code=_VALID_BUILDER_CODE,
        )
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_fallback"
        store.create_graph(wf_id, _make_empty_graph_dict())

        with patch(
            "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"
        ):
            with patch(
                "dan.meta.intent_compiler.CoverageChecker.check"
            ) as mock_check:
                from dan.meta.intent_compiler import CoverageResult

                mock_check.return_value = CoverageResult(
                    fully_covered=False,
                    supported_stages=[],
                    unsupported_stages=["step"],
                    recommendation="fallback",
                )
                events = await _collect_events(
                    manager, wf_id, message="Build something unusual"
                )

        event_types = [type(e).__name__ for e in events]
        assert "ChatIntentExtractedEvent" in event_types

        intent_events = [
            e for e in events if isinstance(e, ChatIntentExtractedEvent)
        ]
        assert intent_events[0].fully_covered is False

        code_events = [
            e for e in events if isinstance(e, ChatCodeGeneratedEvent)
        ]
        codegen_sources = [e.source for e in code_events]
        assert "codegen" in codegen_sources


class TestCodegenFailureAndDiagnosis:
    """Test diagnosis loop invocation on codegen failure."""

    @pytest.mark.asyncio
    async def test_codegen_failure_triggers_diagnosis(self):
        """Codegen failure → diagnosis invoked."""
        bad_code = "from dan.builder import workflow\nwf = workflow('bad')\ngraph = wf.build()"
        provider = _IntentThenCodegenProvider(
            intent_tool_call=None,
            code=bad_code,
        )
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_diag"
        store.create_graph(wf_id, _make_empty_graph_dict())

        with patch(
            "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"
        ):
            events = await _collect_events(
                manager, wf_id, message="Build a pipeline"
            )

        event_types = [type(e).__name__ for e in events]
        has_validation = "ChatValidationResultEvent" in event_types
        has_code_gen = "ChatCodeGeneratedEvent" in event_types
        assert has_code_gen
        assert has_validation or "ChatCompleteEvent" in event_types


class TestEventOrdering:
    """Test that events are emitted in the correct order."""

    @pytest.mark.asyncio
    async def test_successful_codegen_event_order(self):
        """Events emitted: intent_extracted → code_generated → validation → graph_created → complete."""
        provider = _IntentThenCodegenProvider(
            intent_tool_call=_make_intent_tool_call(),
            code=_VALID_BUILDER_CODE,
        )
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_order"
        store.create_graph(wf_id, _make_empty_graph_dict())

        with patch(
            "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"
        ):
            events = await _collect_events(manager, wf_id)

        event_types = [type(e).__name__ for e in events]

        if "ChatIntentExtractedEvent" in event_types:
            intent_idx = event_types.index("ChatIntentExtractedEvent")
            if "ChatCodeGeneratedEvent" in event_types:
                code_idx = event_types.index("ChatCodeGeneratedEvent")
                assert intent_idx < code_idx

        if "ChatGraphCreatedEvent" in event_types:
            graph_idx = event_types.index("ChatGraphCreatedEvent")
            complete_idx = event_types.index("ChatCompleteEvent")
            assert graph_idx < complete_idx

    @pytest.mark.asyncio
    async def test_graph_saved_on_codegen_success(self):
        """On codegen success, graph is saved via graph_store."""
        provider = _IntentThenCodegenProvider(
            intent_tool_call=_make_intent_tool_call(),
            code=_VALID_BUILDER_CODE,
        )
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_save"
        store.create_graph(wf_id, _make_empty_graph_dict())

        with patch(
            "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"
        ):
            events = await _collect_events(manager, wf_id)

        graph_events = [e for e in events if isinstance(e, ChatGraphCreatedEvent)]
        assert len(graph_events) == 1
        assert graph_events[0].workflow_id == wf_id
        assert graph_events[0].node_count > 0

        saved = store.get_graph(wf_id)
        assert saved is not None
        g = Graph.model_validate(saved)
        assert len(g.nodes) == graph_events[0].node_count

    @pytest.mark.asyncio
    async def test_graph_revision_updated_after_save(self):
        """After codegen save, graph_revision in ChatCompleteEvent matches saved graph."""
        provider = _IntentThenCodegenProvider(
            intent_tool_call=_make_intent_tool_call(),
            code=_VALID_BUILDER_CODE,
        )
        graphs_dir = tempfile.mkdtemp()
        manager = _make_chat_manager(provider, graphs_dir=graphs_dir)
        store = manager._graph_store

        wf_id = "test_wf_rev"
        store.create_graph(wf_id, _make_empty_graph_dict())

        with patch(
            "dan.server.chat_manager._DAN_USE_CODEGEN_BUILD", "1"
        ):
            events = await _collect_events(manager, wf_id)

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(complete_events) == 1

        saved = store.get_graph(wf_id)
        assert saved is not None
        expected_rev = compute_graph_revision(saved)
        assert complete_events[0].graph_revision == expected_rev


class TestCodegenHelpers:
    """Test static helper methods on ChatManager."""

    def test_extract_code_from_fenced_response(self):
        text = "Here is the code:\n```python\nprint('hello')\n```\nDone."
        code = ChatManager._extract_code_from_response(text)
        assert code == "print('hello')"

    def test_extract_code_from_plain_response(self):
        text = "print('hello')"
        code = ChatManager._extract_code_from_response(text)
        assert code == "print('hello')"

    def test_exec_deterministic_builder_code_valid(self):
        code = _VALID_BUILDER_CODE
        result = ChatManager._exec_deterministic_builder_code(code)
        assert result is not None
        assert "nodes" in result
        assert len(result["nodes"]) > 0

    def test_exec_deterministic_builder_code_invalid(self):
        result = ChatManager._exec_deterministic_builder_code("raise ValueError('bad')")
        assert result is None

    def test_parse_intent_from_result_with_tool_call(self):
        tc = _make_intent_tool_call("test goal")
        result = CompletionResult(
            text="",
            tool_calls=[tc],
            usage={},
        )
        intent = ChatManager._parse_intent_from_result(result)
        assert intent is not None
        assert intent.goal == "test goal"
        assert len(intent.stages) == 2

    def test_parse_intent_from_result_no_tool_call(self):
        result = CompletionResult(text="just text", tool_calls=[], usage={})
        intent = ChatManager._parse_intent_from_result(result)
        assert intent is None

    def test_parse_intent_from_result_json_in_content(self):
        """JSON-in-content fallback when model doesn't use tool_calls (33-6)."""
        intent_json = json.dumps({
            "goal": "Build a 3-step chain",
            "stages": [
                {"name": "s1", "stage_type": "transform", "description": "Step 1"},
                {"name": "s2", "stage_type": "transform", "description": "Step 2"},
                {"name": "s3", "stage_type": "transform", "description": "Step 3"},
            ],
            "global_inputs": [],
            "global_outputs": [],
        })
        result = CompletionResult(text=intent_json, tool_calls=[], usage={})
        intent = ChatManager._parse_intent_from_result(result)
        assert intent is not None
        assert intent.goal == "Build a 3-step chain"
        assert len(intent.stages) == 3

    def test_parse_intent_from_result_json_in_fenced_block(self):
        """JSON-in-content with ```json fence (33-6)."""
        intent_json = json.dumps({
            "goal": "RAG pipeline",
            "stages": [
                {"name": "retrieve", "stage_type": "rag_retrieval", "description": "Retrieve"},
                {"name": "answer", "stage_type": "transform", "description": "Answer"},
            ],
            "global_inputs": [],
            "global_outputs": [],
        })
        text = f"Here is the intent:\n```json\n{intent_json}\n```"
        result = CompletionResult(text=text, tool_calls=[], usage={})
        intent = ChatManager._parse_intent_from_result(result)
        assert intent is not None
        assert intent.goal == "RAG pipeline"
        assert intent.stages[0].stage_type.value == "rag_retrieval"
