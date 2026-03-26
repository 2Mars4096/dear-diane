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
    _preferred_workflow_edit_tool,
)
from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatInterruptedEvent,
    ChatManager,
    ChatMutationEvent,
    ChatValidationResultEvent,
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

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        if args:
            normalized = dict(kwargs)
            if len(args) >= 1:
                normalized.setdefault("messages", args[0])
            if len(args) >= 2:
                normalized.setdefault("model", args[1])
            kwargs = normalized
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
    capability_registry: ChatCapabilityRegistry | None = None,
) -> ChatManager:
    registry = ProviderRegistry()
    registry.register("default", provider)

    if capability_registry is None:
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


def _named_tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
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


async def _fake_build_messages_with_prompt_detail_disabled_via_metadata(
    self,
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, str]]:
    prompt_metadata_sink = kwargs.get("prompt_metadata_sink")
    if isinstance(prompt_metadata_sink, dict):
        prompt_metadata_sink["supports_load_prompt_detail"] = False
    return await _fake_build_messages_with_prompt_detail(self, *args, **kwargs)


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


def test_preferred_workflow_edit_tool_prefers_plan_for_fresh_fix_requests() -> None:
    assert _preferred_workflow_edit_tool(
        ["workflow_edit"],
        "Please fix the workflow wiring and repair the graph.",
        preview_available=False,
        allow_plan_graph_mutations=True,
        allow_apply_last_mutation=True,
    ) == "plan_graph_mutations"


def test_preferred_workflow_edit_tool_uses_apply_for_preview_confirmation() -> None:
    assert _preferred_workflow_edit_tool(
        ["workflow_edit"],
        "Looks good, apply it.",
        preview_available=True,
        allow_plan_graph_mutations=True,
        allow_apply_last_mutation=True,
    ) == "apply_last_mutation"


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
async def test_chat_manager_preserves_structured_search_replay_metadata_between_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="",
                tool_calls=[
                    _named_tool_call(
                        "web_search",
                        {"query": "latest dan", "fetch_content": True},
                        "call_web",
                    )
                ],
                raw_assistant_message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        _named_tool_call(
                            "web_search",
                            {"query": "latest dan", "fetch_content": True},
                            "call_web",
                        )
                    ],
                    "anthropic_content": [
                        {
                            "type": "tool_use",
                            "id": "call_web",
                            "name": "web_search",
                            "input": {"query": "latest dan", "fetch_content": True},
                        }
                    ],
                },
            ),
            CompletionResult(
                text="Final grounded answer.",
                tool_calls=[],
            ),
        ]
    )
    provider.assistant_replay_mode = "raw"

    async def _web_search_capability(
        args: dict[str, Any], context: CapabilityContext
    ) -> CapabilityResult:
        return CapabilityResult(
            success=True,
            message="Grounded result",
            data={
                "search_result_set": {
                    "query": "latest dan",
                    "results": [
                        {
                            "index": 1,
                            "title": "DAN update",
                            "url": "https://example.com/update",
                            "snippet": "Latest DAN update",
                            "provider": "serper",
                        }
                    ],
                    "grounded_result_count": 1,
                },
                "anthropic_tool_result_content": [
                    {
                        "type": "search_result",
                        "title": "DAN update",
                        "url": "https://example.com/update",
                        "source": {"type": "url", "url": "https://example.com/update"},
                        "content": [{"type": "text", "text": "Latest DAN update"}],
                        "citations": {"enabled": True},
                    }
                ],
            },
        )

    capability_registry = ChatCapabilityRegistry()
    capability_registry.register(
        "web_search",
        build_tool_schema(
            "web_search",
            "Structured search replay test tool.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "fetch_content": {"type": "boolean"},
                },
                "required": ["query"],
            },
        ),
        _web_search_capability,
        modes=["agent"],
    )

    mgr = _make_manager(
        provider,
        tool_call_log=[],
        capability_registry=capability_registry,
    )
    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="_scratch",
            message="Search the web",
            history=[],
            mode="agent",
            thread_id="thread-1",
        )
    )

    assert any(isinstance(event, ChatCompleteEvent) for event in events)
    assert len(provider.requests) >= 2
    second_messages = provider.requests[1]["messages"]
    assert any(
        message.get("role") == "assistant"
        and isinstance(message.get("anthropic_content"), list)
        for message in second_messages
    )
    assert any(
        message.get("role") == "tool"
        and isinstance(message.get("anthropic_tool_result_content"), list)
        and message["anthropic_tool_result_content"][0]["type"] == "search_result"
        for message in second_messages
    )


