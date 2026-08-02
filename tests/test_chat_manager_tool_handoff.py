from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import dan.server.chat_manager as chat_manager_module
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.chat_manager import ChatCompleteEvent, ChatManager


MINIMAL_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "test"},
    "nodes": [
        {
            "id": "input-1",
            "node_type": "input",
            "name": "Input",
            "description": "",
            "input_ports": [],
            "output_ports": [],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "variables": [],
        }
    ],
    "edges": [],
}


def _tool_call(name: str = "mock_tool", args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"call-{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": "{}" if args is None else json.dumps(args),
        },
    }


class RecordingSequenceProvider:
    supports_exact_tool_choice = True

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("Unexpected provider.complete() call")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider.stream() not expected in these tests")


class FakeCapabilityRegistry:
    def __init__(self, result: CapabilityResult) -> None:
        self._result = result
        self._tool_name = "mock_tool"

    def get_tools(self, mode: str) -> list[dict[str, Any]]:
        return [{"name": self._tool_name}]

    def is_available(self, name: str, mode: str) -> bool:
        return name == self._tool_name

    def is_cacheable(self, name: str) -> bool:
        return False

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: CapabilityContext,
        *,
        mode: str,
    ) -> CapabilityResult:
        return self._result


class NamedCapabilityRegistry(FakeCapabilityRegistry):
    def __init__(self, result: CapabilityResult, tool_name: str) -> None:
        super().__init__(result)
        self._tool_name = tool_name


class SequencedCapabilityRegistry(FakeCapabilityRegistry):
    def __init__(self, results: list[CapabilityResult], tool_name: str) -> None:
        super().__init__(results[0] if results else CapabilityResult(success=False, message="missing"))
        self._results = list(results)
        self._tool_name = tool_name

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: CapabilityContext,
        *,
        mode: str,
    ) -> CapabilityResult:
        if not self._results:
            raise AssertionError("Unexpected capability execution")
        return self._results.pop(0)


class MultiToolCapabilityRegistry:
    def __init__(self, results_by_tool: dict[str, list[CapabilityResult]]) -> None:
        self._results_by_tool = {
            name: list(results) for name, results in results_by_tool.items()
        }

    def get_tools(self, mode: str) -> list[dict[str, Any]]:
        return [{"name": name} for name in self._results_by_tool]

    def is_available(self, name: str, mode: str) -> bool:
        return name in self._results_by_tool

    def is_cacheable(self, name: str) -> bool:
        return False

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        ctx: CapabilityContext,
        *,
        mode: str,
    ) -> CapabilityResult:
        queue = self._results_by_tool.get(tool_name)
        if not queue:
            raise AssertionError(f"Unexpected capability execution for {tool_name}")
        return queue.pop(0)


def _make_manager(
    responses: list[Any],
    *,
    capability_result: CapabilityResult,
) -> tuple[ChatManager, RecordingSequenceProvider]:
    registry = ProviderRegistry()
    provider = RecordingSequenceProvider(responses)
    registry.register("default", provider)
    manager = ChatManager(
        registry,
        graph_store=SimpleNamespace(get_graph=lambda workflow_id: MINIMAL_GRAPH),
        capability_registry=FakeCapabilityRegistry(capability_result),
        capability_context=CapabilityContext(workflow_id=""),
    )
    manager._chat_model = "test-model"
    return manager, provider


async def _stub_build_messages(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]


@pytest.mark.asyncio
async def test_turn_cap_synthesis_preserves_run_stream_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _make_manager(
        [
            CompletionResult(
                text="Need more data",
                tool_calls=[_tool_call()],
                usage={"prompt_tokens": 10, "completion_tokens": 3},
            ),
            CompletionResult(
                text="Still researching",
                tool_calls=[_tool_call()],
                usage={"prompt_tokens": 12, "completion_tokens": 4},
            ),
            CompletionResult(
                text="Partial answer from gathered evidence.",
                usage={"prompt_tokens": 5, "completion_tokens": 7},
            ),
        ],
        capability_result=CapabilityResult(
            success=True,
            message="Run started: run-123",
            stream_channel_id="run-123",
        ),
    )
    monkeypatch.setattr(manager, "_build_messages", _stub_build_messages)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **kwargs: None)

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-1",
            message="continue",
            history=[],
            mode="conversation",
            max_tool_turns=1,
        )
    ]

    final_event = next(event for event in reversed(events) if isinstance(event, ChatCompleteEvent))
    assert final_event.stream_channel_id == "run-123"
    assert "Partial answer from gathered evidence." in final_event.content
    assert "tool-call limit (1)" in final_event.content
    assert "tools" not in provider.calls[-1]


