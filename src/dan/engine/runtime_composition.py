"""Validated runtime child-workflow composition and lineage helpers."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel, Field

from dan.engine.runtime_policy import stable_invocation_key
from dan.models.context import BoundaryContract
from dan.models.control_flow import ChildResultEnvelope, ChildWorkflowCall, DynamicExpansionSpec


class RuntimeCompositionError(RuntimeError):
    """Raised when a dynamic child-workflow request violates the runtime contract."""


def dynamic_topology_enabled(config: Any) -> bool:
    """Feature flag gate for validated dynamic topology."""

    import os

    if getattr(config, "dynamic_topology_enabled", False):
        return True
    return os.getenv("DAN_DYNAMIC_TOPOLOGY", "0").strip() in {"1", "true", "yes"}


def _validate_against_schema(data: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    try:
        import jsonschema

        validator = jsonschema.Draft7Validator(schema)
        return [e.message for e in validator.iter_errors(data)]
    except ImportError:
        pass

    errors: list[str] = []
    required = schema.get("required", [])
    props = schema.get("properties", {})
    for key in required:
        if key not in data:
            errors.append(f"Missing required key: '{key}'")
    for key, prop_schema in props.items():
        if key not in data:
            continue
        expected_type = prop_schema.get("type")
        if expected_type == "boolean" and not isinstance(data[key], bool):
            errors.append(f"Key '{key}' must be boolean")
        elif expected_type == "string" and not isinstance(data[key], str):
            errors.append(f"Key '{key}' must be string")
        elif expected_type == "number" and not isinstance(data[key], (int, float)):
            errors.append(f"Key '{key}' must be number")
        elif expected_type == "object" and not isinstance(data[key], dict):
            errors.append(f"Key '{key}' must be object")
        elif expected_type == "array" and not isinstance(data[key], list):
            errors.append(f"Key '{key}' must be array")
    return errors


def _resolve_boundary_contract(
    spec: DynamicExpansionSpec,
    explicit_boundary_contract: BoundaryContract | None,
) -> BoundaryContract | None:
    if explicit_boundary_contract is not None:
        return explicit_boundary_contract
    return spec.boundary_contract


def _validate_boundary_inputs(
    call: ChildWorkflowCall,
    boundary_contract: BoundaryContract | None,
) -> None:
    if boundary_contract is None or boundary_contract.accepts is None:
        return
    errors = _validate_against_schema(call.inputs, boundary_contract.accepts)
    if errors:
        raise RuntimeCompositionError(
            "Child workflow input validation failed: "
            + "; ".join(errors)
        )


def _validate_boundary_outputs(
    outputs: dict[str, Any],
    boundary_contract: BoundaryContract | None,
) -> None:
    if boundary_contract is None or boundary_contract.returns is None:
        return
    errors = _validate_against_schema(outputs, boundary_contract.returns)
    if errors:
        raise RuntimeCompositionError(
            "Child workflow output validation failed: "
            + "; ".join(errors)
        )


def _dynamic_state(state: Any) -> dict[str, Any]:
    return state.run_state.setdefault(
        "dynamic_topology",
        {
            "total_children": 0,
            "spawn_counts": {},
            "completed_calls": {},
            "records": {},
        },
    )


def register_child_record(
    state: Any,
    *,
    call: ChildWorkflowCall,
    child_run_id: str,
    parent_run_id: str,
    lineage_path: list[str],
    status: str,
    outputs: dict[str, Any] | None = None,
    error: str | None = None,
    replayed: bool = False,
) -> ChildResultEnvelope:
    """Persist child invocation lineage and result for checkpoint/replay."""

    dynamic_state = _dynamic_state(state)
    dynamic_state["total_children"] = int(dynamic_state.get("total_children", 0)) + (0 if replayed else 1)
    spawn_counts = dynamic_state.setdefault("spawn_counts", {})
    spawn_counts[call.parent_node_id] = int(spawn_counts.get(call.parent_node_id, 0)) + (0 if replayed else 1)

    envelope = ChildResultEnvelope(
        status=status,
        outputs=outputs or {},
        run_id=child_run_id,
        parent_run_id=parent_run_id,
        parent_node_id=call.parent_node_id,
        template_key=call.spec.ref,
        layer_path=list(lineage_path),
        metadata={
            "mode": call.spec.mode,
            "call_id": call.call_id,
            "replayed": replayed,
            "source": call.source,
            "spawn_policy": call.spec.spawn_policy.model_dump(),
        },
    )
    if error:
        envelope.metadata["error"] = error

    dynamic_state.setdefault("records", {})[call.call_id] = envelope.model_dump()
    if status != "failed":
        invocation_key = stable_invocation_key(call.parent_node_id, call.spec.mode, call.spec.ref, call.inputs)
        dynamic_state.setdefault("completed_calls", {})[invocation_key] = envelope.model_dump()
    return envelope


def lookup_completed_child_result(state: Any, call: ChildWorkflowCall) -> ChildResultEnvelope | None:
    """Return a cached child result for idempotent replay on resume."""

    dynamic_state = _dynamic_state(state)
    invocation_key = stable_invocation_key(call.parent_node_id, call.spec.mode, call.spec.ref, call.inputs)
    data = dynamic_state.setdefault("completed_calls", {}).get(invocation_key)
    if data is None:
        return None
    envelope = ChildResultEnvelope.model_validate(data)
    if envelope.status == "failed":
        return None
    return envelope


def validate_spawn_limits(
    state: Any,
    call: ChildWorkflowCall,
    *,
    layer_depth: int,
) -> None:
    """Enforce bounded runtime dynamic topology limits."""

    dynamic_state = _dynamic_state(state)
    spawn_policy = call.spec.spawn_policy

    if spawn_policy.max_child_depth is not None and layer_depth > int(spawn_policy.max_child_depth):
        raise RuntimeCompositionError(
            f"Child depth {layer_depth} exceeds max_child_depth={spawn_policy.max_child_depth}"
        )

    total_children = int(dynamic_state.get("total_children", 0))
    if (
        spawn_policy.max_total_children is not None
        and total_children >= int(spawn_policy.max_total_children)
    ):
        raise RuntimeCompositionError(
            "Dynamic topology limit reached: "
            f"max_total_children={spawn_policy.max_total_children}"
        )

    per_node = dynamic_state.setdefault("spawn_counts", {})
    parent_count = int(per_node.get(call.parent_node_id, 0))
    if (
        spawn_policy.max_spawns_per_node is not None
        and parent_count >= int(spawn_policy.max_spawns_per_node)
    ):
        raise RuntimeCompositionError(
            "Dynamic topology limit reached: "
            f"node '{call.parent_node_id}' exceeded max_spawns_per_node={spawn_policy.max_spawns_per_node}"
        )


async def invoke_child_workflow(
    *,
    call: ChildWorkflowCall,
    state: Any,
    parent_run_id: str,
    layer_path: tuple[str, ...],
    boundary_contract: BoundaryContract | None,
    run_child: Any,
    emit_event: Any | None = None,
) -> ChildResultEnvelope:
    """Invoke a validated child workflow and persist replay-safe lineage."""

    cached = lookup_completed_child_result(state, call)
    if cached is not None:
        cached.metadata["replayed"] = True
        return cached

    resolved_boundary = _resolve_boundary_contract(call.spec, boundary_contract)
    child_run_id = f"{parent_run_id}:{call.parent_node_id}:{call.call_id}"
    lineage_path = list(layer_path) + [call.parent_node_id, call.spec.ref]

    started_at = time.time()
    try:
        validate_spawn_limits(state, call, layer_depth=len(layer_path) + 1)
        _validate_boundary_inputs(call, resolved_boundary)
        timeout_seconds = call.spec.spawn_policy.timeout_seconds
        if emit_event is not None:
            await emit_event(
                "child_workflow_started",
                {
                    "child_run_id": child_run_id,
                    "parent_node_id": call.parent_node_id,
                    "template_key": call.spec.ref,
                    "mode": call.spec.mode,
                    "call_id": call.call_id,
                    "timeout_seconds": timeout_seconds,
                },
            )
        run_child_coro = run_child(
            call.spec.ref,
            call.inputs,
            parent_node_id=call.parent_node_id,
            boundary_contract=resolved_boundary,
            child_run_id=child_run_id,
            mode=call.spec.mode,
        )
        if timeout_seconds is not None:
            try:
                outputs = await asyncio.wait_for(run_child_coro, timeout=float(timeout_seconds))
            except asyncio.TimeoutError as exc:
                raise RuntimeCompositionError(
                    f"Child workflow timed out after {float(timeout_seconds):g}s"
                ) from exc
        else:
            outputs = await run_child_coro
        _validate_boundary_outputs(outputs, resolved_boundary)
        envelope = register_child_record(
            state,
            call=call,
            child_run_id=child_run_id,
            parent_run_id=parent_run_id,
            lineage_path=lineage_path,
            status="completed",
            outputs=outputs,
        )
        envelope.metadata["elapsed_seconds"] = round(time.time() - started_at, 3)
        if emit_event is not None:
            await emit_event(
                "child_workflow_completed",
                {
                    "child_run_id": child_run_id,
                    "parent_node_id": call.parent_node_id,
                    "template_key": call.spec.ref,
                    "call_id": call.call_id,
                    "elapsed_seconds": envelope.metadata["elapsed_seconds"],
                },
            )
        return envelope
    except Exception as exc:
        envelope = register_child_record(
            state,
            call=call,
            child_run_id=child_run_id,
            parent_run_id=parent_run_id,
            lineage_path=lineage_path,
            status="failed",
            error=str(exc),
        )
        envelope.metadata["elapsed_seconds"] = round(time.time() - started_at, 3)
        if emit_event is not None:
            await emit_event(
                "child_workflow_failed",
                {
                    "child_run_id": child_run_id,
                    "parent_node_id": call.parent_node_id,
                    "template_key": call.spec.ref,
                    "call_id": call.call_id,
                    "error": str(exc),
                    "elapsed_seconds": envelope.metadata["elapsed_seconds"],
                },
            )
        raise RuntimeCompositionError(str(exc)) from exc
