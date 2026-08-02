"""Tests for adapter ↔ concierge parity (29-4 tasks 5-1 through 5-4).

Verifies that messaging adapters construct valid ``SurfaceMessage`` objects,
route through the ``ConcurrentDispatcher``, handle the ``/workflows`` command,
and preserve surface-specific metadata.
"""

from __future__ import annotations

import asyncio
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.server.concierge.models import SurfaceMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_surface_message(
    surface: str = "whatsapp",
    external_id: str = "user@s.whatsapp.net",
    text: str = "hello",
    **kwargs: Any,
) -> SurfaceMessage:
    return SurfaceMessage(
        surface=surface,
        external_id=external_id,
        text=text,
        **kwargs,
    )


class _FakeAdapter:
    """Minimal adapter stub that records method calls."""

    def __init__(self) -> None:
        self._on_new_message = None
        self._sent: list[tuple[str, str]] = []
        self._session_map: dict[str, str] = {}
        self._jid_map: dict[str, str] = {}

    def set_message_callback(self, callback):
        self._on_new_message = callback

    async def send_prompt(self, session_id, prompt, schema=None):
        self._sent.append(("prompt", prompt))

    async def _send_text(self, external_id, text):
        self._sent.append((str(external_id), text))

    async def start(self):
        pass

    async def stop(self):
        pass


class _FakeCompleteEvent:
    """Mimics ``ChatCompleteEvent`` for assertion purposes."""

    def __init__(self, content: str) -> None:
        self.type = "chat_complete"
        self.content = content


