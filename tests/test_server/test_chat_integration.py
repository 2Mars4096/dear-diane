"""Integration tests for chat endpoints — message round-trip, thread
persistence, stale revision rejection, /run command dispatch, mode-specific
behaviour, streaming, mentions, stop generation, and export/search.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, AsyncIterator
from unittest.mock import patch, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DAN_GRAPHS_DIR", tempfile.mkdtemp())
os.environ.setdefault("DAN_CHECKPOINT_DIR", tempfile.mkdtemp())

from dan.providers import CompletionResult, StreamChunk  # noqa: E402
from dan.providers.registry import ProviderRegistry  # noqa: E402
from dan.server.chat_manager import (  # noqa: E402
    ChatCompleteEvent,
    ChatToolCallResultEvent,
    ChatToolCallStartEvent,
    compute_graph_revision,
)
from dan.server.app import app, lifespan  # noqa: E402


# ---------------------------------------------------------------------------
# Mock LLM providers
# ---------------------------------------------------------------------------


class _MockProvider:
    """Minimal LLM provider that returns a canned response."""

    async def complete(self, **kwargs: Any) -> CompletionResult:
        return CompletionResult(
            text="Mock assistant reply",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        text = "Mock streamed reply"
        yield StreamChunk(delta=text, accumulated=text, done=False, usage=None)
        yield StreamChunk(
            delta="",
            accumulated=text,
            done=True,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )


class _MockToolProvider:
    """Provider that returns a mutation tool call (for agent mode)."""

    async def complete(self, **kwargs: Any) -> CompletionResult:
        mutation = {
            "description": "Add a Summarizer node",
            "operations": [{"op": "add_node", "node_type": "llm_operator", "name": "Summarizer"}],
        }
        return CompletionResult(
            text="I'll add a Summarizer node.",
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
        text = "I'll add a Summarizer node."
        yield StreamChunk(delta=text, accumulated=text, done=False, usage=None)
        yield StreamChunk(
            delta="",
            accumulated=text,
            done=True,
            usage={"prompt_tokens": 20, "completion_tokens": 15},
        )


class _SlowMockProvider:
    """Provider with slow streaming to test cancellation."""

    async def complete(self, **kwargs: Any) -> CompletionResult:
        return CompletionResult(
            text="Slow reply",
            tool_calls=[],
            usage={"prompt_tokens": 10, "completion_tokens": 5},
        )

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        words = ["Slow", " streamed", " reply", " that", " takes", " a", " while"]
        accumulated = ""
        for i, w in enumerate(words):
            accumulated += w
            done = i == len(words) - 1
            yield StreamChunk(
                delta=w,
                accumulated=accumulated,
                done=done,
                usage={"prompt_tokens": 10, "completion_tokens": i + 1} if done else None,
            )
            await asyncio.sleep(0.1)


def _mock_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("default", _MockProvider())  # type: ignore[arg-type]
    return registry


def _mock_tool_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("default", _MockToolProvider())  # type: ignore[arg-type]
    return registry


def _mock_slow_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("default", _SlowMockProvider())  # type: ignore[arg-type]
    return registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "test-chat-wf"},
    "nodes": [
        {
            "id": "n1",
            "node_type": "llm_operator",
            "name": "Writer",
            "input_ports": [{"name": "input", "schema": {}}],
            "output_ports": [{"name": "text", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "model": "mock-model",
            "prompt_template": "Write: {input}",
        },
    ],
    "edges": [],
    "sub_graphs": {},
    "entry_points": ["n1"],
    "exit_points": ["n1"],
    "shared_context": [],
    "artifact_refs": [],
}

_LAYOUT_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "layout-revision"},
    "nodes": [
        {
            "id": "n1",
            "node_type": "llm_operator",
            "name": "Planner",
            "input_ports": [{"name": "input", "schema": {}}],
            "output_ports": [{"name": "text", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "model": "mock-model",
            "prompt_template": "Plan: {input}",
        },
        {
            "id": "n2",
            "node_type": "llm_operator",
            "name": "Writer",
            "input_ports": [{"name": "input", "schema": {}}],
            "output_ports": [{"name": "text", "schema": {}}],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "model": "mock-model",
            "prompt_template": "Write: {input}",
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source_node_id": "n1",
            "source_port": "text",
            "target_node_id": "n2",
            "target_port": "input",
            "ui": {},
            "metadata": {},
            "edge_type": "data",
        }
    ],
    "sub_graphs": {},
    "entry_points": ["n1"],
    "exit_points": ["n2"],
    "shared_context": [],
    "artifact_refs": [],
}


@asynccontextmanager
async def _test_client_with_registry(
    provider_registry: ProviderRegistry,
) -> AsyncGenerator[AsyncClient, None]:
    test_home = tempfile.mkdtemp(prefix="dan-chat-test-home-")
    dan_dir = os.path.join(test_home, ".dan")
    blocks_dir = Path(dan_dir) / "blocks"
    with (
        patch.dict(
            os.environ,
            {
                "HOME": test_home,
                "DAN_AUDIT_DIR": os.path.join(dan_dir, "audit"),
                "DAN_PROJECT_STORE_DIR": os.path.join(dan_dir, "projects"),
            },
            clear=False,
        ),
        patch("dan.blocks.registry._USER_BLOCKS_ROOT", blocks_dir),
        patch(
            "dan.server.startup._build_chat_provider_registry",
            return_value=provider_registry,
        ),
        patch("dan.server.startup._build_model_gateway", return_value=None),
    ):
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                yield c


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with _test_client_with_registry(_mock_provider_registry()) as c:
        yield c


async def _ensure_graph(client: AsyncClient, graph_id: str = "chat-test") -> str:
    """Create graph + upload data; return graph_id."""
    resp = await client.post("/api/graphs", json={"graph_id": graph_id})
    if resp.status_code == 409:
        pass
    await client.put(f"/api/graphs/{graph_id}", json=_MINIMAL_GRAPH)
    return graph_id


# ---------------------------------------------------------------------------
# 7-1  /api/chat/message round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_message_returns_stream_channel(client: AsyncClient):
    gid = await _ensure_graph(client, "chat-rt")
    resp = await client.post(
        "/api/chat/message",
        json={"workflow_id": gid, "message": "Hello", "history": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "message_id" in body
    assert "stream_channel_id" in body
    assert body["stream_channel_id"].startswith("chat-")


@pytest.mark.asyncio
async def test_chat_message_stream_delivers_events(client: AsyncClient):
    """Connect to the stream channel and verify we receive chat events."""
    gid = await _ensure_graph(client, "chat-stream")
    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={"workflow_id": gid, "message": "Hi there", "history": [], "mode": "ask"},
    )
    body = resp.json()
    channel_id = body["stream_channel_id"]

    events = await _collect_stream_events(channel_id)

    types = {e.get("type") for e in events}
    assert types & {"chat_token", "chat_complete", "chat_error"}, (
        f"Expected at least one chat event type, got {types}"
    )


@pytest.mark.asyncio
async def test_chat_message_synthesizes_terminal_event_when_stream_ends_silently(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    gid = await _ensure_graph(client, "chat-silent-end")

    class _SilentDispatcher:
        async def dispatch(self, _msg: Any):
            yield ChatToolCallStartEvent(
                tool_call_id="tc-1",
                tool_name="list_directory",
                args_preview='{"path": "/tmp"}',
            )
            yield ChatToolCallResultEvent(
                tool_call_id="tc-1",
                tool_name="list_directory",
                status="success",
                output_preview="Listed one directory",
                duration_ms=1,
            )

    monkeypatch.setattr(app.state.dan, "dispatcher", _SilentDispatcher(), raising=False)

    resp = await client.post(
        "/api/chat/message",
        json={"workflow_id": gid, "message": "Hello", "history": []},
    )
    assert resp.status_code == 200
    channel_id = resp.json()["stream_channel_id"]

    events = await _collect_stream_events(channel_id, timeout=0.2)

    assert any(evt.get("type") == "chat_tool_call_result" for evt in events)
    assert any(
        evt.get("type") == "chat_complete"
        and evt.get("content")
        == (
            "The response stream ended before a final answer was produced. "
            "Please ask me to continue from the latest progress."
        )
        for evt in events
    )


@pytest.mark.asyncio
async def test_chat_message_nonexistent_workflow(client: AsyncClient):
    resp = await client.post(
        "/api/chat/message",
        json={"workflow_id": "no-such-graph", "message": "Hi", "history": []},
    )
    assert resp.status_code in (200, 404, 503)


# ---------------------------------------------------------------------------
# 7-2  Thread persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_create_list_get(client: AsyncClient):
    gid = await _ensure_graph(client, "chat-thread")

    create_resp = await client.post(
        f"/api/chats/{gid}", json={"title": "Test Thread"},
    )
    assert create_resp.status_code == 200
    thread = create_resp.json()
    tid = thread["id"]
    assert thread["title"] == "Test Thread"

    list_resp = await client.get(f"/api/chats/{gid}")
    assert list_resp.status_code == 200
    ids = [t["id"] for t in list_resp.json()["threads"]]
    assert tid in ids

    get_resp = await client.get(f"/api/chats/{gid}/{tid}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == tid


@pytest.mark.asyncio
async def test_thread_list_all_workflows(client: AsyncClient):
    gid_a = await _ensure_graph(client, "chat-thread-all-a")
    gid_b = await _ensure_graph(client, "chat-thread-all-b")

    first = await client.post(f"/api/chats/{gid_a}", json={"title": "A thread"})
    second = await client.post(f"/api/chats/{gid_b}", json={"title": "B thread"})

    assert first.status_code == 200
    assert second.status_code == 200

    list_resp = await client.get("/api/chats")
    assert list_resp.status_code == 200
    threads = list_resp.json()["threads"]

    assert any(
        thread["id"] == first.json()["id"] and thread["workflow_id"] == gid_a
        for thread in threads
    )
    assert any(
        thread["id"] == second.json()["id"] and thread["workflow_id"] == gid_b
        for thread in threads
    )


@pytest.mark.asyncio
async def test_thread_update_messages_persisted(client: AsyncClient):
    gid = await _ensure_graph(client, "chat-persist")
    create = await client.post(f"/api/chats/{gid}", json={"title": "Persist"})
    tid = create.json()["id"]

    msgs = [
        {
            "id": "m1",
            "role": "user",
            "content": "Hello",
            "timestamp": "2025-01-01T00:00:00Z",
            "token_usage": None,
            "mutation_plan": None,
            "dry_run_result": None,
            "mutation_id": None,
            "mutation_status": None,
            "run_ref": None,
            "mentions": [],
        },
        {
            "id": "m2",
            "role": "assistant",
            "content": "Hi!",
            "timestamp": "2025-01-01T00:00:01Z",
            "token_usage": {"prompt": 5, "completion": 3},
            "mutation_plan": None,
            "dry_run_result": None,
            "mutation_id": None,
            "mutation_status": None,
            "run_ref": None,
            "mentions": [],
        },
    ]
    update_resp = await client.put(
        f"/api/chats/{gid}/{tid}", json={"messages": msgs},
    )
    assert update_resp.status_code == 200

    get_resp = await client.get(f"/api/chats/{gid}/{tid}")
    stored = get_resp.json()["messages"]
    assert len(stored) == 2
    assert stored[0]["content"] == "Hello"
    assert stored[1]["content"] == "Hi!"


@pytest.mark.asyncio
async def test_thread_delete(client: AsyncClient):
    gid = await _ensure_graph(client, "chat-del")
    create = await client.post(f"/api/chats/{gid}", json={"title": "ToDelete"})
    tid = create.json()["id"]

    del_resp = await client.delete(f"/api/chats/{gid}/{tid}")
    assert del_resp.status_code == 200

    get_resp = await client.get(f"/api/chats/{gid}/{tid}")
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# 7-3  Stale revision rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_revision_produces_mismatch(client: AsyncClient):
    """Send a message with a deliberately wrong client_graph_revision.

    The stream should contain a chat_complete or chat_error event with
    revision_mismatch=True (the server still answers but flags the mismatch).
    """
    gid = await _ensure_graph(client, "chat-stale")
    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={
            "workflow_id": gid,
            "message": "Any question",
            "history": [],
            "client_graph_revision": "definitely_wrong_revision_00000",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    channel_id = body["stream_channel_id"]

    events = await _collect_stream_events(channel_id, timeout=0.5)

    mismatch_flagged = any(e.get("revision_mismatch") for e in events)
    has_error_with_revision = any(
        "revision" in (e.get("error") or "").lower() for e in events
    )
    assert mismatch_flagged or has_error_with_revision, (
        "Expected revision_mismatch flag or revision error in stream events"
    )


@pytest.mark.asyncio
async def test_layout_load_returns_server_revision_for_chat(client: AsyncClient):
    gid = "chat-layout-revision"
    await client.post("/api/graphs", json={"graph_id": gid})
    raw_graph = copy.deepcopy(_LAYOUT_GRAPH)
    put_resp = await client.put(f"/api/graphs/{gid}", json=raw_graph)
    assert put_resp.status_code == 200
    server_revision = put_resp.json()["graph_revision"]
    get_resp = await client.get(f"/api/graphs/{gid}", params={"layout": "true"})
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["graph_revision"] == server_revision
    assert body["data"]["nodes"][1]["position"] != raw_graph["nodes"][1]["position"]
    assert compute_graph_revision(body["data"]) != server_revision

    chat_resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={
            "workflow_id": gid,
            "message": "What does this workflow do?",
            "history": [],
            "client_graph_revision": body["graph_revision"],
        },
    )
    assert chat_resp.status_code == 200
    channel_id = chat_resp.json()["stream_channel_id"]

    events = await _collect_stream_events(channel_id, timeout=0.5)

    assert not any(e.get("revision_mismatch") for e in events)


# ---------------------------------------------------------------------------
# 7-4  /run command dispatch
# ---------------------------------------------------------------------------

_EMPTY_GRAPH = {
    "version": "dan_graph_v1",
    "metadata": {"name": "empty-run"},
    "nodes": [],
    "edges": [],
    "sub_graphs": {},
    "entry_points": [],
    "exit_points": [],
    "shared_context": [],
    "artifact_refs": [],
}


@pytest.mark.asyncio
async def test_run_command_dispatches(client: AsyncClient):
    """Full-graph /run on an empty graph returns a run-readiness error."""
    gid = "chat-run-empty"
    await client.post("/api/graphs", json={"graph_id": gid})
    await client.put(f"/api/graphs/{gid}", json=_EMPTY_GRAPH)
    resp = await client.post(
        "/api/chat/message",
        json={"workflow_id": gid, "message": "/run", "history": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "run_error"
    assert body["error"]["run_readiness_failure_mode"] == "not_run_ready"
    assert "stream_channel_id" not in body


@pytest.mark.asyncio
async def test_run_command_node_scope_missing_input(client: AsyncClient):
    """Node-scope /run on a node with required inputs returns run_error."""
    gid = await _ensure_graph(client, "chat-run-node")
    resp = await client.post(
        "/api/chat/message",
        json={
            "workflow_id": gid,
            "message": "/run-node @[Writer](node:n1)",
            "history": [],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "run_error"
    assert body["error"]["error_type"] == "input_required"


@pytest.mark.asyncio
async def test_run_command_nonexistent_graph(client: AsyncClient):
    resp = await client.post(
        "/api/chat/message",
        json={"workflow_id": "no-graph", "message": "/run", "history": []},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_run_command_returns_stream_channel(client: AsyncClient):
    """Successful /run includes a valid stream channel id."""
    gid = "chat-run-chan"
    gid = await _ensure_graph(client, gid)
    resp = await client.post(
        "/api/chat/message",
        json={"workflow_id": gid, "message": "/run", "history": []},
    )
    body = resp.json()
    assert body["type"] == "run_started"
    channel_id = body.get("stream_channel_id")
    assert channel_id is not None
    assert channel_id.startswith("run-")


# ---------------------------------------------------------------------------
# 12-6-1  Mode-specific tests
# ---------------------------------------------------------------------------


async def _collect_stream_events(
    channel_id: str, timeout: float = 1.0,
) -> list[dict[str, Any]]:
    """Wait for stream events and collect them from the internal queue."""
    # In-process ASGI transport can start the detached chat producer a few
    # seconds after the request returns, so keep a wider floor here than the
    # nominal per-call timeout.
    deadline = asyncio.get_running_loop().time() + max(timeout, 12.0)
    queue = None
    while True:
        chat_router = importlib.import_module("dan.server.routers.chat")
        chat_streams = getattr(chat_router, "_chat_streams")
        chat_produce_tasks = getattr(chat_router, "_chat_produce_tasks")
        entry = chat_streams.get(channel_id)
        if entry is None:
            if asyncio.get_running_loop().time() >= deadline:
                return []
            await asyncio.sleep(0.1)
            continue
        queue, _ = entry
        task = chat_produce_tasks.get(channel_id)
        producer_running = bool(task and not task.done())
        queue.prime_reconnect_snapshot(producer_running=producer_running)
        if not queue.empty():
            break
        if asyncio.get_running_loop().time() >= deadline:
            return []
        await asyncio.sleep(0.1)

    assert queue is not None
    events: list[dict[str, Any]] = []
    while not queue.empty():
        evt = await asyncio.wait_for(queue.get(), timeout=1.0)
        if evt is None:
            break
        events.append(evt)
    return events


@pytest.mark.asyncio
async def test_ask_mode_returns_text_only(client: AsyncClient):
    """Ask mode: response is text-only, no mutation plan."""
    gid = await _ensure_graph(client, "chat-ask-mode")
    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={"workflow_id": gid, "message": "What does this workflow do?",
               "history": [], "mode": "ask"},
    )
    assert resp.status_code == 200
    body = resp.json()
    events = await _collect_stream_events(body["stream_channel_id"])
    mutation_events = [e for e in events if e.get("type") == "chat_mutation"]
    assert len(mutation_events) == 0, "Ask mode should not produce mutation events"
    has_text = any(e.get("type") in ("chat_token", "chat_complete") for e in events)
    assert has_text, "Ask mode should produce text events"


@pytest.mark.asyncio
async def test_plan_mode_first_message_no_tool_calls(client: AsyncClient):
    """Plan mode: first message doesn't include tool calls."""
    gid = await _ensure_graph(client, "chat-plan-mode")
    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={"workflow_id": gid, "message": "Add a reviewer node",
               "history": [], "mode": "plan"},
    )
    assert resp.status_code == 200
    body = resp.json()
    events = await _collect_stream_events(body["stream_channel_id"])
    mutation_events = [e for e in events if e.get("type") == "chat_mutation"]
    assert len(mutation_events) == 0, "Plan mode first message should not trigger mutations"


