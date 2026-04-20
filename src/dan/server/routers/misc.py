"""Miscellaneous endpoints: health, cache, metrics, files, docs, code-refs,
test cases, memory, errors, and rules."""

from __future__ import annotations

import os
import re
import time
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from dan.server.capabilities.config import _update_env_file
from dan.server.paths import resolve_workspace_root
from dan.server.routers.dependencies import (
    get_app_state,
    get_run_manager,
    get_graph_store,
    get_test_case_store,
    get_mention_resolver,
    get_engine_config,
    get_memory_store,
    resolve_cache_dir,
    validate_path_segment,
)
from dan.worker.organism_log import (
    read_organism_log,
    read_organism_log_rows,
    readable_organism_log_v1_rows,
)
from dan.worker.organism_log_analysis import analyze_organism_log

logger = logging.getLogger(__name__)

router = APIRouter()

_RUNTIME_CONFIG_KEYS = (
    "DAN_CHAT_MODEL",
    "DAN_LLM_MODEL",
    "DAN_LLM_BASE_URL",
    "DAN_BOT_NAME",
    "DAN_ENABLE_TIER_POLICY",
    "DAN_FULL_TOOLS",
    "DAN_TELEMETRY",
    "DAN_LEARNING_MODE",
)

_RESTART_REQUIRED_CONFIG_KEYS = {
    "DAN_LLM_BASE_URL",
    "DAN_ENABLE_TIER_POLICY",
    "DAN_FULL_TOOLS",
    "DAN_TELEMETRY",
    "DAN_LEARNING_MODE",
}

_ORGANISM_LOG_DISCOVERY_PATTERNS = (
    ".dan-code/runs/**/events.jsonl",
    ".dan-research/runs/**/events.jsonl",
    ".dan-research/control-plane-events.jsonl",
)
_MAX_ORGANISM_LOG_DISCOVER_LIMIT = 100


def _normalize_env_bool(key: str, default: bool) -> str:
    raw = str(os.environ.get(key, "")).strip().lower()
    if not raw:
        return "1" if default else "0"
    return "1" if raw in ("1", "true", "yes", "on") else "0"


def _read_runtime_config_values(chat_manager: Any | None = None) -> dict[str, str]:
    chat_model = (
        getattr(chat_manager, "_chat_model", "")
        or os.environ.get("DAN_CHAT_MODEL", "").strip()
    )
    if not chat_model:
        chat_model = os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"

    return {
        "DAN_CHAT_MODEL": chat_model,
        "DAN_LLM_MODEL": os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6"),
        "DAN_LLM_BASE_URL": os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
        "DAN_BOT_NAME": os.environ.get("DAN_BOT_NAME", "DAN"),
        "DAN_ENABLE_TIER_POLICY": _normalize_env_bool("DAN_ENABLE_TIER_POLICY", False),
        "DAN_FULL_TOOLS": _normalize_env_bool("DAN_FULL_TOOLS", True),
        "DAN_TELEMETRY": _normalize_env_bool("DAN_TELEMETRY", True),
        "DAN_LEARNING_MODE": _normalize_env_bool("DAN_LEARNING_MODE", False),
    }


def _request_app_state(request: Request | None) -> Any | None:
    if request is None:
        return None
    return getattr(getattr(request.app, "state", None), "dan", None)


def _isoformat_utc(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return (
        datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_organism_log_root(root_path: str | None = None) -> Path:
    raw = str(root_path or resolve_workspace_root()).strip()
    candidate = Path(raw).expanduser().resolve()
    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"Workspace root not found: {candidate}")
    return candidate


def _resolve_organism_log_path(
    raw_path: str,
    *,
    root_path: Path | None = None,
) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Provide a log file path.")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = (root_path or _resolve_organism_log_root()) / candidate
    resolved = candidate.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"Log file not found: {resolved}")
    return resolved


def _path_relative_to_root(path: Path, root_path: Path) -> str:
    try:
        return str(path.relative_to(root_path))
    except ValueError:
        return str(path)


