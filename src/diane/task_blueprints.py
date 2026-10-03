"""Canonical, revision-safe semantic task blueprints.

Task blueprints describe *what the user means*: the protected task contract,
user-visible work, decisions, artifacts, dependencies, loops, and gates.  They
deliberately do not contain worker/model/tool scheduling.  A separate
``ExecutionAttempt`` binds those operational choices to one exact blueprint
revision.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TaskFamily = Literal[
    "direct",
    "debugging",
    "research",
    "design",
    "meeting",
    "manufacturing",
    "general",
]
NodeKind = Literal["work", "decision", "artifact", "gate", "loop", "composite"]
EdgeKind = Literal[
    "dependency",
    "alternative",
    "feedback",
    "produces",
    "validates",
    "supersedes",
]
NodeState = Literal["planned", "ready", "active", "completed", "blocked", "superseded"]
AttemptStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "blocked",
    "paused",
    "cancelled",
]
AttemptPhase = Literal[
    "planning",
    "executing",
    "validating",
    "repairing",
    "waiting_approval",
    "completed",
]

_DEPENDENCY_KINDS = {"dependency", "alternative", "produces", "validates"}
_PINNED_STATES = {"active", "completed"}
_RISK_ORDER = {"low": 0, "moderate": 1, "high": 2, "critical": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_tuple(values: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value).strip() for value in values or () if str(value).strip()
        )
    )


def _stable_token(value: str, *, fallback: str = "item") -> str:
    token = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return token[:48] or fallback


def _canonical_hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


class AcceptanceCriterion(FrozenModel):
    criterion_id: str
    description: str
    required: bool = True
    evidence_required: tuple[str, ...] = ()
    approval_required: bool = False

    @field_validator("criterion_id", "description")
    @classmethod
    def _required_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("acceptance criterion id and description are required")
        return text

    @field_validator("evidence_required", mode="before")
    @classmethod
    def _normalize_evidence(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)


class RiskPolicy(FrozenModel):
    level: Literal["low", "moderate", "high", "critical"] = "low"
    hazards: tuple[str, ...] = ()
    mitigations: tuple[str, ...] = ()
    prohibited_actions: tuple[str, ...] = ()

    @field_validator("hazards", "mitigations", "prohibited_actions", mode="before")
    @classmethod
    def _normalize_lists(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)


class BudgetLimits(FrozenModel):
    max_wall_seconds: float | None = Field(default=None, gt=0)
    max_work_seconds: float | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, gt=0)
    max_tool_calls: int | None = Field(default=None, gt=0)
    max_parallel_workers: int | None = Field(default=None, gt=0)
    max_revisions: int | None = Field(default=None, gt=0)
    max_auto_fix_rounds: int | None = Field(default=None, ge=0)
    max_validation_cycles: int | None = Field(default=None, gt=0)


class TaskContract(FrozenModel):
    goal: str
    non_goals: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ("workspace:read",)
    risk: RiskPolicy = Field(default_factory=RiskPolicy)
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()

    @field_validator("goal")
    @classmethod
    def _goal_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("task goal is required")
        return text

    @field_validator("non_goals", "constraints", "permissions", mode="before")
    @classmethod
    def _normalize_text_lists(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def _normalize_criteria(cls, value: Any) -> tuple[Any, ...]:
        normalized: list[Any] = []
        for index, item in enumerate(value or (), start=1):
            if isinstance(item, str):
                normalized.append(
                    {
                        "criterion_id": f"criterion-{index}-{_stable_token(item)}",
                        "description": item,
                    }
                )
            else:
                normalized.append(item)
        return tuple(normalized)

    @model_validator(mode="after")
    def _unique_criteria(self) -> "TaskContract":
        ids = [item.criterion_id for item in self.acceptance_criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("acceptance criterion ids must be unique")
        return self

    @property
    def contract_hash(self) -> str:
        return _canonical_hash(self)


class LoopPolicy(FrozenModel):
    max_iterations: int = Field(gt=0)
    exit_condition: str
    progress_criterion: str = (
        "Each pass must produce new evidence or a material revision."
    )
    on_exhaustion: Literal["block", "escalate", "accept_best"] = "escalate"

    @field_validator("exit_condition", "progress_criterion")
    @classmethod
    def _loop_text_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(
                "bounded loops require an exit condition and progress criterion"
            )
        return text


class BlueprintNode(FrozenModel):
    node_id: str
    kind: NodeKind = "work"
    title: str
    description: str = ""
    topology_role: str = "work"
    capability_requirements: tuple[str, ...] = ()
    criterion_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    decision_refs: tuple[str, ...] = ()
    approval_refs: tuple[str, ...] = ()
    loop_policy: LoopPolicy | None = None
    supersedes: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()
    active: bool = True

    @field_validator("node_id", "title", "topology_role")
    @classmethod
    def _node_text_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("node id, title, and topology role are required")
        return text

    @field_validator(
        "capability_requirements",
        "criterion_ids",
        "artifact_refs",
        "evidence_refs",
        "decision_refs",
        "approval_refs",
        "supersedes",
        "superseded_by",
        mode="before",
    )
    @classmethod
    def _normalize_node_lists(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)

    @model_validator(mode="after")
    def _loop_contract(self) -> "BlueprintNode":
        if self.kind == "loop" and self.loop_policy is None:
            raise ValueError(
                f"loop node {self.node_id!r} requires a bounded loop_policy"
            )
        if self.kind != "loop" and self.loop_policy is not None:
            raise ValueError("loop_policy is only valid on loop nodes")
        if not self.active and not (self.superseded_by or self.supersedes):
            raise ValueError("inactive nodes must retain supersession provenance")
        return self


class BlueprintEdge(FrozenModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    kind: EdgeKind = "dependency"
    condition: str = ""
    loop_node_id: str | None = None

    @field_validator("edge_id", "source_node_id", "target_node_id")
    @classmethod
    def _edge_text_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("edge id, source, and target are required")
        return text

    @model_validator(mode="after")
    def _feedback_contract(self) -> "BlueprintEdge":
        if self.kind == "feedback" and not self.loop_node_id:
            raise ValueError("feedback edges must name their bounded loop node")
        if self.kind != "feedback" and self.loop_node_id:
            raise ValueError("loop_node_id is only valid on feedback edges")
        if self.source_node_id == self.target_node_id:
            raise ValueError(
                "self edges are not allowed; use an explicit bounded loop node"
            )
        return self


class DerivedBlueprintState(FrozenModel):
    ready_node_ids: tuple[str, ...] = ()
    deferred_node_ids: tuple[str, ...] = ()
    active_node_ids: tuple[str, ...] = ()
    completed_node_ids: tuple[str, ...] = ()
    blocked_node_ids: tuple[str, ...] = ()
    superseded_node_ids: tuple[str, ...] = ()
    node_states: dict[str, NodeState] = Field(default_factory=dict)
    bounded_loop_node_ids: tuple[str, ...] = ()
    uncovered_criterion_ids: tuple[str, ...] = ()

    @field_validator(
        "ready_node_ids",
        "deferred_node_ids",
        "active_node_ids",
        "completed_node_ids",
        "blocked_node_ids",
        "superseded_node_ids",
        "bounded_loop_node_ids",
        "uncovered_criterion_ids",
        mode="before",
    )
    @classmethod
    def _normalize_state_lists(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)


class RevisionProvenance(FrozenModel):
    author: str = "system"
    reason: str
    evidence_refs: tuple[str, ...] = ()
    proposal_id: str = ""
    approval_id: str = ""
    approval_scope_hash: str = ""

    @field_validator("author", "reason")
    @classmethod
    def _provenance_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("revision author and reason are required")
        return text

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _normalize_refs(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)


class TaskBlueprint(FrozenModel):
    schema_version: Literal["dan_task_blueprint_v1"] = Field(
        default="dan_task_blueprint_v1",
        alias="schema",
    )
    blueprint_id: str
    task_id: str
    revision_id: str
    revision: int = Field(ge=1)
    parent_revision_ids: tuple[str, ...] = ()
    family: TaskFamily = "general"
    contract: TaskContract
    nodes: tuple[BlueprintNode, ...]
    edges: tuple[BlueprintEdge, ...] = ()
    derived_state: DerivedBlueprintState
    update_reason: str
    provenance: RevisionProvenance
    contract_hash: str
    content_hash: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    @field_validator("blueprint_id", "task_id", "revision_id", "update_reason")
    @classmethod
    def _blueprint_text_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("blueprint identity and update reason are required")
        return text

    @field_validator("parent_revision_ids", mode="before")
    @classmethod
    def _normalize_parents(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)

    @model_validator(mode="after")
    def _validate_graph(self) -> "TaskBlueprint":
        _validate_blueprint_graph(
            self.contract, self.nodes, self.edges, self.derived_state
        )
        if self.contract_hash != self.contract.contract_hash:
            raise ValueError("contract_hash does not match the protected contract")
        return self


class ExecutionPolicySnapshot(FrozenModel):
    permission_scope: tuple[str, ...] = ()
    risk_level: Literal["low", "moderate", "high", "critical"] = "low"
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    contract_hash: str = ""
    approval_refs: tuple[str, ...] = ()

    @field_validator("permission_scope", "approval_refs", mode="before")
    @classmethod
    def _normalize_policy_lists(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)


class ExecutionResult(FrozenModel):
    result_id: str
    node_id: str = ""
    status: Literal["completed", "failed", "blocked", "skipped"] = "completed"
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    summary: str = ""

    @field_validator("artifact_refs", "evidence_refs", mode="before")
    @classmethod
    def _normalize_result_lists(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)


class ExecutionAttempt(FrozenModel):
    schema_version: Literal["dan_execution_attempt_v1"] = Field(
        default="dan_execution_attempt_v1",
        alias="schema",
    )
    attempt_id: str
    task_id: str
    blueprint_id: str
    blueprint_revision_id: str
    blueprint_revision: int = Field(ge=1)
    status: AttemptStatus = "pending"
    phase: AttemptPhase = "planning"
    run_id: str = ""
    backend: str = "super_dan"
    policy_snapshot: ExecutionPolicySnapshot
    worker_summary: tuple[str, ...] = ()
    model_summary: tuple[str, ...] = ()
    tool_summary: tuple[str, ...] = ()
    retry_count: int = Field(default=0, ge=0)
    max_retries: int | None = Field(default=None, ge=0)
    schedule: str = ""
    node_states: dict[str, str] = Field(default_factory=dict)
    results: tuple[ExecutionResult, ...] = ()
    created_at: str = Field(default_factory=_now)
    started_at: str = ""
    completed_at: str = ""

    @field_validator("worker_summary", "model_summary", "tool_summary", mode="before")
    @classmethod
    def _normalize_attempt_lists(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            value = (value,)
        return _clean_tuple(value)

    @model_validator(mode="after")
    def _attempt_binding(self) -> "ExecutionAttempt":
        if not all(
            (
                self.attempt_id,
                self.task_id,
                self.blueprint_id,
                self.blueprint_revision_id,
            )
        ):
            raise ValueError(
                "execution attempts require task and exact blueprint revision identity"
            )
        if (
            self.status in {"completed", "failed", "blocked", "cancelled"}
            and not self.completed_at
        ):
            raise ValueError("terminal execution attempts require completed_at")
        return self


class ContractAmendment(FrozenModel):
    goal: str | None = None
    non_goals: tuple[str, ...] | None = None
    constraints: tuple[str, ...] | None = None
    permissions: tuple[str, ...] | None = None
    risk: RiskPolicy | None = None
    budget: BudgetLimits | None = None
    acceptance_criteria: tuple[AcceptanceCriterion, ...] | None = None
    authority: Literal["user", "operator"]
    reason: str
    approval_id: str
    approval_scope_hash: str


class BlueprintPatchOperation(FrozenModel):
    op: Literal[
        "add_node",
        "revise_node",
        "retire_node",
        "supersede_node",
        "split_node",
        "merge_nodes",
        "add_edge",
        "remove_edge",
        "revise_dependency",
        "attach_criterion",
        "attach_evidence",
        "reopen_node",
        "branch_node",
        "narrow_permissions",
        "strengthen_criteria",
    ]
    data: dict[str, Any] = Field(default_factory=dict)


class BlueprintPatch(FrozenModel):
    patch_id: str
    blueprint_id: str
    base_revision_id: str
    base_revision: int
    author: str
    reason: str
    evidence_refs: tuple[str, ...] = ()
    operations: tuple[BlueprintPatchOperation, ...]
    contract_amendment: ContractAmendment | None = None
    created_at: str = Field(default_factory=_now)


class BlueprintRevisionEvent(FrozenModel):
    event: Literal["task_blueprint.revised"] = "task_blueprint.revised"
    event_id: str
    blueprint_id: str
    revision_id: str
    parent_revision_ids: tuple[str, ...]
    patch_id: str
    author: str
    reason: str
    evidence_refs: tuple[str, ...] = ()
    task_blueprint: TaskBlueprint
    created_at: str = Field(default_factory=_now)


class BlueprintUpdate(FrozenModel):
    previous: TaskBlueprint
    current: TaskBlueprint
    patch: BlueprintPatch
    event: BlueprintRevisionEvent


def _criterion_ids(contract: TaskContract) -> set[str]:
    return {criterion.criterion_id for criterion in contract.acceptance_criteria}


def _validate_blueprint_graph(
    contract: TaskContract,
    nodes: Sequence[BlueprintNode],
    edges: Sequence[BlueprintEdge],
    state: DerivedBlueprintState,
) -> None:
    node_ids = [node.node_id for node in nodes]
    if not node_ids:
        raise ValueError("a task blueprint requires at least one semantic node")
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("blueprint node ids must be unique")
    edge_ids = [edge.edge_id for edge in edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ValueError("blueprint edge ids must be unique")

    nodes_by_id = {node.node_id: node for node in nodes}
    criteria = _criterion_ids(contract)
    covered: set[str] = set()
    for node in nodes:
        unknown = set(node.criterion_ids) - criteria
        if unknown:
            raise ValueError(
                f"node {node.node_id!r} references unknown criteria: {sorted(unknown)}"
            )
        covered.update(node.criterion_ids)
        for related in (*node.supersedes, *node.superseded_by):
            if related not in nodes_by_id:
                raise ValueError(
                    f"node {node.node_id!r} has dangling supersession {related!r}"
                )

    for edge in edges:
        if (
            edge.source_node_id not in nodes_by_id
            or edge.target_node_id not in nodes_by_id
        ):
            raise ValueError(f"edge {edge.edge_id!r} has a dangling endpoint")
        if edge.kind == "feedback":
            loop = nodes_by_id.get(str(edge.loop_node_id or ""))
            if loop is None or loop.kind != "loop" or loop.loop_policy is None:
                raise ValueError(
                    f"feedback edge {edge.edge_id!r} is not bound to a bounded loop"
                )

    _assert_acyclic_non_feedback(nodes_by_id, edges)
    known_state_ids = set(state.node_states)
    if not known_state_ids.issubset(nodes_by_id):
        raise ValueError(
            f"derived state has unknown nodes: {sorted(known_state_ids - set(nodes_by_id))}"
        )
    state_lists = (
        state.ready_node_ids,
        state.deferred_node_ids,
        state.active_node_ids,
        state.completed_node_ids,
        state.blocked_node_ids,
        state.superseded_node_ids,
    )
    for values in state_lists:
        if not set(values).issubset(nodes_by_id):
            raise ValueError("derived state contains an unknown node id")
    if set(state.uncovered_criterion_ids) != (_criterion_ids(contract) - covered):
        raise ValueError("derived uncovered criteria do not match graph coverage")


def _assert_acyclic_non_feedback(
    nodes_by_id: Mapping[str, BlueprintNode],
    edges: Sequence[BlueprintEdge],
) -> None:
    outgoing: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}
    indegree = {node_id: 0 for node_id in nodes_by_id}
    for edge in edges:
        if edge.kind in {"feedback", "supersedes", "alternative"}:
            continue
        if edge.target_node_id not in outgoing[edge.source_node_id]:
            outgoing[edge.source_node_id].add(edge.target_node_id)
            indegree[edge.target_node_id] += 1
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    seen = 0
    while queue:
        node_id = queue.pop()
        seen += 1
        for target in outgoing[node_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if seen != len(nodes_by_id):
        raise ValueError(
            "unbounded dependency cycle; use a feedback edge and bounded loop node"
        )


def derive_blueprint_state(
    contract: TaskContract,
    nodes: Sequence[BlueprintNode],
    edges: Sequence[BlueprintEdge],
    *,
    node_states: Mapping[str, str] | None = None,
) -> DerivedBlueprintState:
    nodes_by_id = {node.node_id: node for node in nodes}
    supplied = {
        str(key): str(value).lower() for key, value in (node_states or {}).items()
    }
    unknown = set(supplied) - set(nodes_by_id)
    if unknown:
        raise ValueError(f"runtime state references unknown nodes: {sorted(unknown)}")
    normalized: dict[str, NodeState] = {}
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}
    for edge in edges:
        if edge.kind in _DEPENDENCY_KINDS:
            predecessors[edge.target_node_id].add(edge.source_node_id)
    for node_id, node in nodes_by_id.items():
        if not node.active:
            normalized[node_id] = "superseded"
            continue
        value = supplied.get(node_id, "")
        aliases = {
            "running": "active",
            "done": "completed",
            "complete": "completed",
            "failed": "blocked",
            "deferred": "planned",
            "pending": "planned",
        }
        value = aliases.get(value, value)
        if value in {"active", "completed", "blocked"}:
            normalized[node_id] = value  # type: ignore[assignment]
        else:
            normalized[node_id] = "planned"
    completed = {
        node_id for node_id, value in normalized.items() if value == "completed"
    }
    for node_id, value in tuple(normalized.items()):
        if value != "planned":
            continue
        if all(parent in completed for parent in predecessors[node_id]):
            normalized[node_id] = "ready"
    criterion_ids = _criterion_ids(contract)
    covered = {criterion for node in nodes for criterion in node.criterion_ids}
    values_for = lambda status: tuple(
        node_id for node_id in nodes_by_id if normalized[node_id] == status
    )
    return DerivedBlueprintState(
        ready_node_ids=values_for("ready"),
        deferred_node_ids=values_for("planned"),
        active_node_ids=values_for("active"),
        completed_node_ids=values_for("completed"),
        blocked_node_ids=values_for("blocked"),
        superseded_node_ids=values_for("superseded"),
        node_states=normalized,
        bounded_loop_node_ids=tuple(
            node.node_id for node in nodes if node.kind == "loop" and node.loop_policy
        ),
        uncovered_criterion_ids=tuple(sorted(criterion_ids - covered)),
    )


def _blueprint_content_hash(
    *,
    contract: TaskContract,
    nodes: Sequence[BlueprintNode],
    edges: Sequence[BlueprintEdge],
    family: TaskFamily,
) -> str:
    return _canonical_hash(
        {
            "contract": contract.model_dump(mode="json"),
            "nodes": [node.model_dump(mode="json") for node in nodes],
            "edges": [edge.model_dump(mode="json") for edge in edges],
            "family": family,
        }
    )


def create_blueprint(
    *,
    task_id: str,
    contract: TaskContract,
    family: TaskFamily = "general",
    nodes: Sequence[BlueprintNode | Mapping[str, Any]] | None = None,
    edges: Sequence[BlueprintEdge | Mapping[str, Any]] | None = None,
    blueprint_id: str | None = None,
    update_reason: str = "Initial task interpretation.",
    author: str = "system",
    evidence_refs: Sequence[str] = (),
    node_states: Mapping[str, str] | None = None,
) -> TaskBlueprint:
    blueprint_id = str(blueprint_id or f"bp-{_stable_token(task_id)}").strip()
    if nodes is None:
        topology_nodes, topology_edges = topology_for_family(
            family, task_id=task_id, contract=contract
        )
        nodes = topology_nodes
        if edges is None:
            edges = topology_edges
    typed_nodes = tuple(
        node if isinstance(node, BlueprintNode) else BlueprintNode.model_validate(node)
        for node in nodes or ()
    )
    typed_edges = tuple(
        edge if isinstance(edge, BlueprintEdge) else BlueprintEdge.model_validate(edge)
        for edge in edges or ()
    )
    state = derive_blueprint_state(
        contract, typed_nodes, typed_edges, node_states=node_states
    )
    now = _now()
    return TaskBlueprint(
        blueprint_id=blueprint_id,
        task_id=task_id,
        revision_id=f"{blueprint_id}.r1",
        revision=1,
        family=family,
        contract=contract,
        nodes=typed_nodes,
        edges=typed_edges,
        derived_state=state,
        update_reason=update_reason,
        provenance=RevisionProvenance(
            author=author,
            reason=update_reason,
            evidence_refs=tuple(evidence_refs),
        ),
        contract_hash=contract.contract_hash,
        content_hash=_blueprint_content_hash(
            contract=contract, nodes=typed_nodes, edges=typed_edges, family=family
        ),
        created_at=now,
        updated_at=now,
    )


def make_blueprint_patch(
    current: TaskBlueprint,
    operations: Sequence[BlueprintPatchOperation | Mapping[str, Any]],
    *,
    author: str,
    reason: str,
    evidence_refs: Sequence[str] = (),
    contract_amendment: ContractAmendment | Mapping[str, Any] | None = None,
    base_revision_id: str | None = None,
    patch_id: str | None = None,
) -> BlueprintPatch:
    typed_ops = tuple(
        (
            operation
            if isinstance(operation, BlueprintPatchOperation)
            else BlueprintPatchOperation.model_validate(operation)
        )
        for operation in operations
    )
    amendment = (
        contract_amendment
        if isinstance(contract_amendment, ContractAmendment)
        or contract_amendment is None
        else ContractAmendment.model_validate(contract_amendment)
    )
    return BlueprintPatch(
        patch_id=patch_id or f"patch-{uuid.uuid4().hex[:12]}",
        blueprint_id=current.blueprint_id,
        base_revision_id=base_revision_id or current.revision_id,
        base_revision=current.revision,
        author=author,
        reason=reason,
        evidence_refs=tuple(evidence_refs),
        operations=typed_ops,
        contract_amendment=amendment,
    )


def apply_blueprint_patch(
    current: TaskBlueprint,
    patch: BlueprintPatch,
) -> BlueprintUpdate:
    if patch.blueprint_id != current.blueprint_id:
        raise ValueError("patch targets a different blueprint")
    if (
        patch.base_revision_id != current.revision_id
        or patch.base_revision != current.revision
    ):
        raise ValueError("stale blueprint patch base revision")
    if not patch.reason.strip():
        raise ValueError("blueprint revisions require a reason")

    contract = _apply_contract_amendment(current.contract, patch.contract_amendment)
    nodes = {node.node_id: node for node in current.nodes}
    edges = {edge.edge_id: edge for edge in current.edges}
    states = dict(current.derived_state.node_states)

    for operation in patch.operations:
        contract = _apply_patch_operation(
            operation,
            contract=contract,
            nodes=nodes,
            edges=edges,
            states=states,
        )

    typed_nodes = tuple(nodes.values())
    typed_edges = tuple(edges.values())
    state = derive_blueprint_state(
        contract,
        typed_nodes,
        typed_edges,
        node_states=states,
    )
    newly_uncovered = set(state.uncovered_criterion_ids) - set(
        current.derived_state.uncovered_criterion_ids
    )
    if newly_uncovered:
        raise ValueError(
            "blueprint revision cannot remove acceptance coverage: "
            f"{sorted(newly_uncovered)}"
        )
    revision = current.revision + 1
    revision_id = f"{current.blueprint_id}.r{revision}"
    now = _now()
    provenance = RevisionProvenance(
        author=patch.author,
        reason=patch.reason,
        evidence_refs=patch.evidence_refs,
        proposal_id=patch.patch_id,
        approval_id=(
            patch.contract_amendment.approval_id if patch.contract_amendment else ""
        ),
        approval_scope_hash=(
            patch.contract_amendment.approval_scope_hash
            if patch.contract_amendment
            else ""
        ),
    )
    updated = TaskBlueprint(
        blueprint_id=current.blueprint_id,
        task_id=current.task_id,
        revision_id=revision_id,
        revision=revision,
        parent_revision_ids=(current.revision_id,),
        family=current.family,
        contract=contract,
        nodes=typed_nodes,
        edges=typed_edges,
        derived_state=state,
        update_reason=patch.reason,
        provenance=provenance,
        contract_hash=contract.contract_hash,
        content_hash=_blueprint_content_hash(
            contract=contract,
            nodes=typed_nodes,
            edges=typed_edges,
            family=current.family,
        ),
        created_at=current.created_at,
        updated_at=now,
    )
    event = BlueprintRevisionEvent(
        event_id=f"blueprint-event-{uuid.uuid4().hex[:12]}",
        blueprint_id=updated.blueprint_id,
        revision_id=updated.revision_id,
        parent_revision_ids=updated.parent_revision_ids,
        patch_id=patch.patch_id,
        author=patch.author,
        reason=patch.reason,
        evidence_refs=patch.evidence_refs,
        task_blueprint=updated,
        created_at=now,
    )
    return BlueprintUpdate(previous=current, current=updated, patch=patch, event=event)


def _apply_contract_amendment(
    current: TaskContract,
    amendment: ContractAmendment | None,
) -> TaskContract:
    if amendment is None:
        return current
    if amendment.approval_scope_hash != current.contract_hash:
        raise ValueError(
            "contract amendment approval is not bound to the current contract"
        )
    if not amendment.approval_id.strip() or not amendment.reason.strip():
        raise ValueError("contract amendment requires an explicit approval and reason")
    data = current.model_dump(mode="python")
    for key in (
        "goal",
        "non_goals",
        "constraints",
        "permissions",
        "risk",
        "budget",
        "acceptance_criteria",
    ):
        value = getattr(amendment, key)
        if value is not None:
            data[key] = value
    candidate = TaskContract.model_validate(data)
    _assert_criteria_not_weakened(current, candidate)
    return candidate


def _assert_criteria_not_weakened(old: TaskContract, new: TaskContract) -> None:
    old_by_id = {item.criterion_id: item for item in old.acceptance_criteria}
    new_by_id = {item.criterion_id: item for item in new.acceptance_criteria}
    missing = set(old_by_id) - set(new_by_id)
    if missing:
        raise ValueError(f"acceptance criteria cannot be removed: {sorted(missing)}")
    for criterion_id, before in old_by_id.items():
        after = new_by_id[criterion_id]
        if before.description != after.description:
            raise ValueError(f"criterion {criterion_id!r} cannot be rewritten")
        if before.required and not after.required:
            raise ValueError(f"criterion {criterion_id!r} cannot be made optional")
        if not set(before.evidence_required).issubset(after.evidence_required):
            raise ValueError(
                f"criterion {criterion_id!r} evidence requirements were weakened"
            )
        if before.approval_required and not after.approval_required:
            raise ValueError(f"criterion {criterion_id!r} approval cannot be removed")


def _pinned(node_id: str, states: Mapping[str, str]) -> bool:
    return states.get(node_id) in _PINNED_STATES


def _node_from_data(data: Mapping[str, Any], key: str = "node") -> BlueprintNode:
    value = data.get(key)
    if value is None:
        value = data
    return (
        value
        if isinstance(value, BlueprintNode)
        else BlueprintNode.model_validate(value)
    )


def _edge_from_data(data: Mapping[str, Any], key: str = "edge") -> BlueprintEdge:
    value = data.get(key)
    if value is None:
        value = data
    return (
        value
        if isinstance(value, BlueprintEdge)
        else BlueprintEdge.model_validate(value)
    )


def _assert_mutable_node(
    node_id: str, nodes: Mapping[str, BlueprintNode], states: Mapping[str, str]
) -> None:
    if node_id not in nodes:
        raise ValueError(f"unknown node {node_id!r}")
    if _pinned(node_id, states):
        raise ValueError(
            f"active/completed node {node_id!r} is pinned to its execution attempt"
        )


def _apply_patch_operation(
    operation: BlueprintPatchOperation,
    *,
    contract: TaskContract,
    nodes: dict[str, BlueprintNode],
    edges: dict[str, BlueprintEdge],
    states: dict[str, str],
) -> TaskContract:
    op = operation.op
    data = operation.data
    if op == "add_node":
        node = _node_from_data(data)
        if node.node_id in nodes:
            raise ValueError(f"node {node.node_id!r} already exists")
        nodes[node.node_id] = node
        states[node.node_id] = "planned"
    elif op == "revise_node":
        node = _node_from_data(data)
        _assert_mutable_node(node.node_id, nodes, states)
        before = nodes[node.node_id]
        if (
            before.supersedes != node.supersedes
            or before.superseded_by != node.superseded_by
        ):
            raise ValueError("use a supersession operation to change node lineage")
        nodes[node.node_id] = node
    elif op == "retire_node":
        node_id = str(data.get("node_id") or "")
        _assert_mutable_node(node_id, nodes, states)
        before = nodes[node_id]
        tombstone = str(data.get("tombstone_id") or f"{node_id}:retired")
        if tombstone in nodes:
            raise ValueError(f"node {tombstone!r} already exists")
        nodes[tombstone] = BlueprintNode(
            node_id=tombstone,
            kind="artifact",
            title=f"Retired: {before.title}",
            description=str(data.get("reason") or "Semantic work retired by revision."),
            topology_role="tombstone",
            supersedes=(node_id,),
        )
        nodes[node_id] = before.model_copy(
            update={"active": False, "superseded_by": (tombstone,)}
        )
        states[node_id] = "superseded"
        states[tombstone] = "completed"
    elif op == "supersede_node":
        node_id = str(data.get("node_id") or "")
        _assert_mutable_node(node_id, nodes, states)
        replacement = _node_from_data(data, "replacement")
        if replacement.node_id in nodes:
            raise ValueError(f"node {replacement.node_id!r} already exists")
        replacement = replacement.model_copy(
            update={"supersedes": _clean_tuple((*replacement.supersedes, node_id))}
        )
        nodes[replacement.node_id] = replacement
        nodes[node_id] = nodes[node_id].model_copy(
            update={"active": False, "superseded_by": (replacement.node_id,)}
        )
        states[node_id] = "superseded"
        states[replacement.node_id] = "planned"
    elif op == "split_node":
        node_id = str(data.get("node_id") or "")
        _assert_mutable_node(node_id, nodes, states)
        children = tuple(
            _node_from_data({"node": item}) for item in data.get("nodes") or ()
        )
        if len(children) < 2:
            raise ValueError("split_node requires at least two replacement nodes")
        if any(child.node_id in nodes for child in children):
            raise ValueError("split replacement node id already exists")
        child_ids = tuple(child.node_id for child in children)
        nodes[node_id] = nodes[node_id].model_copy(
            update={"active": False, "superseded_by": child_ids}
        )
        states[node_id] = "superseded"
        for child in children:
            nodes[child.node_id] = child.model_copy(
                update={"supersedes": _clean_tuple((*child.supersedes, node_id))}
            )
            states[child.node_id] = "planned"
    elif op == "merge_nodes":
        node_ids = _clean_tuple(data.get("node_ids") or ())
        if len(node_ids) < 2:
            raise ValueError("merge_nodes requires at least two source nodes")
        for node_id in node_ids:
            _assert_mutable_node(node_id, nodes, states)
        merged = _node_from_data(data, "merged_node")
        if merged.node_id in nodes:
            raise ValueError(f"node {merged.node_id!r} already exists")
        nodes[merged.node_id] = merged.model_copy(
            update={"supersedes": _clean_tuple((*merged.supersedes, *node_ids))}
        )
        states[merged.node_id] = "planned"
        for node_id in node_ids:
            nodes[node_id] = nodes[node_id].model_copy(
                update={"active": False, "superseded_by": (merged.node_id,)}
            )
            states[node_id] = "superseded"
    elif op == "add_edge":
        edge = _edge_from_data(data)
        if edge.edge_id in edges:
            raise ValueError(f"edge {edge.edge_id!r} already exists")
        if _pinned(edge.source_node_id, states) or _pinned(edge.target_node_id, states):
            raise ValueError("cannot rewire an active/completed node")
        edges[edge.edge_id] = edge
    elif op == "remove_edge":
        edge_id = str(data.get("edge_id") or "")
        edge = edges.get(edge_id)
        if edge is None:
            raise ValueError(f"unknown edge {edge_id!r}")
        if _pinned(edge.source_node_id, states) or _pinned(edge.target_node_id, states):
            raise ValueError("cannot rewire an active/completed node")
        del edges[edge_id]
    elif op == "revise_dependency":
        edge_id = str(data.get("edge_id") or "")
        existing = edges.get(edge_id)
        if existing is None:
            raise ValueError(f"unknown edge {edge_id!r}")
        replacement = _edge_from_data(data, "edge")
        if replacement.edge_id != edge_id:
            raise ValueError("dependency revision must retain edge identity")
        for node_id in {
            existing.source_node_id,
            existing.target_node_id,
            replacement.source_node_id,
            replacement.target_node_id,
        }:
            if _pinned(node_id, states):
                raise ValueError("cannot rewire an active/completed node")
        edges[edge_id] = replacement
    elif op == "attach_criterion":
        node_id = str(data.get("node_id") or "")
        _assert_mutable_node(node_id, nodes, states)
        criterion_id = str(data.get("criterion_id") or "")
        if criterion_id not in _criterion_ids(contract):
            raise ValueError(f"unknown acceptance criterion {criterion_id!r}")
        node = nodes[node_id]
        nodes[node_id] = node.model_copy(
            update={"criterion_ids": _clean_tuple((*node.criterion_ids, criterion_id))}
        )
    elif op == "attach_evidence":
        node_id = str(data.get("node_id") or "")
        _assert_mutable_node(node_id, nodes, states)
        evidence_refs = _clean_tuple(data.get("evidence_refs") or ())
        if not evidence_refs:
            raise ValueError("attach_evidence requires immutable evidence references")
        node = nodes[node_id]
        nodes[node_id] = node.model_copy(
            update={
                "evidence_refs": _clean_tuple((*node.evidence_refs, *evidence_refs))
            }
        )
    elif op == "reopen_node":
        node_id = str(data.get("node_id") or "")
        if states.get(node_id) != "completed":
            raise ValueError("only a completed node can be reopened")
        reopened = _node_from_data(data, "reopened_node")
        if reopened.node_id in nodes:
            raise ValueError(f"node {reopened.node_id!r} already exists")
        nodes[reopened.node_id] = reopened.model_copy(
            update={"supersedes": _clean_tuple((*reopened.supersedes, node_id))}
        )
        states[reopened.node_id] = "planned"
    elif op == "branch_node":
        source_id = str(data.get("source_node_id") or "")
        _assert_mutable_node(source_id, nodes, states)
        branch = _node_from_data(data, "branch_node")
        if branch.node_id in nodes:
            raise ValueError(f"node {branch.node_id!r} already exists")
        edge_id = str(
            data.get("edge_id") or f"edge:{source_id}:{branch.node_id}:alternative"
        )
        nodes[branch.node_id] = branch
        states[branch.node_id] = "planned"
        edges[edge_id] = BlueprintEdge(
            edge_id=edge_id,
            source_node_id=source_id,
            target_node_id=branch.node_id,
            kind="alternative",
            condition=str(data.get("condition") or ""),
        )
    elif op == "narrow_permissions":
        permissions = _clean_tuple(data.get("permissions") or ())
        if not set(permissions).issubset(contract.permissions):
            raise ValueError("ordinary patches may only narrow permissions")
        contract = contract.model_copy(update={"permissions": permissions})
    elif op == "strengthen_criteria":
        criterion = data.get("criterion") or data
        typed = (
            criterion
            if isinstance(criterion, AcceptanceCriterion)
            else AcceptanceCriterion.model_validate(criterion)
        )
        if typed.criterion_id in _criterion_ids(contract):
            raise ValueError("strengthen_criteria adds a new stable criterion id")
        contract = contract.model_copy(
            update={"acceptance_criteria": (*contract.acceptance_criteria, typed)}
        )
    return contract


def sync_blueprint_graph(
    current: TaskBlueprint,
    nodes: Sequence[BlueprintNode | Mapping[str, Any]],
    edges: Sequence[BlueprintEdge | Mapping[str, Any]],
    *,
    author: str,
    reason: str,
    evidence_refs: Sequence[str] = (),
    node_states: Mapping[str, str] | None = None,
) -> BlueprintUpdate:
    """Admit a proposed semantic snapshot while pinning active/completed work."""

    proposed_nodes = {
        node.node_id: node
        for node in (
            (
                item
                if isinstance(item, BlueprintNode)
                else BlueprintNode.model_validate(item)
            )
            for item in nodes
        )
    }
    proposed_edges = {
        edge.edge_id: edge
        for edge in (
            (
                item
                if isinstance(item, BlueprintEdge)
                else BlueprintEdge.model_validate(item)
            )
            for item in edges
        )
    }
    current_nodes = {node.node_id: node for node in current.nodes}
    states = dict(current.derived_state.node_states)
    for node_id, before in current_nodes.items():
        if _pinned(node_id, states):
            if proposed_nodes.get(node_id) != before:
                raise ValueError(f"proposed graph rewrites pinned node {node_id!r}")
        elif node_id not in proposed_nodes:
            tombstone_id = f"{node_id}:retired:r{current.revision + 1}"
            proposed_nodes[node_id] = before.model_copy(
                update={"active": False, "superseded_by": (tombstone_id,)}
            )
            proposed_nodes[tombstone_id] = BlueprintNode(
                node_id=tombstone_id,
                kind="artifact",
                title=f"Retired: {before.title}",
                topology_role="tombstone",
                supersedes=(node_id,),
            )
            states[node_id] = "superseded"
            states[tombstone_id] = "completed"
    current_edges = {edge.edge_id: edge for edge in current.edges}
    for edge_id, before in current_edges.items():
        if _pinned(before.source_node_id, states) or _pinned(
            before.target_node_id, states
        ):
            if proposed_edges.get(edge_id) != before:
                raise ValueError(f"proposed graph rewrites pinned edge {edge_id!r}")
    for node_id in proposed_nodes:
        states.setdefault(node_id, "planned")
    proposed_state = dict(node_states or {})
    for node_id, prior_state in states.items():
        requested_state = str(proposed_state.get(node_id) or prior_state).lower()
        if prior_state == "completed" and requested_state not in {
            "completed",
            "done",
            "complete",
        }:
            raise ValueError(f"completed node {node_id!r} cannot be downgraded")
        if prior_state == "active" and requested_state not in {
            "active",
            "running",
            "completed",
            "done",
            "complete",
            "blocked",
            "failed",
        }:
            raise ValueError(f"active node {node_id!r} cannot return to pending work")
    for node_id, value in states.items():
        proposed_state.setdefault(node_id, value)

    revision = current.revision + 1
    now = _now()
    typed_nodes = tuple(proposed_nodes.values())
    typed_edges = tuple(proposed_edges.values())
    derived = derive_blueprint_state(
        current.contract,
        typed_nodes,
        typed_edges,
        node_states=proposed_state,
    )
    newly_uncovered = set(derived.uncovered_criterion_ids) - set(
        current.derived_state.uncovered_criterion_ids
    )
    if newly_uncovered:
        raise ValueError(
            "semantic graph sync cannot remove acceptance coverage: "
            f"{sorted(newly_uncovered)}"
        )
    patch = make_blueprint_patch(
        current,
        (),
        author=author,
        reason=reason,
        evidence_refs=evidence_refs,
    )
    updated = TaskBlueprint(
        blueprint_id=current.blueprint_id,
        task_id=current.task_id,
        revision_id=f"{current.blueprint_id}.r{revision}",
        revision=revision,
        parent_revision_ids=(current.revision_id,),
        family=current.family,
        contract=current.contract,
        nodes=typed_nodes,
        edges=typed_edges,
        derived_state=derived,
        update_reason=reason,
        provenance=RevisionProvenance(
            author=author,
            reason=reason,
            evidence_refs=tuple(evidence_refs),
            proposal_id=patch.patch_id,
        ),
        contract_hash=current.contract_hash,
        content_hash=_blueprint_content_hash(
            contract=current.contract,
            nodes=typed_nodes,
            edges=typed_edges,
            family=current.family,
        ),
        created_at=current.created_at,
        updated_at=now,
    )
    event = BlueprintRevisionEvent(
        event_id=f"blueprint-event-{uuid.uuid4().hex[:12]}",
        blueprint_id=updated.blueprint_id,
        revision_id=updated.revision_id,
        parent_revision_ids=updated.parent_revision_ids,
        patch_id=patch.patch_id,
        author=author,
        reason=reason,
        evidence_refs=tuple(evidence_refs),
        task_blueprint=updated,
        created_at=now,
    )
    return BlueprintUpdate(previous=current, current=updated, patch=patch, event=event)


def replay_blueprint_events(events: Sequence[BlueprintRevisionEvent]) -> TaskBlueprint:
    if not events:
        raise ValueError("cannot replay an empty blueprint event stream")
    current: TaskBlueprint | None = None
    seen: set[str] = set()
    for event in events:
        blueprint = event.task_blueprint
        if event.revision_id != blueprint.revision_id:
            raise ValueError("blueprint event revision payload mismatch")
        if blueprint.revision_id in seen:
            raise ValueError("duplicate blueprint revision event")
        if (
            current is not None
            and current.revision_id not in blueprint.parent_revision_ids
        ):
            raise ValueError("blueprint event lineage is discontinuous")
        seen.add(blueprint.revision_id)
        current = blueprint
    assert current is not None
    return current


def execution_attempt_for_blueprint(
    blueprint: TaskBlueprint,
    *,
    attempt_id: str | None = None,
    run_id: str = "",
    backend: str = "super_dan",
    worker_summary: Sequence[str] = (),
    model_summary: Sequence[str] = (),
    tool_summary: Sequence[str] = (),
    max_retries: int | None = None,
    schedule: str = "",
) -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_id=attempt_id or f"attempt-{uuid.uuid4().hex[:12]}",
        task_id=blueprint.task_id,
        blueprint_id=blueprint.blueprint_id,
        blueprint_revision_id=blueprint.revision_id,
        blueprint_revision=blueprint.revision,
        run_id=run_id,
        backend=backend,
        policy_snapshot=ExecutionPolicySnapshot(
            permission_scope=blueprint.contract.permissions,
            risk_level=blueprint.contract.risk.level,
            budget=blueprint.contract.budget,
            contract_hash=blueprint.contract_hash,
        ),
        worker_summary=tuple(worker_summary),
        model_summary=tuple(model_summary),
        tool_summary=tuple(tool_summary),
        max_retries=max_retries,
        schedule=schedule,
        node_states=dict(blueprint.derived_state.node_states),
    )


def revise_execution_attempt(
    attempt: ExecutionAttempt,
    *,
    status: AttemptStatus | None = None,
    phase: AttemptPhase | None = None,
    worker_summary: Sequence[str] | None = None,
    model_summary: Sequence[str] | None = None,
    tool_summary: Sequence[str] | None = None,
    retry_count: int | None = None,
    node_states: Mapping[str, str] | None = None,
    results: Sequence[ExecutionResult | Mapping[str, Any]] | None = None,
    now: str | None = None,
) -> ExecutionAttempt:
    timestamp = now or _now()
    next_status = status or attempt.status
    update: dict[str, Any] = {
        "status": next_status,
        "phase": phase or attempt.phase,
    }
    if worker_summary is not None:
        update["worker_summary"] = tuple(worker_summary)
    if model_summary is not None:
        update["model_summary"] = tuple(model_summary)
    if tool_summary is not None:
        update["tool_summary"] = tuple(tool_summary)
    if retry_count is not None:
        update["retry_count"] = retry_count
    if node_states is not None:
        update["node_states"] = {
            str(node_id): str(state)
            for node_id, state in node_states.items()
            if str(node_id).strip()
        }
    if results is not None:
        update["results"] = tuple(
            (
                item
                if isinstance(item, ExecutionResult)
                else ExecutionResult.model_validate(item)
            )
            for item in results
        )
    if next_status == "running" and not attempt.started_at:
        update["started_at"] = timestamp
    if next_status in {"completed", "failed", "blocked", "cancelled"}:
        update["completed_at"] = timestamp
        if next_status == "completed":
            update["phase"] = "completed"
    return ExecutionAttempt.model_validate(
        {**attempt.model_dump(mode="python"), **update}
    )


def infer_task_family(objective: str) -> TaskFamily:
    text = str(objective or "").lower()
    patterns: tuple[tuple[TaskFamily, tuple[str, ...]], ...] = (
        (
            "manufacturing",
            ("manufactur", "fabricat", "cad", "dfm", "prototype quote", "supplier"),
        ),
        (
            "meeting",
            (
                "meeting",
                "agenda",
                "minutes",
                "participants",
                "workstream",
                "decision log",
            ),
        ),
        (
            "debugging",
            ("debug", "bug", "repair", "fix", "failure", "regression", "broken"),
        ),
        (
            "research",
            (
                "research",
                "investigate",
                "evidence",
                "sources",
                "literature",
                "compare claims",
            ),
        ),
        ("design", ("design", "variant", "concept", "creative", "mockup", "prototype")),
    )
    for family, terms in patterns:
        if any(term in text for term in terms):
            return family
    action_terms = (
        "create",
        "write",
        "move",
        "copy",
        "rename",
        "send",
        "answer",
        "summarize",
    )
    if len(text.split()) <= 32 and any(term in text for term in action_terms):
        return "direct"
    return "general"


def _criterion_ids_for_gate(contract: TaskContract) -> tuple[str, ...]:
    return tuple(item.criterion_id for item in contract.acceptance_criteria)


def topology_for_family(
    family: TaskFamily,
    *,
    task_id: str,
    contract: TaskContract,
) -> tuple[tuple[BlueprintNode, ...], tuple[BlueprintEdge, ...]]:
    prefix = _stable_token(task_id, fallback="task")
    criteria = _criterion_ids_for_gate(contract)

    def node(
        token: str,
        title: str,
        role: str,
        *,
        kind: NodeKind = "work",
        criterion_ids: tuple[str, ...] = (),
        loop: LoopPolicy | None = None,
    ) -> BlueprintNode:
        return BlueprintNode(
            node_id=f"{prefix}:{token}",
            kind=kind,
            title=title,
            topology_role=role,
            criterion_ids=criterion_ids,
            loop_policy=loop,
        )

    def edge(
        token: str,
        source: BlueprintNode,
        target: BlueprintNode,
        *,
        kind: EdgeKind = "dependency",
        loop_node: BlueprintNode | None = None,
        condition: str = "",
    ) -> BlueprintEdge:
        return BlueprintEdge(
            edge_id=f"{prefix}:edge:{token}",
            source_node_id=source.node_id,
            target_node_id=target.node_id,
            kind=kind,
            loop_node_id=loop_node.node_id if loop_node else None,
            condition=condition,
        )

    if family == "direct":
        fulfill = node("fulfill", "Fulfil the requested action", "direct_action")
        confirm = node(
            "confirm",
            "Confirm the requested outcome",
            "outcome_check",
            kind="gate",
            criterion_ids=criteria,
        )
        return (fulfill, confirm), (
            edge("fulfill-confirm", fulfill, confirm, kind="validates"),
        )

    if family == "debugging":
        observe = node("observe", "Observe the failure", "observation")
        reproduce = node("reproduce", "Reproduce and bound the failure", "reproduction")
        diagnose = node(
            "diagnose",
            "Form competing hypotheses",
            "hypothesis_fanout",
            kind="composite",
        )
        hypothesis_a = node("hypothesis-a", "Probe primary hypothesis", "hypothesis")
        hypothesis_b = node(
            "hypothesis-b", "Probe alternative hypothesis", "hypothesis"
        )
        patch = node("patch", "Apply the smallest supported repair", "repair")
        verify = node(
            "verify",
            "Run focused and regression checks",
            "regression_gate",
            kind="gate",
            criterion_ids=criteria,
        )
        loop = node(
            "repair-loop",
            "Revise diagnosis when evidence rejects the repair",
            "repair_loop",
            kind="loop",
            loop=LoopPolicy(
                max_iterations=3, exit_condition="Focused and regression checks pass."
            ),
        )
        nodes = (
            observe,
            reproduce,
            diagnose,
            hypothesis_a,
            hypothesis_b,
            patch,
            verify,
            loop,
        )
        edges = (
            edge("observe-reproduce", observe, reproduce),
            edge("reproduce-diagnose", reproduce, diagnose),
            edge(
                "diagnose-ha",
                diagnose,
                hypothesis_a,
                kind="alternative",
                condition="primary cause",
            ),
            edge(
                "diagnose-hb",
                diagnose,
                hypothesis_b,
                kind="alternative",
                condition="alternative cause",
            ),
            edge("ha-patch", hypothesis_a, patch, kind="alternative"),
            edge("hb-patch", hypothesis_b, patch, kind="alternative"),
            edge("patch-verify", patch, verify, kind="validates"),
            edge("verify-loop", verify, loop, kind="validates"),
            edge(
                "loop-diagnose",
                loop,
                diagnose,
                kind="feedback",
                loop_node=loop,
                condition="checks fail with new evidence",
            ),
        )
        return nodes, edges

    if family == "research":
        frame = node("frame", "Frame questions and claim boundaries", "question_frame")
        question_a = node(
            "question-a", "Investigate primary question", "research_question"
        )
        question_b = node(
            "question-b", "Investigate counter-question", "research_question"
        )
        evidence_a = node("evidence-a", "Collect primary evidence", "evidence")
        evidence_b = node("evidence-b", "Collect counter-evidence", "evidence")
        claims = node(
            "claims",
            "Connect claims to evidence",
            "claim_source_matrix",
            kind="artifact",
        )
        synthesis = node(
            "synthesis",
            "Synthesize findings and uncertainty",
            "synthesis",
            kind="artifact",
        )
        coverage = node(
            "coverage",
            "Review coverage and uncertainty",
            "coverage_gate",
            kind="gate",
            criterion_ids=criteria,
        )
        nodes = (
            frame,
            question_a,
            question_b,
            evidence_a,
            evidence_b,
            claims,
            synthesis,
            coverage,
        )
        edges = (
            edge("frame-qa", frame, question_a),
            edge("frame-qb", frame, question_b),
            edge("qa-ea", question_a, evidence_a),
            edge("qb-eb", question_b, evidence_b),
            edge("ea-claims", evidence_a, claims, kind="produces"),
            edge("eb-claims", evidence_b, claims, kind="produces"),
            edge("claims-synthesis", claims, synthesis, kind="produces"),
            edge("synthesis-coverage", synthesis, coverage, kind="validates"),
        )
        return nodes, edges

    if family == "design":
        constraints = node(
            "constraints", "Gather references and constraints", "constraints"
        )
        variant_a = node("variant-a", "Develop first variant", "variant")
        variant_b = node("variant-b", "Develop contrasting variant", "variant")
        variant_c = node("variant-c", "Develop exploratory variant", "variant")
        critique = node(
            "critique", "Critique variants against intent", "critique", kind="gate"
        )
        select = node("select", "Select a direction", "selection", kind="decision")
        refine = node("refine", "Refine the selected direction", "refinement")
        package = node(
            "package",
            "Package the chosen artifact",
            "design_artifact",
            kind="artifact",
            criterion_ids=criteria,
        )
        loop = node(
            "iteration-loop",
            "Iterate when critique exposes a material gap",
            "design_loop",
            kind="loop",
            loop=LoopPolicy(
                max_iterations=3,
                exit_condition="Selected variant satisfies constraints and acceptance criteria.",
            ),
        )
        nodes = (
            constraints,
            variant_a,
            variant_b,
            variant_c,
            critique,
            select,
            refine,
            package,
            loop,
        )
        edges = (
            edge("constraints-va", constraints, variant_a, kind="alternative"),
            edge("constraints-vb", constraints, variant_b, kind="alternative"),
            edge("constraints-vc", constraints, variant_c, kind="alternative"),
            edge("va-critique", variant_a, critique, kind="alternative"),
            edge("vb-critique", variant_b, critique, kind="alternative"),
            edge("vc-critique", variant_c, critique, kind="alternative"),
            edge("critique-select", critique, select),
            edge("select-refine", select, refine),
            edge("refine-package", refine, package, kind="produces"),
            edge("package-loop", package, loop, kind="validates"),
            edge(
                "loop-variants",
                loop,
                constraints,
                kind="feedback",
                loop_node=loop,
                condition="material critique remains",
            ),
        )
        return nodes, edges

    if family == "meeting":
        capture = node(
            "capture", "Capture proposals, constraints, and questions", "listen_capture"
        )
        decisions = node(
            "decisions",
            "Separate proposals from confirmed decisions",
            "decision_ledger",
            kind="decision",
        )
        work_a = node("workstream-a", "Advance first workstream", "workstream")
        work_b = node("workstream-b", "Advance parallel workstream", "workstream")
        reconcile = node(
            "reconcile",
            "Reconcile conflicts and dependencies",
            "reconciliation",
            kind="decision",
        )
        approval = node(
            "approval",
            "Request explicit approval for commitments",
            "approval",
            kind="gate",
        )
        actions = node(
            "actions",
            "Publish decisions, owners, and actions",
            "action_ledger",
            kind="artifact",
            criterion_ids=criteria,
        )
        nodes = (capture, decisions, work_a, work_b, reconcile, approval, actions)
        edges = (
            edge("capture-decisions", capture, decisions),
            edge("decisions-wa", decisions, work_a),
            edge("decisions-wb", decisions, work_b),
            edge("wa-reconcile", work_a, reconcile),
            edge("wb-reconcile", work_b, reconcile),
            edge("reconcile-approval", reconcile, approval, kind="validates"),
            edge("approval-actions", approval, actions, kind="produces"),
        )
        return nodes, edges

    if family == "manufacturing":
        intent = node("intent", "Preserve intended use and non-goals", "product_intent")
        specification = node(
            "specification",
            "Build the process-specific specification",
            "specification",
            kind="artifact",
        )
        projections = node(
            "projections",
            "Generate engineering projections",
            "engineering_projection",
            kind="composite",
        )
        dfm = node("dfm", "Review manufacturability", "dfm_gate", kind="gate")
        compliance = node(
            "compliance",
            "Review compliance and responsibility",
            "compliance_gate",
            kind="gate",
        )
        quote = node(
            "quote",
            "Prepare a comparable prototype quote packet",
            "quote_comparison",
            kind="artifact",
        )
        approval = node(
            "sample-approval",
            "Require human sample-release approval",
            "approval",
            kind="gate",
            criterion_ids=criteria,
        )
        loop = node(
            "specification-loop",
            "Revise the package after DFM or compliance findings",
            "specification_loop",
            kind="loop",
            loop=LoopPolicy(
                max_iterations=3,
                exit_condition="DFM and compliance gates pass for a prototype-only package.",
            ),
        )
        nodes = (
            intent,
            specification,
            projections,
            dfm,
            compliance,
            quote,
            approval,
            loop,
        )
        edges = (
            edge("intent-spec", intent, specification, kind="produces"),
            edge("spec-projections", specification, projections, kind="produces"),
            edge("projections-dfm", projections, dfm, kind="validates"),
            edge("projections-compliance", projections, compliance, kind="validates"),
            edge("dfm-quote", dfm, quote),
            edge("compliance-quote", compliance, quote),
            edge("quote-approval", quote, approval, kind="validates"),
            edge("approval-loop", approval, loop, kind="validates"),
            edge(
                "loop-spec",
                loop,
                specification,
                kind="feedback",
                loop_node=loop,
                condition="DFM/compliance/sample review requires revision",
            ),
        )
        return nodes, edges

    understand = node(
        "understand", "Understand the task and constraints", "understanding"
    )
    execute = node("execute", "Produce the intended result", "execution")
    validate = node(
        "validate",
        "Validate the intended result",
        "acceptance_gate",
        kind="gate",
        criterion_ids=criteria,
    )
    return (understand, execute, validate), (
        edge("understand-execute", understand, execute),
        edge("execute-validate", execute, validate, kind="validates"),
    )


__all__ = [
    "AcceptanceCriterion",
    "BlueprintEdge",
    "BlueprintNode",
    "BlueprintPatch",
    "BlueprintPatchOperation",
    "BlueprintRevisionEvent",
    "BlueprintUpdate",
    "BudgetLimits",
    "ContractAmendment",
    "DerivedBlueprintState",
    "ExecutionAttempt",
    "ExecutionPolicySnapshot",
    "ExecutionResult",
    "LoopPolicy",
    "RevisionProvenance",
    "RiskPolicy",
    "TaskBlueprint",
    "TaskContract",
    "apply_blueprint_patch",
    "create_blueprint",
    "derive_blueprint_state",
    "execution_attempt_for_blueprint",
    "infer_task_family",
    "make_blueprint_patch",
    "replay_blueprint_events",
    "revise_execution_attempt",
    "sync_blueprint_graph",
    "topology_for_family",
]