@pytest.mark.asyncio
async def test_chat_manager_emits_citation_warning_notice_for_unverified_numeric_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    monkeypatch.setenv("DAN_VERIFY_CITATIONS", "1")

    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Checking sources.",
                tool_calls=[
                    _named_tool_call(
                        "web_search",
                        {"query": "latest dan revenue", "fetch_content": True},
                        "call_web",
                    )
                ],
            ),
            CompletionResult(
                text="Revenue grew 25% in 2025 [1].",
                tool_calls=[],
            ),
        ]
    )

    async def _web_search_capability(
        args: dict[str, Any], context: CapabilityContext
    ) -> CapabilityResult:
        return CapabilityResult(
            success=True,
            message="Grounded result",
            data={
                "search_result_set": {
                    "query": "latest dan revenue",
                    "results": [
                        {
                            "index": 1,
                            "title": "Report",
                            "url": "https://example.com/report",
                            "snippet": "Revenue grew 24% in 2025.",
                            "fetched_content": "Revenue grew 24% in 2025.",
                            "provider": "serper",
                        }
                    ],
                    "grounded_result_count": 1,
                }
            },
        )

    capability_registry = ChatCapabilityRegistry()
    capability_registry.register(
        "web_search",
        build_tool_schema(
            "web_search",
            "Citation warning test tool.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "fetch_content": {"type": "boolean"},
                },
                "required": ["query"],
            },
        ),
        _web_search_capability,
        modes=["agent"],
    )

    mgr = _make_manager(
        provider,
        tool_call_log=[],
        capability_registry=capability_registry,
    )
    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="_scratch",
            message="Verify the latest DAN revenue growth",
            history=[],
            mode="agent",
            thread_id="thread-1",
        )
    )

    warning = next(event for event in events if getattr(event, "type", "") == "chat_notice")
    assert "could not be verified" in warning.content
    assert any(isinstance(event, ChatCompleteEvent) for event in events)


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
async def test_send_message_with_tools_resolves_fallback_model_via_public_provider_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    primary_provider = _SequenceProvider(
        [
            CompletionResult(
                text="Let me check that.",
                tool_calls=[_tool_call(7)],
                usage={"prompt_tokens": 9, "completion_tokens": 4},
            ),
            _FollowupRateLimitError("rate limit on final answer"),
            _FollowupRateLimitError("rate limit on retry 1"),
            _FollowupRateLimitError("rate limit on retry 2"),
            _FollowupRateLimitError("rate limit during synthesis"),
        ]
    )
    fallback_provider = _SequenceProvider(
        [
            CompletionResult(
                text="Recovered with fallback model.",
                tool_calls=[],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            )
        ]
    )
    mgr = _make_manager(primary_provider, tool_call_log=tool_call_log)
    mgr._providers.register("openai", fallback_provider)
    mgr._providers.set_model_override("alt-model", "default")
    mgr._providers.set_model_override("default-model", "openai")

    resolve_models: list[str | None] = []
    surface_calls: list[dict[str, Any]] = []
    original_resolve_provider = mgr._resolve_provider
    original_complete_chat_surface = chat_manager_module.complete_chat_surface

    def _recording_resolve_provider(
        *,
        pii_session_key: str | None = None,
        model: str | None = None,
    ) -> Any:
        resolve_models.append(model)
        return original_resolve_provider(
            pii_session_key=pii_session_key,
            model=model,
        )

    async def _recording_complete_chat_surface(*args: Any, **kwargs: Any) -> Any:
        surface_calls.append(dict(kwargs))
        return await original_complete_chat_surface(*args, **kwargs)

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    monkeypatch.setattr(
        chat_manager_module,
        "_post_tool_followup_retry_delay_seconds",
        lambda exc, retry_index: 0.0,
    )
    monkeypatch.setattr(mgr, "_resolve_provider", _recording_resolve_provider)
    monkeypatch.setattr(
        chat_manager_module,
        "complete_chat_surface",
        _recording_complete_chat_surface,
    )

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Use the tool, then answer.",
            history=[],
            model_override="alt-model",
        )
    )

    complete_events = [event for event in events if isinstance(event, ChatCompleteEvent)]
    assert tool_call_log == [{"args": {"value": 7}, "workflow_id": "wf1"}]
    assert complete_events
    assert complete_events[-1].content == "Recovered with fallback model."
    assert resolve_models == []
    assert getattr(mgr, "model_gateway", None) is not None
    assert [call["model"] for call in surface_calls] == ["alt-model", "default-model"]
    assert surface_calls[0]["temperature"] == 0.7
    assert int(surface_calls[0]["max_tokens"]) > 0
    assert surface_calls[0]["pii_session_key"] == "wf1"
    assert surface_calls[1]["pii_session_key"] == "wf1"
    assert any(
        "final answer" in str(message.get("content") or "")
        for message in surface_calls[0]["messages"]
        if isinstance(message, dict)
    )


