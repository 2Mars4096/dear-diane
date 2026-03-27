"""Run lifecycle: start, resume, rerun, compare, checkpoints, events, human-input,
scoped run, token breakdown, optimization, and WebSocket streaming."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from dan.server.routers.dependencies import get_graph_store, get_run_manager
from dan.server.run_manager import RunStatus
from dan.server.scoped_run import ScopedRunRequest

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class RunRequest(BaseModel):
    graph_id: str
    inputs: dict[str, Any] | None = None
    run_id: str | None = None
    session_id: str | None = None
    run_policy: dict[str, Any] | None = None


class ResumeRequest(BaseModel):
    graph_id: str
    session_id: str | None = None
    run_policy: dict[str, Any] | None = None


class RerunRequest(BaseModel):
    graph_id: str
    scope_type: Literal["downstream_of", "single_node", "subgraph"]
    target_node_id: str | None = None
    sub_graph_key: str | None = None
    session_id: str | None = None
    run_policy: dict[str, Any] | None = None


class PendingOverlayRequest(BaseModel):
    node_id: str
    patch: dict[str, Any]
    source: str = "user"
    reason: str = ""


# ------------------------------------------------------------------
# Run CRUD
# ------------------------------------------------------------------


@router.post("/api/runs")
async def start_run(req: RunRequest):
    rm = get_run_manager()
    gs = get_graph_store()
    graph = gs.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")
    record = await rm.start_run(
        graph, graph_id=req.graph_id, inputs=req.inputs, run_id=req.run_id,
        session_id=req.session_id,
        run_policy=req.run_policy,
    )
    from dan.server.gateway.router import _event_bus
    if _event_bus is not None:
        from dan.server.run_relay import relay_run_events_to_bus
        asyncio.create_task(
            relay_run_events_to_bus(
                rm=rm,
                run_id=record.run_id,
                workflow_name=req.graph_id,
                surface_id=None,
                bus=_event_bus,
            )
        )
    return {"run_id": record.run_id, "status": record.status.value}


@router.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest):
    rm = get_run_manager()
    gs = get_graph_store()
    graph = gs.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")
    record = await rm.resume_run(
        graph, graph_id=req.graph_id, run_id=run_id,
        session_id=req.session_id,
        run_policy=req.run_policy,
    )
    return {"run_id": record.run_id, "status": record.status.value}


@router.get("/api/runs/compare")
async def compare_runs(run_a: str, run_b: str):
    rm = get_run_manager()
    rec_a = rm.get_run(run_a)
    rec_b = rm.get_run(run_b)
    if rec_a is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_a}' not found")
    if rec_b is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_b}' not found")

    snap_a = rec_a.snapshot()
    snap_b = rec_b.snapshot()

    all_nodes = sorted(
        set(list(snap_a.get("node_statuses", {})) + list(snap_b.get("node_statuses", {})))
    )
    usage_a = snap_a.get("node_usage", {})
    usage_b = snap_b.get("node_usage", {})

    node_diffs: list[dict[str, Any]] = []
    for nid in all_nodes:
        status_a = snap_a.get("node_statuses", {}).get(nid)
        status_b = snap_b.get("node_statuses", {}).get(nid)
        ua = usage_a.get(nid, {})
        ub = usage_b.get(nid, {})
        tok_a = ua.get("total_tokens", 0)
        tok_b = ub.get("total_tokens", 0)
        node_diffs.append(
            {
                "node_id": nid,
                "status_a": status_a,
                "status_b": status_b,
                "status_changed": status_a != status_b,
                "tokens_a": tok_a,
                "tokens_b": tok_b,
                "token_delta": tok_b - tok_a,
                "prompt_tokens_a": ua.get("prompt_tokens", 0),
                "prompt_tokens_b": ub.get("prompt_tokens", 0),
                "completion_tokens_a": ua.get("completion_tokens", 0),
                "completion_tokens_b": ub.get("completion_tokens", 0),
            }
        )

    elapsed_a = snap_a.get("elapsed_seconds") or 0
    elapsed_b = snap_b.get("elapsed_seconds") or 0
    cost_a = snap_a.get("total_cost") or 0
    cost_b = snap_b.get("total_cost") or 0

    return {
        "run_a": snap_a,
        "run_b": snap_b,
        "summary": {
            "elapsed_delta": round(elapsed_b - elapsed_a, 2),
            "token_delta": (snap_b.get("total_tokens", 0) or 0)
            - (snap_a.get("total_tokens", 0) or 0),
            "cost_delta": round(cost_b - cost_a, 6),
            "status_a": snap_a.get("status"),
            "status_b": snap_b.get("status"),
        },
        "node_diffs": node_diffs,
    }


@router.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    rm = get_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return record.snapshot()


@router.get("/api/runs")
async def list_runs(
    workflow_id: str | None = None,
    status: str | None = None,
    after: float | None = None,
    before: float | None = None,
    limit: int = 100,
    offset: int = 0,
):
    rm = get_run_manager()
    runs = rm.list_runs()
    if workflow_id:
        runs = [r for r in runs if r.get("graph_id") == workflow_id]
    if status:
        runs = [r for r in runs if r.get("status") == status]
    if after:
        runs = [r for r in runs if (r.get("started_at") or 0) >= after]
    if before:
        runs = [r for r in runs if (r.get("started_at") or 0) <= before]
    runs.sort(key=lambda r: r.get("started_at", 0), reverse=True)
    return {"runs": runs[offset : offset + limit], "total": len(runs)}


@router.get("/api/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    node_id: str | None = None,
    event_type: str | None = None,
):
    rm = get_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    if record.status in (RunStatus.PENDING, RunStatus.RUNNING):
        return {"events": list(record.events), "source": "live"}
    if rm.run_store is not None:
        events = rm.run_store.load_events(
            record.graph_id, run_id, node_id=node_id, event_type=event_type,
        )
        if events:
            return {"events": events, "source": "persisted"}
    return {"events": list(record.events), "source": "memory"}


@router.post("/api/runs/{run_id}/human-input")
async def submit_human_input(run_id: str, body: dict):
    rm = get_run_manager()
    request_id = body.get("request_id")
    response = body.get("response", {})
    if not request_id:
        raise HTTPException(400, "request_id is required")
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(404, f"Run '{run_id}' not found")
    if isinstance(response, str):
        response = {"response": response}
    ok = rm.submit_human_input(run_id, request_id, response)
    if not ok:
        raise HTTPException(404, f"No pending human-input request '{request_id}'")
    return {"status": "submitted", "request_id": request_id}


# ------------------------------------------------------------------
# Checkpoints
# ------------------------------------------------------------------


@router.get("/api/runs/{run_id}/checkpoints")
async def list_run_checkpoints(run_id: str):
    rm = get_run_manager()
    info = await rm.get_checkpoint_info(run_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"No checkpoint found for run '{run_id}'")

    staleness_info: dict[str, Any] = {}
    record = rm.get_run(run_id)
    gs = get_graph_store()
    if record is not None and info.get("graph_revision"):
        graph = gs.load_as_model(record.graph_id)
        if graph is not None:
            from dan.engine.checkpoint import check_checkpoint_staleness
            staleness = check_checkpoint_staleness(
                info["graph_revision"],
                graph,
                info.get("completed_node_ids"),
            )
            staleness_info = staleness.model_dump()

    return {
        "run_id": run_id,
        "checkpoints": [{
            "checkpoint_id": run_id,
            "timestamp": info.get("timestamp"),
            "graph_id": info.get("graph_id", ""),
            "graph_revision": info.get("graph_revision"),
            "completed_node_count": len(info.get("completed_node_ids", [])),
            "pending_node_ids": info.get("pending_node_ids", []),
            "remaining_node_ids": info.get("remaining_node_ids", []),
            "phase": info.get("phase", ""),
            "stop_reason": info.get("stop_reason", ""),
            "partial": bool(info.get("partial", False)),
            "resumable": bool(info.get("resumable", False)),
            "progress": info.get("progress", {}) or {},
            "effective_run_policy": info.get("effective_run_policy"),
            "has_state": info.get("has_state", False),
            **staleness_info,
        }],
    }


@router.get("/api/runs/{run_id}/checkpoints/{checkpoint_id}")
async def get_checkpoint_detail(run_id: str, checkpoint_id: str):
    rm = get_run_manager()
    info = await rm.get_checkpoint_info(run_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"No checkpoint found for run '{run_id}'")
    return {
        "checkpoint_id": checkpoint_id,
        "run_id": run_id,
        "timestamp": info.get("timestamp"),
        "graph_id": info.get("graph_id", ""),
        "graph_revision": info.get("graph_revision"),
        "completed_node_ids": info.get("completed_node_ids", []),
        "pending_node_ids": info.get("pending_node_ids", []),
        "remaining_node_ids": info.get("remaining_node_ids", []),
        "phase": info.get("phase", ""),
        "stop_reason": info.get("stop_reason", ""),
        "partial": bool(info.get("partial", False)),
        "resumable": bool(info.get("resumable", False)),
        "progress": info.get("progress", {}) or {},
        "effective_run_policy": info.get("effective_run_policy"),
        "node_output_keys": info.get("node_output_keys", []),
    }


@router.post("/api/runs/{run_id}/rerun")
async def rerun_from_checkpoint(run_id: str, req: RerunRequest):
    rm = get_run_manager()
    gs = get_graph_store()
    graph = gs.load_as_model(req.graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.graph_id}' not found")

    from dan.engine.checkpoint import RerunScope

    scope = RerunScope(
        scope_type=req.scope_type,
        target_node_id=req.target_node_id,
        sub_graph_key=req.sub_graph_key,
    )

    try:
        record = await rm.rerun_from_checkpoint(
            graph, graph_id=req.graph_id,
            source_run_id=run_id,
            scope=scope,
            session_id=req.session_id,
            run_policy=req.run_policy,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "run_id": record.run_id,
        "status": record.status.value,
        "source_run_id": run_id,
        "scope": scope.model_dump(),
    }


@router.post("/api/runs/{run_id}/overlay")
async def apply_pending_overlay(run_id: str, req: PendingOverlayRequest):
    rm = get_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    applied = await rm.apply_pending_overlay(
        run_id,
        req.node_id,
        req.patch,
        source=req.source,
        reason=req.reason,
    )
    if not applied:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Overlay was not applied to node '{req.node_id}'. "
                "The run may not be live, or the node is no longer pending."
            ),
        )
    return {
        "run_id": run_id,
        "node_id": req.node_id,
        "applied": True,
    }


# ------------------------------------------------------------------
# Scoped run
# ------------------------------------------------------------------


@router.post("/api/runs/scoped")
async def start_scoped_run(req: ScopedRunRequest):
    from dan.server.scoped_run import ScopedRunResponse, build_scoped_graph

    rm = get_run_manager()
    gs = get_graph_store()
    graph = gs.load_as_model(req.workflow_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{req.workflow_id}' not found")

    result = build_scoped_graph(
        graph, req.scope, req.target_node_id, req.target_subgraph_key, req.inputs,
    )
    if result.error:
        raise HTTPException(status_code=422, detail=result.error.model_dump())

    record = await rm.start_run(
        result.graph, graph_id=req.workflow_id, inputs=req.inputs,
    )
    return ScopedRunResponse(
        run_id=record.run_id,
        status=record.status.value,
        scope=req.scope,
        target=req.target_node_id or req.target_subgraph_key,
    ).model_dump()


# ------------------------------------------------------------------
# Token breakdown / optimization
# ------------------------------------------------------------------


@router.get("/api/runs/{run_id}/token-breakdown")
async def token_breakdown(run_id: str):
    rm = get_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    node_breakdowns: dict[str, Any] = {}
    run_totals = {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost": record.total_cost or 0.0,
    }

    if record.result and isinstance(record.result.metadata, dict):
        cost_snapshot = record.result.metadata.get("__cost_tracker__")
        if isinstance(cost_snapshot, dict):
            node_breakdowns = cost_snapshot.get("node_breakdowns", {})
            for bd in node_breakdowns.values():
                run_totals["total_input_tokens"] += bd.get("total_input_tokens", 0)
                run_totals["total_output_tokens"] += bd.get("total_output_tokens", 0)

    if not node_breakdowns:
        for nid, usage in record.node_usage.items():
            node_breakdowns[nid] = {
                "total_input_tokens": usage.get("prompt_tokens", 0),
                "total_output_tokens": usage.get("completion_tokens", 0),
            }
            run_totals["total_input_tokens"] += usage.get("prompt_tokens", 0)
            run_totals["total_output_tokens"] += usage.get("completion_tokens", 0)

    return {
        "run_id": run_id,
        "nodes": node_breakdowns,
        "run_totals": run_totals,
    }


@router.get("/api/runs/{run_id}/optimization-report")
async def optimization_report(run_id: str):
    rm = get_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    from dan.engine.token_optimization import TokenWasteAnalyzer
    from dan.server.routers.graphs import build_token_analysis_context

    node_breakdowns: dict[str, dict[str, int]] = {}
    node_configs, graph_edges = build_token_analysis_context(record.graph_id)

    if record.result and isinstance(record.result.metadata, dict):
        cost_snap = record.result.metadata.get("__cost_tracker__")
        if isinstance(cost_snap, dict):
            node_breakdowns = cost_snap.get("node_breakdowns", {})

    events_data = [e for e in record.events if isinstance(e, dict)]

    analyzer = TokenWasteAnalyzer()
    report = analyzer.analyze(
        node_breakdowns=node_breakdowns,
        node_configs=node_configs,
        events=events_data,
        graph_edges=graph_edges,
    )
    return {
        "run_id": run_id,
        "report": report.to_dict(),
    }


@router.get("/api/runs/{run_id}/optimization-mutations")
async def optimization_mutations(run_id: str):
    rm = get_run_manager()
    record = rm.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    from dan.engine.token_optimization import OptimizationPlaybook, TokenWasteAnalyzer
    from dan.server.graph_mutator import MutationPlan
    from dan.server.routers.graphs import build_token_analysis_context

    node_breakdowns: dict[str, dict[str, int]] = {}
    node_configs, graph_edges = build_token_analysis_context(record.graph_id)
    if record.result and isinstance(record.result.metadata, dict):
        cost_snap = record.result.metadata.get("__cost_tracker__")
        if isinstance(cost_snap, dict):
            node_breakdowns = cost_snap.get("node_breakdowns", {})

    events_data = [e for e in record.events if isinstance(e, dict)]

    analyzer = TokenWasteAnalyzer()
    report = analyzer.analyze(
        node_breakdowns=node_breakdowns,
        node_configs=node_configs,
        events=events_data,
        graph_edges=graph_edges,
    )

    approval_mode = getattr(rm.engine_config, "optimization_rule_approval_mode", "always_approve")
    playbook = OptimizationPlaybook(approval_mode=approval_mode)
    mutations = []
    for finding in report.findings:
        mut = playbook.generate_mutation(finding)
        if mut is not None:
            mutation_plan = {
                "apply_mode": "all_or_nothing",
                "description": finding.suggestion,
                "operations": [mut],
            }
            try:
                MutationPlan.model_validate(mutation_plan)
            except Exception:
                continue
            mutations.append({
                "graph_id": record.graph_id,
                "mutation": mut,
                "mutation_plan": mutation_plan,
                "apply_request": {"mutation_plan": mutation_plan, "source": "optimization"},
                "finding": finding.to_dict(),
            })

    return {
        "run_id": run_id,
        "mutations": mutations,
        "count": len(mutations),
    }


# ------------------------------------------------------------------
# WebSocket — live run events
# ------------------------------------------------------------------


@router.websocket("/api/runs/{run_id}/events")
async def run_events_ws(websocket: WebSocket, run_id: str):
    rm = get_run_manager()
    await websocket.accept()

    queue = rm.subscribe(run_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if rm.run_is_settled_for_stream(run_id):
                    break
                continue
            await websocket.send_json(event)
            event_type = event.get("event_type", "")
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
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket error for run %s", run_id, exc_info=True)
    finally:
        rm.unsubscribe(run_id, queue)
