"""Workflow experience memory endpoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from dan.server.routers.dependencies import (
    get_graph_store,
    get_run_manager,
    get_experience_index,
    get_experience_store,
    get_memory_store,
    validate_path_segment,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@dataclass
class _TraceDraftBuildResult:
    response: dict[str, Any]
    contract_report: Any | None = None


def _serialize_contract_issues(issues: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "category": str(getattr(issue, "category", "")),
            "message": str(getattr(issue, "message", "")),
            "severity": str(getattr(issue, "severity", "")),
            "artifact_id": getattr(issue, "artifact_id", None),
        }
        for issue in issues
    ]


def _serialize_compile_exception(exc: Exception) -> dict[str, Any]:
    return {
        "validated": False,
        "run_ready": False,
        "graph": None,
        "warnings": [],
        "errors": [{
            "category": "compile",
            "message": str(exc),
            "severity": "fatal",
            "artifact_id": None,
        }],
        "auto_fixes_applied": [],
        "run_readiness_issues": [],
    }


def _build_trace_draft_response(draft: Any) -> dict[str, Any]:
    from dan.server.trace_workflow_distiller import (
        suggest_trace_workflow_id,
        suggest_trace_workflow_name,
    )

    return {
        "status": "draft_only",
        "draft": draft.model_dump(mode="json"),
        "promotion": {
            "suggested_workflow_id": suggest_trace_workflow_id(draft),
            "suggested_name": suggest_trace_workflow_name(draft),
        },
    }


def _compile_trace_draft(
    draft: Any,
    *,
    workflow_id: str | None = None,
) -> _TraceDraftBuildResult:
    from dan.meta.intent_compiler import IntentCompiler
    from dan.meta.workflow_contract import validate_workflow_build_contract

    response = _build_trace_draft_response(draft)

    try:
        graph = IntentCompiler().build_graph(draft.workflow_intent)
        graph_dict = graph.model_dump(mode="json")
        report = validate_workflow_build_contract(
            graph_dict,
            workflow_id=workflow_id,
            apply_repairs=True,
        )
    except Exception as exc:
        response["compiled"] = _serialize_compile_exception(exc)
        return _TraceDraftBuildResult(response=response)

    response["compiled"] = {
        "validated": report.validated,
        "run_ready": report.run_ready,
        "graph": report.graph.model_dump(mode="json") if report.graph is not None else graph_dict,
        "warnings": list(report.warnings),
        "errors": _serialize_contract_issues(report.errors),
        "auto_fixes_applied": list(report.auto_fixes_applied),
        "run_readiness_issues": list(report.run_readiness_issues),
    }
    if report.run_ready:
        response["status"] = "run_ready_draft"
    elif report.validated:
        response["status"] = "validated_draft"
    return _TraceDraftBuildResult(response=response, contract_report=report)


def _resolve_trace_promotion_workflow_id(
    draft: Any,
    *,
    requested_workflow_id: str,
    overwrite: bool,
    request: Request | None = None,
) -> tuple[str, bool]:
    from dan.server.graph_store import _validate_graph_id
    from dan.server.trace_workflow_distiller import suggest_trace_workflow_id

    graph_store = get_graph_store(request) if request is not None else get_graph_store()
    explicit = requested_workflow_id.strip()
    if explicit:
        _validate_graph_id(explicit)
        existing = graph_store.get_graph(explicit) is not None
        return explicit, existing

    base = suggest_trace_workflow_id(draft)
    _validate_graph_id(base)
    if overwrite:
        return base, graph_store.get_graph(base) is not None

    candidate = base
    suffix = 2
    while graph_store.get_graph(candidate) is not None:
        suffix_text = f"-{suffix}"
        candidate = f"{base[:64 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate, False


@router.get("/api/experiences")
async def list_experiences(request: Request):
    try:
        store = get_experience_store(request)
        experiences = await store.list_experiences()
        return {"experiences": [e.model_dump() for e in experiences]}
    except Exception as exc:
        logger.exception("Failed to list experiences")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/experiences/{workflow_id}")
async def get_experience(workflow_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_experience_store(request)
    exp = await store.load_experience(workflow_id)
    if exp is None:
        raise HTTPException(status_code=404, detail=f"No experience for '{workflow_id}'")
    return exp.model_dump()


_experience_index_bootstrap_done = False


@router.post("/api/experiences/search")
async def search_experiences(body: dict[str, Any], request: Request):
    global _experience_index_bootstrap_done
    query = str(body.get("query", "")).strip()
    top_k = int(body.get("top_k", 5))
    if not query:
        raise HTTPException(status_code=422, detail="query is required")
    index = get_experience_index(request)
    store = get_experience_store(request, with_index=True)

    if not _experience_index_bootstrap_done:
        for exp in await store.list_experiences():
            await store.save_experience(exp)
        _experience_index_bootstrap_done = True

    hits = await index.search_similar(query, top_k=max(1, min(top_k, 20)))
    results: list[dict[str, Any]] = []
    for workflow_id, score in hits:
        exp = await store.load_experience(workflow_id)
        results.append({
            "workflow_id": workflow_id,
            "score": score,
            "experience": exp.model_dump() if exp is not None else None,
        })
    return {"query": query, "results": results}


@router.post("/api/experiences/trace-draft")
async def distill_trace_draft(body: dict[str, Any]):
    turn_id = str(body.get("turn_id", "")).strip()
    compile_graph = bool(body.get("compile", True))
    if not turn_id:
        raise HTTPException(status_code=422, detail="turn_id is required")

    try:
        from dan.server.audit import ChatAuditStore
        from dan.server.trace_workflow_distiller import (
            distill_workflow_trace_from_audit,
        )
    except Exception as exc:
        logger.exception("Trace draft dependencies unavailable")
        raise HTTPException(status_code=500, detail=str(exc))

    record = ChatAuditStore().load_by_turn(turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No audit turn '{turn_id}'")

    draft = distill_workflow_trace_from_audit(record)
    if draft is None:
        return {
            "status": "no_draft",
            "reason": "Not enough meaningful audited tool activity to distill a workflow.",
        }

    response = _build_trace_draft_response(draft)
    if not compile_graph:
        return response

    return _compile_trace_draft(draft).response


@router.post("/api/experiences/trace-draft/promote")
async def promote_trace_draft(body: dict[str, Any], request: Request = None):
    turn_id = str(body.get("turn_id", "")).strip()
    requested_workflow_id = str(body.get("workflow_id", "")).strip()
    overwrite = bool(body.get("overwrite", False))
    if not turn_id:
        raise HTTPException(status_code=422, detail="turn_id is required")

    try:
        from dan.server.audit import ChatAuditStore
        from dan.server.chat_manager import compute_graph_revision
        from dan.server.graph_store import GraphSaveValidationError
        from dan.server.trace_workflow_distiller import (
            distill_workflow_trace_from_audit,
            prepare_trace_workflow_graph_for_promotion,
        )
    except Exception as exc:
        logger.exception("Trace promotion dependencies unavailable")
        raise HTTPException(status_code=500, detail=str(exc))

    record = ChatAuditStore().load_by_turn(turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No audit turn '{turn_id}'")

    draft = distill_workflow_trace_from_audit(record)
    if draft is None:
        return {
            "status": "no_draft",
            "reason": "Not enough meaningful audited tool activity to distill a workflow.",
            "promotion": {
                "saved": False,
            },
        }

    try:
        workflow_id, exists = _resolve_trace_promotion_workflow_id(
            draft,
            requested_workflow_id=requested_workflow_id,
            overwrite=overwrite,
            request=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if exists and requested_workflow_id and not overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"Graph '{workflow_id}' already exists",
        )

    build_result = _compile_trace_draft(draft, workflow_id=workflow_id)
    response = build_result.response
    response["promotion"] = {
        **response.get("promotion", {}),
        "workflow_id": workflow_id,
        "saved": False,
        "overwrote_existing": bool(exists and overwrite),
    }

    report = build_result.contract_report
    if report is None:
        response["status"] = "promotion_blocked"
        response["promotion"]["reason"] = "Distilled draft could not be compiled for promotion."
        return response
    if not report.run_ready or report.graph_dict is None:
        response["status"] = "promotion_blocked"
        response["promotion"]["reason"] = (
            "Only run-ready distilled workflows can be promoted into saved workflow artifacts."
        )
        return response

    graph_store = get_graph_store(request) if request is not None else get_graph_store()
    graph_to_save = prepare_trace_workflow_graph_for_promotion(
        report.graph_dict,
        draft,
        workflow_id=workflow_id,
    )
    try:
        if exists and overwrite:
            saved_graph = graph_store.save_graph(workflow_id, graph_to_save)
        else:
            saved_graph = graph_store.create_graph(workflow_id, graph_to_save)
    except GraphSaveValidationError as exc:
        logger.exception("Trace promotion save rejected for workflow '%s'", workflow_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Trace promotion save failed for workflow '%s'", workflow_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    response["status"] = "promoted_workflow"
    response["workflow_id"] = workflow_id
    response["graph_revision"] = compute_graph_revision(saved_graph)
    if "compiled" in response:
        response["compiled"]["graph"] = saved_graph
    response["promotion"] = {
        **response["promotion"],
        "saved": True,
        "graph_revision": response["graph_revision"],
    }
    return response


@router.post("/api/experiences/{workflow_id}/refresh")
async def refresh_experience(workflow_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    from dan.engine.experience import (
        consolidate_experience,
        extract_experience_from_graph,
    )
    from dan.engine.error_memory import PrincipleStore
    from dan.models.graph import Graph

    rm = get_run_manager(request)
    gs = get_graph_store(request)
    try:
        store = get_experience_store(request, with_index=True)
    except HTTPException:
        store = get_experience_store(request, with_index=False)
    graph_data = gs.get_graph(workflow_id)
    if graph_data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{workflow_id}' not found")

    graph = Graph.model_validate(graph_data)
    exp = await store.load_experience(workflow_id)
    if exp is None:
        exp = extract_experience_from_graph(graph)
        exp = exp.model_copy(update={"workflow_id": workflow_id})

    snapshots: list[dict[str, Any]] = []
    if rm.run_store is not None:
        snapshots = rm.run_store.list_summaries(workflow_id=workflow_id, limit=10000)

    principles: list[dict[str, Any]] = []
    try:
        ps = PrincipleStore(get_memory_store(request))
        principles = [p.model_dump() for p in await ps.load_principles(workflow_id)]
    except Exception:
        logger.debug("Failed to load principles for experience refresh", exc_info=True)

    exp = consolidate_experience(exp, snapshots, principles)
    await store.save_experience(exp)
    return {"status": "refreshed", "experience": exp.model_dump()}


@router.delete("/api/experiences/{workflow_id}")
async def delete_experience(workflow_id: str, request: Request):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_experience_store(request)
    deleted = await store.delete_experience(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Experience not found")
    return {"status": "deleted"}