@pytest.mark.asyncio
async def test_send_message_with_tools_uses_gateway_for_fallback_model_recovery_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    monkeypatch.setenv("DAN_SHOW_COST", "0")

    class _ResolvedProvider:
        supports_tool_calls = True

    class _Gateway:
        def __init__(self, responses: list[Any]) -> None:
            self._responses = list(responses)
            self.calls: list[dict[str, Any]] = []
            self.registry = SimpleNamespace(get=lambda name: None)

        def resolve(self, model: str) -> Any:
            return _ResolvedProvider()

        async def complete(self, **kwargs: Any) -> CompletionResult:
            self.calls.append(dict(kwargs))
            if not self._responses:
                raise AssertionError("No more gateway responses configured")
            response = self._responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    gateway = _Gateway(
        [
            CompletionResult(
                text="Let me check that.",
                tool_calls=[_tool_call(9)],
                usage={"prompt_tokens": 9, "completion_tokens": 4},
            ),
            _FollowupRateLimitError("rate limit on final answer"),
            _FollowupRateLimitError("rate limit on retry 1"),
            _FollowupRateLimitError("rate limit on retry 2"),
            _FollowupRateLimitError("rate limit during synthesis"),
            CompletionResult(
                text="Recovered with gateway fallback model.",
                tool_calls=[],
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            ),
        ]
    )
    mgr = _make_manager(_SequenceProvider([]), tool_call_log=tool_call_log)
    mgr.model_gateway = gateway
    mgr._chat_model = "default-model"

    resolve_models: list[str | None] = []
    original_resolve_provider = mgr._resolve_provider

    def _recording_resolve_provider(
        *,
        pii_session_key: str | None = None,
        model: str | None = None,
    ) -> Any:
        resolve_models.append(model)
        return original_resolve_provider(
            pii_session_key=pii_session_key,
            model=model,
        )

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    monkeypatch.setattr(
        chat_manager_module,
        "_post_tool_followup_retry_delay_seconds",
        lambda exc, retry_index: 0.0,
    )
    monkeypatch.setattr(mgr, "_resolve_provider", _recording_resolve_provider)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Use the tool, then answer.",
            history=[],
            model_override="alt-model",
        )
    )

    complete_events = [event for event in events if isinstance(event, ChatCompleteEvent)]
    assert tool_call_log == [{"args": {"value": 9}, "workflow_id": "wf1"}]
    assert complete_events
    assert complete_events[-1].content == "Recovered with gateway fallback model."
    assert resolve_models == []
    assert [call["model"] for call in gateway.calls] == [
        "alt-model",
        "alt-model",
        "alt-model",
        "alt-model",
        "alt-model",
        "default-model",
    ]


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
async def test_send_message_with_tools_forces_plan_graph_mutations_for_workflow_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _SequenceProvider(
        [
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
    mgr = _make_manager(provider, tool_call_log=[])
    provider.supports_exact_tool_choice = True

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Fix the workflow so it builds cleanly.",
            history=[],
            allow_mutation_tool=True,
            required_action_hints=["workflow_edit"],
        )
    )

    mutation_events = [event for event in events if getattr(event, "type", "") == "chat_mutation"]
    assert mutation_events

    first_request = provider.requests[0]
    assert first_request["tool_choice"] == {
        "type": "function",
        "function": {"name": "plan_graph_mutations"},
    }
    assert first_request["max_tokens"] == 4096
    assert [
        tool["function"]["name"]
        for tool in first_request["tools"]
    ] == ["plan_graph_mutations"]


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
async def test_send_message_with_tools_labels_mutation_preview_as_proposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _SequenceProvider(
        [
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
    mgr = _make_manager(provider, tool_call_log=[])

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Build the workflow.",
            history=[],
            allow_mutation_tool=True,
        )
    )

    progress_events = [
        event for event in events
        if isinstance(event, ChatCompleteEvent)
        and getattr(event, "detected_mode", None) == "progress_ack"
    ]
    mutation_event = next(
        event for event in events if getattr(event, "type", "") == "chat_mutation"
    )

    assert any(event.phase_label == "Preparing workflow change preview" for event in progress_events)
    assert "Prepared a workflow update preview." in mutation_event.content
    assert "These changes are proposed, not applied yet." in mutation_event.content
    assert "Planned changes: Add a new input node" in mutation_event.content