@pytest_asyncio.fixture
async def tool_client() -> AsyncGenerator[AsyncClient, None]:
    """Client with a mock provider that returns tool calls."""
    async with _test_client_with_registry(_mock_tool_provider_registry()) as c:
        yield c


@pytest.mark.asyncio
async def test_agent_mode_produces_mutation(tool_client: AsyncClient):
    """Agent mode: tool calling returns a mutation plan."""
    gid = await _ensure_graph(tool_client, "chat-agent-mode")
    resp = await tool_client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={"workflow_id": gid, "message": "Add a Summarizer node",
               "history": [], "mode": "agent"},
    )
    assert resp.status_code == 200
    body = resp.json()
    events = await _collect_stream_events(body["stream_channel_id"])
    mutation_events = [e for e in events if e.get("type") == "chat_mutation"]
    assert len(mutation_events) >= 1, "Agent mode should produce a mutation event"
    plan = mutation_events[0].get("mutation_plan", {})
    ops = plan.get("operations", [])
    assert any(o.get("op") == "add_node" for o in ops)


@pytest.mark.asyncio
async def test_debug_mode_has_debug_context(client: AsyncClient):
    """Debug mode: the LLM request includes debug context."""
    gid = await _ensure_graph(client, "chat-debug-mode")
    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={"workflow_id": gid, "message": "Why did the last run fail?",
               "history": [], "mode": "debug"},
    )
    assert resp.status_code == 200
    body = resp.json()
    events = await _collect_stream_events(body["stream_channel_id"])
    has_response = any(
        e.get("type") in ("chat_token", "chat_complete", "chat_error")
        for e in events
    )
    assert has_response, "Debug mode should produce a response"


