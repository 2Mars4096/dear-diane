from __future__ import annotations

from types import SimpleNamespace

import pytest

import dan.mcp_bridge as mcp_bridge
import dan.server.chat_factory as chat_factory
import dan.server.startup as startup_mod


class FakeBridge:
    def __init__(self) -> None:
        self.shutdown_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_initialize_mcp_bridge_for_server_sets_context_and_autoconnects(monkeypatch):
    fake_bridge = FakeBridge()
    calls: list[tuple[object, object, object]] = []

    async def fake_autoconnect(bridge, capability_registry, tool_registry):
        calls.append((bridge, capability_registry, tool_registry))
        return ["stata"]

    monkeypatch.setattr(mcp_bridge, "MCPBridge", lambda: fake_bridge)
    monkeypatch.setattr(
        mcp_bridge,
        "autoconnect_configured_mcp_servers",
        fake_autoconnect,
    )

    capability_registry = object()
    tool_registry = object()
    capability_context = SimpleNamespace(mcp_bridge=None)
    state = SimpleNamespace(startup_degradations=[])

    bridge = await startup_mod._initialize_mcp_bridge_for_server(
        state=state,
        capability_registry=capability_registry,
        tool_registry=tool_registry,
        capability_context=capability_context,
    )

    assert bridge is fake_bridge
    assert capability_context.mcp_bridge is fake_bridge
    assert calls == [(fake_bridge, capability_registry, tool_registry)]


@pytest.mark.asyncio
async def test_shutdown_mcp_bridge_for_server_calls_bridge_shutdown():
    fake_bridge = FakeBridge()

    await startup_mod._shutdown_mcp_bridge_for_server(fake_bridge)

    assert fake_bridge.shutdown_called is True


@pytest.mark.asyncio
async def test_run_async_init_sync_under_running_loop():
    async def sample(value: int) -> int:
        return value + 1

    result = chat_factory._run_async_init_sync(sample, 41)

    assert result == 42
