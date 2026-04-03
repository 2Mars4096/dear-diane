"""Typed node-worker planning contracts for structured workflow generation."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import re
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from dan.graph_mutator import TOOL_PORT_MANIFESTS
from dan.meta.workflow_spec import (
    ExecutionFamily,
    PortBindingSourceKind,
    PortBindingSpec,
    RunnableTestSpec,
    WorkflowPortSpec,
    WorkflowSpec,
    WorkflowSpecNode,
)
from dan.server.agent_runtime.workflow_sectioning import node_section_map, partition_workflow_spec
from dan.tools import get_all_tools

__all__ = [
    "NodeExecutorKind",
    "NodeGroundingCheck",
    "NodePlan",
    "NodePlanIssue",
    "NodePlanResult",
    "NodeWorkerRuntimeConfig",
    "build_node_plan",
    "build_node_plans",
    "build_node_worker_results",
]


_EXTERNAL_ACTION_VERBS = frozenset({"fetch", "search", "read", "write", "save", "browse", "scrape"})
_GENERIC_INPUT_NAMES = frozenset({"input", "data", "items", "query"})
_DEFAULT_TIMEOUT_FLOOR_SECONDS = 5.0


class NodeExecutorKind(str, Enum):
    """Executor classification resolved before runtime graph materialization."""

    llm = "llm"
    tool = "tool"
    code = "code"
    control_flow = "control_flow"


class NodePlanIssue(BaseModel):
    """Deterministic validation or planning issue."""

    code: str
    message: str
    severity: str = "error"
    node_id: str | None = None


class NodeGroundingCheck(BaseModel):
    """Single node-level grounding validation outcome."""

    name: str
    passed: bool
    detail: str


class NodePlan(BaseModel):
    """Serializable node plan ready for later section assembly."""

    node_id: str
    section_id: str
    node_type: str
    execution_family: ExecutionFamily
    executor_kind: NodeExecutorKind
    purpose: str
    dependencies: list[str] = Field(default_factory=list)
    executor_config: dict[str, Any] = Field(default_factory=dict)
    input_ports: list[WorkflowPortSpec] = Field(default_factory=list)
    output_ports: list[WorkflowPortSpec] = Field(default_factory=list)
    input_bindings: list[PortBindingSpec] = Field(default_factory=list)
    grounding_checks: list[NodeGroundingCheck] = Field(default_factory=list)
    test_contracts: list[RunnableTestSpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class NodePlanResult(BaseModel):
    """Node-worker output envelope with timing and issues."""

    node_id: str
    accepted: bool
    plan: NodePlan | None = None
    issues: list[NodePlanIssue] = Field(default_factory=list)
    elapsed_ms: float = 0.0


class NodeWorkerRuntimeConfig(BaseModel):
    """Runtime knobs for deterministic node planning."""

    worker_pool_cap: int = 8
    node_worker_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls, *, expected_node_count: int = 1) -> NodeWorkerRuntimeConfig:
        """Resolve worker-planning limits from ``DAN_*`` environment variables."""

        worker_pool_cap = _read_int_env("DAN_STRUCTURED_WORKER_POOL_CAP", 8, minimum=1)
        raw_timeout = os.environ.get("DAN_STRUCTURED_NODE_WORKER_TIMEOUT", "").strip()
        if raw_timeout:
            try:
                timeout_seconds = max(float(raw_timeout), _DEFAULT_TIMEOUT_FLOOR_SECONDS)
            except ValueError:
                timeout_seconds = _derived_worker_timeout(expected_node_count)
        else:
            timeout_seconds = _derived_worker_timeout(expected_node_count)
        return cls(
            worker_pool_cap=worker_pool_cap,
            node_worker_timeout_seconds=round(timeout_seconds, 3),
        )


def build_node_plans(
    workflow_spec: WorkflowSpec,
    *,
    section_ids_by_node: dict[str, str] | None = None,
    runtime_config: NodeWorkerRuntimeConfig | None = None,
) -> list[NodePlanResult]:
    """Build deterministic node plans for an entire workflow spec."""

    runtime_config = runtime_config or NodeWorkerRuntimeConfig.from_env(
        expected_node_count=max(len(workflow_spec.nodes), 1)
    )
    if section_ids_by_node is None:
        sections = partition_workflow_spec(workflow_spec)
        section_ids_by_node = node_section_map(sections)
    node_lookup = {node.node_id: node for node in workflow_spec.nodes}
    if len(workflow_spec.nodes) <= 1 or runtime_config.worker_pool_cap <= 1:
        return [
            build_node_plan(
                workflow_spec,
                node,
                node_lookup=node_lookup,
                section_id=section_ids_by_node.get(node.node_id),
                runtime_config=runtime_config,
            )
            for node in workflow_spec.nodes
        ]
    max_workers = min(runtime_config.worker_pool_cap, len(workflow_spec.nodes))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                build_node_plan,
                workflow_spec,
                node,
                node_lookup=node_lookup,
                section_id=section_ids_by_node.get(node.node_id),
                runtime_config=runtime_config,
            )
            for node in workflow_spec.nodes
        ]
        return [future.result() for future in futures]


async def build_node_worker_results(
    workflow_spec: WorkflowSpec,
    sections: list[Any],
    *,
    stage_payload_by_node: dict[str, dict[str, Any]] | None = None,
) -> list[NodePlanResult]:
    """Async compatibility wrapper used by the structured runtime."""

    del stage_payload_by_node
    section_ids_by_node = node_section_map(sections)
    runtime_config = NodeWorkerRuntimeConfig.from_env(
        expected_node_count=max(len(workflow_spec.nodes), 1)
    )
    return await asyncio.to_thread(
        build_node_plans,
        workflow_spec,
        section_ids_by_node=section_ids_by_node,
        runtime_config=runtime_config,
    )


def build_node_plan(
    workflow_spec: WorkflowSpec,
    node: WorkflowSpecNode,
    *,
    node_lookup: dict[str, WorkflowSpecNode] | None = None,
    section_id: str | None = None,
    runtime_config: NodeWorkerRuntimeConfig | None = None,
) -> NodePlanResult:
    """Turn a typed ``WorkflowSpecNode`` into a deterministic serializable node plan."""

    started = time.perf_counter()
    runtime_config = runtime_config or NodeWorkerRuntimeConfig.from_env(
        expected_node_count=max(len(workflow_spec.nodes), 1)
    )
    node_lookup = node_lookup or {item.node_id: item for item in workflow_spec.nodes}
    issues: list[NodePlanIssue] = []
    warnings: list[str] = []

    resolved_section_id = (
        str(section_id or node.section_id or node.section_label or node.chapter_label or "section-unassigned").strip()
        or "section-unassigned"
    )
    executor_kind = NodeExecutorKind(node.execution_family.value)
    tools = _safe_tool_registry()
    input_ports = _build_input_ports(node, tools=tools)
    output_ports = _build_output_ports(node, tools=tools)
    input_bindings = _resolve_input_bindings(
        workflow_spec,
        node,
        node_lookup=node_lookup,
        input_ports=input_ports,
        tools=tools,
    )
    grounding_checks = _run_grounding_checks(
        workflow_spec,
        node,
        node_lookup=node_lookup,
        input_ports=input_ports,
        output_ports=output_ports,
        input_bindings=input_bindings,
        tools=tools,
    )
    for check in grounding_checks:
        if not check.passed:
            issues.append(
                NodePlanIssue(
                    code=_slugify(check.name),
                    message=check.detail,
                    severity="error",
                    node_id=node.node_id,
                )
            )

    executor_config = _build_executor_config(
        node,
        tools=tools,
        input_ports=input_ports,
        output_ports=output_ports,
    )
    if executor_kind == NodeExecutorKind.code and not str(executor_config.get("code") or "").strip():
        issues.append(
            NodePlanIssue(
                code="missing_executable_code",
                message=f"{node.node_type} node {node.node_id!r} must provide non-empty config.code",
                node_id=node.node_id,
            )
        )
    if executor_kind == NodeExecutorKind.tool and not tools.get(
        str(node.grounding.tool_id or next(iter(_worker_tool_ids(node)), "")).strip()
    ):
        issues.append(
            NodePlanIssue(
                code="unknown_tool",
                message=(
                    f"{node.node_type} node {node.node_id!r} references unknown tool_id "
                    f"{str(node.grounding.tool_id or next(iter(_worker_tool_ids(node)), '')).strip()!r}"
                ),
                node_id=node.node_id,
            )
        )

    if not input_ports and node.inputs:
        warnings.append(f"Node {node.node_id!r} declares inputs but no concrete input ports were inferred.")

    plan = None
    if not any(issue.severity == "error" for issue in issues):
        plan = NodePlan(
            node_id=node.node_id,
            section_id=resolved_section_id,
            node_type=node.node_type,
            execution_family=node.execution_family,
            executor_kind=executor_kind,
            purpose=node.purpose,
            dependencies=list(node.dependencies),
            executor_config=executor_config,
            input_ports=input_ports,
            output_ports=output_ports,
            input_bindings=input_bindings,
            grounding_checks=grounding_checks,
            test_contracts=_build_test_contracts(
                workflow_spec,
                node,
                input_ports=input_ports,
                output_ports=output_ports,
                input_bindings=input_bindings,
            ),
            metadata={
                "parallel_safe": True,
                "worker_pool_cap": runtime_config.worker_pool_cap,
                "timeout_seconds": runtime_config.node_worker_timeout_seconds,
                "failure_risk": node.worker_hints.failure_risk.value,
                "test_intent": node.worker_hints.test_intent,
                "expected_side_effects": list(node.worker_hints.expected_side_effects),
            },
            warnings=warnings,
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return NodePlanResult(
        node_id=node.node_id,
        accepted=plan is not None,
        plan=plan,
        issues=issues,
        elapsed_ms=elapsed_ms,
    )


def _build_input_ports(
    node: WorkflowSpecNode,
    *,
    tools: dict[str, tuple[Any, dict[str, Any]]],
) -> list[WorkflowPortSpec]:
    explicit = _coerce_port_specs(node.config.get("input_ports"), required_default=True)
    if explicit:
        return explicit

    required_tool_args = _required_tool_args(str(node.grounding.tool_id or "").strip(), tools=tools)
    bound_args = {
        key
        for key, value in dict(node.grounding.bound_arguments).items()
        if value not in (None, "", [], {})
    }
    input_names = _ordered_unique(
        list(node.inputs) + [arg for arg in sorted(required_tool_args) if arg not in bound_args]
    )
    if not input_names:
        manifest_ports = _manifest_input_ports(node)
        if manifest_ports:
            return manifest_ports
        return []
    return [
        WorkflowPortSpec(
            name=name,
            required=name not in _GENERIC_INPUT_NAMES or bool(node.inputs),
            aliases=_default_input_aliases(node, name),
        )
        for name in input_names
    ]


def _build_output_ports(
    node: WorkflowSpecNode,
    *,
    tools: dict[str, tuple[Any, dict[str, Any]]],
) -> list[WorkflowPortSpec]:
    explicit = _coerce_port_specs(node.config.get("output_ports"), required_default=True)
    if explicit:
        return explicit

    output_names = _ordered_unique(node.outputs)
    if output_names:
        return [
            WorkflowPortSpec(
                name=name,
                required=False,
                aliases=_default_output_aliases(node, name),
            )
            for name in output_names
        ]
    manifest_ports = _manifest_output_ports(node)
    if manifest_ports:
        return manifest_ports
    if (
        node.node_type == "tool_operator"
        and tools.get(str(node.grounding.tool_id or "").strip())
    ) or (
        node.node_type == "worker"
        and node.execution_family == ExecutionFamily.tool
        and any(tool_id in tools for tool_id in _worker_tool_ids(node))
    ):
        return [WorkflowPortSpec(name="result", required=False, aliases=["output"])]
    return _default_output_ports(node)


def _resolve_input_bindings(
    workflow_spec: WorkflowSpec,
    node: WorkflowSpecNode,
    *,
    node_lookup: dict[str, WorkflowSpecNode],
    input_ports: list[WorkflowPortSpec],
    tools: dict[str, tuple[Any, dict[str, Any]]],
) -> list[PortBindingSpec]:
    bindings: list[PortBindingSpec] = []
    bound_arguments = dict(node.grounding.bound_arguments)
    dependencies = [node_lookup[item] for item in node.dependencies if item in node_lookup]
    global_inputs = set(workflow_spec.global_inputs)

    for port in input_ports:
        if port.name in bound_arguments and bound_arguments[port.name] not in (None, "", [], {}):
            bindings.append(
                PortBindingSpec(
                    input_port=port.name,
                    source_kind=PortBindingSourceKind.literal,
                    value=bound_arguments[port.name],
                    description=f"Bound literal for {port.name}",
                )
            )
            continue
        producer = _matching_dependency_output(port, dependencies)
        if producer is not None:
            producer_node, producer_port = producer
            bindings.append(
                PortBindingSpec(
                    input_port=port.name,
                    source_kind=PortBindingSourceKind.dependency_output,
                    source_node_id=producer_node.node_id,
                    source_port=producer_port,
                    description=f"Dependency output {producer_node.node_id}.{producer_port}",
                )
            )
            continue
        matched_global_input = _matching_global_input(port, global_inputs)
        if matched_global_input is not None:
            bindings.append(
                PortBindingSpec(
                    input_port=port.name,
                    source_kind=PortBindingSourceKind.global_input,
                    source_artifact=matched_global_input,
                    description=f"Workflow global input {matched_global_input}",
                )
            )
            continue
        if node.grounding.data_sources:
            bindings.append(
                PortBindingSpec(
                    input_port=port.name,
                    source_kind=PortBindingSourceKind.external_data,
                    source_artifact=node.grounding.data_sources[0].name,
                    description=f"External data source {node.grounding.data_sources[0].name}",
                )
            )
            continue
        if _should_allow_implicit_binding(node, port.name, tools=tools):
            bindings.append(
                PortBindingSpec(
                    input_port=port.name,
                    source_kind=PortBindingSourceKind.implicit,
                    description=f"Implicit runtime value for {port.name}",
                )
            )
    return bindings


def _run_grounding_checks(
    workflow_spec: WorkflowSpec,
    node: WorkflowSpecNode,
    *,
    node_lookup: dict[str, WorkflowSpecNode],
    input_ports: list[WorkflowPortSpec],
    output_ports: list[WorkflowPortSpec],
    input_bindings: list[PortBindingSpec],
    tools: dict[str, tuple[Any, dict[str, Any]]],
) -> list[NodeGroundingCheck]:
    checks: list[NodeGroundingCheck] = []
    tool_id = str(node.grounding.tool_id or "").strip()

    if node.node_type == "tool_operator" or (
        node.node_type == "worker" and node.execution_family == ExecutionFamily.tool
    ):
        resolved_tool_id = tool_id or next(iter(_worker_tool_ids(node)), "")
        checks.append(
            NodeGroundingCheck(
                name="tool registry lookup",
                passed=bool(resolved_tool_id and resolved_tool_id in tools),
                detail=(
                    f"{node.node_type} node {node.node_id!r} resolved registered tool_id {resolved_tool_id!r}"
                    if resolved_tool_id and resolved_tool_id in tools
                    else f"{node.node_type} node {node.node_id!r} references unknown tool_id {resolved_tool_id!r}"
                ),
            )
        )
    if node.node_type == "code_operator" or (
        node.node_type == "worker" and node.execution_family == ExecutionFamily.code
    ):
        code = str(node.config.get("code") or "").strip()
        checks.append(
            NodeGroundingCheck(
                name="code presence",
                passed=bool(code),
                detail=(
                    f"{node.node_type} node {node.node_id!r} carries executable code"
                    if code
                    else f"{node.node_type} node {node.node_id!r} must provide non-empty config.code"
                ),
            )
        )
    declared_actions = {
        action.strip().lower()
        for action in node.grounding.declared_actions
        if action.strip()
    }
    if (
        node.node_type == "llm_operator"
        or (node.node_type == "worker" and node.execution_family == ExecutionFamily.llm)
    ) and declared_actions & _EXTERNAL_ACTION_VERBS:
        checks.append(
            NodeGroundingCheck(
                name="llm external action grounding",
                passed=bool(tool_id),
                detail=(
                    f"{node.node_type} node {node.node_id!r} declares external actions with tool binding {tool_id!r}"
                    if tool_id
                    else f"{node.node_type} node {node.node_id!r} declares external actions but has no tool binding"
                ),
            )
        )
    if node.grounding.requires_external_data:
        has_data_source = bool(node.grounding.data_sources or workflow_spec.data_sources)
        checks.append(
            NodeGroundingCheck(
                name="external data reachability",
                passed=has_data_source or bool(tool_id),
                detail=(
                    f"Node {node.node_id!r} declares reachable external data"
                    if has_data_source or bool(tool_id)
                    else f"Node {node.node_id!r} requires external data but declares no data source or tool"
                ),
            )
        )

    binding_by_port = {binding.input_port: binding for binding in input_bindings}
    for port in input_ports:
        binding = binding_by_port.get(port.name)
        if binding is None:
            checks.append(
                NodeGroundingCheck(
                    name=f"reachable input {port.name}",
                    passed=False,
                    detail=f"Node {node.node_id!r} input {port.name!r} has no reachable binding",
                )
            )
            continue
        if binding.source_kind == PortBindingSourceKind.dependency_output:
            producer = node_lookup.get(str(binding.source_node_id or ""))
            producer_outputs = {item for item in getattr(producer, "outputs", []) if item} if producer else set()
            passed = bool(producer and str(binding.source_port or "") in producer_outputs)
            detail = (
                f"Input {port.name!r} is reachable from dependency {binding.source_node_id}.{binding.source_port}"
                if passed
                else f"Input {port.name!r} depends on unreachable output {binding.source_node_id}.{binding.source_port}"
            )
            checks.append(NodeGroundingCheck(name=f"dependency reachability {port.name}", passed=passed, detail=detail))
        elif binding.source_kind == PortBindingSourceKind.external_data:
            checks.append(
                NodeGroundingCheck(
                    name=f"external source {port.name}",
                    passed=True,
                    detail=f"Input {port.name!r} is grounded by external source {binding.source_artifact!r}",
                )
            )
        else:
            checks.append(
                NodeGroundingCheck(
                    name=f"binding {port.name}",
                    passed=True,
                    detail=f"Input {port.name!r} is bound via {binding.source_kind.value}",
                )
            )

    if not output_ports:
        checks.append(
            NodeGroundingCheck(
                name="output contract",
                passed=False,
                detail=f"Node {node.node_id!r} exposes no output ports",
            )
        )
    return checks


def _build_executor_config(
    node: WorkflowSpecNode,
    *,
    tools: dict[str, tuple[Any, dict[str, Any]]],
    input_ports: list[WorkflowPortSpec],
    output_ports: list[WorkflowPortSpec],
) -> dict[str, Any]:
    config = dict(node.config)
    if node.node_type == "llm_operator":
        llm_tools: list[dict[str, Any]] = []
        tool_id = str(node.grounding.tool_id or "").strip()
        if tool_id and tool_id in tools:
            llm_tools.append(_tool_schema(tool_id, tools=tools))
        return {
            "model": str(config.get("model") or "gpt-5-mini"),
            "prompt_template": str(config.get("prompt_template") or node.purpose).strip(),
            "system_prompt": str(config.get("system_prompt") or "").strip(),
            "temperature": float(config.get("temperature", 0.2)),
            "tools": llm_tools,
        }
    if node.node_type == "worker":
        role = str(config.get("role") or "").strip()
        instruction = str(config.get("instruction") or node.purpose).strip()
        persona = str(config.get("persona") or "").strip()
        llm_hints = _worker_llm_hints(node)
        tool_ids = _worker_tool_ids(node)
        if node.execution_family == ExecutionFamily.llm:
            llm_tools: list[dict[str, Any]] = []
            tool_id = str(node.grounding.tool_id or next(iter(tool_ids), "")).strip()
            if tool_id and tool_id in tools:
                llm_tools.append(_tool_schema(tool_id, tools=tools))
            return {
                "role": role,
                "instruction": instruction,
                "persona": persona,
                "tool_ids": tool_ids,
                "model": str(config.get("model") or "gpt-5-mini"),
                "prompt_template": str(
                    llm_hints.get("prompt_template")
                    or config.get("prompt_template")
                    or config.get("prompt")
                    or node.purpose
                ).strip(),
                "system_prompt": str(
                    llm_hints.get("system_prompt")
                    or config.get("system_prompt")
                    or ""
                ).strip(),
                "temperature": float(llm_hints.get("temperature", config.get("temperature", 0.2))),
                "max_tokens": llm_hints.get("max_tokens"),
                "task_tier": llm_hints.get("task_tier"),
                "tools": llm_tools,
            }
        if node.execution_family == ExecutionFamily.tool:
            return {
                "role": role,
                "instruction": instruction,
                "persona": persona,
                "tool_id": str(node.grounding.tool_id or next(iter(tool_ids), "")).strip(),
                "tool_ids": tool_ids,
                "tool_config": dict(node.grounding.bound_arguments or config.get("tool_config", {}) or {}),
            }
        if node.execution_family == ExecutionFamily.code:
            return {
                "role": role,
                "instruction": instruction,
                "persona": persona,
                "code": str(config.get("code") or "").rstrip(),
                "language": str(config.get("language") or "python").strip() or "python",
                "sandbox_config": dict(config.get("sandbox_config", {}) or {}),
            }
        config.setdefault("declared_input_ports", [item.name for item in input_ports])
        config.setdefault("declared_output_ports", [item.name for item in output_ports])
        return config
    if node.node_type == "tool_operator":
        return {
            "tool_id": str(node.grounding.tool_id or "").strip(),
            "tool_config": dict(node.grounding.bound_arguments),
        }
    if node.node_type == "code_operator":
        return {
            "operation_type": str(node.grounding.operation_type or "code_execution").strip(),
            "code": str(config.get("code") or "").rstrip(),
            "language": str(config.get("language") or "python").strip() or "python",
            "sandbox_config": dict(config.get("sandbox_config", {}) or {}),
        }
    config.setdefault("declared_input_ports", [item.name for item in input_ports])
    config.setdefault("declared_output_ports", [item.name for item in output_ports])
    return config


def _build_test_contracts(
    workflow_spec: WorkflowSpec,
    node: WorkflowSpecNode,
    *,
    input_ports: list[WorkflowPortSpec],
    output_ports: list[WorkflowPortSpec],
    input_bindings: list[PortBindingSpec],
) -> list[RunnableTestSpec]:
    required_inputs = [port.name for port in input_ports if port.required]
    expected_outputs = [port.name for port in output_ports]
    binding_kinds = {binding.input_port: binding.source_kind.value for binding in input_bindings}
    return [
        RunnableTestSpec(
            test_id=f"{node.node_id}.contract",
            kind="contract_validation",
            required_inputs=required_inputs,
            expected_outputs=expected_outputs,
            assertions=[
                f"All required inputs for {node.node_id} are declared",
                f"All expected outputs for {node.node_id} are reachable",
            ],
            fixtures={"binding_kinds": binding_kinds},
        ),
        RunnableTestSpec(
            test_id=f"{node.node_id}.smoke",
            kind=node.worker_hints.test_intent,
            required_inputs=required_inputs,
            expected_outputs=expected_outputs,
            assertions=list(node.test_expectations) or [f"{node.node_id} completes"],
            fixtures={
                "global_inputs": [item for item in required_inputs if item in workflow_spec.global_inputs],
                "side_effects": list(node.worker_hints.expected_side_effects),
            },
        ),
    ]


def _default_output_ports(node: WorkflowSpecNode) -> list[WorkflowPortSpec]:
    defaults = {
        "llm_operator": ["text"],
        "tool_operator": ["result"],
        "code_operator": ["result"],
        "gate": ["true", "false"],
        "for_each": ["results"],
        "while_loop": ["result"],
        "router": ["route"],
        "input": ["input"],
        "rag_operator": ["chunks", "scores"],
        "human": ["response"],
        "validator": ["valid", "invalid"],
        "reflection": ["principles", "principle_count", "source", "text"],
        "vote": ["winner", "winner_model", "winner_index", "all_votes"],
    }
    if node.node_type == "worker":
        port_names = ["text"] if node.execution_family == ExecutionFamily.llm else ["result"]
    else:
        port_names = defaults.get(node.node_type, ["output"])
    return [
        WorkflowPortSpec(
            name=name,
            required=False,
            aliases=_default_output_aliases(node, name),
        )
        for name in port_names
    ]


def _manifest_input_ports(node: WorkflowSpecNode) -> list[WorkflowPortSpec]:
    manifest = TOOL_PORT_MANIFESTS.get(str(node.grounding.tool_id or "").strip())
    if not manifest:
        if node.node_type == "for_each":
            return [WorkflowPortSpec(name="items", required=False)]
        if node.node_type in {"llm_operator", "tool_operator", "code_operator", "gate"}:
            return [WorkflowPortSpec(name="input", required=False)]
        if node.node_type == "worker" and node.execution_family in {
            ExecutionFamily.llm,
            ExecutionFamily.tool,
            ExecutionFamily.code,
        }:
            return [WorkflowPortSpec(name="input", required=False)]
        return []
    return _coerce_port_specs(manifest[0], required_default=False)


def _manifest_output_ports(node: WorkflowSpecNode) -> list[WorkflowPortSpec]:
    manifest = TOOL_PORT_MANIFESTS.get(str(node.grounding.tool_id or "").strip())
    if not manifest:
        return []
    return _coerce_port_specs(manifest[1], required_default=False)


def _coerce_port_specs(raw_ports: Any, *, required_default: bool) -> list[WorkflowPortSpec]:
    if not isinstance(raw_ports, list):
        return []
    ports: list[WorkflowPortSpec] = []
    seen: set[str] = set()
    for item in raw_ports:
        if isinstance(item, WorkflowPortSpec):
            port = item
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            port = WorkflowPortSpec(
                name=name,
                json_schema=dict(item.get("json_schema") or item.get("schema") or {}),
                required=bool(item.get("required", required_default)),
                description=str(item.get("description") or "").strip(),
                aliases=[
                    str(alias).strip()
                    for alias in item.get("aliases", []) or []
                    if str(alias).strip()
                ],
            )
        else:
            name = str(item).strip()
            if not name or name in seen:
                continue
            port = WorkflowPortSpec(name=name, required=required_default)
        if port.name in seen:
            continue
        seen.add(port.name)
        ports.append(port)
    return ports


def _matching_dependency_output(
    port: WorkflowPortSpec,
    dependencies: list[WorkflowSpecNode],
) -> tuple[WorkflowSpecNode, str] | None:
    candidate_names = _ordered_unique([port.name, *list(port.aliases)])
    for dependency in dependencies:
        for candidate in candidate_names:
            if candidate in dependency.outputs:
                return dependency, candidate
    if len(dependencies) == 1:
        dependency = dependencies[0]
        dependency_outputs = _ordered_unique(dependency.outputs)
        if len(dependency_outputs) == 1:
            return dependency, dependency_outputs[0]
    return None


def _matching_global_input(
    port: WorkflowPortSpec,
    global_inputs: set[str],
) -> str | None:
    candidates = [port.name, *list(port.aliases)]
    for candidate in candidates:
        if candidate in global_inputs:
            return candidate
    for global_input in sorted(global_inputs):
        normalized_global = global_input.lower()
        for candidate in candidates:
            normalized_candidate = candidate.lower()
            if (
                normalized_global.endswith(f"_{normalized_candidate}")
                or normalized_candidate in normalized_global
            ):
                return global_input
    return None


def _required_tool_args(
    tool_id: str,
    *,
    tools: dict[str, tuple[Any, dict[str, Any]]],
) -> set[str]:
    if not tool_id or tool_id not in tools:
        return set()
    metadata = tools[tool_id][1]
    parameters = metadata.get("parameters")
    if not isinstance(parameters, dict):
        return set()
    required = parameters.get("required")
    if not isinstance(required, list):
        return set()
    return {
        str(item).strip()
        for item in required
        if str(item).strip()
    }


def _tool_schema(
    tool_id: str,
    *,
    tools: dict[str, tuple[Any, dict[str, Any]]],
) -> dict[str, Any]:
    metadata = dict(tools.get(tool_id, (None, {}))[1] or {})
    return {
        "name": tool_id,
        "description": str(metadata.get("description") or "").strip(),
        "parameters": dict(metadata.get("parameters") or {}),
    }


def _safe_tool_registry() -> dict[str, tuple[Any, dict[str, Any]]]:
    try:
        return get_all_tools()
    except Exception:
        return {}


def _worker_tool_ids(node: WorkflowSpecNode) -> list[str]:
    tool_ids = [
        str(item).strip()
        for item in node.config.get("tool_ids", []) or []
        if str(item).strip()
    ]
    if tool_ids:
        return tool_ids
    tool_id = str(
        node.grounding.tool_id
        or node.config.get("tool_id")
        or node.config.get("tool_name")
        or ""
    ).strip()
    return [tool_id] if tool_id else []


def _worker_llm_hints(node: WorkflowSpecNode) -> dict[str, Any]:
    raw = node.config.get("llm_hints")
    return dict(raw) if isinstance(raw, dict) else {}


def _default_input_aliases(node: WorkflowSpecNode, name: str) -> list[str]:
    aliases: list[str] = []
    default_name = {
        "llm_operator": "input",
        "tool_operator": "input",
        "code_operator": "input",
        "worker": "input",
        "for_each": "items",
    }.get(node.node_type)
    if default_name and default_name != name:
        aliases.append(default_name)
    return aliases


def _default_output_aliases(node: WorkflowSpecNode, name: str) -> list[str]:
    aliases: list[str] = []
    default_name = {
        "llm_operator": "text",
        "tool_operator": "result",
        "code_operator": "result",
        "worker": "text" if node.execution_family == ExecutionFamily.llm else "result",
        "input": "input",
    }.get(node.node_type)
    if default_name and default_name != name:
        aliases.append(default_name)
    if name not in {"output", "result"}:
        aliases.append("output")
    return _ordered_unique(aliases)


def _ordered_unique(items: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            ordered.append(text)
            seen.add(text)
    return ordered


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_") or "issue"


def _should_allow_implicit_binding(
    node: WorkflowSpecNode,
    input_name: str,
    *,
    tools: dict[str, tuple[Any, dict[str, Any]]],
) -> bool:
    if node.execution_family == ExecutionFamily.control_flow:
        return True
    if node.node_type == "tool_operator":
        required_args = _required_tool_args(str(node.grounding.tool_id or "").strip(), tools=tools)
        return input_name not in required_args and input_name in _GENERIC_INPUT_NAMES
    if node.node_type == "worker" and node.execution_family == ExecutionFamily.tool:
        required_args = _required_tool_args(
            str(node.grounding.tool_id or next(iter(_worker_tool_ids(node)), "")).strip(),
            tools=tools,
        )
        return input_name not in required_args and input_name in _GENERIC_INPUT_NAMES
    return input_name in _GENERIC_INPUT_NAMES


def _read_int_env(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    return value


def _derived_worker_timeout(expected_node_count: int) -> float:
    raw_total = os.environ.get("DAN_MAX_GENERATION_SECONDS", "").strip()
    try:
        total_budget = float(raw_total) if raw_total else 120.0
    except ValueError:
        total_budget = 120.0
    divisor = max(expected_node_count, 1)
    return max(total_budget / divisor, _DEFAULT_TIMEOUT_FLOOR_SECONDS)
