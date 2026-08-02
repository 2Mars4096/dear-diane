"""Tests for Plan 28-2: multi-turn tool execution loop in ChatManager."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.providers import CompletionResult, StreamChunk
import dan.server.chat_manager as chat_manager_module
from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatErrorEvent,
    ChatGraphCreatedEvent,
    ChatInterruptedEvent,
    ChatManager,
    ChatMutationEvent,
    ChatToolCallResultEvent,
    ChatToolCallStartEvent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_GRAPH = {"nodes": [], "edges": [], "metadata": {"name": "test"}}
_NON_EMPTY_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "test"},
    "nodes": [
        {
            "id": "n1",
            "node_type": "llm_operator",
            "name": "Writer",
            "input_ports": [],
            "output_ports": [],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "model": "mock-model",
            "prompt_template": "Write: {input}",
        }
    ],
    "edges": [],
    "sub_graphs": {},
    "entry_points": ["n1"],
    "exit_points": ["n1"],
    "shared_context": [],
    "artifact_refs": [],
}


@pytest.fixture(autouse=True)
def _disable_codegen():
    """Disable codegen fast path so the tool loop is always exercised."""
    import dan.server.chat_manager as _cm

    orig = _cm._DAN_USE_CODEGEN_BUILD
    _cm._DAN_USE_CODEGEN_BUILD = "0"
    yield
    _cm._DAN_USE_CODEGEN_BUILD = orig


def _tool_call(name: str, args: dict[str, Any], call_id: str = "tc_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _text_result(text: str) -> CompletionResult:
    return CompletionResult(text=text, tool_calls=None, usage={"total_tokens": 10})


def _tool_result(
    text: str, tool_calls: list[dict[str, Any]]
) -> CompletionResult:
    return CompletionResult(text=text, tool_calls=tool_calls, usage={"total_tokens": 15})


def _requested_tool_names(call_kwargs: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tool in call_kwargs.get("tools", []) or []:
        func = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(func, dict):
            names.append(str(func.get("name") or ""))
    return names


@dataclass
class _FakeCapResult:
    success: bool = True
    message: str = "ok"
    output_preview: str = ""
    stream_channel_id: str | None = None
    data: Any = None
    retryable: bool = False
    error_type: str = ""


def _build_chat_manager(
    provider_responses: list[CompletionResult],
    cap_tools: dict[str, Any] | None = None,
    *,
    cacheable_tools: set[str] | None = None,
) -> ChatManager:
    """Build a ChatManager with mocked provider and capability registry."""
    call_idx = 0

    async def _mock_complete(**kwargs: Any) -> CompletionResult:
        nonlocal call_idx
        idx = min(call_idx, len(provider_responses) - 1)
        call_idx += 1
        return provider_responses[idx]

    mock_provider = MagicMock()
    mock_provider.complete = AsyncMock(side_effect=_mock_complete)

    mock_registry = MagicMock()
    mock_registry.resolve.return_value = mock_provider

    mock_graph_store = MagicMock()
    mock_graph_store.get_graph.return_value = _TEST_GRAPH

    cap_registry = None
    if cap_tools is not None:
        cap_registry = MagicMock()
        cap_registry.get_tools.return_value = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"Tool {name}",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in cap_tools
        ]
        cap_registry.is_available.return_value = True
        cap_registry.is_cacheable.side_effect = lambda name: name in (cacheable_tools or set())

        async def _cap_execute(name: str, args: dict, ctx: Any, **kw: Any) -> _FakeCapResult:
            handler = cap_tools.get(name)
            if handler:
                result = handler(args)
                if asyncio.iscoroutine(result):
                    return await result
                return result
            return _FakeCapResult(success=True, message=f"{name} done")

        cap_registry.execute = AsyncMock(side_effect=_cap_execute)

    from dan.server.capability_registry import CapabilityContext

    cap_ctx = CapabilityContext(workflow_id="test-wf", graph_store=mock_graph_store)

    cm = ChatManager.__new__(ChatManager)
    cm._providers = mock_registry
    cm._graph_store = mock_graph_store
    cm._chat_model = "test-model"
    cm._chat_store = None
    cm._capability_registry = cap_registry
    cm._capability_context = cap_ctx
    cm._conversation_summaries = {}
    cm._conversation_summary_max_entries = 10
    cm._prompt_details_by_workflow = {}
    cm._behavior_store = None
    cm._mention_resolver = None
    cm._user_context_composer = None
    cm._recent_context_composer = None
    cm._user_profile = None
    cm._conversation_memory = None
    cm._memory_kernel = None
    cm._cancel_events = {}
    cm._workflow_generation_runtime = None
    cm._resource_tracker = None

    async def _false_hint(*args: Any, **kwargs: Any) -> bool:
        return False

    cm._should_inject_research_prompt_hint = _false_hint
    cm._should_inject_exploration_prompt_hint = _false_hint

    return cm


async def _collect(stream) -> list:
    events = []
    async for event in stream:
        events.append(event)
    return events


def _install_blocking_complete(
    mock_provider: Any,
    responses: list[CompletionResult],
    *,
    block_on_call: int,
    started: asyncio.Event,
) -> None:
    """Make ``complete()`` block on a specific call until cancelled."""

    call_count = 0

    async def _side_effect(**kwargs: Any) -> CompletionResult:
        nonlocal call_count
        idx = min(call_count, len(responses) - 1)
        resp = responses[idx]
        call_count += 1
        if call_count == block_on_call:
            started.set()
            await asyncio.Event().wait()
        return resp

    mock_provider.complete = AsyncMock(side_effect=_side_effect)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMultiTurnToolLoop:
    """Tests for the multi-turn tool execution loop (plan 28-2)."""

    @pytest.mark.asyncio
    async def test_text_only_single_turn(self):
        """LLM returns text with no tool calls -> single turn, immediate complete."""
        cm = _build_chat_manager([_text_result("Hello there!")])
        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="hi",
                history=[],
            )
        )
        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(complete_events) == 1
        assert complete_events[0].content == "Hello there!"

    @pytest.mark.asyncio
    async def test_tool_then_text_two_turns(self):
        """LLM calls tool on turn 1, returns text on turn 2."""
        cap_tools = {
            "list_directory": lambda args: _FakeCapResult(
                success=True, message="file1.txt\nfile2.txt"
            ),
        }
        responses = [
            _tool_result("", [_tool_call("list_directory", {"path": "/"}, "tc_a")]),
            _text_result("Found 2 files: file1.txt and file2.txt"),
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools=cap_tools,
            cacheable_tools={"file_read"},
        )
        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="list files",
                history=[],
            )
        )

        start_events = [e for e in events if isinstance(e, ChatToolCallStartEvent)]
        result_events = [e for e in events if isinstance(e, ChatToolCallResultEvent)]
        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]

        assert len(start_events) == 1
        assert start_events[0].tool_name == "list_directory"
        assert len(result_events) == 1
        assert result_events[0].status == "success"
        assert len(complete_events) == 1
        assert "2 files" in complete_events[0].content

    @pytest.mark.asyncio
    async def test_tool_loop_does_not_replay_blank_assistant_messages(self):
        """Tool follow-up requests should not send empty assistant transcript turns."""
        cap_tools = {
            "list_directory": lambda args: _FakeCapResult(
                success=True, message="file1.txt\nfile2.txt"
            ),
        }
        responses = [
            _tool_result("", [_tool_call("list_directory", {"path": "/"}, "tc_empty")]),
            _text_result("Found 2 files: file1.txt and file2.txt"),
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools=cap_tools,
        )
        mock_provider = cm._providers.resolve.return_value
        call_idx = 0

        async def _asserting_complete(**kwargs: Any) -> CompletionResult:
            nonlocal call_idx
            if call_idx >= 1:
                assistant_messages = [
                    msg for msg in kwargs.get("messages", [])
                    if msg.get("role") == "assistant"
                ]
                assert assistant_messages
                for msg in assistant_messages:
                    if msg.get("tool_calls"):
                        content = msg.get("content")
                        assert content is None or str(content).strip()
                    else:
                        assert str(msg.get("content") or "").strip()
            idx = min(call_idx, len(responses) - 1)
            call_idx += 1
            return responses[idx]

        mock_provider.complete = AsyncMock(side_effect=_asserting_complete)

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="list files",
                history=[],
            )
        )

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(complete_events) == 1
        assert "2 files" in complete_events[0].content

    @pytest.mark.asyncio
    async def test_followup_turn_uses_bounded_max_tokens(self):
        """Post-tool follow-up completions should reserve explicit output budget."""
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=True, message="draft section"
            ),
        }
        responses = [
            _tool_result("Let me inspect the draft first.", [_tool_call("file_read", {"path": "draft.tex"}, "tc_budget")]),
            _text_result("Here is the updated summary."),
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools=cap_tools,
            cacheable_tools={"file_read"},
        )
        mock_provider = cm._providers.resolve.return_value

        await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="update the draft",
                history=[],
            )
        )

        assert mock_provider.complete.await_count == 2
        followup_kwargs = mock_provider.complete.await_args_list[1].kwargs
        assert followup_kwargs["max_tokens"] == chat_manager_module._completion_max_tokens(
            cm._chat_model
        )

    @pytest.mark.asyncio
    async def test_followup_missing_completion_logs_step_trace(self, caplog: pytest.LogCaptureFixture):
        """Silent post-tool exits should log the follow-up failure with tool context."""
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=True, message="draft section"
            ),
        }
        responses = [
            _tool_result(
                "Let me inspect the draft first.",
                [_tool_call("file_read", {"path": "draft.tex"}, "tc_followup_trace")],
            ),
            None,
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools=cap_tools,
            cacheable_tools={"file_read"},
        )

        with caplog.at_level(logging.INFO):
            events = await _collect(
                cm.send_message_with_tools(
                    workflow_id="test-wf",
                    message="update the draft",
                    history=[],
                )
            )

        assert any(isinstance(event, ChatCompleteEvent) for event in events)
        assert "Multi-turn complete() failed at turn 0" in caplog.text
        assert "file_read" in caplog.text

    @pytest.mark.asyncio
    async def test_allow_mutation_tool_false_skips_codegen_fast_path(self):
        cm = _build_chat_manager([_text_result("Tool-only direct task response")], cap_tools={})
        mock_provider = cm._providers.resolve.return_value
        cm._generate_workflow_from_intent = AsyncMock(  # type: ignore[attr-defined]
            return_value=(None, [])
        )

        await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="write a comprehensive report",
                history=[],
                mode="agent",
                allow_mutation_tool=False,
            )
        )

        cm._generate_workflow_from_intent.assert_not_called()  # type: ignore[attr-defined]
        assert mock_provider.complete.await_count == 1

    @pytest.mark.asyncio
    async def test_allow_mutation_tool_false_skips_structural_macro_fast_path(self, monkeypatch):
        cm = _build_chat_manager([_text_result("Direct task reply")], cap_tools={})
        cm._graph_store.get_graph.return_value = _NON_EMPTY_GRAPH
        mock_provider = cm._providers.resolve.return_value

        structural_called = False

        def _fake_dispatch(*args: Any, **kwargs: Any):
            nonlocal structural_called
            structural_called = True
            raise AssertionError("structural macro fast path should be skipped")

        monkeypatch.setattr(
            "dan.meta.structural_mutations.dispatch_compound_mutations",
            _fake_dispatch,
        )

        await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="write a comprehensive report",
                history=[],
                mode="agent",
                allow_mutation_tool=False,
            )
        )

        assert structural_called is False
        assert mock_provider.complete.await_count >= 1

    @pytest.mark.asyncio
    async def test_structural_macro_fast_path_saves_graph_and_emits_terminal_events(self, monkeypatch):
        cm = _build_chat_manager([_text_result("unused")], cap_tools={})
        cm._graph_store.get_graph.return_value = copy.deepcopy(_NON_EMPTY_GRAPH)
        mock_provider = cm._providers.resolve.return_value

        saved_graph = copy.deepcopy(_NON_EMPTY_GRAPH)
        saved_graph["nodes"].append(
            {
                "id": "n2",
                "node_type": "input",
                "name": "Input",
                "input_ports": [],
                "output_ports": [],
                "position": {"x": 1, "y": 1},
                "ui": {},
                "metadata": {},
            }
        )
        cm._graph_store.save_graph.return_value = saved_graph

        def _fake_dispatch(graph: dict[str, Any], _message: str):
            graph["nodes"].append(
                {
                    "id": "n2",
                    "node_type": "input",
                    "name": "Input",
                    "input_ports": [],
                    "output_ports": [],
                    "position": {"x": 1, "y": 1},
                    "ui": {},
                    "metadata": {},
                }
            )
            return MagicMock(
                matched=True,
                results=[],
                result=MagicMock(success=True, edges_added=1, nodes_added=["n2"]),
                macro_name="add_input",
            )

        monkeypatch.setattr(
            "dan.meta.structural_mutations.dispatch_compound_mutations",
            _fake_dispatch,
        )
        monkeypatch.setattr(
            "dan.meta.workflow_contract.validate_workflow_build_contract",
            lambda graph_dict, workflow_id="", apply_repairs=True: MagicMock(
                validated=True,
                run_ready=True,
                graph_dict=graph_dict,
            ),
        )
        monkeypatch.setattr(
            "dan.meta.graph_quality.compute_quality_report",
            lambda graph_dict, message, tier=None: MagicMock(
                overall_score=90,
                concerns=[],
            ),
        )

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="add an input node",
                history=[],
                mode="agent",
                allow_mutation_tool=True,
            )
        )

        created_events = [event for event in events if isinstance(event, ChatGraphCreatedEvent)]
        assert len(created_events) == 1
        assert created_events[0].workflow_id == "test-wf"
        assert created_events[0].node_count == 2

        complete_events = [event for event in events if isinstance(event, ChatCompleteEvent)]
        assert len(complete_events) == 1
        assert "Applied `add_input`" in complete_events[0].content
        assert "Validated and run-ready." in complete_events[0].content

        cm._graph_store.save_graph.assert_called_once()
        assert mock_provider.complete.await_count == 0

    @pytest.mark.asyncio
    async def test_file_read_reuses_covered_range_and_dedupes_attachment(self, tmp_path):
        report_path = tmp_path / "report.tex"
        report_path.write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n")

        calls: list[dict[str, Any]] = []

        def _file_read(args: dict[str, Any]) -> _FakeCapResult:
            calls.append(args)
            return _FakeCapResult(
                success=True,
                message="file contents",
                data={
                    "path": str(report_path),
                    "requested_start_line": 1,
                    "requested_end_line": 20,
                    "returned_start_line": 1,
                    "returned_end_line": 20,
                    "truncated": False,
                },
            )

        cap_tools = {"file_read": _file_read}
        responses = [
            _tool_result(
                "",
                [_tool_call("file_read", {"path": str(report_path), "start_line": 1, "end_line": 20}, "tc_1")],
            ),
            _tool_result(
                "",
                [_tool_call("file_read", {"path": str(report_path), "start_line": 10, "end_line": 12}, "tc_2")],
            ),
            _text_result("done"),
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools=cap_tools,
            cacheable_tools={"file_read"},
        )

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read the report",
                history=[],
            )
        )

        attachment_events = [
            e for e in events if getattr(e, "type", "") == "chat_file_attachment"
        ]
        assert len(calls) == 1
        assert len(attachment_events) == 1

    @pytest.mark.asyncio
    async def test_truncated_file_read_does_not_fake_coverage_for_later_range(self, tmp_path):
        report_path = tmp_path / "report.tex"
        long_line = "x" * 500
        report_path.write_text("\n".join(long_line for _ in range(40)) + "\n")

        calls: list[dict[str, Any]] = []

        def _file_read(args: dict[str, Any]) -> _FakeCapResult:
            calls.append(args)
            return _FakeCapResult(
                success=True,
                message="file contents",
                data={
                    "path": str(report_path),
                    "requested_start_line": 1,
                    "requested_end_line": 40,
                    "returned_start_line": 1,
                    "returned_end_line": None,
                    "truncated": True,
                },
            )

        cap_tools = {"file_read": _file_read}
        responses = [
            _tool_result("", [_tool_call("file_read", {"path": str(report_path)}, "tc_1")]),
            _tool_result(
                "",
                [_tool_call("file_read", {"path": str(report_path), "start_line": 10, "end_line": 12}, "tc_2")],
            ),
            _text_result("done"),
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools=cap_tools,
            cacheable_tools={"file_read"},
        )

        await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read the report",
                history=[],
            )
        )

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_side_effecting_tools_are_not_cached(self):
        calls: list[dict[str, Any]] = []

        def _file_write(args: dict[str, Any]) -> _FakeCapResult:
            calls.append(args)
            return _FakeCapResult(success=True, message="wrote")

        cap_tools = {"file_write": _file_write}
        responses = [
            _tool_result(
                "",
                [_tool_call("file_write", {"path": "draft.tex", "mode": "append"}, "tc_1")],
            ),
            _tool_result(
                "",
                [_tool_call("file_write", {"path": "draft.tex", "mode": "append"}, "tc_2")],
            ),
            _text_result("done"),
        ]
        cm = _build_chat_manager(responses, cap_tools=cap_tools)

        await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="append twice",
                history=[],
            )
        )

        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_write_invalidates_cached_read_results(self):
        read_calls: list[dict[str, Any]] = []
        write_calls: list[dict[str, Any]] = []

        def _file_read(args: dict[str, Any]) -> _FakeCapResult:
            read_calls.append(args)
            return _FakeCapResult(success=True, message="file contents")

        def _file_write(args: dict[str, Any]) -> _FakeCapResult:
            write_calls.append(args)
            return _FakeCapResult(success=True, message="wrote")

        cap_tools = {"file_read": _file_read, "file_write": _file_write}
        responses = [
            _tool_result("", [_tool_call("file_read", {"path": "draft.tex"}, "tc_1")]),
            _tool_result("", [_tool_call("file_write", {"path": "draft.tex", "mode": "append"}, "tc_2")]),
            _tool_result("", [_tool_call("file_read", {"path": "draft.tex"}, "tc_3")]),
            _text_result("done"),
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools=cap_tools,
            cacheable_tools={"file_read"},
        )

        await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read, write, then read again",
                history=[],
            )
        )

        assert len(write_calls) == 1
        assert len(read_calls) == 2

    @pytest.mark.asyncio
    async def test_json_fallback_respects_allow_mutation_tool_false(self):
        cm = _build_chat_manager([_text_result("unused")], cap_tools={})
        mock_provider = cm._providers.resolve.return_value
        mock_provider.complete = AsyncMock(side_effect=RuntimeError("boom"))
        mutation_json = json.dumps({
            "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "Writer"}],
            "description": "Add a writer",
            "reasoning": "Need a writer",
        })

        async def _stream(**kwargs: Any):
            yield StreamChunk(delta=mutation_json, accumulated=mutation_json, done=False, usage=None)
            yield StreamChunk(
                delta="",
                accumulated=mutation_json,
                done=True,
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

        mock_provider.stream = _stream

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="write a comprehensive report",
                history=[],
                mode="agent",
                allow_mutation_tool=False,
            )
        )

        assert not any(isinstance(e, ChatMutationEvent) for e in events)
        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert complete_events
        assert "operations" in complete_events[-1].content

    @pytest.mark.asyncio
    async def test_json_fallback_persists_latest_mutation_preview_for_thread(self):
        cm = _build_chat_manager([_text_result("unused")], cap_tools={})
        cm._chat_store = MagicMock()
        cm._chat_store.get_thread_meta.return_value = {}
        mock_provider = cm._providers.resolve.return_value
        mock_provider.complete = AsyncMock(side_effect=RuntimeError("boom"))
        mutation_json = json.dumps(
            {
                "operations": [
                    {
                        "op": "add_node",
                        "node_type": "llm_operator",
                        "name": "Writer",
                    }
                ],
                "description": "Add a writer",
                "reasoning": "Need a writer",
            }
        )

        async def _stream(**kwargs: Any):
            yield StreamChunk(
                delta=mutation_json,
                accumulated=mutation_json,
                done=False,
                usage=None,
            )
            yield StreamChunk(
                delta="",
                accumulated=mutation_json,
                done=True,
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

        mock_provider.stream = _stream

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                thread_id="thread-1",
                message="write a comprehensive report",
                history=[],
                mode="agent",
                allow_mutation_tool=True,
            )
        )

        assert any(isinstance(e, ChatMutationEvent) for e in events)
        cm._chat_store.get_thread_meta.assert_called_with("test-wf", "thread-1")
        cm._chat_store.set_thread_meta.assert_called_once()
        saved_meta = cm._chat_store.set_thread_meta.call_args.args[2]
        assert "latest_mutation_preview" in saved_meta
        assert saved_meta["latest_mutation_preview"]["mutation_plan"]["description"] == "Add a writer"

    @pytest.mark.asyncio
    async def test_text_fallback_disables_tool_access_in_messages(self):
        cm = _build_chat_manager([_text_result("unused")], cap_tools={})
        mock_provider = cm._providers.resolve.return_value
        mock_provider.complete = AsyncMock(side_effect=RuntimeError("boom"))
        captured_messages: list[dict[str, Any]] = []

        async def _stream(**kwargs: Any):
            captured_messages.extend(kwargs.get("messages", []))
            text = "Plain-text fallback answer."
            yield StreamChunk(delta=text, accumulated=text, done=False, usage=None)
            yield StreamChunk(
                delta="",
                accumulated=text,
                done=True,
                usage={"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
            )

        mock_provider.stream = _stream

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="list files in this folder",
                history=[],
                mode="agent",
            )
        )

        system_messages = [
            str(message.get("content") or "")
            for message in captured_messages
            if message.get("role") == "system"
        ]
        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]

        assert any(
            "Tool calling is disabled for this response." in content
            for content in system_messages
        )
        assert complete_events[-1].content == "Plain-text fallback answer."

    @pytest.mark.asyncio
    async def test_two_tool_calls_then_text(self):
        """LLM calls tool twice across two turns, then produces text."""
        cap_tools = {
            "web_search": lambda args: _FakeCapResult(
                success=True, message="search result"
            ),
            "web_fetch": lambda args: _FakeCapResult(
                success=True, message="page content"
            ),
        }
        responses = [
            _tool_result("", [_tool_call("web_search", {"q": "test"}, "tc_1")]),
            _tool_result("", [_tool_call("web_fetch", {"url": "http://x"}, "tc_2")]),
            _text_result("Here is the answer based on web data."),
        ]
        cm = _build_chat_manager(responses, cap_tools=cap_tools)
        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="search and fetch",
                history=[],
            )
        )

        start_events = [e for e in events if isinstance(e, ChatToolCallStartEvent)]
        assert len(start_events) == 2
        assert start_events[0].tool_name == "web_search"
        assert start_events[1].tool_name == "web_fetch"

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(complete_events) == 1
        assert "web data" in complete_events[0].content

    @pytest.mark.asyncio
    async def test_turn_cap_enforced(self):
        """Loop stops at max_tool_turns and emits a ChatCompleteEvent."""
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=True, message="content"
            ),
        }
        infinite_tool_response = _tool_result(
            "", [_tool_call("file_read", {"path": "x"}, "tc_loop")]
        )
        responses = [infinite_tool_response] * 15

        cm = _build_chat_manager(responses, cap_tools=cap_tools)

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read forever",
                history=[],
                max_tool_turns=3,
            )
        )

        start_events = [e for e in events if isinstance(e, ChatToolCallStartEvent)]
        assert len(start_events) == 3

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(complete_events) == 1
        assert complete_events[0].content is not None

    @pytest.mark.asyncio
    async def test_cancel_event_interrupts_loop(self):
        """cancel_event stops the loop mid-turn."""
        cancel = asyncio.Event()

        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=True, message="content"
            ),
        }

        original_responses = [
            _tool_result("", [_tool_call("file_read", {"path": "x"}, "tc_c1")]),
            _tool_result("", [_tool_call("file_read", {"path": "y"}, "tc_c2")]),
            _text_result("done"),
        ]
        call_count = 0

        async def _cancel_after_first(**kwargs: Any) -> CompletionResult:
            nonlocal call_count
            idx = min(call_count, len(original_responses) - 1)
            resp = original_responses[idx]
            call_count += 1
            if call_count == 1:
                cancel.set()
            return resp

        cm = _build_chat_manager(
            original_responses,
            cap_tools={
                "file_read": lambda args: _FakeCapResult(success=True, message="ok"),
            },
        )
        mock_provider = cm._providers.resolve.return_value
        mock_provider.complete = AsyncMock(side_effect=_cancel_after_first)

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read stuff",
                history=[],
                cancel_event=cancel,
            )
        )

        # Cancel is set after first complete() returns tool calls.
        # The loop checks cancel at top of next iteration → ChatInterruptedEvent.
        interrupted = [e for e in events if isinstance(e, ChatInterruptedEvent)]
        assert len(interrupted) == 1

    @pytest.mark.asyncio
    async def test_cancel_event_interrupts_continuation_complete(self):
        """Cancellation should stop an in-flight continuation completion."""
        cancel = asyncio.Event()
        started = asyncio.Event()
        responses = [
            CompletionResult(
                text="Part 1",
                tool_calls=None,
                usage={"total_tokens": 10},
                finish_reason="length",
            ),
            _text_result("Part 2"),
        ]
        cm = _build_chat_manager(responses)
        mock_provider = cm._providers.resolve.return_value
        _install_blocking_complete(mock_provider, responses, block_on_call=2, started=started)

        collect_task = asyncio.create_task(
            _collect(
                cm.send_message_with_tools(
                    workflow_id="test-wf",
                    message="continue writing",
                    history=[],
                    cancel_event=cancel,
                    max_tool_turns=2,
                )
            )
        )

        await asyncio.wait_for(started.wait(), timeout=1.0)
        cancel.set()
        events = await asyncio.wait_for(collect_task, timeout=1.0)

        interrupted = [e for e in events if isinstance(e, ChatInterruptedEvent)]
        complete = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(interrupted) == 1
        assert not complete
        assert interrupted[0].content == "Part 1"

    @pytest.mark.asyncio
    async def test_cancel_event_interrupts_post_tool_followup_complete(self):
        """Cancellation should stop an in-flight post-tool follow-up completion."""
        cancel = asyncio.Event()
        started = asyncio.Event()
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(success=True, message="draft content"),
        }
        responses = [
            _tool_result("", [_tool_call("file_read", {"path": "draft.md"}, "tc_follow")]),
            _text_result("Final answer"),
        ]
        cm = _build_chat_manager(responses, cap_tools=cap_tools)
        mock_provider = cm._providers.resolve.return_value
        _install_blocking_complete(mock_provider, responses, block_on_call=2, started=started)

        collect_task = asyncio.create_task(
            _collect(
                cm.send_message_with_tools(
                    workflow_id="test-wf",
                    message="summarize the draft",
                    history=[],
                    cancel_event=cancel,
                    max_tool_turns=2,
                )
            )
        )

        await asyncio.wait_for(started.wait(), timeout=1.0)
        cancel.set()
        events = await asyncio.wait_for(collect_task, timeout=1.0)

        interrupted = [e for e in events if isinstance(e, ChatInterruptedEvent)]
        complete = [e for e in events if isinstance(e, ChatCompleteEvent)]
        tool_results = [e for e in events if isinstance(e, ChatToolCallResultEvent)]
        assert len(interrupted) == 1
        assert not complete
        assert len(tool_results) == 1
        assert tool_results[0].tool_name == "file_read"
        assert interrupted[0].content == ""

    @pytest.mark.asyncio
    async def test_cancel_event_interrupts_turn_cap_synthesis_complete(self):
        """Cancellation should stop an in-flight forced partial-synthesis completion."""
        cancel = asyncio.Event()
        started = asyncio.Event()
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(success=True, message="evidence chunk"),
        }
        responses = [
            _tool_result("", [_tool_call("file_read", {"path": "notes.md"}, "tc_cap_initial")]),
            _tool_result("", [_tool_call("file_read", {"path": "more.md"}, "tc_cap_followup")]),
            _text_result("Partial synthesis"),
        ]
        cm = _build_chat_manager(responses, cap_tools=cap_tools)
        mock_provider = cm._providers.resolve.return_value
        _install_blocking_complete(mock_provider, responses, block_on_call=3, started=started)

        collect_task = asyncio.create_task(
            _collect(
                cm.send_message_with_tools(
                    workflow_id="test-wf",
                    message="research and summarize",
                    history=[],
                    cancel_event=cancel,
                    max_tool_turns=1,
                )
            )
        )

        await asyncio.wait_for(started.wait(), timeout=1.0)
        cancel.set()
        events = await asyncio.wait_for(collect_task, timeout=1.0)

        interrupted = [e for e in events if isinstance(e, ChatInterruptedEvent)]
        complete = [e for e in events if isinstance(e, ChatCompleteEvent)]
        tool_results = [e for e in events if isinstance(e, ChatToolCallResultEvent)]
        assert len(interrupted) == 1
        assert not complete
        assert len(tool_results) == 1
        assert tool_results[0].tool_name == "file_read"
        assert interrupted[0].content == ""

    @pytest.mark.asyncio
    async def test_tool_error_reported(self):
        """Tool returning error shows error status in event."""
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=False, message="Permission denied"
            ),
        }
        responses = [
            _tool_result("", [_tool_call("file_read", {"path": "/secret"}, "tc_err")]),
            _text_result("Sorry, I couldn't read that file."),
        ]
        cm = _build_chat_manager(responses, cap_tools=cap_tools)
        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read secret",
                history=[],
            )
        )

        result_events = [e for e in events if isinstance(e, ChatToolCallResultEvent)]
        assert len(result_events) == 1
        assert result_events[0].status == "error"

    @pytest.mark.asyncio
    async def test_retryable_capability_failure_retries_once(self):
        """Retryable capability failures get one bounded retry inside the loop."""
        attempts = 0

        def _file_read(args: dict[str, Any]) -> _FakeCapResult:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return _FakeCapResult(
                    success=False,
                    message="Temporary timeout talking to filesystem helper",
                    retryable=True,
                    error_type="timeout",
                )
            return _FakeCapResult(success=True, message="Recovered file contents")

        responses = [
            _tool_result("", [_tool_call("file_read", {"path": "draft.md"}, "tc_retry")]),
            _text_result("Recovered file contents."),
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools={"file_read": _file_read},
        )

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read the draft",
                history=[],
            )
        )

        result_events = [e for e in events if isinstance(e, ChatToolCallResultEvent)]
        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]

        assert attempts == 2
        assert len(result_events) == 1
        assert result_events[0].status == "success"
        assert complete_events[-1].content == "Recovered file contents."

    @pytest.mark.asyncio
    async def test_non_retryable_capability_failure_is_not_retried(self):
        """Non-retryable capability failures should not get an internal retry."""
        attempts = 0

        def _file_read(args: dict[str, Any]) -> _FakeCapResult:
            nonlocal attempts
            attempts += 1
            return _FakeCapResult(
                success=False,
                message="Permission denied",
                retryable=False,
                error_type="permission_denied",
            )

        responses = [
            _tool_result("", [_tool_call("file_read", {"path": "/secret"}, "tc_no_retry")]),
            _text_result("Sorry, I couldn't read that file."),
        ]
        cm = _build_chat_manager(
            responses,
            cap_tools={"file_read": _file_read},
        )

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read the secret file",
                history=[],
            )
        )

        result_events = [e for e in events if isinstance(e, ChatToolCallResultEvent)]

        assert attempts == 1
        assert len(result_events) == 1
        assert result_events[0].status == "error"

    @pytest.mark.asyncio
    async def test_missing_target_read_failure_forces_file_write(self):
        """A missing target file on a write task should pivot to file_write immediately."""
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=False,
                message="File not found: draft.md",
                error_type="not_found",
            ),
            "file_write": lambda args: _FakeCapResult(success=True, message="Wrote draft.md"),
        }
        responses = [
            _tool_result("", [_tool_call("file_read", {"path": "draft.md"}, "tc_missing_read")]),
            _tool_result("", [_tool_call("file_write", {"path": "draft.md", "content": "draft"}, "tc_missing_write")]),
            _text_result("Saved draft.md"),
        ]
        cm = _build_chat_manager(responses, cap_tools=cap_tools)
        mock_provider = cm._providers.resolve.return_value
        mock_provider.supports_exact_tool_choice = False
        mock_provider.supports_required_tool_choice = False

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="create draft.md with the new draft",
                history=[],
                required_action_hints=["write_file"],
            )
        )

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        forced_call_kwargs = mock_provider.complete.await_args_list[1].kwargs
        user_messages = [
            str(msg.get("content") or "")
            for msg in forced_call_kwargs["messages"]
            if msg.get("role") == "user"
        ]

        assert complete_events[-1].content == "Saved draft.md"
        assert _requested_tool_names(forced_call_kwargs) == ["file_write"]
        assert forced_call_kwargs["tool_choice"] == "auto"
        assert any(
            "does not exist yet" in content.lower()
            for content in user_messages
        )
        assert any(
            "mode='overwrite'" in content
            for content in user_messages
        )

    @pytest.mark.asyncio
    async def test_tool_loop_replays_raw_assistant_messages_when_requested(self):
        cap_tools = {
            "list_directory": lambda args: _FakeCapResult(
                success=True, message="file1.txt\nfile2.txt"
            ),
        }
        raw_tool_call = _tool_call("list_directory", {"path": "/"}, "tc_raw")
        responses = [
            CompletionResult(
                text="",
                tool_calls=[raw_tool_call],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [raw_tool_call],
                    "reasoning_content": "preserve-this",
                },
                usage={"total_tokens": 15},
            ),
            _text_result("Found 2 files."),
        ]
        cm = _build_chat_manager(responses, cap_tools=cap_tools)
        mock_provider = cm._providers.resolve.return_value
        mock_provider.assistant_replay_mode = "raw"

        await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="list files",
                history=[],
            )
        )

        followup_kwargs = mock_provider.complete.await_args_list[1].kwargs
        assistant_messages = [
            message
            for message in followup_kwargs["messages"]
            if message.get("role") == "assistant"
        ]

        assert assistant_messages
        assert assistant_messages[-1]["reasoning_content"] == "preserve-this"
        assert assistant_messages[-1]["tool_calls"][0]["function"]["name"] == "list_directory"

    def test_missing_action_prompt_reads_before_writing_when_both_pending(self):
        """When read and write are both pending, the retry prompt should say read first."""
        prompt = chat_manager_module._tool_retry_prompt_for_missing_actions(
            ["read_file", "write_file"]
        )

        assert "inspect the referenced local file or folder and write" in prompt.lower()
        assert "do not research or plan further" not in prompt.lower()
        assert "mode='overwrite'" in prompt

    def test_missing_action_prompt_skips_start_run_when_unavailable(self):
        """Retry guidance should not instruct unavailable workflow-run tools."""
        prompt = chat_manager_module._tool_retry_prompt_for_missing_actions(
            ["workflow_run"],
            available_tool_names={"get_run_status", "list_active_runs"},
        )

        assert "`start_run`" not in prompt
        assert "Furnace endpoints" not in prompt
        assert "shell commands" in prompt

    def test_missing_action_prompt_mentions_start_run_when_available(self):
        """Retry guidance should keep explicit workflow-run guidance when the tool is exposed."""
        prompt = chat_manager_module._tool_retry_prompt_for_missing_actions(
            ["workflow_run"],
            available_tool_names={"start_run", "get_run_status"},
        )

        assert "`start_run`" in prompt
        assert "Do NOT use `http_request` or Furnace endpoints" in prompt

    def test_parallel_tool_family_normalizes_related_tools(self):
        """Related tools should map onto shared parallel families."""
        assert chat_manager_module._parallel_tool_family("pdf_read") == "read"
        assert chat_manager_module._parallel_tool_family("search_workflow_history") == "search"
        assert chat_manager_module._parallel_tool_family("file_grep") == "grep"

    def test_prompt_guidance_uses_overwrite_for_first_chunk(self):
        """Prompt guidance should use the real file_write modes or summary wording."""
        capability_reference = chat_manager_module.generate_capability_reference()

        assert "mode='overwrite'" in capability_reference
        assert "mode='write'" not in capability_reference
        assert "write incrementally to disk one section at a time" in (
            chat_manager_module._RESEARCH_REPORT_PROMPT_HINT
        )

    @pytest.mark.asyncio
    async def test_same_family_read_calls_run_in_parallel(self):
        """Multiple tool calls in the same read-family run concurrently."""
        started: list[str] = []
        max_active = 0
        active = 0
        barrier = asyncio.Event()

        async def _parallel_handler(name: str, args: dict) -> _FakeCapResult:
            nonlocal active, max_active
            started.append(name)
            active += 1
            max_active = max(max_active, active)
            if len(started) == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), timeout=0.2)
            await asyncio.sleep(0.01)
            active -= 1
            return _FakeCapResult(success=True, message=f"{name} result")

        cap_tools = {
            "file_read": lambda args: _parallel_handler("file_read", args),
            "pdf_read": lambda args: _parallel_handler("pdf_read", args),
        }
        responses = [
            _tool_result("", [
                _tool_call("file_read", {"path": "a.md"}, "tc_p1"),
                _tool_call("pdf_read", {"path": "b.pdf"}, "tc_p2"),
            ]),
            _text_result("Combined results."),
        ]

        cm = _build_chat_manager(responses, cap_tools=cap_tools)
        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf", message="search", history=[]
            )
        )

        assert max_active == 2, "Same-family read calls should run in parallel"
        assert len([e for e in events if isinstance(e, ChatToolCallResultEvent)]) == 2

    @pytest.mark.asyncio
    async def test_mixed_type_tool_calls_run_sequentially_by_group(self):
        """Tool calls of DIFFERENT types execute sequentially, one type-group at a time."""
        execution_order: list[str] = []

        async def _tracking_handler(name: str, args: dict) -> _FakeCapResult:
            execution_order.append(f"{name}:start")
            await asyncio.sleep(0.01)
            execution_order.append(f"{name}:end")
            return _FakeCapResult(success=True, message=f"{name} ok")

        cap_tools = {
            "web_search": lambda args: _tracking_handler("web_search", args),
            "file_read": lambda args: _tracking_handler("file_read", args),
        }
        responses = [
            _tool_result("", [
                _tool_call("web_search", {"q": "a"}, "tc_ws"),
                _tool_call("file_read", {"path": "x"}, "tc_fr"),
            ]),
            _text_result("Done."),
        ]

        cm = _build_chat_manager(responses, cap_tools=cap_tools)
        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf", message="search and read", history=[]
            )
        )

        assert len([e for e in events if isinstance(e, ChatToolCallResultEvent)]) == 2
        ws_end = execution_order.index("web_search:end")
        fr_start = execution_order.index("file_read:start")
        assert ws_end < fr_start, (
            f"file_read should start after web_search ends; order={execution_order}"
        )

    @pytest.mark.asyncio
    async def test_provider_failure_on_later_turn_yields_error(self):
        """If the LLM fails on turn > 0, we get ChatErrorEvent (not streaming fallback)."""
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=True, message="content"
            ),
        }

        call_count = 0

        async def _fail_on_second(**kwargs: Any) -> CompletionResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_result("", [_tool_call("file_read", {"path": "x"}, "tc_f1")])
            raise RuntimeError("API unavailable")

        cm = _build_chat_manager(
            [
                _tool_result("", [_tool_call("file_read", {"path": "x"}, "tc_f1")]),
                _text_result("unused"),
            ],
            cap_tools={
                "file_read": lambda args: _FakeCapResult(success=True, message="ok"),
            },
        )
        mock_provider = cm._providers.resolve.return_value
        mock_provider.complete = AsyncMock(side_effect=_fail_on_second)

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="read stuff",
                history=[],
            )
        )

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        assert len(complete_events) == 1
        assert complete_events[0].content  # should contain accumulated tool output

    @pytest.mark.asyncio
    async def test_unexpected_provider_cancellation_on_later_turn_yields_complete(
        self,
        caplog: pytest.LogCaptureFixture,
    ):
        """Provider-side CancelledError should degrade into a terminal response, not a silent stream end."""
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=True, message="content"
            ),
        }

        call_count = 0

        async def _cancel_on_second(**kwargs: Any) -> CompletionResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_result("", [_tool_call("file_read", {"path": "x"}, "tc_cancel")])
            if call_count == 2:
                raise asyncio.CancelledError()
            return _text_result("Recovered content from synthesis")

        cm = _build_chat_manager(
            [_tool_result("", [_tool_call("file_read", {"path": "x"}, "tc_cancel_seed")])],
            cap_tools=cap_tools,
        )
        mock_provider = cm._providers.resolve.return_value
        mock_provider.complete = AsyncMock(side_effect=_cancel_on_second)

        with caplog.at_level(logging.WARNING):
            events = await _collect(
                cm.send_message_with_tools(
                    workflow_id="test-wf",
                    message="read stuff",
                    history=[],
                )
            )

        complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)]
        interrupted_events = [e for e in events if isinstance(e, ChatInterruptedEvent)]

        assert not interrupted_events
        assert len(complete_events) == 1
        assert complete_events[0].content
        assert "Guarded completion cancelled unexpectedly" in caplog.text

    @pytest.mark.asyncio
    async def test_progress_ack_timeout_does_not_cancel_followup_completion(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Heartbeat wait timeouts should not cancel an in-flight follow-up completion."""
        cap_tools = {
            "file_read": lambda args: _FakeCapResult(
                success=True, message="draft content"
            ),
        }

        call_count = 0

        async def _slow_followup(**kwargs: Any) -> CompletionResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _tool_result("", [_tool_call("file_read", {"path": "draft.md"}, "tc_slow")])
            await asyncio.sleep(0.02)
            return _text_result("Final answer after heartbeat")

        cm = _build_chat_manager(
            [_tool_result("", [_tool_call("file_read", {"path": "draft.md"}, "tc_seed")])],
            cap_tools=cap_tools,
        )
        mock_provider = cm._providers.resolve.return_value
        mock_provider.complete = AsyncMock(side_effect=_slow_followup)

        original_wait = asyncio.wait

        async def _short_wait(fs, timeout=None, return_when=asyncio.FIRST_COMPLETED):
            effective_timeout = 0.001 if timeout == 8.0 else timeout
            return await original_wait(fs, timeout=effective_timeout, return_when=return_when)

        monkeypatch.setattr(chat_manager_module.asyncio, "wait", _short_wait)

        events = await _collect(
            cm.send_message_with_tools(
                workflow_id="test-wf",
                message="summarize the draft",
                history=[],
            )
        )

        progress_events = [
            e for e in events
            if isinstance(e, ChatCompleteEvent) and getattr(e, "detected_mode", None) == "progress_ack"
        ]
        complete_events = [
            e for e in events
            if isinstance(e, ChatCompleteEvent) and getattr(e, "detected_mode", None) != "progress_ack"
        ]

        assert progress_events
        assert complete_events[-1].content == "Final answer after heartbeat"