# ---------------------------------------------------------------------------
# 12-6-1  Streaming event tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_events_include_chat_token_and_complete(client: AsyncClient):
    """Verify chat_token events arrive and chat_complete terminates the stream."""
    gid = await _ensure_graph(client, "chat-stream-detail")
    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={"workflow_id": gid, "message": "Hello", "history": [], "mode": "ask"},
    )
    body = resp.json()
    events = await _collect_stream_events(body["stream_channel_id"])
    types = [e.get("type") for e in events]
    assert "chat_token" in types, f"Should have chat_token events, got types: {types}"
    assert "chat_complete" in types, "Should have a chat_complete event"
    complete_events = [e for e in events if e.get("type") == "chat_complete"]
    assert complete_events[0].get("content"), "chat_complete should have content"
    assert "message_id" in complete_events[0]


@pytest.mark.asyncio
async def test_streaming_accumulated_text_grows(client: AsyncClient):
    """Verify accumulated text in chat_token events is monotonically growing."""
    gid = await _ensure_graph(client, "chat-stream-accum")
    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={"workflow_id": gid, "message": "Tell me something",
               "history": [], "mode": "ask"},
    )
    body = resp.json()
    events = await _collect_stream_events(body["stream_channel_id"])
    token_events = [e for e in events if e.get("type") == "chat_token"]
    if token_events:
        prev_len = 0
        for te in token_events:
            acc = te.get("accumulated", "")
            assert len(acc) >= prev_len, "Accumulated text should grow monotonically"
            prev_len = len(acc)


