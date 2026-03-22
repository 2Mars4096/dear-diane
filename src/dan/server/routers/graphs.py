"""Graph CRUD, mutation, validation, export, and node-level helpers."""

from __future__ import annotations

import os
import re
import tempfile
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dan.migration.gate_migration import maybe_migrate_graph_dict
from dan.server.routers.dependencies import get_graph_store, get_run_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class CreateGraphRequest(BaseModel):
    graph_id: str
    data: dict[str, Any] | None = None


class ApplyMutationRequest(BaseModel):
    mutation_plan: dict[str, Any]
    idempotency_key: str | None = None
    source: str | None = None


# ------------------------------------------------------------------
# Graph CRUD
# ------------------------------------------------------------------


@router.get("/api/graphs")
async def list_graphs():
    gs = get_graph_store()
    graphs = gs.list_graphs()
    last_opened = gs.get_last_opened()
    return {"graphs": graphs, "last_opened": last_opened}


@router.post("/api/graphs")
async def create_graph(req: CreateGraphRequest):
    from dan.server.graph_store import _validate_graph_id
    from dan.server.chat_manager import compute_graph_revision

    gs = get_graph_store()
    try:
        _validate_graph_id(req.graph_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if gs.get_graph(req.graph_id) is not None:
        raise HTTPException(status_code=409, detail=f"Graph '{req.graph_id}' already exists")
    data = gs.create_graph(req.graph_id, req.data)
    return {
        "graph_id": req.graph_id,
        "data": data,
        "graph_revision": compute_graph_revision(data),
    }


_layout_on_load = os.environ.get("DAN_LAYOUT_ON_LOAD", "").lower() in ("1", "true", "yes")


@router.get("/api/graphs/{graph_id}")
async def get_graph(graph_id: str, layout: bool = False):
    from dan.server.graph_store import _validate_graph_id
    from dan.server.chat_manager import compute_graph_revision

    gs = get_graph_store()
    try:
        _validate_graph_id(graph_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    data = gs.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if isinstance(data, dict):
        data = maybe_migrate_graph_dict(data)
    graph_revision = compute_graph_revision(data) if isinstance(data, dict) else None
    if (layout or _layout_on_load) and isinstance(data, dict):
        from dan.server.layout import apply_layout
        data = apply_layout(data)
    gs.set_last_opened(graph_id)
    return {
        "graph_id": graph_id,
        "data": data,
        "graph_revision": graph_revision,
    }


@router.put("/api/graphs/{graph_id}")
async def update_graph(graph_id: str, body: dict[str, Any]):
    from dan.server.graph_store import GraphSaveValidationError, _validate_graph_id
    from dan.server.chat_manager import compute_graph_revision

    gs = get_graph_store()
    try:
        _validate_graph_id(graph_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        saved = gs.save_graph(graph_id, body)
    except GraphSaveValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "graph_id": graph_id,
        "status": "saved",
        "graph_revision": compute_graph_revision(saved),
    }


@router.delete("/api/graphs/{graph_id}")
async def delete_graph(graph_id: str):
    from dan.server.graph_store import _validate_graph_id

    gs = get_graph_store()
    try:
        _validate_graph_id(graph_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not gs.delete_graph(graph_id):
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    return {"graph_id": graph_id, "status": "deleted"}


# ------------------------------------------------------------------
# Mutation
# ------------------------------------------------------------------

_applied_mutation_keys: set[tuple[str, str]] = set()
_MAX_IDEMPOTENCY_KEYS = 1000

_STRICT_MUTATION_VALIDATION = os.environ.get("DAN_STRICT_MUTATION_VALIDATION", "true").lower() == "true"


@router.post("/api/graphs/{graph_id}/apply-mutation")
async def apply_mutation(graph_id: str, req: ApplyMutationRequest):
    from dan.server.chat_manager import compute_graph_revision
    from dan.server.graph_mutator import GraphMutator, MutationPlan
    from dan.server.mutation_metrics import mutation_metrics
    from dan.models.graph import Graph
    from dan.validation.graph import validate_graph

    gs = get_graph_store()
    data = gs.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    if isinstance(data, dict):
        data = maybe_migrate_graph_dict(data)

    if req.idempotency_key:
        idem_key = (graph_id, req.idempotency_key)
        if idem_key in _applied_mutation_keys:
            return {
                "success": True,
                "new_graph": data,
                "graph_revision": compute_graph_revision(data),
                "errors": [],
                "warnings": [],
                "diagnostics": [],
                "stale_plan": False,
                "idempotent_hit": True,
            }

    try:
        plan = MutationPlan.model_validate(req.mutation_plan)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid mutation plan: {exc}") from exc

    revision = compute_graph_revision(data)
    result = GraphMutator().apply(data, plan, current_revision=revision)

    if not result.success:
        mutation_metrics.record_apply(False)
        resp: dict[str, Any] = {
            "success": False,
            "new_graph": None,
            "errors": [e.model_dump() for e in result.errors],
            "stale_plan": result.stale_plan,
        }
        if result.stale_plan:
            resp["message"] = (
                "The workflow has been modified since this plan was created. "
                "Please refresh and try again."
            )
        return resp

    if _STRICT_MUTATION_VALIDATION:
        try:
            graph = Graph.model_validate(result.new_graph)
        except Exception as exc:
            mutation_metrics.record_apply(False)
            mutation_metrics.record_validation(False)
            return {
                "success": False,
                "errors": [{"message": f"Graph parse error: {exc}"}],
                "stale_plan": False,
            }

        raw_errors = validate_graph(graph)
        warnings: list[str] = []
        fatal: list[str] = []
        for msg in raw_errors:
            lower = msg.lower()
            if any(p in lower for p in ("warning", "deprecated", "untyped")):
                warnings.append(msg)
            else:
                fatal.append(msg)

        mutation_metrics.record_validation(len(fatal) == 0)

        if fatal:
            mutation_metrics.record_apply(False)
            return {
                "success": False,
                "new_graph": None,
                "errors": [{"message": msg} for msg in fatal],
                "stale_plan": False,
            }

        mutation_metrics.record_apply(True)
        saved_graph = gs.save_graph(graph_id, result.new_graph)
        new_revision = compute_graph_revision(saved_graph)

        if req.idempotency_key:
            _applied_mutation_keys.add((graph_id, req.idempotency_key))
            if len(_applied_mutation_keys) > _MAX_IDEMPOTENCY_KEYS:
                _applied_mutation_keys.pop()

        from dan.server.app import _run_manager
        if req.source == "optimization" and _run_manager is not None:
            _run_manager.emit_optimization_applied(graph_id, {
                "graph_id": graph_id,
                "operations": len(plan.operations),
                "description": plan.description or "",
            })

        return {
            "success": True,
            "new_graph": saved_graph,
            "graph_revision": new_revision,
            "errors": [],
            "warnings": warnings,
            "diagnostics": result.diagnostics,
            "stale_plan": False,
        }
    else:
        mutation_metrics.record_apply(True)
        saved_graph = gs.save_graph(graph_id, result.new_graph)
        new_revision = compute_graph_revision(saved_graph)

        if req.idempotency_key:
            _applied_mutation_keys.add((graph_id, req.idempotency_key))
            if len(_applied_mutation_keys) > _MAX_IDEMPOTENCY_KEYS:
                _applied_mutation_keys.pop()

        from dan.server.app import _run_manager
        if req.source == "optimization" and _run_manager is not None:
            _run_manager.emit_optimization_applied(graph_id, {
                "graph_id": graph_id,
                "operations": len(plan.operations),
                "description": plan.description or "",
            })

        return {
            "success": True,
            "new_graph": saved_graph,
            "graph_revision": new_revision,
            "errors": [],
            "warnings": [],
            "diagnostics": result.diagnostics,
            "stale_plan": False,
        }


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------


@router.post("/api/graphs/{graph_id}/validate")
async def validate_graph_endpoint(graph_id: str):
    from dan.models.graph import Graph
    from dan.meta.workflow_contract import validate_workflow_build_contract
    from dan.validation.graph import validate_graph

    gs = get_graph_store()
    data = gs.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    try:
        graph = Graph.model_validate(data)
    except Exception as exc:
        return {
            "errors": [{"message": f"Graph parse error: {exc}"}],
            "warnings": [],
            "run_ready": False,
            "run_readiness_issues": [f"Graph parse error: {exc}"],
        }

    raw_errors = validate_graph(graph)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for msg in raw_errors:
        entry: dict[str, str] = {"message": msg}
        edge_match = re.search(r"(?:Edge|ContextEdge) '([^']+)'", msg)
        node_match = re.search(r"Node '([^']+)'", msg)
        cycle_match = re.search(r"non-loop node '([^']+)'", msg)
        if edge_match:
            entry["edge_id"] = edge_match.group(1)
        elif node_match:
            entry["node_id"] = node_match.group(1)
        elif cycle_match:
            entry["node_id"] = cycle_match.group(1)
        lower = msg.lower()
        is_warning = any(
            p in lower
            for p in ("schema safety bypassed", "untyped data edge", "deprecated")
        )
        if is_warning:
            warnings.append(entry)
        else:
            errors.append(entry)

    contract_report = validate_workflow_build_contract(
        graph.model_dump(mode="json"),
        workflow_id=graph_id,
        apply_repairs=False,
    )

    return {
        "errors": errors,
        "warnings": warnings,
        "run_ready": contract_report.run_ready,
        "run_readiness_issues": list(contract_report.run_readiness_issues),
    }


# ------------------------------------------------------------------
# Boundary validators / node inputs
# ------------------------------------------------------------------


@router.post("/api/graphs/{graph_id}/nodes/{node_id}/add-boundary-validators")
async def add_boundary_validators(graph_id: str, node_id: str):
    from dan.models.graph import Graph

    gs = get_graph_store()
    data = gs.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    try:
        graph = Graph.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Graph parse error: {exc}")

    target = graph.node_by_id(node_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    if not getattr(target, "external_input_schema", None) and not getattr(target, "external_output_schema", None):
        raise HTTPException(
            status_code=422,
            detail=f"Node '{node_id}' has no external_input_schema or external_output_schema — nothing to validate",
        )

    from dan.validation.boundaries import insert_boundary_validators
    new_graph = insert_boundary_validators(graph, node_id)

    gs.save_graph(graph_id, new_graph.model_dump(mode="json"))
    return {"graph_id": graph_id, "node_id": node_id, "status": "validators_inserted"}


@router.get("/api/graphs/{graph_id}/nodes/{node_id}/inputs")
async def get_node_inputs(graph_id: str, node_id: str, run_id: str | None = None):
    from dan.models.graph import Graph

    gs = get_graph_store()
    data = gs.get_graph(graph_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    try:
        graph = Graph.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Graph parse error: {exc}")

    target = graph.node_by_id(node_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found in graph")

    from dan.server.variable_inspector import compute_upstream_variables

    variables = compute_upstream_variables(node_id, graph)

    runtime_values: dict[str, Any] = {}
    if run_id:
        rm = get_run_manager()
        record = rm.get_run(run_id)
        if record is not None and rm.run_store is not None:
            source_node_ids = {v["source_node_id"] for v in variables if v.get("source_node_id")}
            for src_id in source_node_ids:
                events = rm.run_store.load_events(
                    record.graph_id, run_id, node_id=src_id, event_type="node_completed",
                )
                if events:
                    last_evt = events[-1]
                    output = last_evt.get("output") or last_evt.get("outputs") or last_evt.get("data", {}).get("output")
                    if output is not None:
                        runtime_values[src_id] = output

    for var in variables:
        src_id = var.get("source_node_id")
        if src_id and src_id in runtime_values:
            src_output = runtime_values[src_id]
            if isinstance(src_output, dict) and var["source_port"] in src_output:
                var["runtime_value"] = src_output[var["source_port"]]
            else:
                var["runtime_value"] = src_output
        else:
            var["runtime_value"] = None

    return {"node_id": node_id, "graph_id": graph_id, "variables": variables}


# ------------------------------------------------------------------
# Export
# ------------------------------------------------------------------


@router.get("/api/graphs/{graph_id}/export/markdown")
async def export_graph_markdown(graph_id: str):
    from dan.loader.decompiler import decompile_to_markdown

    gs = get_graph_store()
    graph = gs.load_as_model(graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    with tempfile.TemporaryDirectory() as tmpdir:
        result = decompile_to_markdown(graph, tmpdir)
        files: list[dict[str, str]] = []
        for p in result.files:
            content = p.read_text(encoding="utf-8")
            files.append({"path": p.name, "content": content})
        diagnostics: list[dict[str, Any]] = [
            {
                "level": d.level,
                "message": d.message,
                "source_file": str(d.source_file) if d.source_file else None,
                "source_line": d.source_line,
                "source_column": d.source_column,
                "hint": d.hint,
            }
            for d in result.diagnostics
        ]
        return {"files": files, "diagnostics": diagnostics}


@router.get("/api/graphs/{graph_id}/export/python")
async def export_graph_python(graph_id: str):
    from dan.builder.decompiler import decompile as decompile_to_python

    gs = get_graph_store()
    graph = gs.load_as_model(graph_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Graph '{graph_id}' not found")
    code = decompile_to_python(graph)
    return {"code": code}


# ------------------------------------------------------------------
# Token analysis helper (used by runs router too)
# ------------------------------------------------------------------


def build_token_analysis_context(graph_id: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    from dan.models.graph import Graph

    gs = get_graph_store()
    graph_data = gs.get_graph(graph_id)
    if graph_data is None:
        return {}, []
    try:
        graph = Graph.model_validate(graph_data)
    except Exception:
        return {}, []

    node_configs: dict[str, dict[str, Any]] = {}
    for node in graph.nodes:
        tools: list[str] = []
        for tool in getattr(node, "tools", []) or []:
            if isinstance(tool, dict):
                fn = tool.get("function", {})
                name = fn.get("name")
                if isinstance(name, str) and name:
                    tools.append(name)
        node_configs[node.id] = {
            "prompt_template": getattr(node, "prompt_template", ""),
            "system_prompt": getattr(node, "system_prompt", ""),
            "input_ports": [p.name for p in getattr(node, "input_ports", [])],
            "tools": tools,
            "jit_tool_loading": bool(getattr(node, "jit_tool_loading", False)),
        }

    graph_edges: list[dict[str, Any]] = []
    for edge in graph.edges:
        graph_edges.append({
            "id": edge.id,
            "edge_type": getattr(edge, "edge_type", ""),
            "source": edge.source_node_id,
            "target": edge.target_node_id,
            "source_port": edge.source_port,
            "target_port": edge.target_port,
            "pass_by_reference": bool(getattr(edge, "pass_by_reference", False)),
        })
    return node_configs, graph_edges