def _organism_log_display_name(relative_path: str, *, product: str, stream_kind: str) -> str:
    if relative_path.endswith(".dan-research/control-plane-events.jsonl"):
        return "Research control plane"
    match = re.search(r"(?:^|/)(turn-[^/]+)/events\.jsonl$", relative_path)
    if match and product == "dan_code":
        return f"Code {match.group(1)}"
    if match and product == "dan_research":
        return f"Research {match.group(1)}"
    file_name = Path(relative_path).name
    if stream_kind == "control_plane":
        return f"{product or 'organism'} control plane"
    return file_name


def _summarize_organism_log_file(path: Path, *, root_path: Path) -> dict[str, Any] | None:
    try:
        raw_rows = readable_organism_log_v1_rows(read_organism_log(path))
    except Exception as exc:
        logger.warning("Failed to read organism log %s: %s", path, exc)
        return None
    if not raw_rows:
        return None

    try:
        normalized = read_organism_log_rows(path)
    except Exception as exc:
        logger.warning("Failed to normalize organism log %s: %s", path, exc)
        return None
    if not normalized:
        return None

    spans = [row for row in normalized if row.record_kind == "span"]
    events = [row for row in normalized if row.record_kind == "event"]
    first = normalized[0]
    last = normalized[-1]
    stat = path.stat()
    relative_path = _path_relative_to_root(path, root_path)
    started_at = next(
        (
            row.start_timestamp or row.timestamp
            for row in spans
            if (row.start_timestamp or row.timestamp)
        ),
        first.timestamp or None,
    )
    ended_at = next(
        (
            row.end_timestamp or row.timestamp
            for row in reversed(spans)
            if (row.end_timestamp or row.timestamp)
        ),
        last.timestamp or None,
    )
    return {
        "path": str(path),
        "root_path": str(root_path),
        "relative_path": relative_path,
        "display_name": _organism_log_display_name(
            relative_path,
            product=first.product,
            stream_kind=first.stream_kind,
        ),
        "product": first.product,
        "stream_kind": first.stream_kind,
        "session_id": first.session_id,
        "turn_id": first.turn_id,
        "task_id": first.task_id,
        "trace_id": first.trace_id,
        "organism_id": first.organism_id,
        "organ_id": first.organ_id,
        "schema_version": first.schema_version,
        "event_count": len(events),
        "span_count": len(spans),
        "size_bytes": stat.st_size,
        "updated_at": _isoformat_utc(stat.st_mtime),
        "started_at": started_at,
        "ended_at": ended_at,
    }


def _discover_organism_log_files(root_path: Path, *, limit: int) -> list[dict[str, Any]]:
    requested_limit = max(1, min(int(limit), _MAX_ORGANISM_LOG_DISCOVER_LIMIT))
    candidates: dict[str, Path] = {}
    for pattern in _ORGANISM_LOG_DISCOVERY_PATTERNS:
        for candidate in root_path.glob(pattern):
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            candidates[str(resolved)] = resolved

    ordered = sorted(
        candidates.values(),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )

    summaries: list[dict[str, Any]] = []
    for candidate in ordered:
        summary = _summarize_organism_log_file(candidate, root_path=root_path)
        if summary is None:
            continue
        summaries.append(summary)
        if len(summaries) >= requested_limit:
            break
    return summaries


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@router.get("/health")
async def health_check(request: Request = None) -> dict[str, Any]:
    """Health check endpoint for server discovery."""
    from dan.server.runtime_config import provider_readiness_summary

    state = get_app_state(request)
    result: dict[str, Any] = {"status": "ok"}
    run_manager = state.run_manager if state is not None else None
    startup_issues = [dict(item) for item in (getattr(state, "startup_degradations", []) or [])]
    if run_manager is not None:
        runs = run_manager.list_runs()
        active = [r for r in runs if r.get("status") in ("running", "pending")]
        result["active_runs"] = len(active)
    result["startup"] = {
        "status": "degraded" if startup_issues else "ok",
        "issues": startup_issues,
    }
    engine_config = state.engine_config if state is not None else None
    if engine_config is None:
        engine_config = get_engine_config(request)
    if engine_config is not None:
        result["providers"] = provider_readiness_summary(engine_config)
    return result