@pytest.mark.asyncio
async def test_send_message_with_tools_defers_delete_graph_until_after_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []

    async def _list_graphs(args: dict[str, Any], context: CapabilityContext) -> CapabilityResult:
        tool_call_log.append({
            "tool_name": "list_graphs",
            "args": dict(args),
            "workflow_id": context.workflow_id,
        })
        return CapabilityResult(
            success=True,
            message="Found 2 workflow(s):\n- **g1** (0 nodes, 0 edges)\n- **g2** (0 nodes, 0 edges)",
            data=[
                {"graph_id": "g1", "name": "workflow one"},
                {"graph_id": "g2", "name": "workflow two"},
            ],
            output_preview="Found 2 workflow(s): g1, g2",
        )

    async def _delete_graph(args: dict[str, Any], context: CapabilityContext) -> CapabilityResult:
        tool_call_log.append({
            "tool_name": "delete_graph",
            "args": dict(args),
            "workflow_id": context.workflow_id,
        })
        return CapabilityResult(
            success=True,
            message="Deleted workflow `g1`.",
            data={"graph_id": "g1", "deleted": True},
        )

    capability_registry = ChatCapabilityRegistry()
    capability_registry.register(
        "list_graphs",
        build_tool_schema("list_graphs", "List workflows.", {"type": "object", "properties": {}}),
        _list_graphs,
        modes=["agent"],
    )
    capability_registry.register(
        "delete_graph",
        build_tool_schema(
            "delete_graph",
            "Delete a workflow.",
            {
                "type": "object",
                "properties": {"graph_id": {"type": "string"}},
                "required": ["graph_id"],
            },
        ),
        _delete_graph,
        modes=["agent"],
    )

    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Let me check the current workflow inventory first.",
                tool_calls=[
                    _named_tool_call("list_graphs", {}, "call_list"),
                    _named_tool_call("delete_graph", {"graph_id": "equity_report_orchestrator"}, "call_delete"),
                ],
                usage={"prompt_tokens": 12, "completion_tokens": 5},
            ),
            CompletionResult(
                text="The stale workflow is already absent.",
                tool_calls=[],
                usage={"prompt_tokens": 8, "completion_tokens": 4},
            ),
        ]
    )
    mgr = _make_manager(
        provider,
        tool_call_log=[],
        capability_registry=capability_registry,
    )

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Delete the obsolete workflow after checking what exists.",
            history=[],
        )
    )

    result_events = [event for event in events if isinstance(event, ChatToolCallResultEvent)]

    assert [entry["tool_name"] for entry in tool_call_log] == ["list_graphs"]
    assert [event.tool_name for event in result_events] == ["list_graphs"]
    assert len(provider.requests) == 2
    followup_messages = provider.requests[1]["messages"]
    assert any(
        msg.get("role") == "user"
        and "latest workflow inventory" in str(msg.get("content") or "")
        for msg in followup_messages
    )


@pytest.mark.asyncio
async def test_send_message_with_tools_unknown_model_cost_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call_log: list[dict[str, Any]] = []
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="OK",
                tool_calls=[],
                usage={"prompt_tokens": 6, "completion_tokens": 2},
            ),
        ]
    )
    mgr = _make_manager(provider, tool_call_log=tool_call_log)
    mgr._chat_model = "unknown-model"

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    monkeypatch.setenv("DAN_SHOW_COST", "1")

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Reply with OK only.",
            history=[],
        )
    )

    complete_events = [event for event in events if isinstance(event, ChatCompleteEvent)]

    assert complete_events
    assert complete_events[-1].content == "OK"
    assert complete_events[-1].estimated_cost is None


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


