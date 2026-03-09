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
            event = await queue.get()
            enriched = {**event, "surface_id": surface_id, "workflow_name": workflow_name}
            bus.broadcast(enriched)
            if event.get("event_type") in ("run_completed", "run_failed", "run_cancelled"):
                break
    except asyncio.CancelledError:
        pass
    finally:
        rm.unsubscribe(run_id, queue)
