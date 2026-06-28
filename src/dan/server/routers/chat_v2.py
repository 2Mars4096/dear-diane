"""Chat/Agent V2 ingress endpoints."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from dan.agent_runtime.super_tui_contract import (
    SUPER_TUI_DEFAULT_BACKEND,
    apply_super_tui_execute_profile,
    is_super_tui_surface_profile,
    normalize_super_tui_surface_profile,
)
from dan.server.chat_v2 import (
    AgentRunCommand,
    AgentRunEvent,
    SurfaceTurn,
    build_surface_turn_from_chat_request,
    legacy_request_with_v2_context,
    summarize_v2_bridge_context,
)
from dan.server.chat_v2_async_core import (
    ForegroundAdmissionResult,
    admit_foreground_turn,
    build_task_board_snapshot,
    mark_background_run_started,
)
from dan.server.chat_v2_backend import run_agent_backend
from dan.server.chat_v2_store import ChatV2Store
from dan.server.routers.chat import ChatMessageRequest, chat_message
from dan.server.routers.dependencies import get_chat_v2_store

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat-v2"])


class AgentRunExecuteRequest(BaseModel):
    backend: str | None = None
    surface_profile: str | None = None
    background: bool = False
    auto_execute_continuations: bool = True
    max_promoted_continuations: int = 8
    profile_policy: dict[str, Any] = Field(default_factory=dict)
    mutation_policy: dict[str, Any] = Field(default_factory=dict)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunAdmissionRequest(BaseModel):
    turn: SurfaceTurn | None = None
    chat_request: ChatMessageRequest | None = None
    background: bool = True
    execute: AgentRunExecuteRequest = Field(
        default_factory=lambda: AgentRunExecuteRequest(background=True)
    )
    max_parallel_runs: int = 4


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
    turn = build_surface_turn_from_chat_request(bridged_req)
    admitted = admit_foreground_turn(store, turn)
    acceptance = admitted.acceptance
    summary = summarize_v2_bridge_context(bridge_context)
    summary.update(
        {
            "task_id": admitted.decision.task_id,
            "run_id": admitted.decision.run_id,
            "queue_item_id": admitted.decision.queue_item_id,
            "queue_position": admitted.decision.queue_position,
            "admission_action": admitted.decision.action,
            "admission_relation": admitted.decision.relation,
            "admission_reason": admitted.decision.reason,
        }
    )
    task_run_ref = _task_run_ref_from_acceptance(acceptance)
    return {
        "status": "accepted",
        "v2_control_plane": summary,
        "task_run_ref": task_run_ref,
        "admission": admitted.decision.model_dump(mode="json"),
        "board": admitted.board.model_dump(mode="json"),
        "task": (
            acceptance.snapshot.model_dump(mode="json")
            if acceptance is not None and acceptance.snapshot is not None
            else None
        ),
        "event": (
            acceptance.event.model_dump(mode="json")
            if acceptance is not None and acceptance.event is not None
            else None
        ),
    }


@router.post("/api/v2/agent-runs/admit")
async def admit_agent_turn(
    admission: AgentRunAdmissionRequest,
    request: Request = None,
) -> dict[str, Any]:
    """Foreground-admit a V2 Agent turn and optionally start it in background."""

    store = _require_chat_v2_store(request)
    if admission.turn is not None:
        turn = admission.turn
    elif admission.chat_request is not None:
        turn = build_surface_turn_from_chat_request(admission.chat_request)
    else:
        raise HTTPException(status_code=422, detail="turn or chat_request is required")

    admitted = admit_foreground_turn(
        store,
        turn,
        max_parallel_runs=admission.max_parallel_runs,
    )
    execute = admission.execute
    backend_name = _execute_backend_name(execute)
    if (
        admission.background
        and admitted.decision.action == "start_parallel"
        and admitted.decision.run_id
    ):
        asyncio.create_task(
            _execute_agent_run_background(
                store,
                admitted.decision.run_id,
                backend_name=backend_name,
                overrides=_execute_overrides(execute),
                auto_execute_continuations=execute.auto_execute_continuations,
                remaining_continuations=execute.max_promoted_continuations,
            )
        )
        mark_background_run_started(
            store,
            admitted.decision.run_id,
            backend=backend_name or "",
            reason=admitted.decision.reason,
        )
        admitted = _refresh_admission_result(store, admitted, turn)

    return {
        "status": _admission_response_status(admitted),
        "admission": admitted.decision.model_dump(mode="json"),
        "board": admitted.board.model_dump(mode="json"),
        "task": _task_payload_for_admission(store, admitted),
        "run": _run_payload_for_admission(store, admitted),
        "event": (
            admitted.event.model_dump(mode="json")
            if admitted.event is not None
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


@router.get("/api/v2/tasks")
async def list_tasks(
    request: Request = None,
    limit: int = 80,
    workspace_root: str = "",
    thread_id: str = "",
) -> dict[str, Any]:
    store = _require_chat_v2_store(request)
    records = store.list_task_records(
        workspace_root=workspace_root,
        thread_id=thread_id,
        limit=limit,
    )
    return {
        "tasks": [
            _task_record_session_payload(store, record)
            for record in records
        ],
    }


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


@router.get("/api/v2/threads/{thread_id}/prompt-log")
async def get_thread_prompt_log(
    thread_id: str,
    request: Request = None,
) -> dict[str, Any]:
    store = _require_chat_v2_store(request)
    return store.thread_prompt_log(thread_id)


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
    overrides = _execute_overrides(execute)
    backend_name = _execute_backend_name(execute)
    if execute.background:
        asyncio.create_task(
            _execute_agent_run_background(
                store,
                run_id,
                backend_name=backend_name,
                overrides=overrides,
                auto_execute_continuations=execute.auto_execute_continuations,
                remaining_continuations=execute.max_promoted_continuations,
            )
        )
        mark_background_run_started(
            store,
            run_id,
            backend=backend_name or "",
            reason="execute endpoint background request",
        )
        store.update_run_metadata(
            run_id,
            {
                "auto_execute_continuations": bool(execute.auto_execute_continuations),
                "max_promoted_continuations": int(execute.max_promoted_continuations),
            },
        )
        refreshed = store.get_run(run_id)
        return {
            "status": "started",
            "run": refreshed.model_dump(mode="json") if refreshed is not None else None,
        }

    result = await run_agent_backend(
        store,
        run_id,
        backend_name=backend_name,
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
    response = {
        "command": normalized.model_dump(mode="json"),
        "event": event.model_dump(mode="json"),
        "task": snapshot.model_dump(mode="json") if snapshot is not None else None,
    }
    branch_task_id = str(event.payload.get("branch_task_id") or "")
    branch_run_id = str(event.payload.get("branch_run_id") or "")
    if branch_task_id:
        branch_snapshot = store.get_task_snapshot(branch_task_id)
        response["branch_task"] = (
            branch_snapshot.model_dump(mode="json")
            if branch_snapshot is not None
            else None
        )
    if branch_run_id:
        branch_run = store.get_run(branch_run_id)
        response["branch_run"] = (
            branch_run.model_dump(mode="json") if branch_run is not None else None
        )
    return response


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
    auto_execute_continuations: bool = True,
    remaining_continuations: int = 8,
    auto_execute_ready_dependencies: bool = True,
) -> None:
    try:
        await run_agent_backend(
            store,
            run_id,
            backend_name=backend_name,
            overrides=overrides,
        )
        run = store.get_run(run_id)
        if not auto_execute_continuations or remaining_continuations <= 0:
            next_run_id = ""
        else:
            next_run_id = (
                str(run.metadata.get("continued_run_id") or "")
                if run is not None
                else ""
            )
        if next_run_id:
            next_run = store.get_run(next_run_id)
            if next_run is not None and next_run.status == "queued":
                store.update_run_metadata(
                    next_run_id,
                    {
                        "execution_mode": "background",
                        "requested_backend": backend_name or "",
                        "auto_execute_continuations": True,
                        "promoted_from_run_id": run_id,
                    },
                    status="running",
                )
                await _execute_agent_run_background(
                    store,
                    next_run_id,
                    backend_name=backend_name,
                    overrides=overrides,
                    auto_execute_continuations=True,
                    remaining_continuations=remaining_continuations - 1,
                    auto_execute_ready_dependencies=auto_execute_ready_dependencies,
                )
        if not auto_execute_ready_dependencies or run is None:
            return
        for ready in store.promote_waiting_dependency_runs(
            dependency_task_id=run.task_id,
            limit=max(1, remaining_continuations),
        ):
            mark_background_run_started(
                store,
                ready.run_id,
                backend=backend_name or "",
                reason="dependency completed",
            )
            asyncio.create_task(
                _execute_agent_run_background(
                    store,
                    ready.run_id,
                    backend_name=backend_name,
                    overrides=overrides,
                    auto_execute_continuations=auto_execute_continuations,
                    remaining_continuations=remaining_continuations,
                    auto_execute_ready_dependencies=True,
                )
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


def _execute_overrides(execute: AgentRunExecuteRequest) -> dict[str, Any]:
    payload = {
        "profile_policy": execute.profile_policy,
        "mutation_policy": execute.mutation_policy,
        "approval_policy": execute.approval_policy,
        "tool_policy": execute.tool_policy,
        "metadata": execute.metadata,
    }
    if is_super_tui_surface_profile(_execute_surface_profile(execute)):
        return apply_super_tui_execute_profile(payload)
    return payload


def _execute_surface_profile(execute: AgentRunExecuteRequest) -> str:
    explicit = execute.surface_profile
    if not explicit and isinstance(execute.metadata, dict):
        explicit = execute.metadata.get("surface_profile") or execute.metadata.get("compatibility_profile")
    if not explicit and isinstance(execute.profile_policy, dict):
        explicit = execute.profile_policy.get("surface_profile")
    return normalize_super_tui_surface_profile(explicit)


def _execute_backend_name(execute: AgentRunExecuteRequest) -> str | None:
    if execute.backend:
        return execute.backend
    if is_super_tui_surface_profile(_execute_surface_profile(execute)):
        return SUPER_TUI_DEFAULT_BACKEND
    return None


def _refresh_admission_result(
    store: ChatV2Store,
    admitted: ForegroundAdmissionResult,
    turn: SurfaceTurn,
) -> ForegroundAdmissionResult:
    board = build_task_board_snapshot(
        store,
        workspace_root=turn.workspace_root,
        thread_id=turn.thread_id,
        surface_topic_key=str(turn.metadata.get("surface_topic_key") or ""),
    )
    run = store.get_run(str(admitted.decision.run_id or ""))
    event = admitted.event
    if run is not None and run.latest_event_type == "background_run_started":
        events = store.load_run_events(run.run_id)
        if events:
            try:
                event = AgentRunEvent.model_validate(events[-1])
            except Exception:
                event = admitted.event
    return admitted.model_copy(update={"board": board, "event": event})


def _task_payload_for_admission(
    store: ChatV2Store,
    admitted: ForegroundAdmissionResult,
) -> dict[str, Any] | None:
    task_id = str(admitted.decision.task_id or admitted.decision.target_task_id or "")
    snapshot = store.get_task_snapshot(task_id) if task_id else None
    return snapshot.model_dump(mode="json") if snapshot is not None else None


def _run_payload_for_admission(
    store: ChatV2Store,
    admitted: ForegroundAdmissionResult,
) -> dict[str, Any] | None:
    run_id = str(admitted.decision.run_id or admitted.decision.target_run_id or "")
    run = store.get_run(run_id) if run_id else None
    return run.model_dump(mode="json") if run is not None else None


def _task_record_session_payload(store: ChatV2Store, task: Any) -> dict[str, Any]:
    payload = task.snapshot().model_dump(mode="json")
    metadata = payload.setdefault("metadata", {})
    metadata.setdefault("workspace_root", getattr(task, "workspace_root", ""))
    metadata.setdefault("workspace_id", getattr(task, "workspace_id", ""))
    active_run_id = str(getattr(task, "active_run_id", None) or metadata.get("active_run_id") or "")
    run = store.get_run(active_run_id) if active_run_id else None
    if run is None:
        runs = store.list_run_records(task_id=str(getattr(task, "task_id", "")), limit=1)
        run = runs[0] if runs else None
    command_text = _agent_run_command_text(run)
    if command_text:
        metadata["command_text"] = command_text
        payload["title"] = _compact_session_text(command_text, limit=96)
    else:
        latest = str(payload.get("latest_progress") or "").strip()
        payload["title"] = _compact_session_text(latest, limit=96) if latest else "Untitled DAN Super session"
    return payload


def _agent_run_command_text(run: Any | None) -> str:
    if run is None:
        return ""
    command = getattr(run, "command", None)
    payload = getattr(command, "payload", {}) if command is not None else {}
    if not isinstance(payload, dict):
        return ""
    for key in ("text", "objective", "message", "prompt"):
        text = str(payload.get(key) or "").strip()
        if text:
            return text
    return ""


def _compact_session_text(value: Any, *, limit: int = 96) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _admission_response_status(admitted: ForegroundAdmissionResult) -> str:
    if admitted.decision.action == "start_parallel":
        return "started" if admitted.decision.run_id else "accepted"
    if admitted.decision.action == "queue_after":
        return "queued"
    if admitted.decision.action == "append_to_active":
        return "appended"
    if admitted.decision.action == "ask_clarification":
        return "needs_input"
    if admitted.decision.action == "reject_or_defer":
        return "blocked"
    return "reported"


_TERMINAL_RUN_STATUSES = {"completed", "failed", "blocked", "paused", "stopped"}
