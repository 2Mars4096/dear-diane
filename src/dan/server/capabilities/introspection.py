"""Introspection capability handlers: inspect_node, list_test_cases, run_test_case."""
from __future__ import annotations

from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult


_NODE_STRUCTURAL_FIELDS = {
    "id",
    "node_type",
    "type",
    "name",
    "description",
    "input_ports",
    "output_ports",
    "position",
    "ui",
    "metadata",
    "tags",
    "retry_policy",
    "read_set",
    "write_set",
    "memoize",
    "cache_ttl",
}


def _node_dump(node: Any) -> dict[str, Any]:
    if isinstance(node, dict):
        return dict(node)
    if hasattr(node, "model_dump"):
        return node.model_dump(mode="json")
    return {}


def _node_type(node: Any, raw_node: dict[str, Any]) -> str:
    return str(
        raw_node.get("node_type")
        or raw_node.get("type")
        or getattr(node, "node_type", "")
        or getattr(node, "type", "")
        or ""
    )


def _node_config(raw_node: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in raw_node.items()
        if key not in _NODE_STRUCTURAL_FIELDS
    }


async def handle_inspect_node(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    workflow_id = args.get("workflow_id")
    node_id = args.get("node_id")
    if not workflow_id or not node_id:
        return CapabilityResult(success=False, message="Missing workflow_id or node_id")

    try:
        from dan.server.variable_inspector import compute_upstream_variables

        graph = ctx.graph_store.load_as_model(workflow_id)
        if not graph:
            return CapabilityResult(success=False, message=f"Workflow not found: {workflow_id}")

        node = graph.node_by_id(node_id)
        if not node:
            return CapabilityResult(success=False, message=f"Node not found: {node_id}")

        upstream_vars = compute_upstream_variables(node_id, graph)
        raw_node = _node_dump(node)
        node_type = _node_type(node, raw_node)

        data = {
            "node_id": str(raw_node.get("id") or getattr(node, "id", node_id)),
            "name": str(raw_node.get("name") or getattr(node, "name", "")),
            "type": node_type,
            "node_type": node_type,
            "config": _node_config(raw_node),
            "input_ports": raw_node.get("input_ports", []),
            "output_ports": raw_node.get("output_ports", []),
            "upstream_variables": upstream_vars,
        }
        return CapabilityResult(success=True, message="Node inspected", data=data)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error inspecting node: {exc}")


async def handle_list_test_cases(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    workflow_id = args.get("workflow_id")
    node_id = args.get("node_id")
    if not workflow_id or not node_id:
        return CapabilityResult(success=False, message="Missing workflow_id or node_id")

    if not ctx.test_case_store:
        return CapabilityResult(success=False, message="Test case store not available")

    try:
        cases = ctx.test_case_store.list_cases(workflow_id, node_id)
        cases_data = [c.dict() if hasattr(c, "dict") else c.model_dump() for c in cases]
        return CapabilityResult(success=True, message=f"Found {len(cases)} test cases", data=cases_data)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error listing test cases: {exc}")


async def handle_run_test_case(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    workflow_id = args.get("workflow_id")
    node_id = args.get("node_id")
    case_id = args.get("case_id")

    if not all([workflow_id, node_id, case_id]):
        return CapabilityResult(success=False, message="Missing required arguments")

    if not ctx.test_case_store:
        return CapabilityResult(success=False, message="Test case store not available")

    try:
        case = ctx.test_case_store.get_case(workflow_id, node_id, case_id)
        if not case:
            return CapabilityResult(success=False, message=f"Test case {case_id} not found")

        graph = ctx.graph_store.load_as_model(workflow_id)
        if not graph:
            return CapabilityResult(success=False, message=f"Workflow not found: {workflow_id}")

        node = graph.node_by_id(node_id)
        if not node:
            return CapabilityResult(success=False, message=f"Node not found: {node_id}")

        from dan.models.graph import Graph as GraphModel

        synthetic = GraphModel(
            nodes=[node],
            entry_points=[node_id],
            exit_points=[node_id],
        )

        import time
        import asyncio
        run_id = f"test-{case_id}-{int(time.time() * 1000)}"
        await ctx.run_manager.start_run(
            synthetic,
            graph_id=workflow_id,
            inputs=case.inputs,
            run_id=run_id,
        )

        deadline = time.time() + 120
        while True:
            record = ctx.run_manager.get_run(run_id)
            if record is None or record.status in ("completed", "failed", "cancelled"):
                break
            if time.time() > deadline:
                return CapabilityResult(success=False, message="Test case execution timed out")
            await asyncio.sleep(0.5)

        actual_outputs = {}
        if record is not None and record.result is not None:
            actual_outputs = record.result.outputs.get(node_id, {})

        expected_outputs = case.expected_outputs or {}
        passed = True
        diffs = {}

        for k, v in expected_outputs.items():
            actual = actual_outputs.get(k)
            if actual != v:
                passed = False
                diffs[k] = {"expected": v, "actual": actual}

        status = record.status if record is not None else "unknown"
        data = {
            "run_id": run_id,
            "status": status,
            "passed": passed,
            "actual_outputs": actual_outputs,
            "expected_outputs": expected_outputs,
            "diffs": diffs,
        }

        return CapabilityResult(
            success=True,
            message="Test case passed" if passed else "Test case failed",
            data=data,
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error running test case: {exc}")
