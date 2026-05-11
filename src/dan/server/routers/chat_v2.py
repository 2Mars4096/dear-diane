"""Chat/Agent V2 ingress endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from dan.server.chat_v2 import (
    AgentRunCommand,
    AgentRunEvent,
    legacy_request_with_v2_context,
    summarize_v2_bridge_context,
)
from dan.server.chat_v2_backend import run_agent_backend
from dan.server.chat_v2_store import ChatV2Store
from dan.server.routers.chat import ChatMessageRequest, chat_message
from dan.server.routers.dependencies import get_chat_v2_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat-v2"])


class AgentRunExecuteRequest(BaseModel):
    backend: str | None = None
    background: bool = False
    profile_policy: dict[str, Any] = Field(default_factory=dict)
    mutation_policy: dict[str, Any] = Field(default_factory=dict)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/v2/chat/message")
async def chat_v2_message(
    request: Request = None,
    req: ChatMessageRequest | None = None,
    concierge: bool = True,
) -> dict[str, Any]:
    """V2 chat ingress.

    This endpoint is the new surface-facing entry point. For the first cutover
    slice it normalizes the request into V2 ingress metadata, forces the DAN-v2
    control-plane selector, and delegates execution to the existing chat stream
    machinery so legacy clients keep working.
    """

    if req is None and isinstance(request, ChatMessageRequest):
        req = request
        request = None
    if req is None:
        raise TypeError("req is required")

    bridged_req = legacy_request_with_v2_context(req)
    response = await chat_message(request=request, req=bridged_req, concierge=concierge)
    if isinstance(response, dict):
        bridge_context = bridged_req.surface_context.get("v2_control_plane")
        acceptance = None
        store = _optional_chat_v2_store(request)
        if store is not None:
            acceptance = store.accept_bridge_context(
                bridge_context,
                stream_channel_id=str(response.get("stream_channel_id") or ""),
            )
        response.setdefault(
            "v2_control_plane",
            summarize_v2_bridge_context(bridge_context),
        )
        if acceptance is not None:
            response["v2_control_plane"].update(
                {
                    "task_id": acceptance.task_id,
                    "run_id": acceptance.run_id,
                    "queue_item_id": acceptance.queue_item_id,
                    "queue_position": acceptance.queue_position,
                }
            )
            task_run_ref = _task_run_ref_from_acceptance(acceptance)
            if task_run_ref is not None:
                response["task_run_ref"] = task_run_ref
                response["v2_control_plane"]["task_run_ref"] = task_run_ref
        response["v2_endpoint"] = True
    return response


@router.post("/api/v2/agent-runs")
async def create_agent_run(
    request: Request = None,
    req: ChatMessageRequest | None = None,
) -> dict[str, Any]:
    """Create or queue a durable V2 Agent task from a surface turn."""

    if req is None and isinstance(request, ChatMessageRequest):
        req = request
        request = None
    if req is None:
        raise TypeError("req is required")

    bridged_req = legacy_request_with_v2_context(req)
    bridge_context = bridged_req.surface_context.get("v2_control_plane")
    store = _require_chat_v2_store(request)
    acceptance = store.accept_bridge_context(bridge_context)
    summary = summarize_v2_bridge_context(bridge_context)
    summary.update(
        {
            "task_id": acceptance.task_id,
            "run_id": acceptance.run_id,
            "queue_item_id": acceptance.queue_item_id,
            "queue_position": acceptance.queue_position,
        }
    )
    task_run_ref = _task_run_ref_from_acceptance(acceptance)
    return {
        "status": "accepted",
        "v2_control_plane": summary,
        "task_run_ref": task_run_ref,
        "task": (
            acceptance.snapshot.model_dump(mode="json")
            if acceptance.snapshot is not None
            else None
        ),
        "event": (
            acceptance.event.model_dump(mode="json")
            if acceptance.event is not None
            else None
        ),
    }


@router.get("/api/v2/tasks/{task_id}")
async def get_task(
    task_id: str,
    request: Request = None,
) -> dict[str, Any]:
    store = _require_chat_v2_store(request)
    snapshot = store.get_task_snapshot(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": snapshot.model_dump(mode="json")}


@router.get("/api/v2/threads/{thread_id}/tasks")
async def list_thread_tasks(
    thread_id: str,
    request: Request = None,
    limit: int = 50,
) -> dict[str, Any]:
    store = _require_chat_v2_store(request)
    return {
        "thread_id": thread_id,
        "tasks": [
            snapshot.model_dump(mode="json")
            for snapshot in store.list_thread_tasks(thread_id, limit=limit)
        ],
    }


@router.get("/api/v2/agent-runs/{run_id}")
async def get_agent_run(
    run_id: str,
    request: Request = None,
) -> dict[str, Any]:
    store = _require_chat_v2_store(request)
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return {"run": run.model_dump(mode="json")}


@router.post("/api/v2/agent-runs/{run_id}/execute")
async def execute_agent_run(
    run_id: str,
    execute: AgentRunExecuteRequest | None = None,
    request: Request = None,
) -> dict[str, Any]:
    """Execute a persisted V2 Agent run through the selected backend."""

    store = _require_chat_v2_store(request)
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    execute = execute or AgentRunExecuteRequest()
    overrides = {
        "profile_policy": execute.profile_policy,
        "mutation_policy": execute.mutation_policy,
        "approval_policy": execute.approval_policy,
        "tool_policy": execute.tool_policy,
        "metadata": execute.metadata,
    }
    if execute.background:
        asyncio.create_task(
            _execute_agent_run_background(
                store,
                run_id,
                backend_name=execute.backend,
                overrides=overrides,
            )
        )
        store.update_run_metadata(
            run_id,
            {
                "execution_mode": "background",
                "requested_backend": execute.backend or "",
            },
            status="running",
        )
        refreshed = store.get_run(run_id)
        return {
            "status": "started",
            "run": refreshed.model_dump(mode="json") if refreshed is not None else None,
        }

    result = await run_agent_backend(
        store,
        run_id,
        backend_name=execute.backend,
        overrides=overrides,
    )
    refreshed = store.get_run(run_id)
    snapshot = store.get_task_snapshot(run.task_id)
    return {
        "status": result.status,
        "backend": result.backend,
        "result": result.model_dump(mode="json"),
        "run": refreshed.model_dump(mode="json") if refreshed is not None else None,
        "task": snapshot.model_dump(mode="json") if snapshot is not None else None,
    }


@router.get("/api/v2/agent-runs/{run_id}/events")
async def get_agent_run_events(
    run_id: str,
    request: Request = None,
) -> dict[str, Any]:
    store = _require_chat_v2_store(request)
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return {"run_id": run_id, "events": store.load_run_events(run_id)}


@router.websocket("/api/v2/agent-runs/{run_id}/events")
async def agent_run_events_ws(websocket: WebSocket, run_id: str) -> None:
    """Replay and stream persisted V2 Agent run events."""

    try:
        store = _require_chat_v2_store(websocket)
    except HTTPException:
        await websocket.accept()
        await websocket.close(code=1011)
        return
    await websocket.accept()
    if store.get_run(run_id) is None:
        await websocket.send_json(
            {"type": "error", "detail": "Agent run not found", "run_id": run_id}
        )
        await websocket.close(code=1008)
        return

    seen = 0
    try:
        while True:
            events = store.load_run_events(run_id)
            for event in events[seen:]:
                await websocket.send_json(event)
            seen = len(events)
            run = store.get_run(run_id)
            if run is None or run.status in _TERMINAL_RUN_STATUSES:
                break
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("Chat V2 Agent run WebSocket error for %s", run_id, exc_info=True)
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


@router.post("/api/v2/agent-runs/{run_id}/events")
async def append_agent_run_event(
    run_id: str,
    event: AgentRunEvent,
    request: Request = None,
) -> dict[str, Any]:
    store = _require_chat_v2_store(request)
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    normalized = event.model_copy(
        update={
            "run_id": event.run_id or run_id,
        }
    )
    snapshot = store.record_agent_event(normalized)
    return {
        "event": normalized.model_dump(mode="json"),
        "task": snapshot.model_dump(mode="json") if snapshot is not None else None,
    }


@router.post("/api/v2/agent-runs/{run_id}/commands")
async def append_agent_run_command(
    run_id: str,
    command: AgentRunCommand,
    request: Request = None,
) -> dict[str, Any]:
    store = _require_chat_v2_store(request)
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    normalized = command.model_copy(
        update={
            "run_id": command.run_id or run_id,
            "task_id": command.task_id or run.task_id,
        }
    )
    event = store.record_agent_command(normalized)
    snapshot = store.get_task_snapshot(run.task_id)
    return {
        "command": normalized.model_dump(mode="json"),
        "event": event.model_dump(mode="json"),
        "task": snapshot.model_dump(mode="json") if snapshot is not None else None,
    }


def _optional_chat_v2_store(request: Request | None) -> ChatV2Store | None:
    try:
        return get_chat_v2_store(request)
    except HTTPException:
        return None


def _require_chat_v2_store(request: Request | WebSocket | None) -> ChatV2Store:
    return get_chat_v2_store(request)


async def _execute_agent_run_background(
    store: ChatV2Store,
    run_id: str,
    *,
    backend_name: str | None,
    overrides: dict[str, Any],
) -> None:
    try:
        await run_agent_backend(
            store,
            run_id,
            backend_name=backend_name,
            overrides=overrides,
        )
    except Exception:
        logger.exception("Background Chat V2 Agent run failed for %s", run_id)


def _task_run_ref_from_acceptance(acceptance: Any) -> dict[str, Any] | None:
    if not getattr(acceptance, "task_id", None) and not getattr(acceptance, "run_id", None):
        return None
    snapshot = getattr(acceptance, "snapshot", None)
    metadata = getattr(snapshot, "metadata", {}) if snapshot is not None else {}
    return {
        "task_id": getattr(acceptance, "task_id", None),
        "run_id": getattr(acceptance, "run_id", None),
        "status": getattr(snapshot, "status", "") if snapshot is not None else "",
        "workspace_root": metadata.get("workspace_root", ""),
        "workspace_id": metadata.get("workspace_id", ""),
    }


_TERMINAL_RUN_STATUSES = {"completed", "failed", "blocked", "stopped"}
