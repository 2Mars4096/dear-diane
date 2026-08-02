from __future__ import annotations

from dan.server.routers import adapters


def test_status_event_clears_stale_last_error_on_connected() -> None:
    adapters._adapter_status_snapshots.clear()
    adapters._adapter_event_snapshots.clear()
    adapters._adapter_event_subscribers.clear()

    adapters._adapter_status_snapshots["tg-1"] = {
        "connection_state": "error",
        "last_error": "temporary polling failure",
    }

    adapters._publish_adapter_event(
        "tg-1",
        {
            "type": "status",
            "connection_state": "connected",
        },
    )

    assert adapters._adapter_status_snapshots["tg-1"]["connection_state"] == "connected"
    assert adapters._adapter_status_snapshots["tg-1"]["last_error"] is None
