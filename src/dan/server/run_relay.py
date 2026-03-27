"""Relay run events to the global event bus."""

import asyncio
from typing import Any

from dan.server.run_manager import RunManager
from dan.server.gateway.events import GlobalEventBus

async def relay_run_events_to_bus(
    rm: RunManager,
    run_id: str,
    workflow_name: str,
    surface_id: str | None,
    bus: GlobalEventBus,
) -> None:
    """Subscribe to a run's events and relay them to the global bus."""
    queue = rm.subscribe(run_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if rm.run_is_settled_for_stream(run_id):
                    break
                continue
            enriched = {**event, "surface_id": surface_id, "workflow_name": workflow_name}
            bus.broadcast(enriched)
            event_type = event.get("event_type")
            if event_type == "_catchup":
                snapshot = event.get("snapshot", {})
                if (
                    isinstance(snapshot, dict)
                    and snapshot.get("status") in ("completed", "failed", "cancelled")
                    and rm.run_is_settled_for_stream(run_id)
                ):
                    break
                continue
            if event_type in (
                "run_completed",
                "run_cancelled",
                "automatic_recovery_completed",
            ):
                break
    except asyncio.CancelledError:
        pass
    finally:
        rm.unsubscribe(run_id, queue)
