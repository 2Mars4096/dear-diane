"""Graph mutation engine — applies structured operations to a dan_graph_v1 dict."""

from __future__ import annotations

import copy
import logging
import re
import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

__all__ = [
    "AddEdge",
    "AddNode",
    "EditEdge",
    "EditNode",
    "GraphMutator",
    "GraphOperation",
    "MutationPlan",
    "MutationResult",
    "OperationError",
    "RemoveEdge",
    "RemoveNode",
    "ReplaceSubgraph",
    "SetNodePosition",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Operation models (discriminated union on ``op``)
# ---------------------------------------------------------------------------


class AddNode(BaseModel):
    op: Literal["add_node"] = "add_node"
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


class OperationError(BaseModel):
    op_index: int
    op_type: str
    message: str


class MutationResult(BaseModel):
    success: bool
    new_graph: dict[str, Any] | None = None
    applied_ops: list[int] = Field(default_factory=list)
    errors: list[OperationError] = Field(default_factory=list)
    stale_plan: bool = False


# ---------------------------------------------------------------------------
# Execution-order priority (operations are auto-sorted before applying)
# ---------------------------------------------------------------------------

_OP_SORT_ORDER: dict[str, int] = {
    "add_node": 0,
    "set_position": 1,
    "edit_node": 2,
    "add_edge": 3,
    "edit_edge": 4,
    "remove_edge": 5,
    "remove_node": 6,
    "replace_subgraph": 7,
}


# ---------------------------------------------------------------------------
# Default ports by node type
# ---------------------------------------------------------------------------


def _default_ports(node_type: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return default (input_ports, output_ports) for a node type."""
    table: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {
        "llm_operator": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "text", "schema": {}}],
        ),
        "tool_operator": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "code_operator": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "if_else": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "true", "schema": {}}, {"name": "false", "schema": {}}],
        ),
        "gate": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "true", "schema": {}}, {"name": "false", "schema": {}}],
        ),
        "while_loop": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "output", "schema": {}}],
        ),
        "for_each": (
            [{"name": "items", "schema": {}, "required": False}],
            [{"name": "results", "schema": {}}],
        ),
        "reduce": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "router": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "route", "schema": {}}, {"name": "output", "schema": {}}],
        ),
        "human_in_the_loop": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "response", "schema": {}}],
        ),
        "composite": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "output", "schema": {}}],
        ),
        "input": (
            [],
            [{"name": "input", "schema": {}}],
        ),
        "rag_operator": (
            [{"name": "query", "schema": {}, "required": False}],
            [{"name": "chunks", "schema": {}}, {"name": "scores", "schema": {}}],
        ),
        "validator": (
            [{"name": "data", "schema": {}, "required": False}],
            [{"name": "valid", "schema": {}}, {"name": "invalid", "schema": {}}],
        ),
    }
    fallback: tuple[list[dict[str, Any]], list[dict[str, Any]]] = (
        [{"name": "input", "schema": {}, "required": False}],
        [{"name": "output", "schema": {}}],
    )
    inputs, outputs = table.get(node_type, fallback)
    return copy.deepcopy(inputs), copy.deepcopy(outputs)


# ---------------------------------------------------------------------------
# Default node config by type
# ---------------------------------------------------------------------------


def _default_node_config(node_type: str) -> dict[str, Any]:
    """Return sensible default config fields for a node type."""
    table: dict[str, dict[str, Any]] = {
        "llm_operator": {
            "model": "claude-sonnet-4-6",
            "prompt_template": "",
            "system_prompt": "",
            "temperature": 0.7,
        },
        "code_operator": {
            "code": "",
            "language": "python",
            "sandbox_config": {},
        },
        "tool_operator": {
            "tool_id": "",
            "tool_config": {},
        },
        "gate": {
            "gate_mode": "if_else",
            "condition": "",
            "max_iterations": 10,
        },
        "if_else": {
            "condition": "",
        },
        "while_loop": {
            "condition": "",
            "body_graph": "",
            "max_iterations": 10,
        },
        "for_each": {
            "body_graph": "",
            "parallelism": 1,
            "merge_strategy": "append",
        },
        "reduce": {
            "reducer": "",
        },
        "router": {
            "model": "claude-sonnet-4-6",
            "route_descriptions": {},
        },
        "human_in_the_loop": {
            "prompt": "",
            "timeout_seconds": None,
            "default_action": None,
        },
        "composite": {
            "body_graph": "",
            "input_mappings": {},
            "output_mappings": {},
        },
        "rag_operator": {
            "collection": "",
            "top_k": 5,
            "query_template": "{query}",
            "include_metadata": True,
            "rerank": False,
        },
        "validator": {
            "validation_rules": [],
            "on_failure": "route",
            "strict_mode": False,
        },
        "input": {
            "variables": [],
        },
    }
    return copy.deepcopy(table.get(node_type, {}))


# ---------------------------------------------------------------------------
# Slugify / ID generation
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug for node IDs."""
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "node"


def _generate_node_id(name: str, existing_ids: set[str]) -> str:
    """Generate a unique node ID from a name, avoiding collisions."""
    base = _slugify(name)
    if base not in existing_ids:
        return base
    counter = 2
    while f"{base}-{counter}" in existing_ids:
        counter += 1
    return f"{base}-{counter}"


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
            return MutationResult(
                success=False,
                stale_plan=True,
                errors=[
                    OperationError(
                        op_index=-1,
                        op_type="concurrency_check",
                        message=(
                            f"Plan based on revision '{plan.base_graph_revision}' "
                            f"but current is '{current_revision}'"
                        ),
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
        return self.apply(graph_dict, plan, current_revision)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sort_operations(
        operations: list[GraphOperation],
    ) -> list[tuple[int, GraphOperation]]:
        """Return (original_index, op) pairs sorted by execution order."""
        indexed = list(enumerate(operations))
        indexed.sort(key=lambda pair: _OP_SORT_ORDER.get(pair[1].op, 99))
        return indexed

    def _apply_all_or_nothing(
        self,
        working: dict[str, Any],
        sorted_ops: list[tuple[int, GraphOperation]],
    ) -> MutationResult:
        errors: list[OperationError] = []
        applied: list[int] = []

        for orig_idx, op in sorted_ops:
            err = self._apply_op(working, op)
            if err:
                errors.append(OperationError(op_index=orig_idx, op_type=op.op, message=err))
            else:
                applied.append(orig_idx)

        if errors:
            return MutationResult(success=False, new_graph=None, errors=errors)

        dangling = _validate_no_dangling_edges(working)
        if dangling:
            return MutationResult(
                success=False,
                new_graph=None,
                errors=[
                    OperationError(op_index=-1, op_type="validation", message=msg)
                    for msg in dangling
                ],
            )

        return MutationResult(success=True, new_graph=working, applied_ops=applied)

    def _apply_partial(
        self,
        working: dict[str, Any],
        sorted_ops: list[tuple[int, GraphOperation]],
    ) -> MutationResult:
        errors: list[OperationError] = []
        applied: list[int] = []

        for orig_idx, op in sorted_ops:
            snapshot = copy.deepcopy(working)
            err = self._apply_op(working, op)
            if err:
                errors.append(OperationError(op_index=orig_idx, op_type=op.op, message=err))
                working.clear()
                working.update(snapshot)
            else:
                applied.append(orig_idx)

        return MutationResult(
            success=len(errors) == 0,
            new_graph=working,
            applied_ops=applied,
            errors=errors,
        )

    def _apply_op(self, graph: dict[str, Any], op: GraphOperation) -> str | None:
        """Apply a single operation to *graph* in place. Return error message or None."""
        try:
            if isinstance(op, AddNode):
                return self._op_add_node(graph, op)
            if isinstance(op, RemoveNode):
                return self._op_remove_node(graph, op)
            if isinstance(op, EditNode):
                return self._op_edit_node(graph, op)
            if isinstance(op, AddEdge):
                return self._op_add_edge(graph, op)
            if isinstance(op, RemoveEdge):
                return self._op_remove_edge(graph, op)
            if isinstance(op, EditEdge):
                return self._op_edit_edge(graph, op)
            if isinstance(op, SetNodePosition):
                return self._op_set_position(graph, op)
            if isinstance(op, ReplaceSubgraph):
                return self._op_replace_subgraph(graph, op)
            return f"Unknown operation type: {op.op}"
        except Exception as exc:
            logger.exception("Unexpected error applying %s", op.op)
            return f"Internal error: {exc}"

    # -- individual operation handlers ---------------------------------

    def _op_add_node(self, graph: dict[str, Any], op: AddNode) -> str | None:
        existing_ids = _node_ids(graph)
        node_id = _generate_node_id(op.name, existing_ids)

        input_ports, output_ports = _default_ports(op.node_type)
        config = _default_node_config(op.node_type)
        config.update(op.config)

        node: dict[str, Any] = {
            "id": node_id,
            "node_type": op.node_type,
            "name": op.name,
            "description": config.pop("description", ""),
            "input_ports": config.pop("input_ports", input_ports),
            "output_ports": config.pop("output_ports", output_ports),
            "position": config.pop("position", {"x": 0, "y": 0}),
            "ui": config.pop("ui", {}),
            "metadata": config.pop("metadata", {}),
        }
        node.update(config)

        graph.setdefault("nodes", []).append(node)
        logger.debug("Added node %s (%s)", node_id, op.node_type)
        return None

    def _op_remove_node(self, graph: dict[str, Any], op: RemoveNode) -> str | None:
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
        node.update(op.updates)
        return None

    def _op_add_edge(self, graph: dict[str, Any], op: AddEdge) -> str | None:
        ids = _node_ids(graph)
        if op.source_id not in ids:
            return f"Source node '{op.source_id}' not found"
        if op.target_id not in ids:
            return f"Target node '{op.target_id}' not found"

        edge_id = f"{op.source_id}.{op.source_port}->{op.target_id}.{op.target_port}"

        for e in graph.get("edges", []):
            if e.get("id") == edge_id:
                return f"Edge '{edge_id}' already exists"

        edge: dict[str, Any] = {
            "id": edge_id,
            "edge_type": op.edge_type,
            "source_node_id": op.source_id,
            "source_port": op.source_port,
            "target_node_id": op.target_id,
            "target_port": op.target_port,
            "ui": {},
            "metadata": {},
        }
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