# ---------------------------------------------------------------------------
# Model behavior profile integration tests
# ---------------------------------------------------------------------------


class TestModelBehaviorIntegration:
    """Regressions for ModelBehaviorProfile-aware tool loop."""

    def test_tool_choice_degrades_to_auto_when_required_not_supported(self):
        from dan.server.chat.helpers import _tool_choice_for_action_hints

        choice = _tool_choice_for_action_hints(
            ["write_file"],
            set(),
            allow_exact_tool_choice=False,
            allow_required_tool_choice=False,
        )
        assert choice == "auto"

    def test_tool_choice_stays_required_when_supported(self):
        from dan.server.chat.helpers import _tool_choice_for_action_hints

        choice = _tool_choice_for_action_hints(
            ["write_file"],
            set(),
            allow_exact_tool_choice=False,
            allow_required_tool_choice=True,
        )
        assert choice == "required"

    def test_force_single_tool_degrades_to_auto_when_required_not_supported(self):
        from dan.server.chat.helpers import _force_single_tool_request

        tools = [
            {"type": "function", "function": {"name": "file_write", "parameters": {}}},
        ]
        _, choice = _force_single_tool_request(
            tools,
            "file_write",
            allow_exact_tool_choice=False,
            allow_required_tool_choice=False,
        )
        assert choice == "auto"

    def test_build_assistant_followup_with_raw_replay(self):
        raw = {
            "role": "assistant",
            "content": None,
            "reasoning_content": "thinking step",
            "tool_calls": [{"id": "tc_1", "function": {"name": "f"}}],
        }
        msg = chat_manager_module._build_assistant_followup_message(
            text="",
            tool_calls=[{"id": "tc_1", "function": {"name": "f"}}],
            raw_assistant_message=raw,
        )
        assert msg is not None
        assert msg["reasoning_content"] == "thinking step"
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert msg["tool_calls"]

    def test_build_assistant_followup_without_raw_replay(self):
        msg = chat_manager_module._build_assistant_followup_message(
            text="some text",
            tool_calls=[{"id": "tc_1", "function": {"name": "f"}}],
            raw_assistant_message=None,
        )
        assert msg is not None
        assert msg["content"] == "some text"
        assert "reasoning_content" not in msg

    def test_build_assistant_followup_skips_empty_no_tools(self):
        msg = chat_manager_module._build_assistant_followup_message(
            text="",
            tool_calls=[],
            raw_assistant_message=None,
        )
        assert msg is None

    def test_disable_tool_access_appends_to_system_message(self):
        messages = [
            {"role": "system", "content": "You are DAN."},
            {"role": "user", "content": "hello"},
        ]
        disabled = chat_manager_module._disable_tool_access_in_messages(messages)
        assert "Tool calling is disabled" in disabled[0]["content"]
        assert disabled[0]["content"].startswith("You are DAN.")
        assert disabled[1] == messages[1]

    def test_disable_tool_access_creates_system_when_missing(self):
        messages = [
            {"role": "user", "content": "hello"},
        ]
        disabled = chat_manager_module._disable_tool_access_in_messages(messages)
        assert disabled[0]["role"] == "system"
        assert "Tool calling is disabled" in disabled[0]["content"]

    @pytest.mark.asyncio
    async def test_kimi_like_provider_exact_fallback_when_required_not_supported(self):
        """When required is disabled but exact is available, single-tool hints use exact targeting."""
        from dan.providers import ModelBehaviorProfile, get_model_behavior

        cm = _build_chat_manager([_text_result("ok")], cap_tools={})
        mock_provider = cm._providers.resolve.return_value

        mock_provider.get_model_behavior = lambda model: ModelBehaviorProfile(
            supports_tool_calls=True,
            supports_exact_tool_choice=True,
            supports_required_tool_choice=False,
            assistant_replay_mode="raw",
        )

        profile = get_model_behavior(mock_provider, "kimi-k2.5")
        assert profile.supports_required_tool_choice is False

        from dan.server.chat.helpers import _tool_choice_for_action_hints
        choice = _tool_choice_for_action_hints(
            ["write_file"],
            set(),
            allow_exact_tool_choice=profile.supports_exact_tool_choice,
            allow_required_tool_choice=profile.supports_required_tool_choice,
        )
        assert choice == {"type": "function", "function": {"name": "file_write"}}

    def test_multi_tool_hint_falls_back_to_auto_when_required_not_supported(self):
        """When a hint maps to multiple tools and required is disabled, result is auto."""
        from dan.server.chat.helpers import _tool_choice_for_action_hints

        choice = _tool_choice_for_action_hints(
            ["read_file"],
            set(),
            allow_exact_tool_choice=True,
            allow_required_tool_choice=False,
        )
        assert choice == "auto"

    def test_build_assistant_followup_raw_with_empty_string_content(self):
        """raw_assistant_message with content='' and tool_calls is preserved."""
        raw = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc_1", "function": {"name": "f"}}],
        }
        msg = chat_manager_module._build_assistant_followup_message(
            text="",
            tool_calls=[{"id": "tc_1", "function": {"name": "f"}}],
            raw_assistant_message=raw,
        )
        assert msg is not None
        assert msg["content"] == ""
        assert msg["tool_calls"]

    def test_build_assistant_followup_empty_raw_falls_through(self):
        """Empty raw_assistant_message falls through to reconstruction."""
        msg = chat_manager_module._build_assistant_followup_message(
            text="fallback text",
            tool_calls=[],
            raw_assistant_message={},
        )
        assert msg is not None
        assert msg["content"] == "fallback text"
        assert "reasoning_content" not in msg
