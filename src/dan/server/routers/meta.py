"""Meta-orchestrator endpoints: discover, plan, validate, run, sessions, WebSocket."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from dan.server.routers.dependencies import (
    get_graph_store,
    get_run_manager,
    get_experience_index,
    get_experience_store,
    validate_path_segment,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level state for background meta tasks and WS subscribers
_meta_tasks: dict[str, asyncio.Task[Any]] = {}
_meta_subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)


def _build_meta_controller():
    from dan.server.app import _build_meta_controller as _app_build_meta_controller
    return _app_build_meta_controller()


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/api/meta/discover")
async def meta_discover(goal: str = "", top_k: int = 5):
    rm = get_run_manager()
    from dan.meta.discovery import DiscoveryService
    from dan.server.skill_library import SKILL_LIBRARY

    try:
        exp_index = get_experience_index()
    except HTTPException:
        exp_index = None
    gs = get_graph_store()
    svc = DiscoveryService(
        tool_registry=rm.tool_registry,
        experience_index=exp_index,
        experience_store=get_experience_store(with_index=exp_index is not None),
        graph_store=gs,
        skill_library=SKILL_LIBRARY,
    )
    workflow_matches = await svc.discover_workflows(
        goal if goal.strip() else "generic workflow",
        top_k=max(1, min(top_k, 20)),
    )
    return {
        "tools": [t.model_dump() for t in svc.discover_tools()],
        "skills": [s.model_dump() for s in svc.discover_skills()],
        "patterns": [p.model_dump() for p in svc.discover_patterns()],
        "workflows": [w.model_dump() for w in workflow_matches],
    }


@router.post("/api/meta/plan")
async def meta_plan(body: dict[str, Any]):
    goal = str(body.get("goal", "")).strip()
    error_context = body.get("error_context")
    if not goal:
        raise HTTPException(status_code=422, detail="goal is required")
    _, planner, _ = _build_meta_controller()
    output = await planner.plan(goal, error_context)
    return {
        "goal": goal,
        "plan": output.plan.model_dump(),
        "review": output.review.model_dump(),
    }


@router.post("/api/meta/validate-plan")
async def meta_validate_plan(body: dict[str, Any]):
    _, planner, _ = _build_meta_controller()
    plan_data = body.get("plan", body)
    if not isinstance(plan_data, dict):
        raise HTTPException(status_code=422, detail="plan must be an object")

    action = str(plan_data.get("action", "")).upper()
    from dan.meta.planner import AdaptPlan, GeneratePlan, ReusePlan

    if action == "REUSE":
        plan = ReusePlan.model_validate(plan_data)
    elif action == "ADAPT":
        plan = AdaptPlan.model_validate(plan_data)
    elif action == "GENERATE":
        plan = GeneratePlan.model_validate(plan_data)
    else:
        raise HTTPException(status_code=422, detail="Unknown plan action")

    review = planner._validate_plan(plan)
    return {"valid": review.valid, "review": review.model_dump()}


@router.post("/api/meta/run")
async def meta_run(body: dict[str, Any]):
    from dan.meta.controller import MetaControllerConfig

    goal = str(body.get("goal", "")).strip()
    if not goal:
        raise HTTPException(status_code=422, detail="goal is required")

    config = MetaControllerConfig.model_validate(body.get("config", {}))
    controller, _, _ = _build_meta_controller()
    session = await controller.create_session(goal, config)

    async def _runner() -> None:
        try:
            await controller.run_session(session, config)
        finally:
            _meta_tasks.pop(session.session_id, None)

    task = asyncio.create_task(_runner(), name=f"meta-session-{session.session_id}")
    _meta_tasks[session.session_id] = task
    return {
        "session_id": session.session_id,
        "status": "started",
        "session": session.model_dump(),
    }


@router.get("/api/meta/sessions")
async def list_meta_sessions():
    _, _, store = _build_meta_controller()
    sessions = await store.list_sessions()
    return {"sessions": [s.model_dump() for s in sessions]}


@router.get("/api/meta/sessions/{session_id}")
async def get_meta_session(session_id: str):
    validate_path_segment(session_id, "session_id")
    _, _, store = _build_meta_controller()
    session = await store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        **session.model_dump(),
        "is_running": session_id in _meta_tasks and not _meta_tasks[session_id].done(),
    }


@router.get("/api/meta/sessions/{session_id}/events")
async def get_meta_session_events(session_id: str, limit: int = 200):
    validate_path_segment(session_id, "session_id")
    _, _, store = _build_meta_controller()
    session = await store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    lim = max(1, min(limit, 5000))
    return {"session_id": session_id, "events": session.events[-lim:]}


@router.post("/api/meta/sessions/{session_id}/pause")
async def pause_meta_session(session_id: str):
    validate_path_segment(session_id, "session_id")
    _, _, store = _build_meta_controller()
    session = await store.load(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.pause_requested = True
    await store.save(session)
    return {
        "session_id": session_id,
        "status": "pause_requested",
        "is_running": session_id in _meta_tasks and not _meta_tasks[session_id].done(),
    }


@router.post("/api/meta/sessions/{session_id}/resume")
async def resume_meta_session(session_id: str, body: dict[str, Any] | None = None):
    validate_path_segment(session_id, "session_id")
    if session_id in _meta_tasks and not _meta_tasks[session_id].done():
        return {"session_id": session_id, "status": "already_running"}

    from dan.meta.controller import HumanOverride

    controller, _, _ = _build_meta_controller()
    override = None
    if body and body.get("override") is not None:
        override = HumanOverride.model_validate(body["override"])

    async def _runner() -> None:
        try:
            await controller.resume(session_id, override=override)
        finally:
            _meta_tasks.pop(session_id, None)

    task = asyncio.create_task(_runner(), name=f"meta-resume-{session_id}")
    _meta_tasks[session_id] = task
    return {"session_id": session_id, "status": "resuming"}


@router.delete("/api/meta/sessions/{session_id}")
async def delete_meta_session(session_id: str):
    validate_path_segment(session_id, "session_id")
    task = _meta_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()

    _, _, store = _build_meta_controller()
    deleted = await store.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "task_cancelled": task is not None}


# ------------------------------------------------------------------
# WebSocket — meta session events
# ------------------------------------------------------------------


@router.websocket("/api/meta/sessions/{session_id}/events/ws")
async def meta_session_events_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
    _meta_subscribers[session_id].append(queue)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Meta WebSocket error for session %s", session_id, exc_info=True)
    finally:
        subs = _meta_subscribers.get(session_id)
        if subs and queue in subs:
            subs.remove(queue)
        if not _meta_subscribers.get(session_id):
            _meta_subscribers.pop(session_id, None)
