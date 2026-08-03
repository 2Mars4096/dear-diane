"""Reduced Universal Cell contracts compiled onto the existing worker core.

The position-independent cell definition contains only flexible ``context`` and
one typed ``contract``. Execution mechanism is bound separately:

``CellSpec(context, contract) + ExecutorRef -> CellInvocation``

Sequence and fork/join relationships remain in ``CellTopology``. Execution
produces a compact ``CellReport`` with an extensible record ledger rather than
embedding topology, status, model selection, or execution history in the cell.

The original runtime remains compiler output:
``CellInvocation -> WorkerBrief -> ExecutionRequest -> WorkerCoreExecutor``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from fnmatch import fnmatchcase
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dan.worker.brief import RoleSpec, WorkerBrief, request_from_brief
from dan.worker.cell import build_cell
from dan.worker.contracts.sampling import SamplingPolicy, resolve_sampling_policy
from dan.worker.core.contracts import (
    EvidenceBlock,
    ExecutionRequest,
    OutputContract,
    ToolUseContract,
)
from dan.worker.core.executor import WorkerExecutionResult
from dan.worker.core.model import WorkerDefinition

CHILD_JOIN_CONTEXT_KEY = "_cell_child_join"
CHILD_REPORTS_CONTEXT_KEY = "_cell_child_reports"
LINEAGE_CONTEXT_KEY = "_cell_lineage"
PREVIOUS_REPORT_CONTEXT_KEY = "_cell_previous_report"

CellOutcome = Literal["completed", "failed", "blocked"]
JoinMode = Literal["all_success", "all_settled", "quorum", "at_least_one"]


def _unique_strings(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _context_hash(context: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json_text(context).encode("utf-8")).hexdigest()


def _path_parts(path: str) -> tuple[str, ...]:
    text = str(path or "").strip()
    if not text:
        raise ValueError("context paths must be non-empty")
    if text.startswith("/"):
        parts = tuple(
            part.replace("~1", "/").replace("~0", "~")
            for part in text.split("/")[1:]
            if part
        )
    else:
        parts = tuple(part for part in text.split(".") if part)
    if not parts:
        raise ValueError(f"invalid context path: {path!r}")
    return parts


def _get_path(context: Mapping[str, Any], path: str) -> Any:
    current: Any = context
    for part in _path_parts(path):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def _set_path(context: dict[str, Any], path: str, value: Any) -> None:
    parts = _path_parts(path)
    current = context
    for part in parts[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            existing = {}
            current[part] = existing
        current = existing
    current[parts[-1]] = deepcopy(value)


def _delete_path(context: dict[str, Any], path: str) -> None:
    parts = _path_parts(path)
    current: Any = context
    parents: list[tuple[dict[str, Any], str]] = []
    for part in parts[:-1]:
        if not isinstance(current, dict) or not isinstance(current.get(part), dict):
            return
        parents.append((current, part))
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            parent.pop(key, None)


def _merge_monotone_mapping(
    parent: Mapping[str, Any],
    child: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    merged = deepcopy(dict(parent))
    for key, value in child.items():
        if key in merged and merged[key] != value:
            raise ValueError(f"child {label} cannot replace parent key {key!r}")
        merged[key] = deepcopy(value)
    return merged


class ContextProjection(BaseModel):
    """Selected logical context plus the bounded payload rendered to the executor."""

    model_config = ConfigDict(frozen=True)

    selected_context: dict[str, Any] = Field(default_factory=dict)
    model_context: dict[str, Any] = Field(default_factory=dict)
    selected_chars: int = 0
    model_chars: int = 0
    truncated: bool = False
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()


class ContextView(BaseModel):
    """Explicit nested projection used at transfer or executor boundaries."""

    model_config = ConfigDict(frozen=True)

    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    max_rendered_chars: int | None = Field(default=None, ge=128)
    require_included_paths: bool = True

    @field_validator("include_paths", "exclude_paths", mode="before")
    @classmethod
    def _normalize_paths(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            value = (value,)
        normalized = _unique_strings(tuple(value))
        for path in normalized:
            _path_parts(path)
        return normalized

    def project(self, context: Mapping[str, Any]) -> ContextProjection:
        source = deepcopy(dict(context))
        selected: dict[str, Any]
        if self.include_paths:
            selected = {}
            for path in self.include_paths:
                try:
                    value = _get_path(source, path)
                except KeyError:
                    if self.require_included_paths:
                        raise ValueError(
                            f"context view selected missing path {path!r}"
                        ) from None
                    continue
                _set_path(selected, path, value)
        else:
            selected = source
        for path in self.exclude_paths:
            _delete_path(selected, path)

        rendered = _json_text(selected)
        truncated = (
            self.max_rendered_chars is not None
            and len(rendered) > self.max_rendered_chars
        )
        if truncated:
            limit = int(self.max_rendered_chars or 0)
            model_context = {
                "_bounded_context_json": rendered[:limit],
                "_context_projection": {
                    "truncated": True,
                    "selected_chars": len(rendered),
                    "preview_chars": limit,
                    "logical_context_available_by_reference": True,
                },
            }
        else:
            model_context = deepcopy(selected)
        return ContextProjection(
            selected_context=selected,
            model_context=model_context,
            selected_chars=len(rendered),
            model_chars=len(_json_text(model_context)),
            truncated=truncated,
            include_paths=self.include_paths,
            exclude_paths=self.exclude_paths,
        )


class CellRecord(BaseModel):
    """One runtime-observed source, modification, artifact, event, or future fact."""

    model_config = ConfigDict(frozen=True)

    kind: str
    resource: str = ""
    role: str = ""
    digest: str | None = None
    location: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind_is_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("record kind must be non-empty")
        return text

    @field_validator("resource", "role")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        return str(value or "").strip()


class RecordRequirement(BaseModel):
    """Acceptance predicate requiring matching runtime-authored records."""

    model_config = ConfigDict(frozen=True)

    kind: str
    role: str | None = None
    media_type: str | None = None
    resource_pattern: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    min_count: int = Field(default=1, ge=1)

    @field_validator("kind")
    @classmethod
    def _kind_is_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("record requirement kind must be non-empty")
        return text

    def matches(self, record: CellRecord) -> bool:
        if record.kind != self.kind:
            return False
        if self.role is not None and record.role != self.role:
            return False
        if (
            self.media_type is not None
            and record.metadata.get("media_type") != self.media_type
        ):
            return False
        if self.resource_pattern is not None and not fnmatchcase(
            record.resource,
            self.resource_pattern,
        ):
            return False
        return all(
            record.metadata.get(key) == value for key, value in self.metadata.items()
        )

    def describe(self) -> str:
        qualifiers = [f"kind={self.kind!r}"]
        if self.role is not None:
            qualifiers.append(f"role={self.role!r}")
        if self.media_type is not None:
            qualifiers.append(f"media_type={self.media_type!r}")
        if self.resource_pattern is not None:
            qualifiers.append(f"resource={self.resource_pattern!r}")
        qualifiers.append(f"min_count={self.min_count}")
        return ", ".join(qualifiers)


class CellAcceptance(BaseModel):
    """Output shape, semantic checks, and evidence required for acceptance."""

    model_config = ConfigDict(frozen=True)

    output: OutputContract = Field(default_factory=OutputContract)
    checks: dict[str, Any] = Field(default_factory=dict)
    required_records: tuple[RecordRequirement, ...] = ()

    def compose_for_child(self, requested: "CellAcceptance") -> "CellAcceptance":
        default = CellAcceptance()
        output = self.output if requested.output == default.output else requested.output
        requirements = list(self.required_records)
        known = {_json_text(item.model_dump(mode="json")) for item in requirements}
        for requirement in requested.required_records:
            key = _json_text(requirement.model_dump(mode="json"))
            if key not in known:
                requirements.append(requirement)
                known.add(key)
        return CellAcceptance(
            output=output,
            checks=_merge_monotone_mapping(
                self.checks,
                requested.checks,
                label="acceptance check",
            ),
            required_records=tuple(requirements),
        )

    def missing_records(
        self, records: Sequence[CellRecord]
    ) -> tuple[RecordRequirement, ...]:
        return tuple(
            requirement
            for requirement in self.required_records
            if sum(requirement.matches(record) for record in records)
            < requirement.min_count
        )


class CellContract(BaseModel):
    """Universal behavioral and authority boundary for one cell definition."""

    model_config = ConfigDict(frozen=True)

    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    dos: tuple[str, ...] = ()
    donts: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    limits: dict[str, int] = Field(default_factory=dict)
    acceptance: CellAcceptance = Field(default_factory=CellAcceptance)

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _normalize_tools(cls, value: Any) -> frozenset[str]:
        if value is None:
            return frozenset()
        if isinstance(value, str):
            value = (value,)
        return frozenset(_unique_strings(tuple(value)))

    @field_validator("dos", "donts", "preferences", mode="before")
    @classmethod
    def _normalize_rules(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            value = (value,)
        return _unique_strings(tuple(value))

    @field_validator("limits")
    @classmethod
    def _validate_limits(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for key, limit in value.items():
            name = str(key).strip()
            if not name:
                raise ValueError("limit names must be non-empty")
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError(f"limit {name!r} must be a non-negative integer")
            normalized[name] = limit
        return normalized

    def narrow_for_child(self, requested: "CellContract") -> "CellContract":
        added_tools = requested.allowed_tools - self.allowed_tools
        if added_tools:
            raise ValueError(
                "child contract cannot add tools: " + ", ".join(sorted(added_tools))
            )

        limits = dict(self.limits)
        for name, child_limit in requested.limits.items():
            parent_limit = limits.get(name)
            if parent_limit is not None and child_limit > parent_limit:
                raise ValueError(f"child limit {name!r} cannot exceed parent limit")
            limits[name] = child_limit

        return CellContract(
            allowed_tools=requested.allowed_tools,
            dos=_unique_strings((*self.dos, *requested.dos)),
            donts=_unique_strings((*self.donts, *requested.donts)),
            preferences=_unique_strings((*self.preferences, *requested.preferences)),
            limits=limits,
            acceptance=self.acceptance.compose_for_child(requested.acceptance),
        )


class CellSpec(BaseModel):
    """Position-independent Universal Cell definition: context plus contract."""

    model_config = ConfigDict(frozen=True)

    context: dict[str, Any] = Field(default_factory=dict)
    contract: CellContract = Field(default_factory=CellContract)

    @property
    def task(self) -> str:
        for key in ("task", "objective", "instruction"):
            value = self.context.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        raise ValueError(
            "cell context must define task, objective, or instruction before execution"
        )

    @property
    def role(self) -> str:
        return str(self.context.get("role") or "cell").strip() or "cell"


class ExecutorRef(BaseModel):
    """Execution-mechanism binding; currently lowered through the model worker core."""

    model_config = ConfigDict(frozen=True)

    executor_id: str
    kind: str = "model"
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("executor_id", "kind")
    @classmethod
    def _identity_is_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("executor_id and kind must be non-empty")
        return text

    @property
    def model_name(self) -> str:
        if self.kind not in {"model", "llm"}:
            raise ValueError(
                f"executor kind {self.kind!r} has no compiler adapter; expected 'model' or 'llm'"
            )
        return str(self.config.get("model") or self.executor_id)


class CellInvocation(BaseModel):
    """Structural identity plus one cell definition and its executor binding."""

    model_config = ConfigDict(frozen=True)

    cell_id: str = Field(default_factory=lambda: f"cell-{uuid4().hex[:12]}")
    cell: CellSpec
    executor: ExecutorRef

    @field_validator("cell_id")
    @classmethod
    def _cell_id_is_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("cell_id must be non-empty")
        return text

    @property
    def context(self) -> dict[str, Any]:
        return self.cell.context

    @property
    def contract(self) -> CellContract:
        return self.cell.contract


class SequenceRelation(BaseModel):
    """Full logical-context handoff between same-level cell invocations."""

    model_config = ConfigDict(frozen=True)

    source_cell_id: str
    target_cell_id: str
    relation_id: str = ""

    @field_validator("source_cell_id", "target_cell_id")
    @classmethod
    def _cell_ids_are_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("sequence cell ids must be non-empty")
        return text

    @model_validator(mode="after")
    def _different_cells(self) -> "SequenceRelation":
        if self.source_cell_id == self.target_cell_id:
            raise ValueError("a sequence relation cannot point a cell to itself")
        return self

    @property
    def identity(self) -> str:
        return (
            self.relation_id or f"sequence:{self.source_cell_id}->{self.target_cell_id}"
        )


class JoinPolicy(BaseModel):
    """Admission policy evaluated only after every declared child settles."""

    model_config = ConfigDict(frozen=True)

    mode: JoinMode = "all_success"
    quorum: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _quorum_matches_mode(self) -> "JoinPolicy":
        if self.mode == "quorum" and self.quorum is None:
            raise ValueError("quorum join policy requires quorum")
        if self.mode != "quorum" and self.quorum is not None:
            raise ValueError("quorum is valid only for quorum join policy")
        return self

    def accepts(self, reports: Sequence["CellReport"]) -> bool:
        completed = sum(report.outcome == "completed" for report in reports)
        if self.mode == "all_success":
            return completed == len(reports)
        if self.mode == "all_settled":
            return True
        if self.mode == "at_least_one":
            return completed >= 1
        return completed >= int(self.quorum or 0)


class ForkJoinRelation(BaseModel):
    """One parent, explicit child views, bounded fan-out, and one join policy."""

    model_config = ConfigDict(frozen=True)

    parent_cell_id: str
    child_cell_ids: tuple[str, ...]
    context_views: dict[str, ContextView]
    join_policy: JoinPolicy = Field(default_factory=JoinPolicy)
    max_concurrency: int | None = Field(default=None, ge=1)
    relation_id: str = ""

    @field_validator("child_cell_ids", mode="before")
    @classmethod
    def _normalize_child_ids(cls, value: Any) -> tuple[str, ...]:
        return _unique_strings(tuple(value or ()))

    @field_validator("parent_cell_id")
    @classmethod
    def _parent_id_is_required(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("fork/join parent_cell_id must be non-empty")
        return text

    @model_validator(mode="after")
    def _every_child_has_one_view(self) -> "ForkJoinRelation":
        if not self.child_cell_ids:
            raise ValueError("fork/join relations require at least one child")
        if self.parent_cell_id in set(self.child_cell_ids):
            raise ValueError("a parent cannot be its own child")
        expected = set(self.child_cell_ids)
        if expected != set(self.context_views):
            raise ValueError(
                "fork/join context_views must match child_cell_ids exactly"
            )
        if self.join_policy.mode == "quorum" and int(
            self.join_policy.quorum or 0
        ) > len(expected):
            raise ValueError("join quorum cannot exceed child count")
        return self

    @property
    def identity(self) -> str:
        return self.relation_id or f"fork:{self.parent_cell_id}"


class CellTopology(BaseModel):
    """External structural configuration for cell invocations."""

    model_config = ConfigDict(frozen=True)

    sequences: tuple[SequenceRelation, ...] = ()
    fork_joins: tuple[ForkJoinRelation, ...] = ()

    @model_validator(mode="after")
    def _topology_is_unambiguous(self) -> "CellTopology":
        sequence_sources = [relation.source_cell_id for relation in self.sequences]
        sequence_targets = [relation.target_cell_id for relation in self.sequences]
        fork_parents = [relation.parent_cell_id for relation in self.fork_joins]
        fork_children = [
            child for relation in self.fork_joins for child in relation.child_cell_ids
        ]
        if len(sequence_sources) != len(set(sequence_sources)):
            raise ValueError("a cell may have only one sequence successor")
        if len(sequence_targets) != len(set(sequence_targets)):
            raise ValueError("a cell may have only one sequence predecessor")
        if len(fork_parents) != len(set(fork_parents)):
            raise ValueError("a cell may own only one fork/join relation")
        if len(fork_children) != len(set(fork_children)):
            raise ValueError("a child may belong to only one fork/join parent")
        identities = [
            *(relation.identity for relation in self.sequences),
            *(relation.identity for relation in self.fork_joins),
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("topology relation identities must be unique")
        return self

    def sequence_from(self, cell_id: str) -> SequenceRelation | None:
        return next(
            (item for item in self.sequences if item.source_cell_id == cell_id), None
        )

    def sequence_to(self, cell_id: str) -> SequenceRelation | None:
        return next(
            (item for item in self.sequences if item.target_cell_id == cell_id), None
        )

    def fork_for_parent(self, cell_id: str) -> ForkJoinRelation | None:
        return next(
            (item for item in self.fork_joins if item.parent_cell_id == cell_id), None
        )


class CellRuntimeConfig(BaseModel):
    """Execution machinery shared by invocations but absent from cell definitions."""

    model_config = ConfigDict(frozen=True)

    sampling_policy: SamplingPolicy = Field(default_factory=resolve_sampling_policy)
    model_context_view: ContextView = Field(
        default_factory=lambda: ContextView(max_rendered_chars=32_000)
    )
    max_concurrency: int = Field(default=8, ge=1)
    max_retries: int = Field(default=0, ge=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sampling_policy", mode="before")
    @classmethod
    def _resolve_sampling(cls, value: Any) -> SamplingPolicy:
        return resolve_sampling_policy(value)


class UniversalCellConfig(BaseModel):
    """Cell definitions, executor bindings, external topology, and shared runtime."""

    model_config = ConfigDict(frozen=True)

    cells: dict[str, CellSpec]
    executors: dict[str, ExecutorRef]
    topology: CellTopology = Field(default_factory=CellTopology)
    runtime: CellRuntimeConfig = Field(default_factory=CellRuntimeConfig)

    @field_validator("executors", mode="before")
    @classmethod
    def _normalize_executors(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        return {
            str(cell_id): (
                {"executor_id": executor} if isinstance(executor, str) else executor
            )
            for cell_id, executor in value.items()
        }

    @model_validator(mode="after")
    def _validate_system(self) -> "UniversalCellConfig":
        cell_ids = set(self.cells)
        if set(self.executors) != cell_ids:
            missing = sorted(cell_ids - set(self.executors))
            unexpected = sorted(set(self.executors) - cell_ids)
            details = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if unexpected:
                details.append("unexpected=" + ",".join(unexpected))
            raise ValueError(
                "executor bindings must match cells exactly: " + "; ".join(details)
            )

        referenced = {
            item
            for relation in self.topology.sequences
            for item in (relation.source_cell_id, relation.target_cell_id)
        }
        referenced.update(
            item
            for relation in self.topology.fork_joins
            for item in (relation.parent_cell_id, *relation.child_cell_ids)
        )
        missing_refs = sorted(referenced - cell_ids)
        if missing_refs:
            raise ValueError(
                "topology references unknown cells: " + ", ".join(missing_refs)
            )

        successors = {
            relation.source_cell_id: relation.target_cell_id
            for relation in self.topology.sequences
        }
        for start in successors:
            seen: set[str] = set()
            current = start
            while current in successors:
                if current in seen:
                    raise ValueError("sequence topology cannot contain cycles")
                seen.add(current)
                current = successors[current]

        for relation in self.topology.fork_joins:
            parent = self.cells[relation.parent_cell_id]
            for child_id in relation.child_cell_ids:
                parent.contract.narrow_for_child(self.cells[child_id].contract)

        descendants = {
            relation.parent_cell_id: relation.child_cell_ids
            for relation in self.topology.fork_joins
        }

        def visit(cell_id: str, active: set[str], finished: set[str]) -> None:
            if cell_id in active:
                raise ValueError(
                    "fork/join topology cannot contain parent/child cycles"
                )
            if cell_id in finished:
                return
            active.add(cell_id)
            for child_id in descendants.get(cell_id, ()):
                visit(child_id, active, finished)
            active.remove(cell_id)
            finished.add(cell_id)

        finished: set[str] = set()
        for parent_id in descendants:
            visit(parent_id, set(), finished)
        return self

    def invocation(self, cell_id: str) -> CellInvocation:
        try:
            return CellInvocation(
                cell_id=cell_id,
                cell=self.cells[cell_id],
                executor=self.executors[cell_id],
            )
        except KeyError:
            raise ValueError(f"unknown cell id {cell_id!r}") from None


class CellReport(BaseModel):
    """Compact outcome with result, state delta, and extensible observed records."""

    model_config = ConfigDict(frozen=True)

    cell_id: str
    outcome: CellOutcome = "completed"
    result: Any = None
    context_delta: dict[str, Any] = Field(default_factory=dict)
    records: tuple[CellRecord, ...] = ()
    error: str | None = None

    @model_validator(mode="after")
    def _unsuccessful_report_has_error(self) -> "CellReport":
        if self.outcome in {"failed", "blocked"} and not str(self.error or "").strip():
            raise ValueError(f"{self.outcome} cell reports require an error")
        return self

    def compact_payload(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "outcome": self.outcome,
            "result": deepcopy(self.result),
            "context_delta": deepcopy(self.context_delta),
            "records": [record.model_dump(mode="json") for record in self.records],
            "error": self.error,
        }


class CompiledCellInvocation(BaseModel):
    """Inspectable compiler output consumed by the unchanged worker core."""

    model_config = ConfigDict(frozen=True)

    cell_id: str
    invocation: CellInvocation
    worker: WorkerDefinition
    brief: WorkerBrief
    request: ExecutionRequest
    context_projection: ContextProjection


ChildRunner = Callable[[CellInvocation], Awaitable[CellReport]]


def build_cell_spec(
    context: Mapping[str, Any],
    contract: CellContract | Mapping[str, Any] | None = None,
) -> CellSpec:
    """Build the minimal position-independent Universal Cell definition."""

    resolved_contract = (
        contract
        if isinstance(contract, CellContract)
        else CellContract.model_validate(contract or {})
    )
    return CellSpec(context=deepcopy(dict(context)), contract=resolved_contract)


def bind_cell(
    cell: CellSpec,
    executor: ExecutorRef | Mapping[str, Any] | str,
    *,
    cell_id: str | None = None,
) -> CellInvocation:
    """Bind a cell definition to structural identity and an execution mechanism."""

    if isinstance(executor, ExecutorRef):
        resolved_executor = executor
    elif isinstance(executor, str):
        resolved_executor = ExecutorRef(executor_id=executor)
    else:
        resolved_executor = ExecutorRef.model_validate(dict(executor))
    payload: dict[str, Any] = {"cell": cell, "executor": resolved_executor}
    if cell_id is not None:
        payload["cell_id"] = cell_id
    return CellInvocation.model_validate(payload)


def build_structured_cell(
    executor: ExecutorRef | Mapping[str, Any] | str,
    context: Mapping[str, Any],
    contract: CellContract | Mapping[str, Any] | None = None,
    *,
    cell_id: str | None = None,
) -> CellInvocation:
    """Compatibility convenience: build a CellSpec and bind its executor."""

    return bind_cell(
        build_cell_spec(context, contract),
        executor,
        cell_id=cell_id,
    )


def link_linear(*cells: CellInvocation | str) -> CellTopology:
    """Create external sequence edges without mutating cell definitions."""

    cell_ids = tuple(
        cell.cell_id if isinstance(cell, CellInvocation) else str(cell)
        for cell in cells
    )
    if len(set(cell_ids)) != len(cell_ids):
        raise ValueError("linear cell ids must be unique")
    return CellTopology(
        sequences=tuple(
            SequenceRelation(source_cell_id=source, target_cell_id=target)
            for source, target in zip(cell_ids, cell_ids[1:])
        )
    )


def _context_string_list(context: Mapping[str, Any], key: str) -> list[str]:
    raw = context.get(key)
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _evidence_from_context(context: Mapping[str, Any]) -> list[EvidenceBlock]:
    raw = context.get("evidence")
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    evidence: list[EvidenceBlock] = []
    for index, value in enumerate(values):
        if isinstance(value, EvidenceBlock):
            evidence.append(value)
        elif isinstance(value, Mapping) and "label" in value and "content" in value:
            evidence.append(EvidenceBlock.model_validate(dict(value)))
        else:
            evidence.append(
                EvidenceBlock(label=f"context evidence {index + 1}", content=value)
            )
    return evidence


def compile_cell(
    invocation: CellInvocation,
    runtime: CellRuntimeConfig | None = None,
    *,
    context_view: ContextView | None = None,
) -> CompiledCellInvocation:
    """Compile the reduced definition and executor binding into runtime contracts."""

    runtime = runtime or CellRuntimeConfig()
    cell = invocation.cell
    contract = cell.contract
    projection = (context_view or runtime.model_context_view).project(cell.context)
    model_context = deepcopy(projection.model_context)
    if "workspace_root" in projection.selected_context:
        model_context.setdefault(
            "workspace_root", projection.selected_context["workspace_root"]
        )

    success_criteria = _context_string_list(cell.context, "success_criteria")
    if contract.acceptance.output.definition_of_done:
        success_criteria.append(contract.acceptance.output.definition_of_done)
    sampling = runtime.sampling_policy
    if "max_tokens" in contract.limits:
        sampling = sampling.model_copy(
            update={"max_tokens": contract.limits["max_tokens"]}
        )
    hard_constraints = [
        *(f"Do: {rule}" for rule in contract.dos),
        *(f"Do not: {rule}" for rule in contract.donts),
    ]
    context_packet = projection.model_context.get("context_packet")
    brief = WorkerBrief(
        role=RoleSpec(
            role_label=cell.role,
            responsibility=str(cell.context.get("responsibility") or ""),
            success_criteria=success_criteria,
            artifact_targets=_context_string_list(cell.context, "artifact_targets"),
            trace_role=str(cell.context.get("trace_role") or cell.role),
        ),
        task=cell.task,
        scope=(
            str(cell.context["scope"])
            if cell.context.get("scope") is not None
            else None
        ),
        hard_constraints=hard_constraints,
        soft_constraints=list(contract.preferences),
        tool_policy=ToolUseContract(
            allowed_tool_ids=sorted(contract.allowed_tools),
            preferred_tool_ids=sorted(contract.allowed_tools),
            max_tool_calls=contract.limits.get("max_tool_calls"),
        ),
        runtime_policy={
            "max_retries": runtime.max_retries,
            "timeout_seconds": runtime.timeout_seconds,
            "limits": dict(contract.limits),
        },
        validation_policy={
            **deepcopy(contract.acceptance.checks),
            "required_records": [
                requirement.model_dump(mode="json")
                for requirement in contract.acceptance.required_records
            ],
        },
        output_contract=contract.acceptance.output,
        sampling_policy=sampling,
        evidence=_evidence_from_context(projection.model_context),
        context_packet=(
            deepcopy(dict(context_packet))
            if isinstance(context_packet, Mapping)
            else {}
        ),
        input_payload=model_context,
        metadata={
            "structured_cell": True,
            "structured_cell_id": invocation.cell_id,
            "cell_executor": invocation.executor.model_dump(mode="json"),
            "logical_context_sha256": _context_hash(cell.context),
            "logical_context_chars": len(_json_text(cell.context)),
            "context_projection": projection.model_dump(
                mode="json",
                exclude={"selected_context", "model_context"},
            ),
            "runtime_metadata": deepcopy(runtime.metadata),
        },
    )
    request = request_from_brief(brief)
    worker = build_cell(
        invocation.executor.model_name,
        brief.sampling_policy,
        brief.role.role_label,
    )
    return CompiledCellInvocation(
        cell_id=invocation.cell_id,
        invocation=invocation,
        worker=worker,
        brief=brief,
        request=request,
        context_projection=projection,
    )


def worker_definition_from_cell(
    invocation: CellInvocation,
    runtime: CellRuntimeConfig | None = None,
) -> WorkerDefinition:
    return compile_cell(invocation, runtime).worker


def execution_request_from_cell(
    invocation: CellInvocation,
    runtime: CellRuntimeConfig | None = None,
) -> ExecutionRequest:
    return compile_cell(invocation, runtime).request


def _value_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _record_from_value(
    value: Any,
    *,
    default_kind: str | None = None,
    default_role: str = "",
) -> CellRecord:
    if isinstance(value, CellRecord):
        return value
    if not isinstance(value, Mapping):
        if default_kind is None:
            raise ValueError("record strings require a default kind")
        return CellRecord(kind=default_kind, resource=str(value), role=default_role)

    payload = deepcopy(dict(value))
    kind = str(payload.pop("kind", default_kind or "")).strip()
    role = str(payload.pop("role", default_role) or "").strip()
    resource = ""
    for key in ("resource", "path", "url", "ref_id", "id"):
        candidate = payload.pop(key, None)
        if candidate is not None and str(candidate).strip():
            resource = str(candidate).strip()
            break
    digest = payload.pop("digest", None)
    location = payload.pop("location", {})
    if not isinstance(location, Mapping):
        location = {"value": location}
    location = deepcopy(dict(location))
    for key in (
        "line",
        "line_start",
        "line_end",
        "column",
        "column_start",
        "column_end",
    ):
        if key in payload:
            location[key] = payload.pop(key)
    provenance = payload.pop("provenance", {})
    if not isinstance(provenance, Mapping):
        provenance = {"value": provenance}
    metadata = payload.pop("metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {"value": metadata}
    return CellRecord(
        kind=kind,
        resource=resource,
        role=role,
        digest=(str(digest) if digest is not None else None),
        location=location,
        provenance=deepcopy(dict(provenance)),
        metadata={**deepcopy(dict(metadata)), **payload},
    )


def _collect_records(
    invocation: CellInvocation,
    result: WorkerExecutionResult,
) -> tuple[CellRecord, ...]:
    records: list[CellRecord] = []

    def extend(raw: Any, *, kind: str | None = None, role: str = "") -> None:
        for value in _value_sequence(raw):
            records.append(
                _record_from_value(value, default_kind=kind, default_role=role)
            )

    for container in (invocation.context, result.outputs, result.metadata):
        extend(container.get("records"))
    for container in (invocation.context, result.outputs, result.metadata):
        extend(container.get("input_sources"), kind="source", role="input")
        extend(container.get("source_refs"), kind="source", role="evidence")
    for container in (result.outputs, result.metadata):
        extend(container.get("modifications"), kind="modification", role="change")
        extend(container.get("modified_files"), kind="modification", role="change")
        extend(container.get("output_files"), kind="artifact", role="output")
        extend(container.get("artifact_refs"), kind="artifact", role="output")
        extend(container.get("deliverables"), kind="artifact", role="deliverable")
        extend(container.get("evidence_refs"), kind="source", role="evidence")

    unique: list[CellRecord] = []
    seen: set[str] = set()
    for record in records:
        key = _json_text(record.model_dump(mode="json"))
        if key not in seen:
            unique.append(record)
            seen.add(key)
    return tuple(unique)


async def execute_structured_cell(
    executor: Any,
    invocation: CellInvocation,
    runtime: CellRuntimeConfig | None = None,
) -> CellReport:
    """Execute one invocation, collect records, and enforce record acceptance."""

    resolved_runtime = runtime or CellRuntimeConfig()
    compiled = compile_cell(invocation, resolved_runtime)
    result: WorkerExecutionResult | None = None
    last_error: str | None = None
    for _attempt in range(resolved_runtime.max_retries + 1):
        try:
            execution = executor.execute(compiled.worker, compiled.request)
            result = (
                await asyncio.wait_for(
                    execution, timeout=resolved_runtime.timeout_seconds
                )
                if resolved_runtime.timeout_seconds is not None
                else await execution
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            result = None
            continue
        if result.status == "completed":
            break
        last_error = result.error or "worker execution failed"

    if result is None:
        return CellReport(
            cell_id=invocation.cell_id,
            outcome="failed",
            error=last_error or "worker execution failed",
        )

    outcome: CellOutcome = "completed" if result.status == "completed" else "failed"
    context_delta = result.outputs.get("context_delta")
    records = _collect_records(invocation, result)
    missing_records = invocation.contract.acceptance.missing_records(records)
    if outcome == "completed" and missing_records:
        outcome = "failed"
        last_error = "acceptance missing required records: " + "; ".join(
            requirement.describe() for requirement in missing_records
        )
    return CellReport(
        cell_id=invocation.cell_id,
        outcome=outcome,
        result=deepcopy(result.outputs),
        context_delta=(
            deepcopy(context_delta) if isinstance(context_delta, Mapping) else {}
        ),
        records=records,
        error=(
            None
            if outcome == "completed"
            else result.error or last_error or "worker execution failed"
        ),
    )


def inherit_previous_report(
    previous: CellInvocation,
    report: CellReport,
    target: CellInvocation,
    relation: SequenceRelation,
) -> CellInvocation:
    """Transfer full logical context and the compact report across a sequence edge."""

    if report.cell_id != previous.cell_id:
        raise ValueError("previous report does not belong to previous invocation")
    if relation.source_cell_id != previous.cell_id:
        raise ValueError("sequence source does not match previous invocation")
    if relation.target_cell_id != target.cell_id:
        raise ValueError("sequence target does not match target invocation")
    inherited = deepcopy(previous.context)
    inherited.update(deepcopy(report.context_delta))
    previous_lineage = inherited.get(LINEAGE_CONTEXT_KEY)
    lineage = list(previous_lineage) if isinstance(previous_lineage, list) else []
    if previous.cell_id not in lineage:
        lineage.append(previous.cell_id)
    inherited.update(deepcopy(target.context))
    inherited[LINEAGE_CONTEXT_KEY] = lineage
    inherited[PREVIOUS_REPORT_CONTEXT_KEY] = report.compact_payload()
    target_cell = target.cell.model_copy(update={"context": inherited}, deep=True)
    return target.model_copy(update={"cell": target_cell}, deep=True)


def prepare_children(
    config: UniversalCellConfig,
    parent_cell_id: str,
) -> tuple[CellInvocation, tuple[CellInvocation, ...]]:
    """Apply nested context views and monotone contracts to one child wave."""

    relation = config.topology.fork_for_parent(parent_cell_id)
    if relation is None:
        raise ValueError(f"cell {parent_cell_id!r} has no fork/join relation")
    parent = config.invocation(parent_cell_id)
    children: list[CellInvocation] = []
    for child_id in relation.child_cell_ids:
        child_template = config.invocation(child_id)
        selected = (
            relation.context_views[child_id].project(parent.context).selected_context
        )
        child_context = deepcopy(selected)
        child_context.update(deepcopy(child_template.context))
        child_contract = parent.contract.narrow_for_child(child_template.contract)
        child_cell = child_template.cell.model_copy(
            update={"context": child_context, "contract": child_contract},
            deep=True,
        )
        children.append(
            child_template.model_copy(update={"cell": child_cell}, deep=True)
        )
    return parent, tuple(children)


def collapse_child_reports(
    parent: CellInvocation,
    relation: ForkJoinRelation,
    reports: Sequence[CellReport],
) -> CellInvocation:
    """Collapse one all-settled child wave into compact, namespaced reports."""

    expected = relation.child_cell_ids
    received = tuple(report.cell_id for report in reports)
    if len(set(received)) != len(received):
        raise ValueError("a child may report only once per collapse")
    if set(received) != set(expected) or len(received) != len(expected):
        missing = sorted(set(expected) - set(received))
        unexpected = sorted(set(received) - set(expected))
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ValueError(
            "child collapse requires exactly the declared reports: "
            + "; ".join(details)
        )
    report_by_id = {report.cell_id: report for report in reports}
    ordered_reports = [report_by_id[child_id] for child_id in expected]
    accepted = relation.join_policy.accepts(ordered_reports)
    context = deepcopy(parent.context)
    context[CHILD_REPORTS_CONTEXT_KEY] = {
        report.cell_id: report.compact_payload() for report in ordered_reports
    }
    context[CHILD_JOIN_CONTEXT_KEY] = {
        "relation_id": relation.identity,
        "all_settled": True,
        "accepted": accepted,
        "join_mode": relation.join_policy.mode,
        "completed_count": sum(
            report.outcome == "completed" for report in ordered_reports
        ),
        "child_count": len(ordered_reports),
    }
    parent_cell = parent.cell.model_copy(update={"context": context}, deep=True)
    return parent.model_copy(update={"cell": parent_cell}, deep=True)


def join_ready(parent: CellInvocation, relation: ForkJoinRelation) -> bool:
    state = parent.context.get(CHILD_JOIN_CONTEXT_KEY)
    reports = parent.context.get(CHILD_REPORTS_CONTEXT_KEY)
    return bool(
        isinstance(state, Mapping)
        and state.get("relation_id") == relation.identity
        and state.get("all_settled") is True
        and state.get("accepted") is True
        and isinstance(reports, Mapping)
        and set(reports) == set(relation.child_cell_ids)
    )


async def fan_out_and_collapse(
    config: UniversalCellConfig,
    parent_cell_id: str,
    runner: ChildRunner,
) -> tuple[CellInvocation, tuple[CellReport, ...]]:
    """Run a bounded parallel child wave, settle every report, then join."""

    parent, children = prepare_children(config, parent_cell_id)
    relation = config.topology.fork_for_parent(parent_cell_id)
    assert relation is not None
    capacity = min(
        config.runtime.max_concurrency,
        relation.max_concurrency or config.runtime.max_concurrency,
    )
    semaphore = asyncio.Semaphore(capacity)

    async def run_one(child: CellInvocation) -> CellReport:
        async with semaphore:
            try:
                report = await runner(child)
            except Exception as exc:
                return CellReport(
                    cell_id=child.cell_id,
                    outcome="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            if report.cell_id != child.cell_id:
                return CellReport(
                    cell_id=child.cell_id,
                    outcome="failed",
                    error=f"runner returned report for {report.cell_id!r}",
                )
            return report

    reports = tuple(await asyncio.gather(*(run_one(child) for child in children)))
    return collapse_child_reports(parent, relation, reports), reports


def handoff_to_next(
    config: UniversalCellConfig,
    focal: CellInvocation,
    report: CellReport,
) -> CellInvocation:
    """Advance through topology only after any focal fork/join is accepted."""

    if report.cell_id != focal.cell_id:
        raise ValueError(f"focal report must belong to {focal.cell_id!r}")
    if report.outcome != "completed":
        raise ValueError("a non-completed focal cell cannot advance to next")
    fork = config.topology.fork_for_parent(focal.cell_id)
    if fork is not None and not join_ready(focal, fork):
        raise ValueError("focal cell cannot advance until its child join is accepted")
    sequence = config.topology.sequence_from(focal.cell_id)
    if sequence is None:
        raise ValueError(f"cell {focal.cell_id!r} has no sequence successor")
    return inherit_previous_report(
        focal,
        report,
        config.invocation(sequence.target_cell_id),
        sequence,
    )


__all__ = [
    "CHILD_JOIN_CONTEXT_KEY",
    "CHILD_REPORTS_CONTEXT_KEY",
    "LINEAGE_CONTEXT_KEY",
    "PREVIOUS_REPORT_CONTEXT_KEY",
    "CellAcceptance",
    "CellContract",
    "CellInvocation",
    "CellRecord",
    "CellReport",
    "CellRuntimeConfig",
    "CellSpec",
    "CellTopology",
    "CompiledCellInvocation",
    "ContextProjection",
    "ContextView",
    "ExecutorRef",
    "ForkJoinRelation",
    "JoinPolicy",
    "RecordRequirement",
    "SequenceRelation",
    "UniversalCellConfig",
    "bind_cell",
    "build_cell_spec",
    "build_structured_cell",
    "collapse_child_reports",
    "compile_cell",
    "execute_structured_cell",
    "execution_request_from_cell",
    "fan_out_and_collapse",
    "handoff_to_next",
    "inherit_previous_report",
    "join_ready",
    "link_linear",
    "prepare_children",
    "worker_definition_from_cell",
]
