"""FastAPI router for the multi-surface gateway."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from dan.server.run_manager import RunManager

from .activity import ActivityTracker
from .events import GlobalEventBus
from .models import (
    ActivitySnapshot,
    CancelRequest,
    CancelResult,
    DispatchRequest,
    DispatchResult,
    SubmitInputRequest,
    SurfaceRegistration,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gateway", tags=["gateway"])

_run_manager: RunManager | None = None
_activity_tracker: ActivityTracker | None = None
_event_bus: GlobalEventBus | None = None
_workspace: Path | None = None
_graph_store: Any = None
_relay_tasks: set[asyncio.Task] = set()


def init_gateway(
    run_manager: RunManager,
    workspace: Path,
    graph_store: Any = None,
) -> None:
    """Initialize gateway with server dependencies. Called during lifespan."""
    global _run_manager, _activity_tracker, _event_bus, _workspace, _graph_store
    _run_manager = run_manager
    _activity_tracker = ActivityTracker(run_manager)
    _event_bus = GlobalEventBus()
    _workspace = workspace
    _graph_store = graph_store


def _require_rm() -> RunManager:
    if _run_manager is None:
        raise HTTPException(503, "Gateway not initialized")
    return _run_manager


def _require_tracker() -> ActivityTracker:
    if _activity_tracker is None:
        raise HTTPException(503, "Gateway not initialized")
    return _activity_tracker


def _require_bus() -> GlobalEventBus:
    if _event_bus is None:
        raise HTTPException(503, "Gateway not initialized")
    return _event_bus


# ── Dispatch ──────────────────────────────────────────────────────────

@router.post("/dispatch", response_model=DispatchResult)
async def dispatch_workflow(req: DispatchRequest) -> DispatchResult:
    rm = _require_rm()
    tracker = _require_tracker()
    bus = _require_bus()

    if req.surface_id:
        tracker.touch_surface(req.surface_id)

    graph = None
    workflow_name = "unknown"

    if req.workflow_id and _graph_store:
        try:
            graph = _graph_store.load_as_model(req.workflow_id)
            workflow_name = req.workflow_id
        except Exception as exc:
            logger.warning("Failed to load workflow %s: %s", req.workflow_id, exc)
            raise HTTPException(404, f"Workflow not found: {req.workflow_id}")

    elif req.workflow_path:
        from dan.utils.workflow_loader import (
            WorkflowLoadError,
            load_graph,
            validate_workflow_path,
        )

        wp = Path(req.workflow_path)
        if _workspace:
            try:
                wp = validate_workflow_path(wp, _workspace)
            except WorkflowLoadError:
                raise HTTPException(404, "Workflow not found")

        try:
            graph = load_graph(wp)
            workflow_name = wp.stem
        except WorkflowLoadError as exc:
            raise HTTPException(422, str(exc))

    elif req.text:
        raise HTTPException(
            501,
            "Text dispatch (MetaController / IntentRegistry) not yet implemented",
        )

    if graph is None:
        raise HTTPException(400, "Could not resolve workflow from request")

    graph_id = req.workflow_id or req.workflow_path or "dispatch"

    record = await rm.start_run(
        graph=graph,
        graph_id=graph_id,
        inputs=req.inputs,
    )

    run_event = {
        "event_type": "run_dispatched",
        "run_id": record.run_id,
        "workflow_name": workflow_name,
        "surface_id": req.surface_id,
        "timestamp": time.time(),
    }
    bus.broadcast(run_event)

    task = asyncio.create_task(
        _relay_run_events_to_bus(rm, record.run_id, workflow_name, req.surface_id, bus)
    )
    _relay_tasks.add(task)
    task.add_done_callback(_relay_tasks.discard)

    return DispatchResult(
        run_id=record.run_id,
        workflow_name=workflow_name,
        status=str(record.status.value) if hasattr(record.status, "value") else str(record.status),
        surface_id=req.surface_id,
    )


async def _relay_run_events_to_bus(
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


# ── Cancel ────────────────────────────────────────────────────────────

@router.post("/cancel", response_model=CancelResult)
async def cancel_workflow(req: CancelRequest) -> CancelResult:
    rm = _require_rm()
    cancelled = rm.cancel_run(req.run_id)
    return CancelResult(run_id=req.run_id, cancelled=cancelled)


# ── Activity ──────────────────────────────────────────────────────────

@router.get("/activity", response_model=ActivitySnapshot)
async def get_activity() -> ActivitySnapshot:
    tracker = _require_tracker()
    return tracker.get_activity()


@router.get("/surfaces")
async def get_surfaces() -> list[dict[str, Any]]:
    tracker = _require_tracker()
    return tracker.get_active_surfaces()


@router.post("/surfaces/register")
async def register_surface(req: SurfaceRegistration) -> dict[str, str]:
    tracker = _require_tracker()
    tracker.register_surface(req.surface_id, req.surface_type)
    return {"status": "registered", "surface_id": req.surface_id}


# ── HumanNode ─────────────────────────────────────────────────────────

@router.get("/pending-inputs")
async def get_pending_inputs() -> list[dict[str, Any]]:
    rm = _require_rm()
    return rm.get_all_pending_human_inputs()


@router.post("/submit-input")
async def submit_input(req: SubmitInputRequest) -> dict[str, Any]:
    rm = _require_rm()
    success = rm.submit_human_input(req.run_id, req.request_id, req.response)
    if not success:
        raise HTTPException(404, "Input request not found or already resolved")
    bus = _require_bus()
    bus.broadcast({
        "event_type": "human_input_resolved",
        "run_id": req.run_id,
        "request_id": req.request_id,
        "responder_surface": req.responder_surface,
        "timestamp": time.time(),
    })
    return {"status": "resolved", "run_id": req.run_id, "request_id": req.request_id}


# ── Global Event Bus ──────────────────────────────────────────────────

@router.websocket("/events")
async def global_events_ws(ws: WebSocket) -> None:
    await ws.accept()
    # NOTE: No authentication on WebSocket. Plan 23-1 accepts this for
    # Phase 13 (localhost-only binding). Add auth when exposing externally.
    bus = _require_bus()
    sub_id = str(uuid.uuid4())

    params = ws.query_params
    surface_filter = params.get("surface")
    workflow_filter = params.get("workflow")

    try:
        queue = bus.subscribe(sub_id, surface_filter, workflow_filter)
    except RuntimeError as exc:
        await ws.close(code=1013, reason=str(exc))
        return

    try:
        while True:
            event = await queue.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        bus.unsubscribe(sub_id)