class _FakeDispatcher:
    """Records dispatched messages and yields fake events."""

    def __init__(self, response_text: str = "concierge reply") -> None:
        self.dispatched: list[SurfaceMessage] = []
        self._response_text = response_text

    async def dispatch(self, msg: SurfaceMessage):
        self.dispatched.append(msg)
        yield _FakeCompleteEvent(self._response_text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSurfaceMessageConstruction:
    """Adapter constructs a valid SurfaceMessage from incoming text."""

    def test_whatsapp_surface_message(self):
        msg = _make_surface_message(
            surface="whatsapp",
            external_id="user@s.whatsapp.net",
            text="build a stock tracker",
            metadata={"adapter_id": "abc123"},
        )
        assert msg.surface == "whatsapp"
        assert msg.external_id == "user@s.whatsapp.net"
        assert msg.text == "build a stock tracker"
        assert msg.metadata["adapter_id"] == "abc123"

    def test_telegram_surface_message(self):
        msg = _make_surface_message(
            surface="telegram",
            external_id="12345678",
            text="search for papers on LLMs",
        )
        assert msg.surface == "telegram"
        assert msg.external_id == "12345678"
        assert msg.text == "search for papers on LLMs"

    def test_surface_message_with_attachments(self):
        msg = _make_surface_message(
            surface="whatsapp",
            external_id="user@s.whatsapp.net",
            text="[Attachment: /tmp/photo.jpg]",
            attachments=["/tmp/photo.jpg"],
        )
        assert msg.attachments == ["/tmp/photo.jpg"]

    def test_surface_message_preserves_metadata(self):
        msg = _make_surface_message(
            surface="telegram",
            external_id="99999",
            text="hello",
            metadata={"chat_type": "private", "username": "testuser"},
        )
        assert msg.metadata["chat_type"] == "private"
        assert msg.metadata["username"] == "testuser"


class TestAdapterRoutesThoughDispatcher:
    """Adapter message handler routes through dispatcher when available."""

    @pytest.mark.asyncio
    async def test_concierge_dispatch_called(self):
        """When dispatcher is set, adapter message routes through it."""
        fake_dispatcher = _FakeDispatcher("ok from concierge")
        adapter = _FakeAdapter()
        adapter_id = "test-adapter-1"

        from dan.server.concierge.models import SurfaceMessage

        await _simulate_concierge_dispatch(
            adapter_id=adapter_id,
            adapter=adapter,
            surface="whatsapp",
            external_id="user@s.whatsapp.net",
            text="build me a data pipeline",
            dispatcher=fake_dispatcher,
        )

        assert len(fake_dispatcher.dispatched) == 1
        dispatched = fake_dispatcher.dispatched[0]
        assert dispatched.surface == "whatsapp"
        assert dispatched.external_id == "user@s.whatsapp.net"
        assert dispatched.text == "build me a data pipeline"
        assert dispatched.metadata["adapter_id"] == adapter_id

    @pytest.mark.asyncio
    async def test_dispatcher_reply_sent_to_adapter(self):
        """Dispatcher's ChatCompleteEvent is forwarded to the adapter."""
        fake_dispatcher = _FakeDispatcher("Here's your workflow result")
        adapter = _FakeAdapter()

        await _simulate_concierge_dispatch(
            adapter_id="test-2",
            adapter=adapter,
            surface="telegram",
            external_id="12345",
            text="hello",
            dispatcher=fake_dispatcher,
        )

        assert any("Here's your workflow result" in s[1] for s in adapter._sent)

    @pytest.mark.asyncio
    async def test_dispatcher_error_sends_error_message(self):
        """On dispatcher exception, adapter sends an error message."""

        class _BrokenDispatcher:
            async def dispatch(self, msg):
                raise RuntimeError("boom")
                yield  # noqa: F401 — makes this an async generator

        adapter = _FakeAdapter()
        await _simulate_concierge_dispatch(
            adapter_id="test-err",
            adapter=adapter,
            surface="whatsapp",
            external_id="user@s.whatsapp.net",
            text="break things",
            dispatcher=_BrokenDispatcher(),
        )

        assert any("wrong" in s[1].lower() for s in adapter._sent)


class TestWorkflowsCommand:
    """``/workflows`` command returns formatted workflow list."""

    @pytest.mark.asyncio
    async def test_workflows_lists_saved_graphs(self):
        """The /workflows command lists available workflows."""
        adapter = _FakeAdapter()
        fake_graphs = [
            {"graph_id": "wf1", "name": "Stock Tracker", "description": "Tracks stocks daily"},
            {"graph_id": "wf2", "name": "PDF Summary", "description": "Summarizes PDFs"},
        ]

        await _simulate_workflows_command(adapter, "ext1", fake_graphs)

        assert len(adapter._sent) == 1
        text = adapter._sent[0][1]
        assert "Stock Tracker" in text
        assert "PDF Summary" in text

    @pytest.mark.asyncio
    async def test_workflows_empty(self):
        """When no workflows exist, show an appropriate message."""
        adapter = _FakeAdapter()

        await _simulate_workflows_command(adapter, "ext1", [])

        assert len(adapter._sent) == 1
        assert "no saved" in adapter._sent[0][1].lower()

    @pytest.mark.asyncio
    async def test_workflows_truncates_long_list(self):
        """Lists > 20 workflows are truncated."""
        adapter = _FakeAdapter()
        fake_graphs = [
            {"graph_id": f"wf{i}", "name": f"Workflow {i}", "description": f"Desc {i}"}
            for i in range(25)
        ]

        await _simulate_workflows_command(adapter, "ext1", fake_graphs)

        text = adapter._sent[0][1]
        assert "5 more" in text


class TestAdapterPreservesMetadata:
    """Surface-specific metadata (chat_id, etc.) is preserved through dispatch."""

    def test_whatsapp_jid_preserved(self):
        msg = _make_surface_message(
            surface="whatsapp",
            external_id="1234567890@s.whatsapp.net",
            text="hi",
            metadata={"adapter_id": "wa-1"},
        )
        assert "@s.whatsapp.net" in msg.external_id

    def test_telegram_chat_id_preserved(self):
        msg = _make_surface_message(
            surface="telegram",
            external_id="987654321",
            text="hi",
            metadata={"adapter_id": "tg-1"},
        )
        assert msg.external_id == "987654321"

    def test_adapter_id_in_metadata(self):
        msg = _make_surface_message(
            surface="whatsapp",
            external_id="user@s.whatsapp.net",
            text="hi",
            metadata={"adapter_id": "my-adapter"},
        )
        assert msg.metadata["adapter_id"] == "my-adapter"


class TestAppHandlerIntegration:
    """Integration-level test using the actual app module functions."""

    @pytest.mark.asyncio
    async def test_handler_uses_dispatcher_when_available(self):
        """_run_adapter_message_handler routes through dispatcher."""
        import dan.server.app as app_mod

        fake_dispatcher = _FakeDispatcher("dispatched reply")
        adapter = _FakeAdapter()

        old_dispatcher = app_mod._dispatcher
        adapters = app_mod._active_adapters
        surfaces = app_mod._adapter_surface_types

        try:
            app_mod._dispatcher = fake_dispatcher
            adapters["int-test"] = (adapter, MagicMock())
            surfaces["int-test"] = "whatsapp"

            app_mod._run_adapter_message_handler("int-test", "ext@jid", "hello world")

            await asyncio.sleep(0.1)

            assert len(fake_dispatcher.dispatched) == 1
            assert fake_dispatcher.dispatched[0].surface == "whatsapp"
            assert fake_dispatcher.dispatched[0].text == "hello world"
        finally:
            app_mod._dispatcher = old_dispatcher
            adapters.pop("int-test", None)
            surfaces.pop("int-test", None)

    @pytest.mark.asyncio
    async def test_handler_falls_back_to_engine_without_dispatcher(self):
        """Without dispatcher, _run_adapter_message_handler needs a renderer+graph."""
        import dan.server.app as app_mod

        adapter = _FakeAdapter()
        old_dispatcher = app_mod._dispatcher
        adapters = app_mod._active_adapters
        surfaces = app_mod._adapter_surface_types
        renderers = app_mod._adapter_renderers

        try:
            app_mod._dispatcher = None
            adapters["fb-test"] = (adapter, MagicMock())
            surfaces["fb-test"] = "telegram"
            renderers["fb-test"] = (MagicMock(), None)

            app_mod._run_adapter_message_handler("fb-test", "123", "hello")

            await asyncio.sleep(0.05)
        finally:
            app_mod._dispatcher = old_dispatcher
            adapters.pop("fb-test", None)
            surfaces.pop("fb-test", None)
            renderers.pop("fb-test", None)

    @pytest.mark.asyncio
    async def test_workflows_command_integration(self):
        """``/workflows`` intercepts before dispatch."""
        import dan.server.app as app_mod

        fake_dispatcher = _FakeDispatcher("should not be called")
        adapter = _FakeAdapter()

        old_dispatcher = app_mod._dispatcher
        old_graph_store = app_mod._graph_store
        adapters = app_mod._active_adapters
        surfaces = app_mod._adapter_surface_types

        try:
            app_mod._dispatcher = fake_dispatcher
            adapters["wf-test"] = (adapter, MagicMock())
            surfaces["wf-test"] = "whatsapp"
            mock_store = MagicMock()
            mock_store.list_graphs.return_value = [
                {"graph_id": "wf1", "name": "My Flow", "description": "test"},
            ]
            app_mod._graph_store = mock_store

            app_mod._run_adapter_message_handler("wf-test", "ext1", "/workflows")

            await asyncio.sleep(0.1)

            assert len(fake_dispatcher.dispatched) == 0
            assert any("My Flow" in s[1] for s in adapter._sent)
        finally:
            app_mod._dispatcher = old_dispatcher
            app_mod._graph_store = old_graph_store
            adapters.pop("wf-test", None)
            surfaces.pop("wf-test", None)


# ---------------------------------------------------------------------------
# Simulation helpers (avoid importing full server machinery)
# ---------------------------------------------------------------------------


async def _simulate_concierge_dispatch(
    adapter_id: str,
    adapter: _FakeAdapter,
    surface: str,
    external_id: str,
    text: str,
    dispatcher: Any,
) -> None:
    """Simulate what ``_run_adapter_concierge`` does with a fake dispatcher."""
    from dan.server.concierge.models import SurfaceMessage

    msg = SurfaceMessage(
        surface=surface,
        external_id=external_id,
        text=text,
        metadata={"adapter_id": adapter_id},
    )
    try:
        async for event in dispatcher.dispatch(msg):
            evt_type = getattr(event, "type", "")
            if evt_type == "chat_complete":
                content = getattr(event, "content", "")
                if content:
                    await adapter._send_text(external_id, content)
    except Exception:
        await adapter._send_text(external_id, "Something went wrong. Please try again.")


async def _simulate_workflows_command(
    adapter: _FakeAdapter,
    external_id: str,
    graphs: list[dict[str, Any]],
) -> None:
    """Simulate the /workflows command logic."""
    if not graphs:
        await adapter._send_text(external_id, "No saved workflows found.")
        return
    lines = ["*Saved Workflows*\n"]
    for i, g in enumerate(graphs[:20], 1):
        name = g.get("name", g.get("graph_id", "?"))
        desc = g.get("description", "")
        line = f"{i}. *{name}*"
        if desc:
            line += f" — {desc[:80]}"
        lines.append(line)
    if len(graphs) > 20:
        lines.append(f"\n…and {len(graphs) - 20} more.")
    await adapter._send_text(external_id, "\n".join(lines))
