from __future__ import annotations

from types import SimpleNamespace

import pytest

from dan.cli.chat_local import LocalChatRuntime


@pytest.mark.asyncio
async def test_local_chat_runtime_v1_delegates_to_server_chat_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LocalChatRuntime()
    runtime._initialized = True
    runtime._services = SimpleNamespace()
    runtime._app_state = object()

    captured: dict[str, object] = {}

    async def _fake_chat_message(request, req, concierge=True):
        captured["request"] = request
        captured["req"] = req
        captured["concierge"] = concierge
        return {
            "stream_channel_id": "chat-v1-local",
            "control_plane_mode": "v1",
            "control_plane_mode_source": "env",
        }

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v1-local"
        yield {"type": "chat_complete", "content": "from legacy router"}

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v1")
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    response = await runtime.send_chat_message(
        workflow_id="wf-1",
        message="stay on the shared legacy router",
        thread_id="thread-1",
        mode="auto",
    )

    events = []
    async for event in runtime.stream_chat_events(response["stream_channel_id"]):
        events.append(event)

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.workflow_id == "wf-1"
    assert req.thread_id == "thread-1"
    assert req.session_id == "thread-1"
    assert req.surface == "cli:local"
    assert req.control_plane_mode is None
    assert response["control_plane_mode"] == "v1"
    assert response["control_plane_mode_source"] == "env"
    assert events == [{"type": "chat_complete", "content": "from legacy router"}]


@pytest.mark.asyncio
async def test_local_chat_runtime_v2_delegates_to_server_chat_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LocalChatRuntime()
    runtime._initialized = True
    runtime._services = SimpleNamespace()
    runtime._app_state = object()

    captured: dict[str, object] = {}

    async def _fake_chat_message(request, req, concierge=True):
        captured["request"] = request
        captured["req"] = req
        captured["concierge"] = concierge
        return {
            "stream_channel_id": "chat-v2-local",
            "control_plane_mode": "v2",
            "control_plane_mode_source": "env",
        }

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v2-local"
        yield {"type": "chat_complete", "content": "from dan-v2"}

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v2")
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    response = await runtime.send_chat_message(
        workflow_id="wf-1",
        message="route this through dan-v2",
        thread_id="thread-1",
        mode="agent",
    )

    events = []
    async for event in runtime.stream_chat_events(response["stream_channel_id"]):
        events.append(event)

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.surface == "cli:local"
    assert req.control_plane_mode is None
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "env"
    assert events == [{"type": "chat_complete", "content": "from dan-v2"}]


@pytest.mark.asyncio
async def test_local_chat_runtime_cli_override_can_force_v2_when_global_is_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LocalChatRuntime()
    runtime._initialized = True
    runtime._services = SimpleNamespace()
    runtime._app_state = object()

    captured: dict[str, object] = {}

    async def _fake_chat_message(request, req, concierge=True):
        captured["request"] = request
        captured["req"] = req
        captured["concierge"] = concierge
        return {
            "stream_channel_id": "chat-v2-override",
            "control_plane_mode": "v2",
            "control_plane_mode_source": "request",
        }

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v2-override"
        yield {"type": "chat_complete", "content": "from dan-v2 override"}

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v1")
    monkeypatch.setenv("DAN_CLI_CONTROL_PLANE", "new")
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    response = await runtime.send_chat_message(
        workflow_id="wf-1",
        message="route this through v2",
        thread_id="thread-1",
        mode="agent",
    )

    events = []
    async for event in runtime.stream_chat_events(response["stream_channel_id"]):
        events.append(event)

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.control_plane_mode == "v2"
    assert response["control_plane_mode"] == "v2"
    assert response["control_plane_mode_source"] == "request"
    assert events == [{"type": "chat_complete", "content": "from dan-v2 override"}]


@pytest.mark.asyncio
async def test_local_chat_runtime_cli_override_can_force_v1_when_global_is_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LocalChatRuntime()
    runtime._initialized = True
    runtime._services = SimpleNamespace()
    runtime._app_state = object()

    captured: dict[str, object] = {}

    async def _fake_chat_message(request, req, concierge=True):
        captured["request"] = request
        captured["req"] = req
        captured["concierge"] = concierge
        return {
            "stream_channel_id": "chat-v1-override",
            "control_plane_mode": "v1",
            "control_plane_mode_source": "request",
        }

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v1-override"
        yield {"type": "chat_complete", "content": "forced legacy reply"}

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v2")
    monkeypatch.setenv("DAN_CLI_CONTROL_PLANE", "legacy")
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    response = await runtime.send_chat_message(
        workflow_id="wf-1",
        message="stay on legacy",
        thread_id="thread-1",
        mode="agent",
    )

    events = []
    async for event in runtime.stream_chat_events(response["stream_channel_id"]):
        events.append(event)

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.control_plane_mode == "v1"
    assert response["control_plane_mode"] == "v1"
    assert response["control_plane_mode_source"] == "request"
    assert events == [{"type": "chat_complete", "content": "forced legacy reply"}]


@pytest.mark.asyncio
async def test_local_chat_runtime_streams_run_channels_through_shared_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LocalChatRuntime()
    runtime._initialized = True
    runtime._services = SimpleNamespace()
    runtime._app_state = object()

    async def _fake_chat_message(request, req, concierge=True):
        return {
            "type": "run_started",
            "run_id": "run-1",
            "scope": "full",
            "stream_channel_id": "run-shared-1",
        }

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "run-shared-1"
        yield {
            "type": "chat_run_event",
            "run_event": {
                "event_type": "run_completed",
                "summary": "Run finished.",
            },
        }

    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    response = await runtime.send_chat_message(
        workflow_id="wf-1",
        message="/run",
        thread_id="thread-1",
        mode="agent",
    )

    events = []
    async for event in runtime.stream_chat_events(response["stream_channel_id"]):
        events.append(event)

    assert response["type"] == "run_started"
    assert response["stream_channel_id"] == "run-shared-1"
    assert events == [
        {
            "type": "chat_run_event",
            "run_event": {
                "event_type": "run_completed",
                "summary": "Run finished.",
            },
        }
    ]