@pytest.mark.asyncio
async def test_normal_post_tool_completion_preserves_run_stream_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _provider = _make_manager(
        [
            CompletionResult(
                text="Starting run",
                tool_calls=[_tool_call()],
                usage={"prompt_tokens": 10, "completion_tokens": 3},
            ),
            CompletionResult(
                text="Run launched successfully.",
                usage={"prompt_tokens": 8, "completion_tokens": 6},
            ),
        ],
        capability_result=CapabilityResult(
            success=True,
            message="Run started: run-789",
            stream_channel_id="run-789",
        ),
    )
    monkeypatch.setattr(manager, "_build_messages", _stub_build_messages)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **kwargs: None)

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-2",
            message="run it",
            history=[],
            mode="conversation",
            max_tool_turns=3,
        )
    ]

    final_event = next(event for event in reversed(events) if isinstance(event, ChatCompleteEvent))
    assert final_event.content == "Run launched successfully."
    assert final_event.stream_channel_id == "run-789"


@pytest.mark.asyncio
async def test_start_run_forces_status_check_before_final_narrative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _make_manager(
        [
            CompletionResult(
                text="Starting the workflow.",
                tool_calls=[_tool_call("start_run")],
                usage={"prompt_tokens": 10, "completion_tokens": 3},
            ),
            CompletionResult(
                text="Checking run status.",
                tool_calls=[_tool_call("get_run_status", {"run_id": "run-321"})],
                usage={"prompt_tokens": 9, "completion_tokens": 4},
            ),
            CompletionResult(
                text="The run is active and currently waiting for review input.",
                usage={"prompt_tokens": 7, "completion_tokens": 8},
            ),
        ],
        capability_result=CapabilityResult(success=True, message="unused"),
    )
    manager._capability_registry = MultiToolCapabilityRegistry(
        {
            "start_run": [
                CapabilityResult(
                    success=True,
                    message=(
                        "Run started: run-321. "
                        "This only starts execution; call get_run_status to inspect the current outcome."
                    ),
                    data={"run_id": "run-321", "status": "pending"},
                    stream_channel_id="run-321",
                )
            ],
            "get_run_status": [
                CapabilityResult(
                    success=True,
                    message="**run-321** (wf-3) | Status: waiting | Phase: active",
                    data={"run_id": "run-321", "status": "waiting", "phase": "active"},
                )
            ],
        }
    )
    monkeypatch.setattr(manager, "_build_messages", _stub_build_messages)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **kwargs: None)

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-3",
            message="Can you test run it now?",
            history=[],
            mode="conversation",
            max_tool_turns=4,
            required_action_hints=["workflow_run"],
        )
    ]

    final_event = next(event for event in reversed(events) if isinstance(event, ChatCompleteEvent))
    assert final_event.content == "The run is active and currently waiting for review input."
    assert final_event.stream_channel_id == "run-321"
    assert provider.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "start_run"},
    }
    assert provider.calls[1]["tool_choice"] == {
        "type": "function",
        "function": {"name": "get_run_status"},
    }
    forced_tools = provider.calls[1]["tools"]
    assert forced_tools == [{"name": "get_run_status"}]
    followup_messages = provider.calls[1]["messages"]
    assert any(
        "has only been started so far" in str(msg.get("content") or "")
        and "run-321" in str(msg.get("content") or "")
        for msg in followup_messages
        if isinstance(msg, dict)
    )


@pytest.mark.asyncio
async def test_required_write_action_forces_required_tool_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _make_manager(
        [
            CompletionResult(
                text="Writing now",
                tool_calls=[_tool_call("file_write", {"path": "/tmp/out.tex", "content": "hello"})],
                usage={"prompt_tokens": 11, "completion_tokens": 4},
            ),
            CompletionResult(
                text="Saved to /tmp/out.tex",
                usage={"prompt_tokens": 8, "completion_tokens": 6},
            ),
        ],
        capability_result=CapabilityResult(success=True, message="saved"),
    )
    manager._capability_registry = NamedCapabilityRegistry(
        CapabilityResult(success=True, message="saved"),
        tool_name="file_write",
    )
    monkeypatch.setattr(manager, "_build_messages", _stub_build_messages)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **kwargs: None)

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-write-1",
            message="Write a file to /tmp/out.tex",
            history=[],
            mode="agent",
            required_action_hints=["write_file"],
        )
    ]

    final_event = next(event for event in reversed(events) if isinstance(event, ChatCompleteEvent))
    assert final_event.content == "Saved to /tmp/out.tex"
    assert provider.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "file_write"},
    }


