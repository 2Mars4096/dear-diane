"""Focused regressions for post-tool follow-up recovery hardening."""

from __future__ import annotations

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
    ChatManager,
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

    assert len(start_events) == 1
    assert start_events[0].tool_name == "stub_capability"
    assert len(result_events) == 1
    assert result_events[0].status == "success"
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
