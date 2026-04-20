from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_adapter_concierge_routes_legacy_mode_through_chat_router_stream(monkeypatch):
    from dan.server.routers import adapters as adapters_module

    captured: dict[str, object] = {}

    async def _fake_chat_message(req, concierge=True):
        captured["req"] = req
        captured["concierge"] = concierge
        return {"stream_channel_id": "chat-v1-123"}

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v1-123"
        yield {
            "type": "chat_queued",
            "stream_channel_id": "queued-123",
            "queue_position": 1,
        }
        yield {
            "type": "chat_complete",
            "content": "Queued reply delivered.",
        }

    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v1")
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    class _Adapter:
        pass

    await adapters_module._run_adapter_concierge(
        "telegram",
        _Adapter(),
        "telegram",
        "chat-123",
        "second message",
    )

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.workflow_id == "_scratch"
    assert req.surface == "telegram:telegram"
    assert req.control_plane_mode is None
    assert sent == [
        ("chat-123", "Queued (position 1) — I'll reply when ready."),
        ("chat-123", "Queued reply delivered."),
    ]


@pytest.mark.asyncio
async def test_adapter_concierge_v2_uses_chat_router_stream(monkeypatch):
    from dan.server.routers import adapters as adapters_module

    captured: dict[str, object] = {}

    async def _fake_chat_message(req, concierge=True):
        captured["req"] = req
        captured["concierge"] = concierge
        return {"stream_channel_id": "chat-v2-123"}

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v2-123"
        yield {
            "type": "chat_queued",
            "stream_channel_id": "queued-456",
            "queue_position": 2,
        }
        yield {
            "type": "chat_complete",
            "content": "V2 reply delivered.",
        }

    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v2")
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    class _Adapter:
        pass

    await adapters_module._run_adapter_concierge(
        "telegram",
        _Adapter(),
        "telegram",
        "chat-123",
        "route through dan-v2",
    )

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.workflow_id == "_scratch"
    assert req.surface == "telegram:telegram"
    assert req.thread_id == "chat-123"
    assert sent == [
        ("chat-123", "Queued (position 2) — I'll reply when ready."),
        ("chat-123", "V2 reply delivered."),
    ]


@pytest.mark.asyncio
async def test_adapter_concierge_surface_override_can_force_v2_when_global_is_v1(monkeypatch):
    from dan.server.routers import adapters as adapters_module

    captured: dict[str, object] = {}

    async def _fake_chat_message(req, concierge=True):
        captured["req"] = req
        captured["concierge"] = concierge
        return {"stream_channel_id": "chat-v2-override"}

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v2-override"
        yield {
            "type": "chat_complete",
            "content": "Surface override delivered.",
        }

    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v1")
    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "new")
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    class _Adapter:
        pass

    await adapters_module._run_adapter_concierge(
        "telegram",
        _Adapter(),
        "telegram",
        "chat-123",
        "route only telegram through dan-v2",
    )

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.control_plane_mode == "v2"
    assert sent == [("chat-123", "Surface override delivered.")]


@pytest.mark.asyncio
async def test_adapter_concierge_surface_override_can_force_v1_when_global_is_v2(monkeypatch):
    from dan.server.routers import adapters as adapters_module

    captured: dict[str, object] = {}

    async def _fake_chat_message(req, concierge=True):
        captured["req"] = req
        captured["concierge"] = concierge
        return {"stream_channel_id": "chat-v1-override"}

    async def _fake_iter_local_chat_stream_events(channel_id: str):
        assert channel_id == "chat-v1-override"
        yield {
            "type": "chat_queued",
            "stream_channel_id": "queued-legacy",
            "queue_position": 1,
        }
        yield {
            "type": "chat_complete",
            "content": "Forced legacy reply.",
        }

    sent: list[tuple[str, str]] = []

    async def _fake_send(_adapter, external_id: str, text: str) -> None:
        sent.append((external_id, text))

    monkeypatch.setenv("DAN_CONTROL_PLANE", "v2")
    monkeypatch.setenv("DAN_TELEGRAM_CONTROL_PLANE", "legacy")
    monkeypatch.setattr(adapters_module, "_send_adapter_text", _fake_send)
    monkeypatch.setattr("dan.server.routers.chat.chat_message", _fake_chat_message)
    monkeypatch.setattr(
        "dan.server.routers.chat.iter_local_chat_stream_events",
        _fake_iter_local_chat_stream_events,
    )

    class _Adapter:
        pass

    await adapters_module._run_adapter_concierge(
        "telegram",
        _Adapter(),
        "telegram",
        "chat-123",
        "stay on legacy",
    )

    req = captured["req"]
    assert captured["concierge"] is True
    assert req.control_plane_mode == "v1"
    assert sent == [
        ("chat-123", "Queued (position 1) — I'll reply when ready."),
        ("chat-123", "Forced legacy reply."),
    ]