@pytest.mark.asyncio
async def test_send_message_with_tools_prefers_prompt_metadata_for_prompt_detail_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _SequenceProvider([CompletionResult(text="OK", tool_calls=[])])

    async def _load_prompt_detail(
        args: dict[str, Any], context: CapabilityContext
    ) -> CapabilityResult:
        return CapabilityResult(success=True, message="Prompt detail", data={"detail_id": args.get("detail_id")})

    capability_registry = ChatCapabilityRegistry()
    capability_registry.register(
        "load_prompt_detail",
        build_tool_schema(
            "load_prompt_detail",
            "Load expanded prompt detail for the current turn.",
            {
                "type": "object",
                "properties": {
                    "detail_id": {"type": "string"},
                },
                "required": ["detail_id"],
            },
        ),
        _load_prompt_detail,
        modes=["agent"],
    )

    mgr = _make_manager(
        provider,
        tool_call_log=[],
        capability_registry=capability_registry,
    )

    monkeypatch.setattr(
        ChatManager,
        "_build_messages",
        _fake_build_messages_with_prompt_detail_disabled_via_metadata,
    )

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Use the tool, then answer.",
            history=[],
        )
    )

    assert any(isinstance(event, ChatCompleteEvent) for event in events)
    assert provider.requests
    tool_names = [
        str(((tool.get("function") or {}).get("name")) or "")
        for tool in (provider.requests[0].get("tools") or [])
        if isinstance(tool, dict)
    ]
    assert "load_prompt_detail" not in tool_names


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_message",
    [
        "Build the workflow and run it.",
        "I do not care what you call it, just make the workflow change and launch it.",
        "Please set this up and execute it right away, no need to wait for me.",
    ],
)
async def test_send_message_with_tools_auto_apply_builds_and_continues_to_run(
    monkeypatch: pytest.MonkeyPatch,
    user_message: str,
) -> None:
    """auto_apply=true on plan_graph_mutations applies the mutation server-side
    and continues the tool loop so the model can call start_run."""
    saved_graphs: list[tuple[str, dict[str, Any]]] = []

    graph_store = SimpleNamespace(
        get_graph=lambda workflow_id: dict(MINIMAL_GRAPH),
        save_graph=lambda wid, g: saved_graphs.append((wid, g)),
    )

    mutation_args = json.dumps({
        "description": "Add an input node",
        "auto_apply": True,
        "operations": [
            {"op": "add_node", "node_type": "input", "name": "My Input"},
        ],
    })
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Building and running the workflow.",
                tool_calls=[
                    {
                        "id": "call_mut",
                        "type": "function",
                        "function": {
                            "name": "plan_graph_mutations",
                            "arguments": mutation_args,
                        },
                    },
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
            CompletionResult(
                text="Workflow is running now.",
                tool_calls=[],
                usage={"prompt_tokens": 15, "completion_tokens": 3},
            ),
        ]
    )

    tool_call_log: list[dict[str, Any]] = []
    mgr = _make_manager(provider, tool_call_log=tool_call_log)
    mgr._graph_store = graph_store

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message=user_message,
            history=[],
            allow_mutation_tool=True,
        )
    )

    mutation_events = [e for e in events if isinstance(e, ChatMutationEvent)]
    assert len(mutation_events) == 1
    mut = mutation_events[0]
    assert mut.applied is True
    assert "applied" in mut.content.lower()
    assert "ready to run" in mut.content.lower()

    assert saved_graphs, "graph_store.save_graph should have been called"
    assert saved_graphs[0][0] == "wf1"

    assert len(provider.requests) == 2, "A follow-up LLM call should have been made"
    followup_req = provider.requests[1]
    followup_messages = followup_req["messages"]
    assert any(
        msg.get("role") == "tool"
        and "applied" in str(msg.get("content") or "")
        for msg in followup_messages
    ), "Follow-up messages should contain the apply tool result"

    followup_tool_choice = followup_req.get("tool_choice")
    assert followup_tool_choice == "auto", (
        f"Follow-up should use tool_choice='auto' (got {followup_tool_choice!r}) "
        "so the model can call start_run instead of being forced back to plan_graph_mutations"
    )

    complete_events = [e for e in events if isinstance(e, ChatCompleteEvent)
                       and getattr(e, "detected_mode", None) != "progress_ack"]
    assert complete_events, "Should end with a ChatCompleteEvent"
    assert "running" in complete_events[-1].content.lower()