@router.get("/api/health")
async def health():
    """Minimal liveness probe for desktop/editor reconnect flows."""
    return {"status": "ok", "pid": os.getpid(), "timestamp": time.time()}


@router.get("/api/config")
async def get_runtime_config(request: Request) -> dict[str, Any]:
    """Return the subset of runtime settings that the desktop UI can edit."""
    state = _request_app_state(request)
    return {
        "values": _read_runtime_config_values(
            getattr(state, "chat_manager", None),
        ),
        "restart_required_keys": sorted(_RESTART_REQUIRED_CONFIG_KEYS),
    }


@router.post("/api/config")
async def set_runtime_config(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Persist a runtime setting to both the live server env and project .env."""
    key = str(body.get("key") or "").strip().upper()
    if key not in _RUNTIME_CONFIG_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported config key '{key}'.",
        )

    raw_value = body.get("value", "")
    if isinstance(raw_value, bool):
        value = "1" if raw_value else "0"
    else:
        value = str(raw_value).strip()

    os.environ[key] = value

    state = _request_app_state(request)
    chat_manager = getattr(state, "chat_manager", None)

    if key in ("DAN_LLM_MODEL", "DAN_CHAT_MODEL") and chat_manager is not None:
        chat_manager._chat_model = (
            os.environ.get("DAN_CHAT_MODEL", "").strip()
            or os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6").strip()
            or "claude-sonnet-4-6"
        )

    if key == "DAN_BOT_NAME":
        try:
            from dan.server.concierge import identity as concierge_identity

            concierge_identity._cached_bot_name = None
        except Exception:
            pass

    try:
        _update_env_file(key, value)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Saved '{key}' in the running server, but failed to update .env: {exc}",
        ) from exc

    return {
        "status": "ok",
        "key": key,
        "value": value,
        "restart_required": key in _RESTART_REQUIRED_CONFIG_KEYS,
    }


# ------------------------------------------------------------------
# Cache
# ------------------------------------------------------------------


@router.post("/api/cache/clear")
async def clear_cache(request: Request):
    config = get_engine_config(request)
    base = resolve_cache_dir(config)
    deleted_files = 0

    if base.exists():
        for pattern in ("*.json", "*.tmp", "**/*.json", "**/*.tmp"):
            for p in base.glob(pattern):
                if not p.is_file():
                    continue
                try:
                    p.unlink()
                    deleted_files += 1
                except OSError:
                    pass

    return {
        "status": "cleared",
        "cache_dir": str(base),
        "deleted_files": deleted_files,
    }


@router.get("/api/cache/stats")
async def cache_stats(request: Request):
    config = get_engine_config(request)
    base = resolve_cache_dir(config)
    file_count = 0
    total_bytes = 0
    if base.exists():
        for p in base.rglob("*.json"):
            if p.is_file():
                file_count += 1
                try:
                    total_bytes += p.stat().st_size
                except OSError:
                    pass

    latest_run_cache: dict[str, Any] | None = None
    state = _request_app_state(request)
    run_manager = state.run_manager if state is not None else None
    if run_manager is not None:
        runs = run_manager.list_runs()
        if runs:
            latest = max(runs, key=lambda r: float(r.get("started_at", 0.0) or 0.0))
            record = run_manager.get_run(str(latest.get("run_id", "")))
            if record is not None and record.result is not None:
                meta = record.result.metadata
                if isinstance(meta, dict):
                    latest_run_cache = meta.get("__run_cache__")

    return {
        "cache_dir": str(base),
        "cache_enabled": config.cache_enabled,
        "cache_max_size_mb": config.cache_max_size_mb,
        "semantic_cache_threshold": config.semantic_cache_threshold,
        "semantic_cache_ttl_hours": config.semantic_cache_ttl_hours,
        "disk_file_count": file_count,
        "disk_size_bytes": total_bytes,
        "latest_run_cache": latest_run_cache,
    }


# ------------------------------------------------------------------
# Mutation metrics
# ------------------------------------------------------------------


@router.get("/api/metrics/mutations")
async def get_mutation_metrics():
    from dan.server.mutation_metrics import mutation_metrics
    return mutation_metrics.summary()


@router.post("/api/metrics/mutations/reset")
async def reset_mutation_metrics():
    from dan.server.mutation_metrics import mutation_metrics
    return mutation_metrics.reset()


# ------------------------------------------------------------------
# Files / docs / code-refs
# ------------------------------------------------------------------


@router.get("/api/files/list")
async def list_workspace_files(request: Request):
    resolver = get_mention_resolver(request)
    if resolver is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    files = resolver.file_resolver.list_files()
    return {"files": files}


@router.get("/api/organism-logs")
async def list_organism_logs(
    root_path: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Discover known organism-log files under one workspace root."""

    resolved_root = _resolve_organism_log_root(root_path)
    logs = _discover_organism_log_files(resolved_root, limit=limit)
    return {
        "root_path": str(resolved_root),
        "logs": logs,
    }


@router.get("/api/organism-logs/analyze")
async def analyze_organism_log_file(
    path: str,
    root_path: str | None = None,
) -> dict[str, Any]:
    """Analyze one organism-log file into timeline and dependency payloads."""

    resolved_root = _resolve_organism_log_root(root_path)
    resolved_path = _resolve_organism_log_path(path, root_path=resolved_root)
    summary = _summarize_organism_log_file(resolved_path, root_path=resolved_root)
    if summary is None:
        raise HTTPException(
            status_code=400,
            detail="Selected file is not a readable organism_log_v1 JSONL trace.",
        )
    analysis = analyze_organism_log(resolved_path)
    return {
        "path": str(resolved_path),
        "log": summary,
        "analysis": analysis.model_dump(mode="json"),
    }


@router.get("/api/docs/list")
async def list_docs(request: Request):
    resolver = get_mention_resolver(request)
    if resolver is None:
        raise HTTPException(status_code=503, detail="Server not fully initialised")
    docs = resolver.docs_resolver.list_docs()
    return {"docs": docs}


@router.get("/api/code-refs/{workflow_id}")
async def list_code_refs(workflow_id: str, request: Request):
    gs = get_graph_store(request)
    graph_dict = gs.get_graph(workflow_id)
    if graph_dict is None:
        raise HTTPException(status_code=404, detail=f"Graph '{workflow_id}' not found")
    from dan.server.mention_resolver import CodeResolver
    refs = CodeResolver.list_code_refs(graph_dict)
    return {"refs": refs}


# ------------------------------------------------------------------
# Test cases
# ------------------------------------------------------------------

from dan.server.test_cases import NodeTestCase, TestCaseRunResult


@router.get("/api/test-cases/{workflow_id}/{node_id}")
async def list_test_cases(workflow_id: str, node_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_test_case_store(request)
    cases = store.list_cases(workflow_id, node_id)
    return {"cases": [c.model_dump() for c in cases]}


@router.post("/api/test-cases/{workflow_id}/{node_id}")
async def create_or_update_test_case(
    workflow_id: str,
    node_id: str,
    request: Request,
    body: dict[str, Any],
):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_test_case_store(request)
    body.setdefault("node_id", node_id)
    body.setdefault("updated_at", time.time())
    if "id" not in body:
        body["id"] = str(uuid.uuid4())
    if "created_at" not in body:
        body["created_at"] = time.time()
    case = NodeTestCase.model_validate(body)
    store.save_case(workflow_id, node_id, case)
    return {"case": case.model_dump()}


@router.delete("/api/test-cases/{workflow_id}/{node_id}/{case_id}")
async def delete_test_case(
    workflow_id: str,
    node_id: str,
    case_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_test_case_store(request)
    if not store.delete_case(workflow_id, node_id, case_id):
        raise HTTPException(status_code=404, detail=f"Test case '{case_id}' not found")
    return {"status": "deleted", "case_id": case_id}


@router.post("/api/test-cases/{workflow_id}/{node_id}/{case_id}/run")
async def run_test_case(
    workflow_id: str,
    node_id: str,
    case_id: str,
    request: Request,
):
    import asyncio

    validate_path_segment(workflow_id, "workflow_id")
    rm = get_run_manager(request)
    gs = get_graph_store(request)
    store = get_test_case_store(request)

    case = store.get_case(workflow_id, node_id, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"Test case '{case_id}' not found")

    graph = gs.load_as_model(workflow_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{workflow_id}' not found")

    target_node = graph.node_by_id(node_id)
    if target_node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in graph")

    from dan.models.graph import Graph as GraphModel

    synthetic = GraphModel(
        nodes=[target_node],
        entry_points=[node_id],
        exit_points=[node_id],
    )

    run_id = f"test-{case_id}-{int(time.time() * 1000)}"
    record = await rm.start_run(
        synthetic,
        graph_id=workflow_id,
        inputs=case.inputs,
        run_id=run_id,
    )

    deadline = time.time() + 120
    while True:
        current = rm.get_run(record.run_id)
        if current is None:
            break
        if current.status.value in ("completed", "failed"):
            break
        if time.time() > deadline:
            break
        await asyncio.sleep(0.1)

    current = rm.get_run(record.run_id)
    actual_outputs: dict[str, Any] = {}
    execution_metadata: dict[str, Any] = {}
    error_msg: str | None = None

    if current and current.result:
        actual_outputs = current.result.outputs or {}
        if node_id in actual_outputs and isinstance(actual_outputs[node_id], dict):
            actual_outputs = actual_outputs[node_id]
        if current.result.errors:
            error_msg = "; ".join(
                f"{k}: {v}" for k, v in current.result.errors.items()
            )
        execution_metadata = {
            "run_id": record.run_id,
            "elapsed_seconds": current.elapsed_seconds,
            "total_tokens": current.total_tokens,
            "total_cost": current.total_cost,
            "model": current.model,
        }

    passed = True
    diff: dict[str, Any] | None = None

    if error_msg:
        passed = False
    elif case.expected_outputs is not None:
        diff = {}
        for key, expected_val in case.expected_outputs.items():
            actual_val = actual_outputs.get(key)
            if actual_val != expected_val:
                diff[key] = {"expected": expected_val, "actual": actual_val}
        passed = len(diff) == 0
        if not diff:
            diff = None

    result = TestCaseRunResult(
        passed=passed,
        actual_outputs=actual_outputs,
        expected_outputs=case.expected_outputs,
        diff=diff,
        execution_metadata=execution_metadata,
        error=error_msg,
    )
    return result.model_dump()


# ------------------------------------------------------------------
# Memory
# ------------------------------------------------------------------


@router.get("/api/memory/{workflow_id}/{session_id}")
async def list_memory_keys(workflow_id: str, session_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(session_id, "session_id")
    store = get_memory_store(request)
    keys = await store.list_keys(workflow_id, session_id)
    return {"workflow_id": workflow_id, "session_id": session_id, "keys": keys}


@router.get("/api/memory/{workflow_id}/{session_id}/{key:path}")
async def read_memory_entry(
    workflow_id: str,
    session_id: str,
    key: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(session_id, "session_id")
    store = get_memory_store(request)
    entry = await store.read(workflow_id, session_id, key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Memory key '{key}' not found")
    return entry.model_dump()


@router.delete("/api/memory/{workflow_id}/{session_id}")
async def clear_session_memory(workflow_id: str, session_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(session_id, "session_id")
    store = get_memory_store(request)
    await store.clear_session(workflow_id, session_id)
    return {"status": "cleared", "workflow_id": workflow_id, "session_id": session_id}


@router.get("/api/memory/{workflow_id}")
async def list_sessions(workflow_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_memory_store(request)
    sessions = await store.list_sessions(workflow_id)
    return {"workflow_id": workflow_id, "sessions": sessions}


# ------------------------------------------------------------------
# Error memory
# ------------------------------------------------------------------


@router.get("/api/errors/{workflow_id}")
async def list_error_memory(workflow_id: str, request: Request, limit: int = 50):
    rm = get_run_manager(request)
    index = rm._get_error_memory_index()
    if index is None:
        return {"errors": [], "message": "Error memory not enabled"}
    try:
        stats = await index.stats(workflow_id)
        return {"workflow_id": workflow_id, **stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/api/errors/{workflow_id}")
async def clear_error_memory(workflow_id: str, request: Request):
    rm = get_run_manager(request)
    index = rm._get_error_memory_index()
    if index is None:
        raise HTTPException(status_code=400, detail="Error memory not enabled")
    await index.clear(workflow_id)
    return {"status": "cleared", "workflow_id": workflow_id}


@router.get("/api/errors/{workflow_id}/search")
async def search_error_memory(
    workflow_id: str,
    request: Request,
    q: str = "",
    top_k: int = 5,
):
    rm = get_run_manager(request)
    index = rm._get_error_memory_index()
    if index is None:
        raise HTTPException(status_code=400, detail="Error memory not enabled")
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    results = await index.query_similar(workflow_id, q.strip(), top_k=top_k)
    return {"workflow_id": workflow_id, "query": q, "results": results}


# ------------------------------------------------------------------
# Rules
# ------------------------------------------------------------------


@router.get("/api/rules/{workflow_id}")
async def list_generated_rules(
    workflow_id: str,
    request: Request,
    status: str | None = None,
):
    validate_path_segment(workflow_id, "workflow_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        return {"rules": [], "message": "Self-evolving rules not enabled"}
    rules = manager.list_rules(workflow_id, status=status)
    return {
        "workflow_id": workflow_id,
        "rules": [r.model_dump() for r in rules],
    }


@router.post("/api/rules/{workflow_id}/{rule_id}/disable")
async def disable_generated_rule(
    workflow_id: str,
    rule_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(rule_id, "rule_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.disable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    rm.emit_rule_lifecycle_event(workflow_id, "rule_disabled", {
        "rule_id": rule_id,
        "reason": "manual_api",
    })
    return {"status": "disabled", "rule_id": rule_id}


@router.post("/api/rules/{workflow_id}/{rule_id}/enable")
async def enable_generated_rule(
    workflow_id: str,
    rule_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(rule_id, "rule_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.enable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "enabled", "rule_id": rule_id}


@router.post("/api/rules/{workflow_id}/{rule_id}/approve")
async def approve_generated_rule(
    workflow_id: str,
    rule_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(rule_id, "rule_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.enable_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "approved", "rule_id": rule_id}


@router.delete("/api/rules/{workflow_id}/{rule_id}")
async def delete_generated_rule(
    workflow_id: str,
    rule_id: str,
    request: Request,
):
    validate_path_segment(workflow_id, "workflow_id")
    validate_path_segment(rule_id, "rule_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    if not manager.delete_rule(workflow_id, rule_id):
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found")
    return {"status": "deleted", "rule_id": rule_id}


@router.post("/api/rules/{workflow_id}/rollback")
async def rollback_generated_rules(
    workflow_id: str,
    request: Request,
    body: dict[str, Any],
):
    validate_path_segment(workflow_id, "workflow_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        raise HTTPException(status_code=400, detail="Self-evolving rules not enabled")
    before = body.get("before")
    if not before:
        raise HTTPException(status_code=400, detail="'before' timestamp is required")
    disabled = manager.rollback(workflow_id, float(before))
    return {"disabled_count": len(disabled), "disabled_rule_ids": disabled}


@router.get("/api/rules/{workflow_id}/stats")
async def generated_rules_stats(workflow_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    rm = get_run_manager(request)
    manager = rm._get_rule_lifecycle_manager()
    if manager is None:
        return {"message": "Self-evolving rules not enabled"}
    return manager.stats(workflow_id)
