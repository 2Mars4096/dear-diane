from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from dan.agent_runtime.mutation_fallback import stream_json_fallback_response as canonical_stream_json_fallback_response
from dan.server.chat.mutation_fallback import stream_json_fallback_response as legacy_stream_json_fallback_response


class _Chunk:
    def __init__(
        self,
        delta: str,
        accumulated: str,
        done: bool,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.delta = delta
        self.accumulated = accumulated
        self.done = done
        self.usage = usage or {}


class _Provider:
    async def stream(self, *args: Any, **kwargs: Any):
        payload = """```json
{"description":"make it so","reasoning":"plan","operations":[]}
```"""
        yield _Chunk(payload, payload, True, {"prompt_tokens": 3, "completion_tokens": 4})


class _DryRunResult(SimpleNamespace):
    def model_dump(self) -> dict[str, Any]:
        return {"success": self.success, "stale_plan": self.stale_plan, "errors": []}


class _GraphMutator:
    def dry_run(self, graph_dict: dict[str, Any], plan: Any, current_revision: str) -> _DryRunResult:
        return _DryRunResult(success=True, stale_plan=False, new_graph=None, errors=[])


@pytest.mark.asyncio
async def test_canonical_mutation_fallback_matches_compatibility_import() -> None:
    assert canonical_stream_json_fallback_response is legacy_stream_json_fallback_response


@pytest.mark.asyncio
async def test_canonical_mutation_fallback_emits_mutation_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_SHOW_COST", "0")
    monkeypatch.setattr("dan.agent_runtime.mutation_fallback.GraphMutator", _GraphMutator)

    events = []
    async for event in canonical_stream_json_fallback_response(
        provider=_Provider(),
        messages=[{"role": "user", "content": "build"}],
        message_id="m-1",
        revision="r-1",
        revision_mismatch=False,
        graph_dict={"nodes": [], "edges": []},
        workflow_id="wf-1",
        thread_id=None,
        user_message="build",
        chat_store=None,
        effective_model="test-model",
        record_conversation_summary=lambda **kwargs: None,
        persist_latest_mutation_preview=lambda *args, **kwargs: None,
        normalize_mutation_ops=lambda graph, ops, is_empty: (ops, []),
        assess_graph_quality=None,
    ):
        events.append(event.model_dump())

    assert events[-1]["type"] == "chat_mutation"
    assert events[-1]["content"] == "plan"
    assert events[-1]["token_usage"]["prompt_tokens"] == 3
    assert events[-1]["token_usage"]["completion_tokens"] == 4


class _InterruptedProvider:
    async def stream(self, *args: Any, **kwargs: Any):
        yield _Chunk("building", "building", False)
        payload = """```json
{"description":"make it so","reasoning":"plan","operations":[]}
```"""
        yield _Chunk(payload, payload, True, {"prompt_tokens": 9, "completion_tokens": 10})


@pytest.mark.asyncio
async def test_canonical_mutation_fallback_interrupts_before_mutation_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_SHOW_COST", "0")

    cancel_event = asyncio.Event()
    events = []
    async for event in canonical_stream_json_fallback_response(
        provider=_InterruptedProvider(),
        messages=[{"role": "user", "content": "build"}],
        message_id="m-1",
        revision="r-1",
        revision_mismatch=False,
        graph_dict={"nodes": [], "edges": []},
        workflow_id="wf-1",
        thread_id=None,
        user_message="build",
        chat_store=None,
        cancel_event=cancel_event,
        effective_model="test-model",
        record_conversation_summary=lambda **kwargs: None,
        persist_latest_mutation_preview=lambda *args, **kwargs: None,
        normalize_mutation_ops=lambda graph, ops, is_empty: (ops, []),
        assess_graph_quality=None,
    ):
        events.append(event.model_dump())
        if event.type == "chat_token":
            cancel_event.set()

    assert events[-1]["type"] == "chat_interrupted"
    assert events[-1]["content"] == "```json\n{\"description\":\"make it so\",\"reasoning\":\"plan\",\"operations\":[]}\n```"
    assert events[-1]["token_usage"]["prompt_tokens"] == 9
    assert events[-1]["token_usage"]["completion_tokens"] == 10
