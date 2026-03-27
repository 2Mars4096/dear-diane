"""Structured workflow spec contract used by the staged generation pipeline."""

from __future__ import annotations

import os
import re
from enum import Enum
from heapq import heappop, heappush
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dan.models.node_taxonomy import RUNTIME_NODE_TYPES

__all__ = [
    "ArtifactKind",
    "ArtifactSpec",
    "CandidateWorkflowWorkspace",
    "DataSourceKind",
    "DataSourceSpec",
    "ExecutionFamily",
    "NodeWorkerHints",
    "NodeGrounding",
    "PortBindingSourceKind",
    "PortBindingSpec",
    "RunnableTestSpec",
    "ScheduleIntent",
    "StructuredSectioningConfig",
    "WorkflowPortSpec",
    "WorkflowRiskLevel",
    "WorkflowSpec",
    "WorkflowSpecNode",
    "WorkflowSection",
    "infer_execution_family",
    "workflow_spec_from_intent",
]


class ExecutionFamily(str, Enum):
    """Coarse execution family distinct from runtime ``node_type``."""

    llm = "llm"
    tool = "tool"
    code = "code"
    control_flow = "control_flow"


class DataSourceKind(str, Enum):
    """High-level source category for external data dependencies."""

    api = "api"
    file = "file"
    url = "url"
    dataset = "dataset"
    memory = "memory"
    rag_collection = "rag_collection"
    workflow = "workflow"
    custom = "custom"


class ArtifactKind(str, Enum):
    """Workflow artifact intent for expected inputs and outputs."""

    input = "input"
    output = "output"
    intermediate = "intermediate"
    schedule = "schedule"


class ScheduleIntent(BaseModel):
    """Sidecar schedule intent preserved outside the graph structure."""

    trigger: str
    delivery_target: str | None = None
    run_profile: str | None = None
    timezone: str | None = None
    enabled: bool = True
    notes: list[str] = Field(default_factory=list)


class DataSourceSpec(BaseModel):
    """Declared data source for a node or workflow."""

    kind: DataSourceKind
    name: str
    description: str = ""
    config: dict[str, Any] = Field(default_factory=dict)


