"""Regression tests for app-managed adapter relay helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _StubTelegramAdapter:
    def __init__(self) -> None:
        self._send_text = AsyncMock()


@pytest.mark.asyncio
async def test_send_adapter_text_parses_topic_scoped_telegram_external_id() -> None:
    from dan.server.app import _send_adapter_text

    adapter = _StubTelegramAdapter()

    await _send_adapter_text(adapter, "100:55", "hello")

    adapter._send_text.assert_awaited_once_with(100, "hello", thread_id=55)


@pytest.mark.asyncio
async def test_run_adapter_engine_uses_run_manager_engine_seam(monkeypatch) -> None:
    from dan.server.routers import adapters as adapters_module

    calls: list[dict[str, object]] = []
    block_registry = object()

    class _FakeEngine:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

        async def run(self, graph, inputs=None):
            return SimpleNamespace(success=True, errors={}, outputs={"ok": True})

    class _FakeRunManager:
        def _make_engine(self, **kwargs):
            return _FakeEngine(**kwargs)

    class _FakeSession:
        session_id = "session-1"

    class _FakeSessionStore:
        def __init__(self) -> None:
            self.removed: list[str] = []
            self.states: list[tuple[str, object]] = []

        async def create(self, external_id: str):
            return _FakeSession()

        async def update_state(self, session_id: str, state: object) -> None:
            self.states.append((session_id, state))

        async def remove(self, session_id: str) -> None:
            self.removed.append(session_id)

    class _FakeRenderer:
        def __init__(self, adapter, session_store) -> None:
            self.adapter = adapter
            self.session_store = session_store
            self.active_session_id = None

    class _FakeAdapter:
        def __init__(self) -> None:
            self.registered: list[tuple[str, object]] = []
            self.unregistered: list[str] = []

        def register_session(self, session_id: str, external_id: object) -> None:
            self.registered.append((session_id, external_id))

        def unregister_session(self, session_id: str) -> None:
            self.unregistered.append(session_id)

    import dan.adapters as adapters_pkg

    monkeypatch.setattr(adapters_module, "_adapter_session_stores", {"adapter-1": _FakeSessionStore()})
    monkeypatch.setattr(adapters_pkg, "MessagingHumanRenderer", _FakeRenderer)
    monkeypatch.setattr(adapters_module, "get_run_manager", lambda: _FakeRunManager())
    monkeypatch.setattr(adapters_module, "get_block_registry", lambda: block_registry)
    monkeypatch.setattr(adapters_module, "get_engine_config", lambda: (_ for _ in ()).throw(AssertionError("fallback engine path should not be used")))

    adapter = _FakeAdapter()
    await adapters_module._run_adapter_engine(
        "adapter-1",
        adapter,
        None,
        graph=SimpleNamespace(),
        external_id="123",
        message_text="hello world",
    )

    assert calls and calls[0]["block_registry"] is block_registry
    assert calls[0]["human_renderer"].active_session_id == "session-1"
    assert adapter.registered == [("session-1", 123)]
    assert adapter.unregistered == ["session-1"]
