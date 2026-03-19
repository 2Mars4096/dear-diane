"""Run lifecycle capability handlers."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import _truncate

logger = logging.getLogger(__name__)


def _find_persisted_run(run_store: Any, run_id: str) -> dict[str, Any] | None:
    """Look up a single run by scanning workflow directories for its summary file."""
    base = getattr(run_store, "_base", None)
    if base is None:
        return None
    base = Path(base)
    if not base.exists():
        return None
    for wf_dir in base.iterdir():
        if not wf_dir.is_dir():
            continue
        summary = run_store.load_summary(wf_dir.name, run_id)
        if summary is not None:
            return summary
    return None


def get_pending_run_disambiguation(run_manager: Any) -> str | None:
    """Return a disambiguation message when multiple runs have pending HumanNode inputs."""
    pending = run_manager.get_all_pending_human_inputs()
    if len(pending) <= 1:
        return None
    lines = ["Multiple runs have pending input. Please specify a run_id:"]
    seen_runs = set()
    for p in pending:
        data = p.get("data", {})
        run_id = data.get("run_id") or p.get("run_id", "?")
        if run_id in seen_runs:
            continue
        seen_runs.add(run_id)
        node_id = data.get("node_id", p.get("node_id", "?"))
        prompt_preview = (data.get("prompt", "") or "")[:80]
        lines.append(f"  - **{run_id}** (node: {node_id}) — {prompt_preview}")
    return "\n".join(lines)


def resolve_run_reference(
    ref: str,
    run_manager: Any,
    run_store: Any | None = None,
) -> str | None:
    """Map 'latest', 'last_failed', 'paused' to concrete run_id."""
    if ref in ("latest", "last_failed", "paused"):
        runs = run_manager.list_runs()
        if ref == "latest":
            if not runs:
                if run_store:
                    summaries = run_store.list_summaries(limit=1)
                    return summaries[0]["run_id"] if summaries else None
                return None
            sorted_runs = sorted(runs, key=lambda r: r.get("started_at", 0), reverse=True)
            return sorted_runs[0]["run_id"]
        elif ref == "last_failed":
            failed = [r for r in runs if r.get("status") == "failed"]
            if failed:
                failed.sort(key=lambda r: r.get("started_at", 0), reverse=True)
                return failed[0]["run_id"]
            if run_store:
                summaries = run_store.list_summaries(status="failed", limit=1)
                return summaries[0]["run_id"] if summaries else None
            return None
        elif ref == "paused":
            pending = run_manager.get_all_pending_human_inputs()
            if len(pending) == 1:
                return (pending[0].get("data") or {}).get("run_id") or pending[0].get("run_id")
            return None
    return ref


async def handle_start_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graph_id = str(args.get("workflow_id", "")).strip() or ctx.workflow_id
    if not graph_id:
        return CapabilityResult(success=False, message="workflow_id is required or set current workflow context.")
    graph_dict = ctx.graph_store.get_graph(graph_id)
    if graph_dict is None:
        return CapabilityResult(success=False, message=f"Workflow '{graph_id}' not found.")
    if not graph_dict.get("nodes"):
        return CapabilityResult(success=False, message="Workflow has no nodes — nothing to run. Try asking me directly instead.")
    from dan.models.graph import Graph
    try:
        graph = Graph.model_validate(graph_dict)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Invalid graph: {exc}")
    inputs = args.get("inputs")
    run_id = args.get("run_id")
    session_id = args.get("session_id")
    run_policy = args.get("run_policy")
    try:
        record = await ctx.run_manager.start_run(
            graph,
            graph_id=graph_id,
            inputs=inputs,
            run_id=run_id,
            session_id=session_id,
            run_policy=run_policy,
        )
        if ctx.event_bus is not None:
            from dan.server.run_relay import relay_run_events_to_bus
            import asyncio
            asyncio.create_task(
                relay_run_events_to_bus(
                    rm=ctx.run_manager,
                    run_id=record.run_id,
                    workflow_name=graph_id,
                    surface_id=None,
                    bus=ctx.event_bus,
                )
            )
    except Exception as exc:
        logger.exception("start_run failed")
        return CapabilityResult(success=False, message=f"Start run failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Run started: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value},
        output_preview=f"Run {record.run_id} started.",
        stream_channel_id=f"run-{record.run_id}",
    )


async def handle_get_run_status(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    record = ctx.run_manager.get_run(run_id)
    if record is None and ctx.run_store:
        snap = _find_persisted_run(ctx.run_store, run_id)
        if snap is None:
            return CapabilityResult(success=False, message=f"Run '{run_id}' not found.")
    elif record is None:
        return CapabilityResult(success=False, message=f"Run '{run_id}' not found.")
    else:
        snap = record.snapshot()
    parts = [
        f"**{snap.get('run_id', '?')}** ({snap.get('graph_id', '?')})",
        f"Status: {snap.get('status', '?')}",
    ]
    if snap.get("phase"):
        parts.append(f"Phase: {snap.get('phase')}")
    if snap.get("stop_reason"):
        parts.append(f"Stop: {snap.get('stop_reason')}")
    if snap.get("partial"):
        parts.append("Partial: yes")
    if snap.get("resumable"):
        parts.append("Resumable: yes")
    node_statuses = snap.get("node_statuses", {})
    if node_statuses:
        done = sum(1 for s in node_statuses.values() if s in ("completed", "failed", "skipped"))
        parts.append(f"Nodes: {done}/{len(node_statuses)}")
    if snap.get("elapsed_seconds") is not None:
        parts.append(f"Elapsed: {snap['elapsed_seconds']:.1f}s")
    if snap.get("total_tokens"):
        parts.append(f"Tokens: {snap['total_tokens']}")
    if snap.get("total_cost") is not None:
        parts.append(f"Cost: ${snap['total_cost']:.4f}")
    if snap.get("error"):
        parts.append(f"Error: {_truncate(snap['error'], 200)}")
    text = " | ".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=snap,
        output_preview=_truncate(text),
    )


async def handle_list_active_runs(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.activity_tracker is None:
        return CapabilityResult(
            success=False,
            message="Activity tracker not available.",
        )
    snapshot = ctx.activity_tracker.get_activity()
    data = snapshot.model_dump() if hasattr(snapshot, "model_dump") else (snapshot if isinstance(snapshot, dict) else {})
    active = data.get("active", [])
    recent = data.get("recent", [])
    surfaces = data.get("connected_surfaces", [])
    parts = []
    if active:
        parts.append(f"**Active runs ({len(active)}):**")
        for r in active[:10]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")
    else:
        parts.append("No active runs.")
    if recent:
        parts.append(f"\n**Recent ({len(recent)}):**")
        for r in recent[:5]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")
    if surfaces:
        parts.append(f"\n**Connected surfaces:** {len(surfaces)}")
    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=data,
        output_preview=_truncate(text),
    )


async def handle_cancel_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    run_id = str(args.get("run_id", "")).strip()
    if not run_id:
        return CapabilityResult(success=False, message="run_id is required.")
    ref = run_id
    run_id = resolve_run_reference(run_id, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message="Could not resolve run reference.")
    ok = ctx.run_manager.cancel_run(run_id)
    return CapabilityResult(
        success=True,
        message=f"Run {run_id} {'cancelled' if ok else 'not found or already finished'}.",
        data={"run_id": run_id, "cancelled": ok},
        output_preview=f"Cancelled {run_id}" if ok else f"Run {run_id} not found.",
    )


async def handle_resume_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    run_id = str(args.get("run_id", "")).strip()
    workflow_id = str(args.get("workflow_id", "")).strip()
    if not run_id or not workflow_id:
        return CapabilityResult(success=False, message="run_id and workflow_id are required.")
    graph = ctx.graph_store.load_as_model(workflow_id)
    if graph is None:
        return CapabilityResult(success=False, message=f"Workflow '{workflow_id}' not found.")
    session_id = args.get("session_id")
    run_policy = args.get("run_policy")
    try:
        record = await ctx.run_manager.resume_run(
            graph,
            graph_id=workflow_id,
            run_id=run_id,
            session_id=session_id,
            run_policy=run_policy,
        )
    except Exception as exc:
        logger.exception("resume_run failed")
        return CapabilityResult(success=False, message=f"Resume failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Run resumed: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value},
        output_preview=f"Run {record.run_id} resumed.",
    )


async def handle_get_run_logs(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    limit = max(1, min(int(args.get("limit", 50)), 500))
    node_id = args.get("node_id")
    if node_id is not None:
        node_id = str(node_id).strip() or None
    record = ctx.run_manager.get_run(run_id)
    workflow_id = None
    events = []
    if record is not None:
        workflow_id = record.graph_id
        events = list(record.events) if hasattr(record, "events") else []
    if workflow_id is None and ctx.run_store:
        persisted = _find_persisted_run(ctx.run_store, run_id)
        if persisted is not None:
            workflow_id = persisted.get("graph_id")
    if workflow_id and ctx.run_store:
        loaded = ctx.run_store.load_events(workflow_id, run_id, node_id=node_id)
        if loaded:
            events = loaded
    if node_id:
        events = [e for e in events if e.get("node_id") == node_id]
    events = events[-limit:]
    lines = []
    for e in events:
        etype = e.get("event_type", "?")
        nid = e.get("node_id", "")
        ts = e.get("timestamp", 0)
        data = e.get("data") or {}
        line = f"[{ts:.0f}] {etype}"
        if nid:
            line += f" node={nid}"
        if data:
            line += f" {_truncate(str(data), 80)}"
        lines.append(line)
    text = "\n".join(lines) if lines else "No events."
    return CapabilityResult(
        success=True,
        message=text,
        data={"events": events},
        output_preview=_truncate(text),
    )


async def handle_get_run_checkpoints(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    try:
        info = await ctx.run_manager.get_checkpoint_info(run_id)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Checkpoint info failed: {exc}")
    if info is None:
        return CapabilityResult(
            success=True,
            message="No checkpoint found for this run.",
            data=None,
            output_preview="No checkpoint.",
        )
    parts = [
        f"Run: {info.get('run_id', '?')}",
        f"Phase: {info.get('phase', '?')}",
        f"Completed nodes: {info.get('completed_node_ids', [])}",
        f"Pending nodes: {info.get('pending_node_ids', [])}",
        f"Node output keys: {info.get('node_output_keys', [])}",
    ]
    if info.get("stop_reason"):
        parts.append(f"Stop reason: {info.get('stop_reason')}")
    if info.get("remaining_node_ids"):
        parts.append(f"Remaining nodes: {info.get('remaining_node_ids', [])}")
    if info.get("graph_revision"):
        parts.append(f"Graph revision: {info['graph_revision']}")
    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=info,
        output_preview=_truncate(text),
    )


async def handle_rerun_from_checkpoint(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    source_run_id = str(args.get("source_run_id", "")).strip()
    workflow_id = str(args.get("workflow_id", "")).strip()
    scope_type = str(args.get("scope_type", "")).strip()
    if not source_run_id or not workflow_id or not scope_type:
        return CapabilityResult(success=False, message="source_run_id, workflow_id, and scope_type are required.")
    from dan.engine.checkpoint import RerunScope
    scope = RerunScope(
        scope_type=scope_type,
        target_node_id=args.get("target_node_id") or None,
        sub_graph_key=args.get("sub_graph_key") or None,
    )
    graph = ctx.graph_store.load_as_model(workflow_id)
    if graph is None:
        return CapabilityResult(success=False, message=f"Workflow '{workflow_id}' not found.")
    session_id = args.get("session_id")
    run_policy = args.get("run_policy")
    try:
        record = await ctx.run_manager.rerun_from_checkpoint(
            graph,
            graph_id=workflow_id,
            source_run_id=source_run_id,
            scope=scope,
            session_id=session_id,
            run_policy=run_policy,
        )
    except ValueError as exc:
        return CapabilityResult(success=False, message=f"Invalid scope or checkpoint: {exc}")
    except RuntimeError as exc:
        return CapabilityResult(success=False, message=f"Stale checkpoint: {exc}")
    except Exception as exc:
        logger.exception("rerun_from_checkpoint failed")
        return CapabilityResult(success=False, message=f"Rerun failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Rerun started: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value, "source_run_id": source_run_id},
        output_preview=f"Rerun {record.run_id} started from checkpoint.",
    )


async def handle_apply_pending_overlay(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    node_id = str(args.get("node_id", "")).strip()
    patch = args.get("patch")
    if not ref or not node_id:
        return CapabilityResult(success=False, message="run_id and node_id are required.")
    if not isinstance(patch, dict) or not patch:
        return CapabilityResult(success=False, message="patch must be a non-empty dict.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    source = str(args.get("source", "user")).strip() or "user"
    reason = str(args.get("reason", "")).strip()
    try:
        applied = await ctx.run_manager.apply_pending_overlay(
            run_id,
            node_id,
            patch,
            source=source,
            reason=reason,
        )
    except Exception as exc:
        logger.exception("apply_pending_overlay failed")
        return CapabilityResult(success=False, message=f"Apply overlay failed: {exc}")
    if not applied:
        return CapabilityResult(
            success=False,
            message=(
                f"Overlay was not applied to node '{node_id}'. "
                "The run may not be live, or the node is no longer pending."
            ),
            data={"run_id": run_id, "node_id": node_id, "applied": False},
            output_preview=f"Overlay not applied to {node_id}.",
        )
    return CapabilityResult(
        success=True,
        message=f"Overlay applied to node '{node_id}' in run {run_id}.",
        data={"run_id": run_id, "node_id": node_id, "applied": True},
        output_preview=f"Overlay applied to {node_id}.",
    )


async def handle_submit_human_input(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    run_id = str(args.get("run_id", "")).strip()
    request_id = str(args.get("request_id", "")).strip()
    response = args.get("response")
    if not run_id or not request_id:
        return CapabilityResult(success=False, message="run_id and request_id are required.")
    if response is None or not isinstance(response, dict):
        return CapabilityResult(success=False, message="response must be a dict.")
    ok = ctx.run_manager.submit_human_input(run_id, request_id, response)
    return CapabilityResult(
        success=True,
        message=f"Input {'submitted' if ok else 'rejected (already resolved or wrong run)'}.",
        data={"submitted": ok},
        output_preview="Submitted." if ok else "Rejected.",
    )
