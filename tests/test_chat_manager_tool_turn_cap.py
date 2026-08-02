from __future__ import annotations

import json
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
from dan.server.chat_manager import ChatCompleteEvent, ChatManager


def _tool_call(tool_name: str, args: dict[str, Any], *, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(args),
        },
    }


class RecordingProvider:
    def __init__(self, responses: list[CompletionResult]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("Unexpected provider.complete() call")
        return self._responses.pop(0)

    async def stream(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider.stream() not expected in this test")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_turn_cap_forces_final_no_tools_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = RecordingProvider(
        [
            CompletionResult(
                text="Looking up sources.",
                tool_calls=[
                    _tool_call("web_search", {"query": "ev battery supply chain"}, call_id="call-1")
                ],
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
            CompletionResult(
                text="I should inspect one more source.",
                tool_calls=[
                    _tool_call("web_search", {"query": "battery mineral refining"}, call_id="call-2")
                ],
                usage={"prompt_tokens": 12, "completion_tokens": 6},
            ),
            CompletionResult(
                text="Partial answer based on the gathered sources.",
                usage={"prompt_tokens": 8, "completion_tokens": 9},
            ),
        ]
    )

    async def fake_execute(_args: dict[str, Any], _ctx: CapabilityContext) -> CapabilityResult:
        return CapabilityResult(
            success=True,
            message="Tool result: source data",
            output_preview="source data",
        )

    capability_registry = ChatCapabilityRegistry()
    capability_registry.register(
        "web_search",
        build_tool_schema(
            "web_search",
            "Search the web.",
            {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        fake_execute,
        modes=["agent"],
    )

    manager = ChatManager(
        ProviderRegistry(),
        graph_store=SimpleNamespace(
            get_graph=lambda _workflow_id: {"nodes": [], "edges": []}
        ),
        capability_registry=capability_registry,
        capability_context=CapabilityContext(workflow_id=""),
    )
    manager._chat_model = "default"

    async def fake_build_messages(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{"role": "user", "content": "Write the report."}]

    monkeypatch.setattr(manager, "_build_messages", fake_build_messages)
    monkeypatch.setattr(manager, "_resolve_provider", lambda **_kwargs: provider)
    monkeypatch.setattr(manager, "_record_conversation_summary", lambda **_kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_try_persist_audit", lambda **_kwargs: None)
    monkeypatch.setattr(chat_manager_module, "_DAN_USE_CODEGEN_BUILD", "0")

    events = [
        event
        async for event in manager.send_message_with_tools(
            workflow_id="wf-1",
            message="Write the report.",
            history=[],
            mode="agent",
            max_tool_turns=1,
        )
    ]

    complete = next(event for event in events if isinstance(event, ChatCompleteEvent))

    assert len(provider.calls) == 3
    assert "tools" not in provider.calls[-1]
    assert "Partial answer based on the gathered sources." in complete.content
    assert "tool-call limit (1)" in complete.content