class ArtifactSpec(BaseModel):
    """Expected artifact or boundary contract for the staged workflow."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    kind: ArtifactKind = ArtifactKind.output
    description: str = ""
    required: bool = True
    schema_: dict[str, Any] | None = Field(default=None, alias="schema", serialization_alias="schema")
    mime_type: str | None = None


class WorkflowRiskLevel(str, Enum):
    """Deterministic failure-risk hint for staged node planning."""

    low = "low"
    medium = "medium"
    high = "high"


class WorkflowPortSpec(BaseModel):
    """Serializable port contract shared across staged planning layers."""

    name: str
    json_schema: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    description: str = ""
    aliases: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> WorkflowPortSpec:
        name = self.name.strip()
        if not name:
            raise ValueError("WorkflowPortSpec.name must not be empty")
        aliases: list[str] = []
        seen: set[str] = {name}
        for alias in self.aliases:
            cleaned = str(alias).strip()
            if cleaned and cleaned not in seen:
                aliases.append(cleaned)
                seen.add(cleaned)
        self.name = name
        self.aliases = aliases
        return self


class NodeWorkerHints(BaseModel):
    """Worker-local hints that keep node planning deterministic."""

    test_intent: str = "contract_smoke"
    failure_risk: WorkflowRiskLevel = WorkflowRiskLevel.medium
    expected_side_effects: list[str] = Field(default_factory=list)


class NodeGrounding(BaseModel):
    """Semantic grounding for a planned node."""

    tool_id: str | None = None
    operation_type: str | None = None
    declared_actions: list[str] = Field(default_factory=list)
    data_sources: list[DataSourceSpec] = Field(default_factory=list)
    bound_arguments: dict[str, Any] = Field(default_factory=dict)
    requires_external_data: bool = False
    notes: list[str] = Field(default_factory=list)


class PortBindingSourceKind(str, Enum):
    """Where a node input is expected to be satisfied from."""

    global_input = "global_input"
    dependency_output = "dependency_output"
    external_data = "external_data"
    literal = "literal"
    implicit = "implicit"


class PortBindingSpec(BaseModel):
    """Serializable node-input binding used by staged planners."""

    input_port: str
    source_kind: PortBindingSourceKind
    source_node_id: str | None = None
    source_port: str | None = None
    source_artifact: str | None = None
    value: Any | None = None
    description: str = ""


class RunnableTestSpec(BaseModel):
    """Minimal runnable fixture metadata for node/section/graph smoke tests."""

    test_id: str
    kind: str
    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)
    fixtures: dict[str, Any] = Field(default_factory=dict)


class WorkflowSpecNode(BaseModel):
    """Single planned node inside a staged workflow spec."""

    node_id: str
    purpose: str
    node_type: str
    execution_family: ExecutionFamily
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    test_expectations: list[str] = Field(default_factory=list)
    chapter_label: str | None = None
    section_label: str | None = None
    section_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    worker_hints: NodeWorkerHints = Field(default_factory=NodeWorkerHints)
    grounding: NodeGrounding = Field(default_factory=NodeGrounding)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> WorkflowSpecNode:
        node_type = self.node_type.strip()
        if not node_type:
            raise ValueError("node_type must not be empty")
        if node_type not in RUNTIME_NODE_TYPES:
            raise ValueError(f"Unknown runtime node_type: {self.node_type!r}")
        if infer_execution_family(node_type) != self.execution_family:
            raise ValueError(
                f"node_type {node_type!r} is not compatible with execution_family "
                f"{self.execution_family.value!r}"
            )
        if not self.node_id.strip():
            raise ValueError("node_id must not be empty")
        if not self.purpose.strip():
            raise ValueError(f"Node {self.node_id!r} must declare a purpose")

        grounding = self.grounding
        declared_actions = {action.strip().lower() for action in grounding.declared_actions if action.strip()}
        external_action_verbs = {"fetch", "search", "read", "write", "save", "browse", "scrape"}

        if node_type == "tool_operator" and not grounding.tool_id:
            raise ValueError(
                f"tool_operator node {self.node_id!r} must declare grounding.tool_id"
            )
        if node_type == "code_operator" and not grounding.operation_type:
            raise ValueError(
                f"code_operator node {self.node_id!r} must declare grounding.operation_type"
            )
        if declared_actions & external_action_verbs:
            if node_type == "llm_operator" and not grounding.tool_id:
                raise ValueError(
                    f"llm_operator node {self.node_id!r} declares external actions but has no grounding.tool_id"
                )
            if not grounding.tool_id and not grounding.data_sources and not grounding.requires_external_data:
                raise ValueError(
                    f"Node {self.node_id!r} declares external actions but lacks a tool binding or data source declaration"
                )
        if grounding.requires_external_data and not grounding.tool_id and not grounding.data_sources:
            raise ValueError(
                f"Node {self.node_id!r} requires external data but does not declare a tool or data source"
            )
        return self


class WorkflowSpec(BaseModel):
    """High-level staged workflow contract."""

    workflow_id: str | None = None
    goal: str
    nodes: list[WorkflowSpecNode] = Field(default_factory=list)
    global_inputs: list[str] = Field(default_factory=list)
    global_outputs: list[str] = Field(default_factory=list)
    expected_inputs: list[ArtifactSpec] = Field(default_factory=list)
    expected_outputs: list[ArtifactSpec] = Field(default_factory=list)
    data_sources: list[DataSourceSpec] = Field(default_factory=list)
    schedule: ScheduleIntent | None = None
    candidate_workspace_id: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> WorkflowSpec:
        node_ids: set[str] = set()
        for node in self.nodes:
            if node.node_id in node_ids:
                raise ValueError(f"Duplicate node_id: {node.node_id!r}")
            node_ids.add(node.node_id)

        for node in self.nodes:
            for dependency in node.dependencies:
                if dependency not in node_ids:
                    raise ValueError(
                        f"Node {node.node_id!r} depends on unknown node_id {dependency!r}"
                    )

        _assert_acyclic(self.nodes)
        return self

    def node_by_id(self, node_id: str) -> WorkflowSpecNode | None:
        """Return the node with ``node_id`` if present."""

        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None


class CandidateWorkflowWorkspace(BaseModel):
    """Detached staging wrapper for candidate workflow generation."""

    workflow_id: str
    thread_id: str | None = None
    spec: WorkflowSpec
    persisted_graph_id: str | None = None
    state: str = "staged"
    notes: list[str] = Field(default_factory=list)


class StructuredSectioningConfig(BaseModel):
    """Deterministic sectioning knobs for staged workflow partitioning."""

    max_section_size: int = Field(default=6, ge=1)
    min_section_size: int = Field(default=2, ge=1)
    cross_section_penalty: float = Field(default=1.5, gt=0.0)

    @classmethod
    def from_env(cls) -> StructuredSectioningConfig:
        """Load the sectioning config from DAN_* environment variables."""

        return cls(
            max_section_size=_read_int_env("DAN_STRUCTURED_MAX_SECTION_SIZE", 6, minimum=1),
            min_section_size=_read_int_env("DAN_STRUCTURED_MIN_SECTION_SIZE", 2, minimum=1),
            cross_section_penalty=_read_float_env("DAN_STRUCTURED_CROSS_SECTION_PENALTY", 1.5),
        )


class WorkflowSection(BaseModel):
    """Deterministic section emitted by the structured partitioner."""

    section_id: str
    index: int
    node_ids: list[str]
    nodes: list[WorkflowSpecNode]
    estimated_weight: float
    chapter_label: str | None = None
    section_label: str | None = None
    upstream_dependencies: list[str] = Field(default_factory=list)
    downstream_dependents: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


_FAMILY_BY_RUNTIME_NODE_TYPE: dict[str, ExecutionFamily] = {
    "llm_operator": ExecutionFamily.llm,
    "tool_operator": ExecutionFamily.tool,
    "code_operator": ExecutionFamily.code,
    "rag_operator": ExecutionFamily.tool,
    "reflection": ExecutionFamily.llm,
}

_EXTERNAL_READ_CONTEXT_HINTS = (
    "file",
    "folder",
    "path",
    "document",
    "pdf",
    "csv",
    "url",
    "web",
    "page",
)

_EXTERNAL_WRITE_CONTEXT_HINTS = (
    "file",
    "folder",
    "path",
    "disk",
    "csv",
    "json",
    "pdf",
    "markdown",
    "txt",
)

for _control_node_type in (
    "input",
    "gate",
    "if_else",
    "while_loop",
    "for_each",
    "parallel_subagents",
    "orchestrator",
    "reduce",
    "router",
    "human",
    "human_in_the_loop",
    "validator",
    "composite",
    "agent_team",
    "vote",
    "goal_loop",
):
    _FAMILY_BY_RUNTIME_NODE_TYPE.setdefault(_control_node_type, ExecutionFamily.control_flow)


def infer_execution_family(node_type: str) -> ExecutionFamily:
    """Infer the execution family for a runtime node type."""

    return _FAMILY_BY_RUNTIME_NODE_TYPE.get(node_type, ExecutionFamily.control_flow)


def workflow_spec_from_intent(
    intent: Any,
    *,
    workflow_id: str | None = None,
    candidate_workspace_id: str | None = None,
    schedule: ScheduleIntent | None = None,
    constraints: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> WorkflowSpec:
    """Project an existing ``WorkflowIntent`` into the structured spec contract."""

    stages = list(getattr(intent, "stages", []) or [])
    nodes = [
        _workflow_spec_node_from_stage(stage)
        for stage in stages
    ]
    nodes = _normalize_node_dependencies_from_intent(nodes, stages)
    expected_inputs = [
        ArtifactSpec(
            name=name,
            kind=ArtifactKind.input,
            description=f"Declared workflow input: {name}",
        )
        for name in getattr(intent, "global_inputs", []) or []
    ]
    expected_outputs = [
        ArtifactSpec(
            name=name,
            kind=ArtifactKind.output,
            description=f"Declared workflow output: {name}",
        )
        for name in getattr(intent, "global_outputs", []) or []
    ]
    data_sources = [
        _data_source_spec_from_intent_source(source)
        for source in getattr(intent, "data_sources", []) or []
    ]
    return WorkflowSpec(
        workflow_id=workflow_id,
        goal=str(getattr(intent, "goal", "") or "").strip(),
        nodes=nodes,
        global_inputs=list(getattr(intent, "global_inputs", []) or []),
        global_outputs=list(getattr(intent, "global_outputs", []) or []),
        expected_inputs=expected_inputs,
        expected_outputs=expected_outputs,
        data_sources=data_sources,
        schedule=schedule,
        candidate_workspace_id=candidate_workspace_id,
        constraints=dict(getattr(intent, "constraints", {}) or {}) | dict(constraints or {}),
        notes=list(notes or []),
    )


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


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _assert_acyclic(nodes: list[WorkflowSpecNode]) -> None:
    node_ids = {node.node_id for node in nodes}
    outgoing: dict[str, list[str]] = {node.node_id: [] for node in nodes}
    incoming_count: dict[str, int] = {node.node_id: 0 for node in nodes}

    for node in nodes:
        for dependency in node.dependencies:
            if dependency in node_ids:
                outgoing[dependency].append(node.node_id)
                incoming_count[node.node_id] += 1

    ready: list[str] = []
    for node_id, count in incoming_count.items():
        if count == 0:
            heappush(ready, node_id)

    visited = 0
    while ready:
        node_id = heappop(ready)
        visited += 1
        for child in sorted(outgoing[node_id]):
            incoming_count[child] -= 1
            if incoming_count[child] == 0:
                heappush(ready, child)

    if visited != len(nodes):
        raise ValueError("Circular dependency detected in workflow spec")


def _workflow_spec_node_from_stage(stage: Any) -> WorkflowSpecNode:
    node_type = _runtime_node_type_for_stage(stage)
    config = dict(getattr(stage, "config", {}) or {})
    grounding = NodeGrounding(
        tool_id=_stage_tool_id(stage, node_type=node_type),
        operation_type=_stage_operation_type(stage, node_type=node_type),
        declared_actions=_stage_declared_actions(stage),
        data_sources=_stage_data_sources(stage),
        bound_arguments=dict(config.get("tool_config", {}) or {}),
        requires_external_data=bool(config.get("requires_external_data", False)),
    )
    return WorkflowSpecNode(
        node_id=str(getattr(stage, "name", "") or "").strip(),
        purpose=str(getattr(stage, "description", "") or getattr(stage, "name", "") or "").strip(),
        node_type=node_type,
        execution_family=infer_execution_family(node_type),
        inputs=[str(item).strip() for item in getattr(stage, "inputs", []) or [] if str(item).strip()],
        outputs=[str(item).strip() for item in getattr(stage, "outputs", []) or [] if str(item).strip()],
        dependencies=_stage_dependencies(stage),
        test_expectations=_stage_test_expectations(stage),
        chapter_label=str(config.get("chapter_label") or "").strip() or None,
        section_label=str(config.get("section_label") or "").strip() or None,
        section_id=str(config.get("section_id") or "").strip() or None,
        config=config,
        worker_hints=_stage_worker_hints(stage),
        grounding=grounding,
        notes=[str(item).strip() for item in config.get("notes", []) or [] if str(item).strip()],
    )


def _normalize_node_dependencies_from_intent(
    nodes: list[WorkflowSpecNode],
    stages: list[Any],
) -> list[WorkflowSpecNode]:
    stage_names = {
        str(getattr(stage, "name", "") or "").strip()
        for stage in stages
        if str(getattr(stage, "name", "") or "").strip()
    }
    output_to_stage: dict[str, str] = {}
    for stage in stages:
        stage_name = str(getattr(stage, "name", "") or "").strip()
        if not stage_name:
            continue
        for output_name in getattr(stage, "outputs", []) or []:
            output = str(output_name).strip()
            if output:
                output_to_stage[output] = stage_name

    normalized: list[WorkflowSpecNode] = []
    for node in nodes:
        dependencies: list[str] = []
        seen: set[str] = set()
        for dependency in node.dependencies:
            candidate = output_to_stage.get(dependency, dependency)
            if candidate in stage_names and candidate not in seen and candidate != node.node_id:
                dependencies.append(candidate)
                seen.add(candidate)
        normalized.append(node.model_copy(update={"dependencies": dependencies}))
    return normalized


def _runtime_node_type_for_stage(stage: Any) -> str:
    stage_type = str(getattr(getattr(stage, "stage_type", None), "value", None) or getattr(stage, "stage_type", "") or "").strip()
    return {
        "tool_call": "tool_operator",
        "code_execution": "code_operator",
        "fan_out": "for_each",
        "conditional": "gate",
        "loop": "while_loop",
        "review_loop": "while_loop",
        "human_approval": "human",
        "rag_retrieval": "rag_operator",
    }.get(stage_type, "llm_operator")


def _stage_tool_id(stage: Any, *, node_type: str) -> str | None:
    config = dict(getattr(stage, "config", {}) or {})
    if node_type in {"tool_operator", "rag_operator", "llm_operator"}:
        tool_id = (
            config.get("tool_id")
            or config.get("tool")
            or config.get("tool_name")
        )
        if tool_id:
            return str(tool_id).strip()
    return None


def _stage_operation_type(stage: Any, *, node_type: str) -> str | None:
    if node_type != "code_operator":
        return None
    config = dict(getattr(stage, "config", {}) or {})
    return str(
        config.get("operation_type")
        or getattr(getattr(stage, "stage_type", None), "value", None)
        or "code_execution"
    ).strip()


def _stage_dependencies(stage: Any) -> list[str]:
    inputs = [str(item).strip() for item in getattr(stage, "inputs", []) or [] if str(item).strip()]
    outputs = {str(item).strip() for item in getattr(stage, "outputs", []) or [] if str(item).strip()}
    return [item for item in inputs if item not in outputs]


def _stage_declared_actions(stage: Any) -> list[str]:
    config = dict(getattr(stage, "config", {}) or {})
    actions = config.get("declared_actions")
    if isinstance(actions, list):
        return [str(item).strip() for item in actions if str(item).strip()]
    description = str(getattr(stage, "description", "") or "").lower()
    declared: list[str] = []
    for action in ("search", "fetch", "browse", "scrape"):
        if re.search(rf"\b{re.escape(action)}\b", description):
            declared.append(action)
    if (
        re.search(r"\bread\b", description)
        and any(token in description for token in _EXTERNAL_READ_CONTEXT_HINTS)
    ):
        declared.append("read")
    if (
        re.search(r"\bwrite\b", description)
        and any(token in description for token in _EXTERNAL_WRITE_CONTEXT_HINTS)
    ):
        declared.append("write")
    if (
        re.search(r"\bsave\b", description)
        and any(token in description for token in _EXTERNAL_WRITE_CONTEXT_HINTS)
    ):
        declared.append("save")
    return declared


def _stage_test_expectations(stage: Any) -> list[str]:
    config = dict(getattr(stage, "config", {}) or {})
    expectations = config.get("test_expectations")
    if isinstance(expectations, list):
        return [str(item).strip() for item in expectations if str(item).strip()]
    name = str(getattr(stage, "name", "") or "node").strip()
    return [f"{name} completes with its declared outputs available"]


def _stage_worker_hints(stage: Any) -> NodeWorkerHints:
    config = dict(getattr(stage, "config", {}) or {})
    side_effects = config.get("expected_side_effects")
    if not isinstance(side_effects, list):
        side_effects = config.get("side_effects")
    if not isinstance(side_effects, list):
        side_effects = []
    failure_risk = str(config.get("failure_risk") or "medium").strip().lower()
    if failure_risk not in {item.value for item in WorkflowRiskLevel}:
        failure_risk = WorkflowRiskLevel.medium.value
    return NodeWorkerHints(
        test_intent=str(config.get("test_intent") or "contract_smoke").strip() or "contract_smoke",
        failure_risk=WorkflowRiskLevel(failure_risk),
        expected_side_effects=[
            str(item).strip()
            for item in side_effects
            if str(item).strip()
        ],
    )


def _stage_data_sources(stage: Any) -> list[DataSourceSpec]:
    config = dict(getattr(stage, "config", {}) or {})
    raw_sources = config.get("data_sources")
    if not isinstance(raw_sources, list):
        return []
    parsed: list[DataSourceSpec] = []
    for source in raw_sources:
        if isinstance(source, DataSourceSpec):
            parsed.append(source)
            continue
        if not isinstance(source, dict):
            continue
        kind = str(source.get("kind", "custom") or "custom").strip().lower()
        try:
            source_kind = DataSourceKind(kind)
        except ValueError:
            source_kind = DataSourceKind.custom
        parsed.append(
            DataSourceSpec(
                kind=source_kind,
                name=str(source.get("name", "") or "").strip() or "unnamed_source",
                description=str(source.get("description", "") or "").strip(),
                config=dict(source.get("config", {}) or {}),
            )
        )
    return parsed


def _data_source_spec_from_intent_source(source: Any) -> DataSourceSpec:
    source_type = str(getattr(getattr(source, "type", None), "value", None) or getattr(source, "type", "") or "").strip().lower()
    try:
        kind = DataSourceKind(source_type)
    except ValueError:
        kind = DataSourceKind.custom
    return DataSourceSpec(
        kind=kind,
        name=str(getattr(source, "description", "") or kind.value).strip() or kind.value,
        description=str(getattr(source, "description", "") or "").strip(),
        config=dict(getattr(source, "config", {}) or {}),
    )