@pytest.mark.asyncio
async def test_send_message_with_tools_auto_apply_false_still_returns_proposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without auto_apply, plan_graph_mutations stays proposed and the
    generator terminates without a follow-up LLM call."""
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Here is the workflow preview.",
                tool_calls=[
                    {
                        "id": "call_mut",
                        "type": "function",
                        "function": {
                            "name": "plan_graph_mutations",
                            "arguments": json.dumps({
                                "description": "Add an input node",
                                "operations": [
                                    {"op": "add_node", "node_type": "input", "name": "My Input"},
                                ],
                            }),
                        },
                    },
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
        ]
    )

    mgr = _make_manager(provider, tool_call_log=[])

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Build the workflow.",
            history=[],
            allow_mutation_tool=True,
        )
    )

    mutation_events = [e for e in events if isinstance(e, ChatMutationEvent)]
    assert len(mutation_events) == 1
    mut = mutation_events[0]
    assert mut.applied is False
    assert "proposed" in mut.content.lower()

    assert len(provider.requests) == 1, "No follow-up LLM call when auto_apply is false"


@pytest.mark.asyncio
async def test_send_message_with_tools_auto_apply_blocks_non_run_ready_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-apply must not save or continue when the applied graph is not run-ready."""
    saved_graphs: list[tuple[str, dict[str, Any]]] = []

    graph_store = SimpleNamespace(
        get_graph=lambda workflow_id: dict(MINIMAL_GRAPH),
        save_graph=lambda wid, g: saved_graphs.append((wid, g)),
    )

    mutation_args = json.dumps({
        "description": "Add an input node",
        "auto_apply": True,
        "operations": [
            {"op": "add_node", "node_type": "input", "name": "My Input"},
        ],
    })
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Building the workflow.",
                tool_calls=[
                    {
                        "id": "call_mut",
                        "type": "function",
                        "function": {
                            "name": "plan_graph_mutations",
                            "arguments": mutation_args,
                        },
                    },
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
        ]
    )

    mgr = _make_manager(provider, tool_call_log=[])
    mgr._graph_store = graph_store

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)
    monkeypatch.setattr(
        "dan.meta.workflow_contract.validate_workflow_build_contract",
        lambda graph_dict, workflow_id="", apply_repairs=True: SimpleNamespace(
            validated=True,
            run_ready=False,
            errors=[],
            run_readiness_issues=["Auto-apply blocked: workflow is not run-ready."],
            graph_dict=graph_dict,
        ),
    )

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Build the workflow and run it.",
            history=[],
            allow_mutation_tool=True,
        )
    )

    mutation_events = [e for e in events if isinstance(e, ChatMutationEvent)]
    assert len(mutation_events) == 1
    assert mutation_events[0].applied is False
    assert "proposed" in mutation_events[0].content.lower()

    validation_events = [e for e in events if isinstance(e, ChatValidationResultEvent)]
    assert validation_events
    assert any(
        "run-ready" in error.lower() or "no nodes" in error.lower()
        for error in validation_events[-1].errors
    )
    assert validation_events[-1].build_status == "validated"
    assert validation_events[-1].failure_bucket == "semantic_reprompt_or_diagnosis"
    assert validation_events[-1].handoff_reason == "run_readiness_gap"
    assert "not run-ready" in str(validation_events[-1].build_summary).lower()

    assert not saved_graphs, "graph_store.save_graph must not be called"
    assert len(provider.requests) == 1, "No follow-up LLM call when auto-apply is blocked"


