"""Workflow experience memory endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

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


@router.get("/api/experiences")
async def list_experiences():
    try:
        store = get_experience_store()
        experiences = await store.list_experiences()
        return {"experiences": [e.model_dump() for e in experiences]}
    except Exception as exc:
        logger.exception("Failed to list experiences")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/experiences/{workflow_id}")
async def get_experience(workflow_id: str):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_experience_store()
    exp = await store.load_experience(workflow_id)
    if exp is None:
        raise HTTPException(status_code=404, detail=f"No experience for '{workflow_id}'")
    return exp.model_dump()


_experience_index_bootstrap_done = False


@router.post("/api/experiences/search")
async def search_experiences(body: dict[str, Any]):
    global _experience_index_bootstrap_done
    query = str(body.get("query", "")).strip()
    top_k = int(body.get("top_k", 5))
    if not query:
        raise HTTPException(status_code=422, detail="query is required")
    index = get_experience_index()
    store = get_experience_store(with_index=True)

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
        from dan.meta.intent_compiler import IntentCompiler
        from dan.meta.workflow_contract import validate_workflow_build_contract
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

    response: dict[str, Any] = {
        "status": "draft_only",
        "draft": draft.model_dump(mode="json"),
    }
    if not compile_graph:
        return response

    try:
        graph = IntentCompiler().build_graph(draft.workflow_intent)
        graph_dict = graph.model_dump(mode="json")
        report = validate_workflow_build_contract(graph_dict, apply_repairs=True)
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
    except Exception as exc:
        response["compiled"] = {
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
    return response


@router.post("/api/experiences/{workflow_id}/refresh")
async def refresh_experience(workflow_id: str):
    validate_path_segment(workflow_id, "workflow_id")
    from dan.engine.experience import (
        consolidate_experience,
        extract_experience_from_graph,
    )
    from dan.engine.error_memory import PrincipleStore
    from dan.models.graph import Graph

    rm = get_run_manager()
    gs = get_graph_store()
    try:
        store = get_experience_store(with_index=True)
    except HTTPException:
        store = get_experience_store(with_index=False)
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
        ps = PrincipleStore(get_memory_store())
        principles = [p.model_dump() for p in await ps.load_principles(workflow_id)]
    except Exception:
        logger.debug("Failed to load principles for experience refresh", exc_info=True)

    exp = consolidate_experience(exp, snapshots, principles)
    await store.save_experience(exp)
    return {"status": "refreshed", "experience": exp.model_dump()}


@router.delete("/api/experiences/{workflow_id}")
async def delete_experience(workflow_id: str):
    validate_path_segment(workflow_id, "workflow_id")
    store = get_experience_store()
    deleted = await store.delete_experience(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Experience not found")
    return {"status": "deleted"}