@pytest.mark.asyncio
async def test_unfulfilled_write_action_retries_until_file_write_occurs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _make_manager(
        [
            CompletionResult(
                text="I'll",
                usage={"prompt_tokens": 9, "completion_tokens": 2},
            ),
            CompletionResult(
                text="Saving now",
                tool_calls=[_tool_call("file_write", {"path": "/tmp/out.tex", "content": "hello"})],
                usage={"prompt_tokens": 12, "completion_tokens": 5},
            ),
            CompletionResult(
                text="Saved to /tmp/out.tex",
                usage={"prompt_tokens": 8, "completion_tokens": 6},
            ),
        ],
        capability_result=CapabilityResult(success=True, message="saved"),
    )
    manager._capability_registry = NamedCapabilityRegistry(
        CapabilityResult(success=True, message="saved"),
        tool_name="file_write",
    )
    monkeypatch.setattr(manager, "_build_messages", _stub_build_messages)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **kwargs: None)

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-write-2",
            message="Write a file to /tmp/out.tex",
            history=[],
            mode="agent",
            required_action_hints=["write_file"],
        )
    ]

    final_event = next(event for event in reversed(events) if isinstance(event, ChatCompleteEvent))
    assert final_event.content == "Saved to /tmp/out.tex"
    assert len(provider.calls) == 3
    assert provider.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "file_write"},
    }
    assert provider.calls[1]["tool_choice"] == {
        "type": "function",
        "function": {"name": "file_write"},
    }


@pytest.mark.asyncio
async def test_failed_write_action_does_not_satisfy_required_action_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _make_manager(
        [
            CompletionResult(
                text="Saving now",
                tool_calls=[_tool_call("file_write", {"path": "/tmp/out.tex", "content": "draft"})],
                usage={"prompt_tokens": 10, "completion_tokens": 4},
            ),
            CompletionResult(
                text="Retrying save",
                tool_calls=[_tool_call("file_write", {"path": "/tmp/out.tex", "content": "final"})],
                usage={"prompt_tokens": 11, "completion_tokens": 5},
            ),
            CompletionResult(
                text="Saved to /tmp/out.tex",
                usage={"prompt_tokens": 7, "completion_tokens": 6},
            ),
        ],
        capability_result=CapabilityResult(success=False, message="write failed"),
    )
    manager._capability_registry = SequencedCapabilityRegistry(
        [
            CapabilityResult(success=False, message="write failed"),
            CapabilityResult(success=True, message="saved"),
        ],
        tool_name="file_write",
    )
    monkeypatch.setattr(manager, "_build_messages", _stub_build_messages)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **kwargs: None)

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-write-3",
            message="Write a file to /tmp/out.tex",
            history=[],
            mode="agent",
            required_action_hints=["write_file"],
        )
    ]

    final_event = next(event for event in reversed(events) if isinstance(event, ChatCompleteEvent))
    assert final_event.content == "Saved to /tmp/out.tex"
    assert len(provider.calls) == 3
    assert provider.calls[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "file_write"},
    }
    assert provider.calls[1]["tool_choice"] == {
        "type": "function",
        "function": {"name": "file_write"},
    }


@pytest.mark.asyncio
async def test_required_read_action_keeps_generic_required_tool_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _make_manager(
        [
            CompletionResult(
                text="Reading now",
                tool_calls=[_tool_call("file_read", {"path": "/tmp/in.tex", "start_line": 1, "end_line": 50})],
                usage={"prompt_tokens": 11, "completion_tokens": 4},
            ),
            CompletionResult(
                text="Read the requested section.",
                usage={"prompt_tokens": 8, "completion_tokens": 6},
            ),
        ],
        capability_result=CapabilityResult(success=True, message="read"),
    )
    manager._capability_registry = NamedCapabilityRegistry(
        CapabilityResult(success=True, message="read"),
        tool_name="file_read",
    )
    monkeypatch.setattr(manager, "_build_messages", _stub_build_messages)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **kwargs: None)

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-read-1",
            message="Read a file section",
            history=[],
            mode="agent",
            required_action_hints=["read_file"],
        )
    ]

    final_event = next(event for event in reversed(events) if isinstance(event, ChatCompleteEvent))
    assert final_event.content == "Read the requested section."
    assert provider.calls[0]["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_write_action_falls_back_to_required_when_exact_tool_choice_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, provider = _make_manager(
        [
            CompletionResult(
                text="Writing now",
                tool_calls=[_tool_call("file_write", {"path": "/tmp/out.tex", "content": "hello"})],
                usage={"prompt_tokens": 11, "completion_tokens": 4},
            ),
            CompletionResult(
                text="Saved to /tmp/out.tex",
                usage={"prompt_tokens": 8, "completion_tokens": 6},
            ),
        ],
        capability_result=CapabilityResult(success=True, message="saved"),
    )
    provider.supports_exact_tool_choice = False
    manager._capability_registry = NamedCapabilityRegistry(
        CapabilityResult(success=True, message="saved"),
        tool_name="file_write",
    )
    monkeypatch.setattr(manager, "_build_messages", _stub_build_messages)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **kwargs: None)

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-write-4",
            message="Write a file to /tmp/out.tex",
            history=[],
            mode="agent",
            required_action_hints=["write_file"],
        )
    ]

    final_event = next(event for event in reversed(events) if isinstance(event, ChatCompleteEvent))
    assert final_event.content == "Saved to /tmp/out.tex"
    assert provider.calls[0]["tool_choice"] == "required"
