from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from dan.adapters.whatsapp_web_adapter import (
    WhatsAppWebAdapter,
    WhatsAppWebAdapterConfig,
)


def test_normalize_pair_status_variants() -> None:
    assert (
        WhatsAppWebAdapter._normalize_pair_status(SimpleNamespace(Status=2)) == "paired"
    )
    assert (
        WhatsAppWebAdapter._normalize_pair_status(SimpleNamespace(Status=1)) == "failed"
    )
    assert (
        WhatsAppWebAdapter._normalize_pair_status(SimpleNamespace(Status="waiting"))
        == "waiting"
    )


def test_connection_snapshot_includes_qr_data() -> None:
    adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())
    adapter._set_connection_snapshot(
        "pairing",
        last_error=None,
        paired=False,
        qr_data="qr-inline-value",
    )

    snapshot = adapter.get_connection_snapshot()
    assert snapshot["connection_state"] == "pairing"
    assert snapshot["paired"] is False
    assert snapshot["qr_data"] == "qr-inline-value"


def test_event_callback_receives_payload() -> None:
    adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())
    events: list[dict[str, object]] = []
    adapter.set_event_callback(events.append)

    adapter._emit_event({"type": "qr", "qr_data": "qr-inline-value"})

    assert events == [{"type": "qr", "qr_data": "qr-inline-value"}]


@pytest.mark.asyncio
async def test_terminal_connect_failures_clear_running_flag() -> None:
    adapter = WhatsAppWebAdapter(WhatsAppWebAdapterConfig())

    class FailingClient:
        def connect(self) -> None:
            raise RuntimeError("boom")

    async def no_sleep(_seconds: float) -> None:
        await asyncio.sleep(0)

    adapter._client = FailingClient()
    adapter._running = True
    adapter._sleep = no_sleep  # type: ignore[method-assign]

    await adapter._connect_with_retry()

    snapshot = adapter.get_connection_snapshot()
    assert adapter._running is False
    assert snapshot["connection_state"] == "error"
    assert snapshot["last_error"] == "boom"
