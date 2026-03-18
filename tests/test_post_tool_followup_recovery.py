"""Focused regressions for post-tool follow-up recovery hardening."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from typing import Any

import pytest

import dan.server.chat_manager as chat_manager_module
from dan.providers import CompletionResult
from dan.providers.registry import ProviderRegistry
from dan.server.capability_registry import (
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
    build_tool_schema,
)
from dan.server.chat.helpers import (
    _build_tool_followup_error_intro,
    _build_tool_followup_recovery_prompt,
    _post_tool_followup_retry_delay_seconds,
)
from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatInterruptedEvent,
    ChatManager,
    ChatTokenEvent,
    ChatToolCallResultEvent,
    ChatToolCallStartEvent,
)


MINIMAL_GRAPH: dict[str, Any] = {
    "version": "dan_graph_v1",
    "metadata": {"name": "test"},
    "nodes": [
        {
            "id": "n1",
            "name": "Node 1",
            "node_type": "llm_operator",
            "model": "test-model",
            "prompt_template": "Hello",
        },
    ],
    "edges": [],
}


class _FollowupRateLimitError(RuntimeError):
    def __init__(
        self,
        message: str = "429 rate limit",
        *,
        retry_after: Any | None = None,
        headers: dict[str, str] | None = None,
        status_code: int = 429,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code
        self.response = SimpleNamespace(
            status_code=status_code,
            headers=headers or {},
        )


class _SequenceProvider:
    supports_tool_calls = True
    supports_required_tool_choice = True

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> CompletionResult:
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("No more provider responses configured")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


async def _collect_events(gen: Any) -> list[Any]:
    events: list[Any] = []
    async for event in gen:
        events.append(event)
    return events


def _make_manager(
    provider: _SequenceProvider,
    *,
    tool_call_log: list[dict[str, Any]],
    capability_side_effect: Any | None = None,
) -> ChatManager:
    registry = ProviderRegistry()
    registry.register("default", provider)

    async def _stub_capability(args: dict[str, Any], context: CapabilityContext) -> CapabilityResult:
        tool_call_log.append(
            {
                "args": dict(args),
                "workflow_id": context.workflow_id,
            }
        )
        if capability_side_effect is not None:
            capability_side_effect(args, context)
        return CapabilityResult(
            success=True,
            message="Stub capability tool result",
            data={"value": args.get("value")},
        )

    capability_registry = ChatCapabilityRegistry()
    capability_registry.register(
        "stub_capability",
        build_tool_schema(
            "stub_capability",
            "Stub capability tool used by tests.",
            {
                "type": "object",
                "properties": {
                    "value": {"type": "integer"},
                },
                "required": ["value"],
            },
        ),
        _stub_capability,
        modes=["agent"],
    )

    graph_store = SimpleNamespace(get_graph=lambda workflow_id: dict(MINIMAL_GRAPH))
    mgr = ChatManager(
        registry,
        graph_store=graph_store,
        capability_registry=capability_registry,
        capability_context=CapabilityContext(workflow_id="seed"),
    )
    mgr._chat_model = "default-model"
    return mgr


def _tool_call(value: int = 1) -> dict[str, Any]:
    return {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "stub_capability",
            "arguments": json.dumps({"value": value}),
        },
    }


async def _fake_build_messages(self, *args: Any, **kwargs: Any) -> list[dict[str, str]]:
    return [{"role": "user", "content": "hello"}]


async def _fake_build_messages_with_prompt_detail(
    self,
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, str]]:
    tools_available = kwargs.get("tools_available", True)
    system_content = (
        "You may call load_prompt_detail with detail_id=`prompt:research_specializer:full`."
        if tools_available
        else "Tools are unavailable."
    )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "hello"},
    ]


def test_post_tool_followup_retry_delay_parses_retry_after_http_date() -> None:
    now = datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc)
    retry_at = format_datetime(now + timedelta(seconds=6))
    exc = _FollowupRateLimitError(headers={"Retry-After": retry_at})

    delay = _post_tool_followup_retry_delay_seconds(exc, 2, now=now)

    assert delay == pytest.approx(6.0)


def test_post_tool_followup_retry_delay_uses_bounded_exponential_backoff() -> None:
    exc = RuntimeError("rate limit without retry-after metadata")

    assert _post_tool_followup_retry_delay_seconds(exc, 1) == pytest.approx(2.0)
    assert _post_tool_followup_retry_delay_seconds(exc, 2) == pytest.approx(4.0)
    assert _post_tool_followup_retry_delay_seconds(exc, 3) == pytest.approx(8.0)
    assert _post_tool_followup_retry_delay_seconds(exc, 4) == pytest.approx(8.0)


def test_tool_followup_wording_mentions_final_answer_from_completed_tools() -> None:
    exc = _FollowupRateLimitError()

    recovery_prompt = _build_tool_followup_recovery_prompt(exc)
    error_intro = _build_tool_followup_error_intro(exc)

    assert "final answer" in recovery_prompt
    assert "completed tool results" in recovery_prompt
    assert (
        error_intro
        == "The provider hit a rate or quota limit while generating the final answer from completed tool results."
    )


@pytest.mark.asyncio
async def test_send_message_with_tools_retries_transient_post_tool_followup_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Let me check that.",
                tool_calls=[_tool_call(7)],
                usage={"prompt_tokens": 10, "completion_tokens": 3},
            ),
            _FollowupRateLimitError("rate limit while generating final answer", retry_after=5),
            CompletionResult(
                text="Recovered final answer from the completed tool results.",
                tool_calls=[],
                usage={"prompt_tokens": 5, "completion_tokens": 7},
            ),
        ]
    )
    mgr = _make_manager(provider, tool_call_log=tool_call_log)

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    monkeypatch.setattr(
        chat_manager_module,
        "_post_tool_followup_retry_delay_seconds",
        lambda exc, retry_index: 0.0,
    )

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Use the tool, then answer.",
            history=[],
        )
    )

    start_events = [event for event in events if isinstance(event, ChatToolCallStartEvent)]
    result_events = [event for event in events if isinstance(event, ChatToolCallResultEvent)]
    complete_events = [event for event in events if isinstance(event, ChatCompleteEvent)]
    token_events = [event for event in events if isinstance(event, ChatTokenEvent)]

    assert len(start_events) == 1
    assert start_events[0].tool_name == "stub_capability"
    assert len(result_events) == 1
    assert result_events[0].status == "success"
    assert all(event.delta != "Let me check that." for event in token_events)
    assert tool_call_log == [{"args": {"value": 7}, "workflow_id": "wf1"}]
    assert len(provider.requests) == 3
    assert complete_events[-1].content == "Recovered final answer from the completed tool results."


@pytest.mark.asyncio
async def test_send_message_with_tools_surfaces_updated_followup_rate_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Let me check that.",
                tool_calls=[_tool_call(3)],
                usage={"prompt_tokens": 9, "completion_tokens": 4},
            ),
            _FollowupRateLimitError("rate limit on final answer"),
            _FollowupRateLimitError("rate limit on retry 1"),
            _FollowupRateLimitError("rate limit on retry 2"),
            _FollowupRateLimitError("rate limit during synthesis"),
        ]
    )
    mgr = _make_manager(provider, tool_call_log=tool_call_log)

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    monkeypatch.setattr(
        chat_manager_module,
        "_post_tool_followup_retry_delay_seconds",
        lambda exc, retry_index: 0.0,
    )

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Use the tool, then answer.",
            history=[],
        )
    )

    complete_events = [event for event in events if isinstance(event, ChatCompleteEvent)]
    assert tool_call_log == [{"args": {"value": 3}, "workflow_id": "wf1"}]
    assert len(provider.requests) == 5
    assert complete_events
    assert complete_events[-1].content.startswith(
        "The provider hit a rate or quota limit while generating the final answer from completed tool results."
    )
    assert "stub capability" in complete_events[-1].content


@pytest.mark.asyncio
async def test_send_message_with_tools_stop_after_tool_results_keeps_interrupted_content_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    cancel_event = asyncio.Event()
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="",
                tool_calls=[_tool_call(11)],
                usage={"prompt_tokens": 8, "completion_tokens": 2},
            ),
        ]
    )
    mgr = _make_manager(
        provider,
        tool_call_log=tool_call_log,
        capability_side_effect=lambda *_args: cancel_event.set(),
    )

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Use the tool, then stop.",
            history=[],
            cancel_event=cancel_event,
        )
    )

    interrupted_events = [event for event in events if isinstance(event, ChatInterruptedEvent)]
    assert tool_call_log == [{"args": {"value": 11}, "workflow_id": "wf1"}]
    assert interrupted_events
    assert interrupted_events[-1].content == ""


@pytest.mark.asyncio
async def test_send_message_with_tools_retries_with_auto_tool_choice_when_provider_rejects_exact_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    provider = _SequenceProvider(
        [
            RuntimeError("tool_choice 'specified' is incompatible with thinking enabled"),
            CompletionResult(
                text=json.dumps(
                    {
                        "description": "Add a new input node",
                        "operations": [
                            {"op": "add_node", "node_type": "input", "name": "Input"},
                        ],
                    }
                ),
                usage={"prompt_tokens": 5, "completion_tokens": 2},
            ),
        ]
    )
    mgr = _make_manager(provider, tool_call_log=tool_call_log)

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Build the workflow.",
            history=[],
            allow_mutation_tool=True,
            required_action_hints=["workflow_edit"],
        )
    )

    mutation_events = [event for event in events if getattr(event, "type", "") == "chat_mutation"]
    assert mutation_events
    assert provider.requests[0]["tool_choice"] != "auto"
    assert provider.requests[1]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_send_message_with_tools_adds_followup_action_prompt_when_tool_choice_is_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Let me inspect first.",
                tool_calls=[_tool_call(9)],
                usage={"prompt_tokens": 7, "completion_tokens": 2},
            ),
            CompletionResult(
                text=json.dumps(
                    {
                        "description": "Add a new input node",
                        "operations": [
                            {"op": "add_node", "node_type": "input", "name": "Input"},
                        ],
                    }
                ),
                usage={"prompt_tokens": 6, "completion_tokens": 3},
            ),
        ]
    )
    provider.supports_exact_tool_choice = False
    provider.supports_required_tool_choice = False
    mgr = _make_manager(provider, tool_call_log=tool_call_log)

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Inspect first, then build the workflow.",
            history=[],
            allow_mutation_tool=True,
            required_action_hints=["workflow_edit"],
        )
    )

    mutation_events = [event for event in events if getattr(event, "type", "") == "chat_mutation"]
    followup_messages = provider.requests[1]["messages"]

    assert mutation_events
    assert tool_call_log == [{"args": {"value": 9}, "workflow_id": "wf1"}]
    assert provider.requests[1]["tool_choice"] == "auto"
    assert any(
        message.get("role") == "user"
        and "plan_graph_mutations" in str(message.get("content") or "")
        for message in followup_messages
        if isinstance(message, dict)
    )


@pytest.mark.asyncio
async def test_send_message_with_tools_normalizes_build_mode_for_capability_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Let me check that.",
                tool_calls=[_tool_call(5)],
                usage={"prompt_tokens": 6, "completion_tokens": 2},
            ),
            CompletionResult(
                text="The build tool step completed.",
                tool_calls=[],
                usage={"prompt_tokens": 4, "completion_tokens": 3},
            ),
        ]
    )
    mgr = _make_manager(provider, tool_call_log=tool_call_log)

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Build the workflow and inspect the tool result.",
            history=[],
            mode="build",
            allow_mutation_tool=False,
        )
    )

    complete_events = [event for event in events if isinstance(event, ChatCompleteEvent)]
    first_request_tool_names = [
        str(((tool.get("function") or {}).get("name")) or "")
        for tool in provider.requests[0]["tools"]
    ]

    assert "stub_capability" in first_request_tool_names
    assert tool_call_log == [{"args": {"value": 5}, "workflow_id": "wf1"}]
    assert complete_events[-1].content == "The build tool step completed."


@pytest.mark.asyncio
async def test_tool_fallback_rebuilds_messages_without_prompt_detail_tool_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    provider = _SequenceProvider([RuntimeError("tool-calling unavailable")])
    mgr = _make_manager(provider, tool_call_log=tool_call_log)

    captured_messages: list[dict[str, str]] = []

    async def _fake_stream_with_json_fallback(self, provider, messages, *args, **kwargs):
        captured_messages.extend(messages)
        yield ChatCompleteEvent(
            message_id="fallback",
            content="fallback response",
            graph_revision="",
        )

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages_with_prompt_detail)
    monkeypatch.setattr(ChatManager, "_stream_with_json_fallback", _fake_stream_with_json_fallback)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Use the tool, then answer.",
            history=[],
        )
    )

    assert any(isinstance(event, ChatCompleteEvent) for event in events)
    system_messages = [m for m in captured_messages if m.get("role") == "system"]
    assert system_messages
    assert "load_prompt_detail" not in system_messages[0]["content"]