# ---------------------------------------------------------------------------
# 12-6-1  Mention resolution tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mention_resolution_invoked(client: AsyncClient):
    """Send a message with structured mentions and verify resolver is invoked."""
    gid = await _ensure_graph(client, "chat-mention-test")
    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={
            "workflow_id": gid,
            "message": "Tell me about @[Writer](node:n1)",
            "history": [],
            "mode": "ask",
            "mentions": [{"type": "node", "identifier": "n1"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    events = await _collect_stream_events(body["stream_channel_id"])
    has_response = any(
        e.get("type") in ("chat_token", "chat_complete") for e in events
    )
    assert has_response, "Message with mentions should produce a response"


# ---------------------------------------------------------------------------
# 12-6-1  Stop generation tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def slow_client() -> AsyncGenerator[AsyncClient, None]:
    """Client with a slow-streaming provider for stop/cancel testing."""
    async with _test_client_with_registry(_mock_slow_provider_registry()) as c:
        yield c


@pytest.mark.asyncio
async def test_stop_generation(slow_client: AsyncClient):
    """Start a stream, call the stop endpoint, verify chat_interrupted event."""
    gid = await _ensure_graph(slow_client, "chat-stop-test")
    resp = await slow_client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={"workflow_id": gid, "message": "Tell me a long story",
               "history": [], "mode": "ask"},
    )
    assert resp.status_code == 200
    body = resp.json()
    channel_id = body["stream_channel_id"]

    await asyncio.sleep(0.2)

    stop_resp = await slow_client.post(f"/api/chat/{channel_id}/stop")
    assert stop_resp.status_code == 200

    await asyncio.sleep(1.0)

    events = await _collect_stream_events(channel_id, timeout=0.1)
    types = {e.get("type") for e in events}
    assert "chat_interrupted" in types or "chat_complete" in types, (
        f"Expected chat_interrupted or chat_complete after stop, got {types}"
    )


@pytest.mark.asyncio
async def test_stop_nonexistent_stream(client: AsyncClient):
    """Stopping a non-existent stream returns 404."""
    resp = await client.post("/api/chat/fake-channel-id/stop")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_capability_run_stream_handoff_survives_fast_completion(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    """Fast capability run handoff keeps run stream channel available."""
    gid = await _ensure_graph(client, "chat-cap-run-fast")
    state = app.state.dan
    chat_manager = state.chat_manager
    run_manager = state.run_manager
    assert chat_manager is not None
    assert run_manager is not None

    async def _fake_send_with_tools(*args, **kwargs):
        _ = args, kwargs
        yield ChatCompleteEvent(
            message_id="msg-1",
            content="Started run run-fast",
            graph_revision="rev-fast",
            stream_channel_id="run-fast",
        )

    def _fake_subscribe(run_id: str):
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        q.put_nowait({"event_type": "run_completed", "run_id": run_id, "data": {}})
        return q

    monkeypatch.setattr(chat_manager, "send_message_with_tools", _fake_send_with_tools)
    monkeypatch.setattr(run_manager, "subscribe", _fake_subscribe)
    monkeypatch.setattr(run_manager, "unsubscribe", lambda run_id, q: None)

    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={
            "workflow_id": gid,
            "message": "start a run",
            "history": [],
            "mode": "agent",
        },
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.2)

    chat_router = importlib.import_module("dan.server.routers.chat")
    chat_streams = getattr(chat_router, "_chat_streams")
    assert "run-fast" in chat_streams, f"Expected run-fast in _chat_streams, got: {list(chat_streams.keys())}"
    events = await _collect_stream_events("run-fast", timeout=0.05)
    run_events = [e for e in events if e.get("type") == "chat_run_event"]
    assert run_events, "Expected run handoff stream to contain run events"


@pytest.mark.asyncio
async def test_run_handoff_stream_stays_open_through_automatic_recovery(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
):
    gid = await _ensure_graph(client, "chat-cap-run-auto-recovery")
    state = app.state.dan
    chat_manager = state.chat_manager
    run_manager = state.run_manager
    assert chat_manager is not None
    assert run_manager is not None

    async def _fake_send_with_tools(*args, **kwargs):
        _ = args, kwargs
        yield ChatCompleteEvent(
            message_id="msg-1",
            content="Started run run-auto",
            graph_revision="rev-auto",
            stream_channel_id="run-auto",
        )

    def _fake_subscribe(run_id: str):
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        q.put_nowait({"event_type": "run_failed", "run_id": run_id, "data": {"error": "boom"}})
        q.put_nowait({"event_type": "automatic_recovery_started", "run_id": run_id, "data": {"selected_action": "rerun_from_checkpoint"}})
        q.put_nowait({"event_type": "automatic_recovery_completed", "run_id": run_id, "data": {"status": "completed"}})
        return q

    monkeypatch.setattr(chat_manager, "send_message_with_tools", _fake_send_with_tools)
    monkeypatch.setattr(run_manager, "subscribe", _fake_subscribe)
    monkeypatch.setattr(run_manager, "unsubscribe", lambda run_id, q: None)

    resp = await client.post(
        "/api/chat/message",
        params={"concierge": "false"},
        json={
            "workflow_id": gid,
            "message": "start a run",
            "history": [],
            "mode": "agent",
        },
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.2)

    events = await _collect_stream_events("run-auto", timeout=0.05)
    run_events = [e["run_event"] for e in events if e.get("type") == "chat_run_event"]
    assert [e["event_type"] for e in run_events] == [
        "run_failed",
        "automatic_recovery_started",
        "automatic_recovery_completed",
    ]


# ---------------------------------------------------------------------------
# 12-6-1  Export and search tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_thread_markdown(client: AsyncClient):
    """Create a thread with messages, export as markdown, verify structure."""
    gid = await _ensure_graph(client, "chat-export-md")
    create = await client.post(f"/api/chats/{gid}", json={"title": "Export Test"})
    tid = create.json()["id"]

    msgs = [
        {
            "id": "ex1", "role": "user", "content": "What is DAN?",
            "timestamp": "2025-06-01T10:00:00Z", "token_usage": None,
            "mutation_plan": None, "dry_run_result": None,
            "mutation_id": None, "mutation_status": None,
            "run_ref": None, "mentions": [],
        },
        {
            "id": "ex2", "role": "assistant",
            "content": "DAN is a Deep Agent Network.",
            "timestamp": "2025-06-01T10:00:05Z",
            "token_usage": {"prompt": 10, "completion": 8},
            "mutation_plan": None, "dry_run_result": None,
            "mutation_id": None, "mutation_status": None,
            "run_ref": None, "mentions": [],
        },
    ]
    await client.put(f"/api/chats/{gid}/{tid}", json={"messages": msgs})

    export_resp = await client.get(f"/api/chats/{gid}/{tid}/export?format=md")
    assert export_resp.status_code == 200
    content = export_resp.json()["content"]
    assert "Export Test" in content or "What is DAN?" in content
    assert "What is DAN?" in content
    assert "DAN is a Deep Agent Network." in content
    assert export_resp.json()["format"] == "md"


@pytest.mark.asyncio
async def test_export_thread_json(client: AsyncClient):
    """Export as JSON returns valid thread data."""
    gid = await _ensure_graph(client, "chat-export-json")
    create = await client.post(f"/api/chats/{gid}", json={"title": "JSON Export"})
    tid = create.json()["id"]

    msgs = [{
        "id": "j1", "role": "user", "content": "Test message",
        "timestamp": "2025-06-01T10:00:00Z", "token_usage": None,
        "mutation_plan": None, "dry_run_result": None,
        "mutation_id": None, "mutation_status": None,
        "run_ref": None, "mentions": [],
    }]
    await client.put(f"/api/chats/{gid}/{tid}", json={"messages": msgs})

    export_resp = await client.get(f"/api/chats/{gid}/{tid}/export?format=json")
    assert export_resp.status_code == 200
    data = json.loads(export_resp.json()["content"])
    assert data["title"] in {"JSON Export", "Test message"}
    assert len(data["messages"]) == 1


@pytest.mark.asyncio
async def test_search_finds_messages(client: AsyncClient):
    """Create threads with messages, verify search finds them."""
    gid = await _ensure_graph(client, "chat-search-test")
    create = await client.post(f"/api/chats/{gid}", json={"title": "Search Thread"})
    tid = create.json()["id"]

    msgs = [{
        "id": "s1", "role": "user", "content": "The quick brown fox jumps over",
        "timestamp": "2025-06-01T10:00:00Z", "token_usage": None,
        "mutation_plan": None, "dry_run_result": None,
        "mutation_id": None, "mutation_status": None,
        "run_ref": None, "mentions": [],
    }]
    await client.put(f"/api/chats/{gid}/{tid}", json={"messages": msgs})

    search_resp = await client.get(
        "/api/chats/search", params={"q": "brown fox", "workflow_id": gid},
    )
    assert search_resp.status_code == 200
    results = search_resp.json()["results"]
    assert len(results) >= 1
    assert any("brown fox" in r.get("message_preview", "").lower() for r in results)


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(client: AsyncClient):
    """Empty search query returns no results."""
    resp = await client.get("/api/chats/search", params={"q": ""})
    assert resp.status_code == 200
    assert resp.json()["results"] == []