@pytest.mark.asyncio
async def test_send_message_with_tools_auto_apply_text_based_mutation_uses_user_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When auto_apply comes from a text-based mutation (no native tool call),
    the follow-up uses a user message instead of a tool result message to
    avoid violating the provider API contract."""
    saved_graphs: list[tuple[str, dict[str, Any]]] = []

    graph_store = SimpleNamespace(
        get_graph=lambda workflow_id: dict(MINIMAL_GRAPH),
        save_graph=lambda wid, g: saved_graphs.append((wid, g)),
    )

    provider = _SequenceProvider(
        [
            CompletionResult(
                text=json.dumps({
                    "description": "Add an input node",
                    "auto_apply": True,
                    "operations": [
                        {"op": "add_node", "node_type": "input", "name": "My Input"},
                    ],
                }),
                tool_calls=[],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
            CompletionResult(
                text="Workflow is running now.",
                tool_calls=[],
                usage={"prompt_tokens": 15, "completion_tokens": 3},
            ),
        ]
    )

    mgr = _make_manager(provider, tool_call_log=[])
    mgr._graph_store = graph_store

    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Build the workflow and run it.",
            history=[],
            allow_mutation_tool=True,
        )
    )

    mutation_events = [e for e in events if isinstance(e, ChatMutationEvent)]
    assert len(mutation_events) == 1
    assert mutation_events[0].applied is True

    assert saved_graphs, "graph_store.save_graph should have been called"
    assert len(provider.requests) == 2

    followup_messages = provider.requests[1]["messages"]
    assert not any(
        msg.get("role") == "tool"
        for msg in followup_messages
    ), "Text-based mutations must NOT use tool result messages (no matching tool_call)"
    assert any(
        msg.get("role") == "user"
        and "applied automatically" in str(msg.get("content") or "")
        for msg in followup_messages
    ), "Text-based mutations should use a user message continuation"


@pytest.mark.asyncio
async def test_send_message_with_tools_repairs_invalid_mutation_plan_internally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _SequenceProvider(
        [
            CompletionResult(
                text="Here is the workflow preview.",
                tool_calls=[
                    {
                        "id": "call_bad_mut",
                        "type": "function",
                        "function": {
                            "name": "plan_graph_mutations",
                            "arguments": json.dumps({
                                "description": "Add an input node",
                                "operations": [
                                    {
                                        "node_type": "input",
                                        "name": "Workflow Input",
                                    }
                                ],
                            }),
                        },
                    },
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
            CompletionResult(
                text="Repairing the preview.",
                tool_calls=[
                    {
                        "id": "call_good_mut",
                        "type": "function",
                        "function": {
                            "name": "plan_graph_mutations",
                            "arguments": json.dumps({
                                "description": "Add an input node",
                                "operations": [
                                    {
                                        "op": "add_node",
                                        "node_type": "input",
                                        "name": "Workflow Input",
                                    }
                                ],
                            }),
                        },
                    },
                ],
                usage={"prompt_tokens": 12, "completion_tokens": 6},
            ),
        ]
    )

    mgr = _make_manager(provider, tool_call_log=[])
    monkeypatch.setattr(ChatManager, "_build_messages", _fake_build_messages)

    events = await _collect_events(
        mgr.send_message_with_tools(
            workflow_id="wf1",
            message="Build the workflow.",
            history=[],
            allow_mutation_tool=True,
        )
    )

    mutation_events = [e for e in events if isinstance(e, ChatMutationEvent)]
    assert len(mutation_events) == 1
    assert mutation_events[0].dry_run_result["success"] is True
    assert len(provider.requests) in {1, 2}

    if len(provider.requests) == 2:
        repair_messages = provider.requests[1]["messages"]
        assert any(
            msg.get("role") == "system"
            and "repair dan workflow mutation plans" in str(msg.get("content") or "").lower()
            for msg in repair_messages
        )
        system_messages = [
            str(msg.get("content") or "")
            for msg in repair_messages
            if msg.get("role") == "system"
        ]
        assert any("Workflow Generation Contract" in content for content in system_messages)
        assert any("replace_body_graph" in content for content in system_messages)
        assert any("items` and `results" in content for content in system_messages)
        assert any("item` and `index" in content for content in system_messages)
        assert any("Never invent pseudo-ops" in content for content in system_messages)
        assert any("Keep the requested outcome intact" in content for content in system_messages)
        assert any(
            msg.get("role") == "user"
            and "Compilation or validation failures" in str(msg.get("content") or "")
            and "Current mutation proposal" in str(msg.get("content") or "")
            for msg in repair_messages
        )
    else:
        repaired_ops = mutation_events[0].mutation_plan["operations"]
        assert repaired_ops[0]["op"] == "add_node"
