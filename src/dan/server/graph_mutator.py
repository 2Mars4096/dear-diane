"""Graph mutation engine — applies structured operations to a dan_graph_v1 dict."""

from __future__ import annotations

import copy
import enum
import logging
import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from dan.models.node_taxonomy import BODY_GRAPH_RUNTIME_NODE_TYPES
from dan.models.graph import Graph
from dan.validation.graph import validate_graph
from dan.server.graph_mutator_helpers import (
    TOOL_PORT_MANIFESTS,
    _INPUT_VARIABLE_NAME_RE,
    _default_node_config,
    _default_ports,
    _generate_node_id,
    _graph_mutator_uses_workers,
    _resolve_source_output_port,
    _slugify,
    _workerize_mutation_node_config,
)
from dan.server.graph_mutator_patterns import (
    PATTERN_LIBRARY,
    _pattern_chain,
    _pattern_data_analysis,
    _pattern_data_ingest,
    _pattern_fan_out,
    _pattern_rag_qa,
    _pattern_review_loop,
)

__all__ = [
    "AddEdge",
    "AddHyperedge",
    "AddNode",
    "ApplySkill",
    "EditEdge",
    "EditHyperedge",
    "EditNode",
    "ExpandPattern",
    "GraphMutator",
    "GraphOperation",
    "MutationPlan",
    "MutationFailureClass",
    "MutationResult",
    "OperationError",
    "PATTERN_LIBRARY",
    "RemoveEdge",
    "RemoveHyperedge",
    "RemoveNode",
    "ReplaceBodyGraph",
    "ReplaceSubgraph",
    "SetNodePosition",
    "TOOL_PORT_MANIFESTS",
    "_pattern_chain",
    "_pattern_data_analysis",
    "_pattern_data_ingest",
    "_pattern_fan_out",
    "_pattern_rag_qa",
    "_pattern_review_loop",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Operation models (discriminated union on ``op``)
# ---------------------------------------------------------------------------


class AddNode(BaseModel):
    op: Literal["add_node"] = "add_node"
    id: str = ""
    node_type: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class RemoveNode(BaseModel):
    op: Literal["remove_node"] = "remove_node"
    node_id: str


class EditNode(BaseModel):
    op: Literal["edit_node"] = "edit_node"
    node_id: str
    updates: dict[str, Any]


class AddEdge(BaseModel):
    op: Literal["add_edge"] = "add_edge"
    edge_type: str = "data"
    source_id: str
    source_port: str
    target_id: str
    target_port: str
    spread: bool = False
    strict: bool = False


class RemoveEdge(BaseModel):
    op: Literal["remove_edge"] = "remove_edge"
    source_id: str
    source_port: str
    target_id: str
    target_port: str


class EditEdge(BaseModel):
    op: Literal["edit_edge"] = "edit_edge"
    edge_id: str
    updates: dict[str, Any]


class SetNodePosition(BaseModel):
    op: Literal["set_position"] = "set_position"
    node_id: str
    x: float
    y: float


class ReplaceSubgraph(BaseModel):
    op: Literal["replace_subgraph"] = "replace_subgraph"
    node_ids_to_remove: list[str]
    new_nodes: list[dict[str, Any]]
    new_edges: list[dict[str, Any]]


class ReplaceBodyGraph(BaseModel):
    op: Literal["replace_body_graph"] = "replace_body_graph"
    node_id: str
    operations: list["BodyGraphOperation"] = Field(default_factory=list)
    entry_ids: list[str] | None = None
    exit_ids: list[str] | None = None


class ExpandPattern(BaseModel):
    op: Literal["expand_pattern"] = "expand_pattern"
    pattern: str
    params: dict[str, Any] = Field(default_factory=dict)


class ApplySkill(BaseModel):
    op: Literal["apply_skill"] = "apply_skill"
    skill: str
    target_nodes: list[str] = Field(default_factory=list)
    target_tag: str = ""


class AddHyperedge(BaseModel):
    op: Literal["add_hyperedge"] = "add_hyperedge"
    hyperedge: dict[str, Any]


class RemoveHyperedge(BaseModel):
    op: Literal["remove_hyperedge"] = "remove_hyperedge"
    hyperedge_id: str


class EditHyperedge(BaseModel):
    op: Literal["edit_hyperedge"] = "edit_hyperedge"
    hyperedge_id: str
    updates: dict[str, Any]


BodyGraphOperation = Annotated[
    Union[
        AddNode,
        RemoveNode,
        EditNode,
        AddEdge,
        RemoveEdge,
        EditEdge,
        SetNodePosition,
        ExpandPattern,
        ApplySkill,
        AddHyperedge,
        RemoveHyperedge,
        EditHyperedge,
    ],
    Field(discriminator="op"),
]


GraphOperation = Annotated[
    Union[
        AddNode,
        RemoveNode,
        EditNode,
        AddEdge,
        RemoveEdge,
        EditEdge,
        SetNodePosition,
        ReplaceSubgraph,
        ReplaceBodyGraph,
        ExpandPattern,
        ApplySkill,
        AddHyperedge,
        RemoveHyperedge,
        EditHyperedge,
    ],
    Field(discriminator="op"),
]


# ---------------------------------------------------------------------------
# Plan / result models
# ---------------------------------------------------------------------------


class MutationPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    base_graph_revision: str | None = None
    base_graph_hash: str | None = None
    apply_mode: Literal["all_or_nothing", "partial"] = "all_or_nothing"
    operations: list[GraphOperation]
    description: str = ""
    reasoning: str = ""


class MutationFailureClass(str, enum.Enum):
    """Stable failure codes for dry-run and apply diagnostics."""

    STALE_REVISION = "stale_revision"
    MISSING_INPUT_VARIABLE_PORT = "missing_input_variable_port"
    STALE_PORT_ALIAS = "stale_port_alias"
    DUPLICATE_ADD_NODE = "duplicate_add_node"
    DUPLICATE_ADD_EDGE = "duplicate_add_edge"
    REQUIRED_INPUT_DISCONNECT = "required_input_disconnect"
    AMBIGUOUS_REWIRE = "ambiguous_rewire"
    GRAPH_SHAPE = "graph_shape"
    VALIDATION = "validation"
    COMPILATION = "compilation"
    APPLY = "apply"
    UNKNOWN = "unknown"


class OperationError(BaseModel):
    op_index: int
    op_type: str
    message: str
    stage: str = "apply"
    failure_class: MutationFailureClass = MutationFailureClass.UNKNOWN
    context: dict[str, Any] = Field(default_factory=dict)


class MutationResult(BaseModel):
    success: bool
    new_graph: dict[str, Any] | None = None
    applied_ops: list[int] = Field(default_factory=list)
    errors: list[OperationError] = Field(default_factory=list)
    stale_plan: bool = False
    validation_warnings: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    stage_timings: list[dict[str, Any]] = Field(default_factory=list)


def _trim_failure_value(value: Any, *, max_items: int = 8, max_chars: int = 240) -> Any:
    """Keep structured failure context compact and prompt-safe."""
    if isinstance(value, str):
        text = value.strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."
    if isinstance(value, dict):
        trimmed: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= max_items:
                trimmed["_truncated"] = True
                break
            trimmed[str(key)] = _trim_failure_value(item, max_items=max_items, max_chars=max_chars)
        return trimmed
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        trimmed_items = [
            _trim_failure_value(item, max_items=max_items, max_chars=max_chars)
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            trimmed_items.append("...truncated...")
        return trimmed_items
    return value


def _error_context_from_op(op: GraphOperation | None) -> dict[str, Any]:
    if op is None:
        return {}
    context: dict[str, Any] = {"op": op.op}
    if isinstance(op, AddNode):
        if op.id:
            context["node_id"] = op.id
        context["node_type"] = op.node_type
        if op.name:
            context["node_name"] = op.name
        return context
    if isinstance(op, RemoveNode):
        return {"op": op.op, "node_id": op.node_id}
    if isinstance(op, EditNode):
        return {
            "op": op.op,
            "node_id": op.node_id,
            "update_keys": sorted(str(key) for key in op.updates.keys()),
        }
    if isinstance(op, (AddEdge, RemoveEdge)):
        return {
            "op": op.op,
            "source_id": op.source_id,
            "source_port": op.source_port,
            "target_id": op.target_id,
            "target_port": op.target_port,
        }
    if isinstance(op, EditEdge):
        return {
            "op": op.op,
            "edge_id": op.edge_id,
            "update_keys": sorted(str(key) for key in op.updates.keys()),
        }
    if isinstance(op, SetNodePosition):
        return {"op": op.op, "node_id": op.node_id}
    if isinstance(op, ReplaceSubgraph):
        return {
            "op": op.op,
            "remove_node_ids": list(op.node_ids_to_remove[:8]),
            "new_node_count": len(op.new_nodes),
            "new_edge_count": len(op.new_edges),
        }
    if isinstance(op, ReplaceBodyGraph):
        return {
            "op": op.op,
            "node_id": op.node_id,
            "body_op_count": len(op.operations),
        }
    if isinstance(op, ExpandPattern):
        return {"op": op.op, "pattern": op.pattern}
    if isinstance(op, ApplySkill):
        return {
            "op": op.op,
            "skill": op.skill,
            "target_nodes": list(op.target_nodes[:8]),
            "target_tag": op.target_tag,
        }
    if isinstance(op, RemoveHyperedge):
        return {"op": op.op, "hyperedge_id": op.hyperedge_id}
    if isinstance(op, EditHyperedge):
        return {
            "op": op.op,
            "hyperedge_id": op.hyperedge_id,
            "update_keys": sorted(str(key) for key in op.updates.keys()),
        }
    if isinstance(op, AddHyperedge):
        hyperedge_id = str(op.hyperedge.get("id") or "").strip()
        if hyperedge_id:
            context["hyperedge_id"] = hyperedge_id
        return context
    return context


def _classify_mutation_failure(
    *,
    stage: str,
    op: GraphOperation | None,
    message: str,
) -> MutationFailureClass:
    normalized = str(message or "").strip().lower()
    op_type = getattr(op, "op", "")

    if stage == "compilation":
        return MutationFailureClass.COMPILATION
    if stage == "concurrency_check" or "plan based on revision" in normalized:
        return MutationFailureClass.STALE_REVISION
    if "would disconnect required input port" in normalized:
        return MutationFailureClass.REQUIRED_INPUT_DISCONNECT
    if op_type == "add_node" and "already exists" in normalized:
        return MutationFailureClass.DUPLICATE_ADD_NODE
    if op_type == "add_edge" and "already exists" in normalized:
        return MutationFailureClass.DUPLICATE_ADD_EDGE
    if op_type == "add_edge" and "has no output port" in normalized:
        if isinstance(op, AddEdge) and _INPUT_VARIABLE_NAME_RE.fullmatch(op.source_port):
            return MutationFailureClass.MISSING_INPUT_VARIABLE_PORT
        return MutationFailureClass.STALE_PORT_ALIAS
    if op_type in {"add_edge", "remove_edge", "edit_edge"} and "has no input port" in normalized:
        return MutationFailureClass.STALE_PORT_ALIAS
    if op_type == "remove_edge" and normalized.startswith("no edge from"):
        return MutationFailureClass.AMBIGUOUS_REWIRE
    if stage == "validation":
        return MutationFailureClass.VALIDATION
    if "not found" in normalized or "missing" in normalized or "dangling" in normalized:
        return MutationFailureClass.GRAPH_SHAPE
    if stage == "apply":
        return MutationFailureClass.APPLY
    return MutationFailureClass.UNKNOWN


def _operation_error(
    *,
    op_index: int,
    op_type: str,
    message: str,
    stage: str,
    op: GraphOperation | None = None,
    extra_context: dict[str, Any] | None = None,
) -> OperationError:
    context = _error_context_from_op(op)
    if extra_context:
        context.update(extra_context)
    return OperationError(
        op_index=op_index,
        op_type=op_type,
        message=message,
        stage=stage,
        failure_class=_classify_mutation_failure(stage=stage, op=op, message=message),
        context=_trim_failure_value(context) if context else {},
    )


# ---------------------------------------------------------------------------
# Execution-order priority (operations are auto-sorted before applying)
# ---------------------------------------------------------------------------

_OP_SORT_ORDER: dict[str, int] = {
    "expand_pattern": 0,
    "add_node": 0,
    "set_position": 1,
    "edit_node": 2,
    "replace_body_graph": 2,
    "apply_skill": 2,
    "add_hyperedge": 2,
    "edit_hyperedge": 2,
    "remove_hyperedge": 2,
    "add_edge": 3,
    "edit_edge": 4,
    "remove_edge": 5,
    "remove_node": 6,
    "replace_subgraph": 7,
}


# ---------------------------------------------------------------------------
# Graph-dict helpers
# ---------------------------------------------------------------------------


def _find_node(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


def _node_ids(graph: dict[str, Any]) -> set[str]:
    return {n["id"] for n in graph.get("nodes", [])}


def _remove_edges_for_node(graph: dict[str, Any], node_id: str) -> None:
    graph["edges"] = [
        e
        for e in graph.get("edges", [])
        if e.get("source_node_id") != node_id and e.get("target_node_id") != node_id
    ]


def _validate_no_dangling_edges(graph: dict[str, Any]) -> list[str]:
    """Return a list of problems for edges referencing missing nodes."""
    ids = _node_ids(graph)
    problems: list[str] = []
    for e in graph.get("edges", []):
        eid = e.get("id", "?")
        src = e.get("source_node_id")
        tgt = e.get("target_node_id")
        if src not in ids:
            problems.append(f"Edge '{eid}' references missing source node '{src}'")
        if tgt not in ids:
            problems.append(f"Edge '{eid}' references missing target node '{tgt}'")
    return problems


_WARNING_PATTERNS = ("warning", "deprecated", "untyped")


def _is_validation_warning(msg: str) -> bool:
    lower = msg.lower()
    return any(p in lower for p in _WARNING_PATTERNS)


def _recompute_entry_exit_points(graph: dict[str, Any]) -> None:
    """Recompute entry_points and exit_points based on edge connectivity."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_ids = {n["id"] for n in nodes}

    has_incoming = {
        e["target_node_id"]
        for e in edges
        if e.get("edge_type", "data") == "data" and e.get("target_node_id") in node_ids
    }
    has_outgoing = {
        e["source_node_id"]
        for e in edges
        if e.get("edge_type", "data") == "data" and e.get("source_node_id") in node_ids
    }

    graph["entry_points"] = [n["id"] for n in nodes if n["id"] not in has_incoming]
    graph["exit_points"] = [n["id"] for n in nodes if n["id"] not in has_outgoing]


def _refresh_entry_exit(graph: dict[str, Any]) -> None:
    """Backward-compatible wrapper for inferring graph entry/exit points."""
    _recompute_entry_exit_points(graph)


def _ensure_subgraph(graph: dict[str, Any], parent_id: str, body_nodes: list[dict], body_edges: list[dict], entry_ids: list[str], exit_ids: list[str]) -> str:
    """Create a sub-graph entry in graph['sub_graphs'] for a control-flow node.
    Returns the sub_graph key."""
    parent_node = _find_node(graph, parent_id)
    key = str((parent_node or {}).get("body_graph") or f"{parent_id}__body")
    sub = {
        "version": "dan_graph_v1",
        "metadata": {"name": f"{parent_id} body", "description": "", "version": "1"},
        "nodes": copy.deepcopy(body_nodes),
        "edges": copy.deepcopy(body_edges),
        "sub_graphs": {},
        "entry_points": entry_ids,
        "exit_points": exit_ids,
    }
    graph.setdefault("sub_graphs", {})[key] = sub
    if parent_node is not None:
        parent_node["body_graph"] = key
    return key


def _node_uses_body_graph(node_type: str) -> bool:
    return node_type in BODY_GRAPH_RUNTIME_NODE_TYPES

# ---------------------------------------------------------------------------
# GraphMutator
# ---------------------------------------------------------------------------


class GraphMutator:
    """Applies a MutationPlan to a graph dict with validation and transactional semantics."""

    def apply(
        self,
        graph_dict: dict[str, Any],
        plan: MutationPlan,
        current_revision: str | None = None,
    ) -> MutationResult:
        """Apply a mutation plan to a graph dict.

        Returns a MutationResult with the new graph on success, or errors
        describing what went wrong.  The original *graph_dict* is never
        modified — all work is done on a deep copy.
        """
        if (
            plan.base_graph_revision
            and current_revision
            and plan.base_graph_revision != current_revision
        ):
            message = (
                f"Plan based on revision '{plan.base_graph_revision}' "
                f"but current is '{current_revision}'"
            )
            return MutationResult(
                success=False,
                stale_plan=True,
                errors=[
                    _operation_error(
                        op_index=-1,
                        op_type="concurrency_check",
                        message=message,
                        stage="concurrency_check",
                        extra_context={
                            "expected_revision": plan.base_graph_revision,
                            "current_revision": current_revision,
                        },
                    ),
                ],
            )

        sorted_ops = self._sort_operations(plan.operations)
        working = copy.deepcopy(graph_dict)

        if plan.apply_mode == "all_or_nothing":
            return self._apply_all_or_nothing(working, sorted_ops)
        return self._apply_partial(working, sorted_ops)

    def dry_run(
        self,
        graph_dict: dict[str, Any],
        plan: MutationPlan,
        current_revision: str | None = None,
    ) -> MutationResult:
        """Run apply in dry-run mode — returns result + validation without persisting."""
        result = self.apply(graph_dict, plan, current_revision)
        if not result.success:
            return result

        try:
            graph = Graph.model_validate(result.new_graph)
        except Exception as exc:
            message = f"Graph parse error: {exc}"
            return MutationResult(
                success=False,
                errors=[
                    _operation_error(
                        op_index=-1,
                        op_type="validation",
                        message=message,
                        stage="validation",
                    )
                ],
            )

        raw_errors = validate_graph(graph)
        fatal = [m for m in raw_errors if not _is_validation_warning(m)]
        warnings = [m for m in raw_errors if _is_validation_warning(m)]

        if fatal:
            return MutationResult(
                success=False,
                errors=[
                    _operation_error(
                        op_index=-1,
                        op_type="validation",
                        message=msg,
                        stage="validation",
                    )
                    for msg in fatal
                ],
            )

        result.validation_warnings = warnings
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sort_operations(
        operations: list[GraphOperation],
    ) -> list[tuple[int, GraphOperation]]:
        """Return (original_index, op) pairs sorted by execution order."""
        indexed = list(enumerate(operations))
        replacement_ids = {
            op.node_id
            for op in operations
            if isinstance(op, RemoveNode)
            and any(
                isinstance(candidate, AddNode)
                and bool(candidate.id)
                and candidate.id == op.node_id
                for candidate in operations
            )
        }

        def _replacement_priority(op: GraphOperation) -> float | None:
            if not replacement_ids:
                return None
            if isinstance(op, RemoveEdge) and (
                op.source_id in replacement_ids or op.target_id in replacement_ids
            ):
                return 5.0
            if isinstance(op, RemoveNode) and op.node_id in replacement_ids:
                return 6.0
            if isinstance(op, AddNode) and op.id in replacement_ids:
                return 6.5
            if isinstance(op, (EditNode, SetNodePosition, ReplaceBodyGraph)) and (
                getattr(op, "node_id", None) in replacement_ids
            ):
                return 6.7
            if isinstance(op, ApplySkill) and any(
                target in replacement_ids for target in op.target_nodes
            ):
                return 6.7
            if isinstance(op, AddEdge) and (
                op.source_id in replacement_ids or op.target_id in replacement_ids
            ):
                return 7.0
            return None

        indexed.sort(
            key=lambda pair: (
                _replacement_priority(pair[1])
                if _replacement_priority(pair[1]) is not None
                else _OP_SORT_ORDER.get(pair[1].op, 99),
                pair[0],
            )
        )
        return indexed

    _STRUCTURAL_OPS = frozenset({
        "add_node", "remove_node", "add_edge", "remove_edge",
        "edit_edge", "replace_subgraph", "expand_pattern",
    })

    @staticmethod
    def _resolve_op_aliases(
        op: GraphOperation, alias: dict[str, str],
    ) -> GraphOperation:
        """Rewrite node-ID references in *op* using the alias table."""
        if not alias:
            return op
        updates: dict[str, Any] = {}
        for field in ("node_id", "source_id", "target_id"):
            val = getattr(op, field, None)
            if val and val in alias:
                updates[field] = alias[val]
        if isinstance(op, ReplaceSubgraph):
            new_ids = [alias.get(n, n) for n in op.node_ids_to_remove]
            if new_ids != op.node_ids_to_remove:
                updates["node_ids_to_remove"] = new_ids
        if isinstance(op, ApplySkill):
            new_targets = [alias.get(n, n) for n in op.target_nodes]
            if new_targets != op.target_nodes:
                updates["target_nodes"] = new_targets
        return op.model_copy(update=updates) if updates else op

    def _track_add_node_alias(
        self,
        op: AddNode,
        working: dict[str, Any],
        alias: dict[str, str],
        add_node_seq: int,
        original_ids: set[str] | None = None,
    ) -> None:
        """After a successful add_node, record sequential and explicit aliases.

        Skips alias installation when the placeholder name collides with a
        node ID that existed before the mutation batch began.
        """
        actual_id = working["nodes"][-1]["id"]
        seq_key = f"node_{add_node_seq}"
        if seq_key != actual_id:
            if original_ids is None or seq_key not in original_ids:
                alias[seq_key] = actual_id
        if op.id and op.id != actual_id:
            if original_ids is None or op.id not in original_ids:
                alias[op.id] = actual_id

    def _apply_all_or_nothing(
        self,
        working: dict[str, Any],
        sorted_ops: list[tuple[int, GraphOperation]],
    ) -> MutationResult:
        errors: list[OperationError] = []
        applied: list[int] = []
        diagnostics: list[str] = []
        has_structural = False
        alias: dict[str, str] = {}
        add_node_seq = 0
        original_ids = _node_ids(working)

        for orig_idx, op in sorted_ops:
            op = self._resolve_op_aliases(op, alias)
            err = self._apply_op(working, op, diagnostics)
            if err:
                errors.append(
                    _operation_error(
                        op_index=orig_idx,
                        op_type=op.op,
                        message=err,
                        stage="apply",
                        op=op,
                    )
                )
            else:
                applied.append(orig_idx)
                if op.op in self._STRUCTURAL_OPS:
                    has_structural = True
                if isinstance(op, AddNode):
                    add_node_seq += 1
                    self._track_add_node_alias(op, working, alias, add_node_seq, original_ids)

        if errors:
            return MutationResult(success=False, new_graph=None, errors=errors)

        dangling = _validate_no_dangling_edges(working)
        if dangling:
            return MutationResult(
                success=False,
                new_graph=None,
                errors=[
                    _operation_error(
                        op_index=-1,
                        op_type="validation",
                        message=msg,
                        stage="validation",
                    )
                    for msg in dangling
                ],
            )

        if has_structural:
            _recompute_entry_exit_points(working)
        return MutationResult(
            success=True,
            new_graph=working,
            applied_ops=applied,
            diagnostics=diagnostics,
        )

    def _apply_partial(
        self,
        working: dict[str, Any],
        sorted_ops: list[tuple[int, GraphOperation]],
    ) -> MutationResult:
        errors: list[OperationError] = []
        applied: list[int] = []
        diagnostics: list[str] = []
        has_structural = False
        alias: dict[str, str] = {}
        add_node_seq = 0
        original_ids = _node_ids(working)

        for orig_idx, op in sorted_ops:
            op = self._resolve_op_aliases(op, alias)
            snapshot = copy.deepcopy(working)
            err = self._apply_op(working, op, diagnostics)
            if err:
                errors.append(
                    _operation_error(
                        op_index=orig_idx,
                        op_type=op.op,
                        message=err,
                        stage="apply",
                        op=op,
                    )
                )
                working.clear()
                working.update(snapshot)
            else:
                applied.append(orig_idx)
                if op.op in self._STRUCTURAL_OPS:
                    has_structural = True
                if isinstance(op, AddNode):
                    add_node_seq += 1
                    self._track_add_node_alias(op, working, alias, add_node_seq, original_ids)

        if has_structural:
            _recompute_entry_exit_points(working)
        return MutationResult(
            success=len(errors) == 0,
            new_graph=working,
            applied_ops=applied,
            errors=errors,
            diagnostics=diagnostics,
        )

    def _apply_op(
        self,
        graph: dict[str, Any],
        op: GraphOperation,
        diagnostics: list[str] | None = None,
    ) -> str | None:
        """Apply a single operation to *graph* in place. Return error message or None."""
        try:
            if isinstance(op, AddNode):
                return self._op_add_node(graph, op)
            if isinstance(op, RemoveNode):
                return self._op_remove_node(graph, op, diagnostics)
            if isinstance(op, EditNode):
                return self._op_edit_node(graph, op)
            if isinstance(op, AddEdge):
                return self._op_add_edge(graph, op, diagnostics)
            if isinstance(op, RemoveEdge):
                return self._op_remove_edge(graph, op)
            if isinstance(op, EditEdge):
                return self._op_edit_edge(graph, op)
            if isinstance(op, SetNodePosition):
                return self._op_set_position(graph, op)
            if isinstance(op, ReplaceSubgraph):
                return self._op_replace_subgraph(graph, op)
            if isinstance(op, ReplaceBodyGraph):
                return self._op_replace_body_graph(graph, op, diagnostics)
            if isinstance(op, ExpandPattern):
                return self._op_expand_pattern(graph, op, diagnostics)
            if isinstance(op, ApplySkill):
                return self._op_apply_skill(graph, op)
            if isinstance(op, AddHyperedge):
                return self._op_add_hyperedge(graph, op)
            if isinstance(op, RemoveHyperedge):
                return self._op_remove_hyperedge(graph, op)
            if isinstance(op, EditHyperedge):
                return self._op_edit_hyperedge(graph, op)
            return f"Unknown operation type: {op.op}"
        except Exception as exc:
            logger.exception("Unexpected error applying %s", op.op)
            return f"Internal error: {exc}"

    # -- individual operation handlers ---------------------------------

    def _op_add_node(self, graph: dict[str, Any], op: AddNode) -> str | None:
        existing_ids = _node_ids(graph)
        if op.id:
            if op.id in existing_ids:
                return f"Node ID '{op.id}' already exists in the graph"
            node_id = op.id
        else:
            node_id = _generate_node_id(op.name, existing_ids)

        requested_node_type = op.node_type
        config = _default_node_config(requested_node_type)
        config.update(op.config)
        input_ports, output_ports = _default_ports(requested_node_type, config)
        effective_node_type, effective_config = _workerize_mutation_node_config(
            requested_node_type,
            config,
        )

        node: dict[str, Any] = {
            "id": node_id,
            "node_type": effective_node_type,
            "name": op.name,
            "description": effective_config.pop("description", ""),
            "input_ports": effective_config.pop("input_ports", input_ports),
            "output_ports": effective_config.pop("output_ports", output_ports),
            "position": effective_config.pop("position", {"x": 0, "y": 0}),
            "ui": effective_config.pop("ui", {}),
            "metadata": effective_config.pop("metadata", {}),
        }
        node.update(effective_config)

        graph.setdefault("nodes", []).append(node)
        if _node_uses_body_graph(effective_node_type):
            if effective_node_type != "worker" or bool(node.get("body_graph")):
                _ensure_subgraph(graph, node_id, [], [], [], [])
        logger.debug("Added node %s (%s)", node_id, effective_node_type)
        return None

    def _op_remove_node(
        self,
        graph: dict[str, Any],
        op: RemoveNode,
        diagnostics: list[str] | None = None,
    ) -> str | None:
        if _find_node(graph, op.node_id) is None:
            return f"Node '{op.node_id}' not found"

        graph["nodes"] = [n for n in graph["nodes"] if n["id"] != op.node_id]
        _remove_edges_for_node(graph, op.node_id)

        for key in ("entry_points", "exit_points"):
            pts = graph.get(key, [])
            if op.node_id in pts:
                graph[key] = [x for x in pts if x != op.node_id]

        return None

    def _op_edit_node(self, graph: dict[str, Any], op: EditNode) -> str | None:
        node = _find_node(graph, op.node_id)
        if node is None:
            return f"Node '{op.node_id}' not found"
        updates = dict(op.updates)
        config_updates = updates.pop("config", None)
        if isinstance(config_updates, dict):
            existing_config = node.get("config")
            if isinstance(existing_config, dict):
                merged_config = dict(existing_config)
                merged_config.update(config_updates)
                node["config"] = merged_config
            else:
                node.update(config_updates)
        node.update(updates)
        return None

    def _op_add_edge(
        self,
        graph: dict[str, Any],
        op: AddEdge,
        diagnostics: list[str] | None = None,
    ) -> str | None:
        """Add edge; when target_port is missing, auto-create (unless strict) and append to diagnostics.
        When diagnostics is provided, appends warning messages in-place (e.g. auto-created port)."""
        ids = _node_ids(graph)
        if op.source_id not in ids:
            return f"Source node '{op.source_id}' not found"
        if op.target_id not in ids:
            return f"Target node '{op.target_id}' not found"

        source_node = _find_node(graph, op.source_id)
        target_node = _find_node(graph, op.target_id)

        resolved_source_port, source_port_error = _resolve_source_output_port(
            source_node,
            requested_port=op.source_port,
            node_id=op.source_id,
            diagnostics=diagnostics,
        )
        if source_port_error is not None:
            return source_port_error

        target_ports = [p["name"] for p in target_node.get("input_ports", [])]
        if op.target_port not in target_ports:
            if op.strict:
                return (
                    f"Target node '{op.target_id}' has no input port '{op.target_port}'. "
                    f"Available ports: {target_ports}. "
                    "Use strict=False to auto-create (not recommended)."
                )
            target_node.setdefault("input_ports", []).append(
                {"name": op.target_port, "schema": {}, "required": False}
            )
            msg = (
                f"Auto-created input port '{op.target_port}' on node '{op.target_id}' "
                "(port not declared). Verify spelling."
            )
            if diagnostics is not None:
                diagnostics.append(msg)
            logger.debug("Auto-created input port '%s' on node '%s'", op.target_port, op.target_id)

        edge_id = f"{op.source_id}.{resolved_source_port}->{op.target_id}.{op.target_port}"

        for e in graph.get("edges", []):
            if e.get("id") == edge_id:
                return f"Edge '{edge_id}' already exists"

        edge: dict[str, Any] = {
            "id": edge_id,
            "edge_type": op.edge_type,
            "source_node_id": op.source_id,
            "source_port": resolved_source_port,
            "target_node_id": op.target_id,
            "target_port": op.target_port,
            "ui": {},
            "metadata": {},
        }
        if op.edge_type == "data" and op.spread:
            edge["spread"] = True
        graph.setdefault("edges", []).append(edge)
        return None

    def _op_remove_edge(self, graph: dict[str, Any], op: RemoveEdge) -> str | None:
        edges = graph.get("edges", [])
        before = len(edges)
        graph["edges"] = [
            e
            for e in edges
            if not (
                e.get("source_node_id") == op.source_id
                and e.get("source_port") == op.source_port
                and e.get("target_node_id") == op.target_id
                and e.get("target_port") == op.target_port
            )
        ]
        if len(graph["edges"]) == before:
            return (
                f"No edge from {op.source_id}.{op.source_port} "
                f"-> {op.target_id}.{op.target_port}"
            )
        return None

    def _op_edit_edge(self, graph: dict[str, Any], op: EditEdge) -> str | None:
        for e in graph.get("edges", []):
            if e.get("id") == op.edge_id:
                e.update(op.updates)
                return None
        return f"Edge '{op.edge_id}' not found"

    def _op_set_position(self, graph: dict[str, Any], op: SetNodePosition) -> str | None:
        node = _find_node(graph, op.node_id)
        if node is None:
            return f"Node '{op.node_id}' not found"
        node["position"] = {"x": op.x, "y": op.y}
        return None

    def _op_replace_subgraph(
        self, graph: dict[str, Any], op: ReplaceSubgraph
    ) -> str | None:
        existing_ids = _node_ids(graph)
        missing = [nid for nid in op.node_ids_to_remove if nid not in existing_ids]
        if missing:
            return f"Nodes to remove not found: {', '.join(missing)}"

        for nid in op.node_ids_to_remove:
            graph["nodes"] = [n for n in graph["nodes"] if n["id"] != nid]
            _remove_edges_for_node(graph, nid)
            for key in ("entry_points", "exit_points"):
                pts = graph.get(key, [])
                if nid in pts:
                    graph[key] = [x for x in pts if x != nid]

        for node_dict in op.new_nodes:
            graph.setdefault("nodes", []).append(node_dict)

        for edge_dict in op.new_edges:
            graph.setdefault("edges", []).append(edge_dict)

        return None

    def _op_replace_body_graph(
        self,
        graph: dict[str, Any],
        op: ReplaceBodyGraph,
        diagnostics: list[str] | None = None,
    ) -> str | None:
        node = _find_node(graph, op.node_id)
        if node is None:
            return f"Node '{op.node_id}' not found"
        node_type = str(node.get("node_type") or "")
        if not _node_uses_body_graph(node_type):
            return (
                f"Node '{op.node_id}' ({node_type}) does not support body sub-graphs"
            )

        body_graph = {
            "version": "dan_graph_v1",
            "metadata": {"name": f"{op.node_id} body", "description": "", "version": "1"},
            "nodes": [],
            "edges": [],
            "sub_graphs": {},
            "entry_points": [],
            "exit_points": [],
        }
        sorted_ops = self._sort_operations(op.operations)
        for _original_index, body_op in sorted_ops:
            err = self._apply_op(body_graph, body_op, diagnostics)
            if err:
                return f"Body graph for '{op.node_id}': {err}"

        entry_ids = list(op.entry_ids) if op.entry_ids is not None else []
        exit_ids = list(op.exit_ids) if op.exit_ids is not None else []
        if not entry_ids or not exit_ids:
            _refresh_entry_exit(body_graph)
            if not entry_ids:
                entry_ids = list(body_graph.get("entry_points", []))
            if not exit_ids:
                exit_ids = list(body_graph.get("exit_points", []))

        _ensure_subgraph(
            graph,
            op.node_id,
            body_graph.get("nodes", []),
            body_graph.get("edges", []),
            entry_ids,
            exit_ids,
        )
        body_key = str(node.get("body_graph") or f"{op.node_id}__body")
        graph.setdefault("sub_graphs", {})[body_key]["sub_graphs"] = copy.deepcopy(
            body_graph.get("sub_graphs", {}),
        )
        return None

    def _op_apply_skill(self, graph: dict[str, Any], op: ApplySkill) -> str | None:
        from dan.server.skill_library import SKILL_LIBRARY
        if op.skill not in SKILL_LIBRARY:
            available = ", ".join(sorted(SKILL_LIBRARY.keys()))
            return f"Unknown skill '{op.skill}' (available: {available})"
        skill = SKILL_LIBRARY[op.skill]
        skill_text = skill["text"]
        inject_as = skill.get("inject_as", "system")

        if "hyperedges" in graph or not graph.get("nodes"):
            he_dict: dict[str, Any] = {
                "id": f"skill_{op.skill}_{uuid.uuid4().hex[:8]}",
                "name": skill.get("name", op.skill),
                "hyperedge_type": "skill",
                "hook": "pre_prompt",
                "content": skill_text,
                "enabled": True,
                "propagate": True,
            }
            if op.target_nodes:
                he_dict["attach_to"] = list(op.target_nodes)
            elif op.target_tag:
                he_dict["attach_to_tags"] = [op.target_tag]
            else:
                he_dict["attach_to_tags"] = skill.get("tags", [])

            graph.setdefault("hyperedges", []).append(he_dict)
            return None

        nodes = graph.get("nodes", [])
        matched = []
        for node in nodes:
            if op.target_nodes and node["id"] in op.target_nodes:
                matched.append(node)
            elif op.target_tag:
                node_tags = node.get("metadata", {}).get("tags", [])
                if op.target_tag in node_tags:
                    matched.append(node)

        if not matched:
            if op.target_nodes:
                return f"No matching nodes found for target_nodes={op.target_nodes}"
            if op.target_tag:
                return f"No nodes found with tag '{op.target_tag}'"
            return "No target specified (provide target_nodes or target_tag)"

        for node in matched:
            llm_hints = node.get("llm_hints")
            if inject_as == "system":
                if isinstance(llm_hints, dict):
                    existing = str(llm_hints.get("system_prompt", "") or "")
                    if skill_text not in existing:
                        llm_hints["system_prompt"] = (
                            f"{skill_text}\n\n{existing}" if existing else skill_text
                        )
                else:
                    existing = str(node.get("system_prompt", "") or "")
                    if skill_text not in existing:
                        node["system_prompt"] = (
                            f"{skill_text}\n\n{existing}" if existing else skill_text
                        )
            else:
                if isinstance(llm_hints, dict):
                    existing = str(llm_hints.get("prompt_template", "") or "")
                    if skill_text not in existing:
                        llm_hints["prompt_template"] = (
                            f"{skill_text}\n\n{existing}" if existing else skill_text
                        )
                else:
                    existing = str(node.get("prompt_template", "") or "")
                    if skill_text not in existing:
                        node["prompt_template"] = (
                            f"{skill_text}\n\n{existing}" if existing else skill_text
                        )

        return None

    def _op_add_hyperedge(self, graph: dict[str, Any], op: AddHyperedge) -> str | None:
        hyperedges = graph.setdefault("hyperedges", [])
        he_id = op.hyperedge.get("id")
        if he_id and any(h.get("id") == he_id for h in hyperedges):
            return f"Hyperedge '{he_id}' already exists"
        hyperedges.append(copy.deepcopy(op.hyperedge))
        return None

    def _op_remove_hyperedge(self, graph: dict[str, Any], op: RemoveHyperedge) -> str | None:
        hyperedges = graph.get("hyperedges", [])
        before = len(hyperedges)
        graph["hyperedges"] = [h for h in hyperedges if h.get("id") != op.hyperedge_id]
        if len(graph["hyperedges"]) == before:
            return f"Hyperedge '{op.hyperedge_id}' not found"
        return None

    def _op_edit_hyperedge(self, graph: dict[str, Any], op: EditHyperedge) -> str | None:
        for h in graph.get("hyperedges", []):
            if h.get("id") == op.hyperedge_id:
                h.update(op.updates)
                return None
        return f"Hyperedge '{op.hyperedge_id}' not found"

    def _op_expand_pattern(
        self,
        graph: dict[str, Any],
        op: ExpandPattern,
        diagnostics: list[str] | None = None,
    ) -> str | None:
        if op.pattern not in PATTERN_LIBRARY:
            available = ", ".join(sorted(PATTERN_LIBRARY.keys()))
            return f"Unknown pattern '{op.pattern}' (available: {available})"

        pattern_fn = PATTERN_LIBRARY[op.pattern]
        try:
            raw_ops = pattern_fn(op.params)
        except Exception as exc:
            return f"Pattern expansion error: {exc}"

        _OP_MODELS: dict[str, type[BaseModel]] = {
            "add_node": AddNode,
            "remove_node": RemoveNode,
            "edit_node": EditNode,
            "add_edge": AddEdge,
            "remove_edge": RemoveEdge,
            "edit_edge": EditEdge,
            "replace_body_graph": ReplaceBodyGraph,
            "set_position": SetNodePosition,
            "apply_skill": ApplySkill,
        }

        for i, raw_op in enumerate(raw_ops):
            op_type = raw_op.get("op", "")
            model_cls = _OP_MODELS.get(op_type)
            if model_cls is None:
                return f"Pattern '{op.pattern}' step {i}: unknown op '{op_type}'"
            try:
                parsed_op = model_cls.model_validate(raw_op)
            except Exception as exc:
                return f"Pattern '{op.pattern}' step {i}: {exc}"
            err = self._apply_op(graph, parsed_op, diagnostics)
            if err:
                return f"Pattern '{op.pattern}' step {i}: {err}"

        return None
