"""Graph mutation engine — applies structured operations to a dan_graph_v1 dict."""

from __future__ import annotations

import copy
import enum
import logging
import os
import re
import uuid
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from dan.models.node_taxonomy import BODY_GRAPH_RUNTIME_NODE_TYPES
from dan.models.graph import Graph
from dan.models.node_taxonomy import worker_builder_uses_workers
from dan.validation.graph import validate_graph

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
]

logger = logging.getLogger(__name__)

_INPUT_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_WORKER_MUTATOR_LEGACY_COMPUTE_TYPES: frozenset[str] = frozenset({
    "llm_operator",
    "tool_operator",
    "code_operator",
    "rag_operator",
    "router",
    "validator",
    "reflection",
    "input",
    "human",
    "human_in_the_loop",
    "vote",
    "reduce",
})


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
# Worker-builder rollout helpers
# ---------------------------------------------------------------------------


def _graph_mutator_uses_workers() -> bool:
    return worker_builder_uses_workers(mode=os.environ.get("DAN_WORKER_BUILDER"))


def _workerize_mutation_node_config(
    node_type: str,
    config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if not _graph_mutator_uses_workers() or node_type not in _WORKER_MUTATOR_LEGACY_COMPUTE_TYPES:
        return node_type, config

    worker_config = copy.deepcopy(config)
    metadata = dict(worker_config.pop("metadata", {}) or {})

    if node_type == "llm_operator":
        llm_hints = dict(worker_config.pop("llm_hints", {}) or {})
        for key in (
            "prompt_template",
            "system_prompt",
            "temperature",
            "max_tokens",
            "output_json_schema",
            "tools",
            "max_tool_rounds",
            "history_policy",
            "task_tier",
        ):
            if key in worker_config:
                llm_hints[key] = worker_config.pop(key)
        worker_config["llm_hints"] = llm_hints
        return "worker", worker_config

    if node_type == "tool_operator":
        tool_id = worker_config.pop("tool_id", "")
        worker_config["tool_ids"] = [tool_id] if tool_id else []
        tool_config = worker_config.pop("tool_config", None)
        if tool_config:
            metadata["tool_config"] = tool_config
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "code_operator":
        worker_config.pop("sandbox_config", None)
        return "worker", worker_config

    if node_type == "rag_operator":
        worker_config["role"] = "rag"
        metadata.update({
            "rag_collection": worker_config.pop("collection", ""),
            "rag_top_k": worker_config.pop("top_k", 5),
            "rag_query_template": worker_config.pop("query_template", "{query}"),
            "rag_include_metadata": worker_config.pop("include_metadata", True),
            "rag_rerank": worker_config.pop("rerank", False),
        })
        if "similarity_threshold" in worker_config:
            metadata["rag_similarity_threshold"] = worker_config.pop("similarity_threshold")
        if worker_config.get("embedding_model"):
            metadata["rag_embedding_model"] = worker_config.pop("embedding_model")
        if worker_config.get("vector_store_config"):
            metadata["rag_vector_store_config"] = worker_config.pop("vector_store_config")
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "router":
        worker_config["role"] = "router"
        metadata["route_descriptions"] = dict(worker_config.pop("route_descriptions", {}) or {})
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "validator":
        worker_config["role"] = "validator"
        worker_config["validation_rules"] = list(worker_config.pop("validation_rules", []))
        metadata["validator_on_failure"] = worker_config.pop("on_failure", "route")
        metadata["validator_strict_mode"] = bool(worker_config.pop("strict_mode", False))
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "reflection":
        worker_config["role"] = "reflection"
        reflection_model = worker_config.pop("reflection_model", None)
        source = worker_config.pop("source", None)
        source_config = worker_config.pop("source_config", None)
        output_format = worker_config.pop("output_format", None)
        max_principles = worker_config.pop("max_principles", None)
        min_confidence = worker_config.pop("min_confidence", None)
        dedup_strategy = worker_config.pop("dedup_strategy", None)
        metadata["reflection_prompt"] = worker_config.pop("reflection_prompt", "")
        if reflection_model is not None:
            worker_config["model"] = reflection_model
            metadata["reflection_model"] = reflection_model
        if source is not None:
            metadata["reflection_source"] = source
        if source_config is not None:
            metadata["reflection_source_config"] = source_config
        if output_format is not None:
            metadata["reflection_output_format"] = output_format
        if max_principles is not None:
            metadata["reflection_max_principles"] = max_principles
        if min_confidence is not None:
            metadata["reflection_min_confidence"] = min_confidence
        if dedup_strategy is not None:
            metadata["reflection_dedup_strategy"] = dedup_strategy
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "input":
        metadata["input_variables"] = worker_config.pop("variables", [])
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type in {"human", "human_in_the_loop"}:
        worker_config["role"] = node_type
        metadata["human_prompt"] = worker_config.pop("prompt", "")
        if "timeout_seconds" in worker_config:
            metadata["human_timeout_seconds"] = worker_config.pop("timeout_seconds")
        if "default_action" in worker_config:
            metadata["human_default_action"] = worker_config.pop("default_action")
        if "input_schema" in worker_config:
            metadata["human_input_schema"] = worker_config.pop("input_schema")
        if "output_schema" in worker_config:
            metadata["human_output_schema"] = worker_config.pop("output_schema")
        metadata["human_render_mode"] = worker_config.pop("render_mode", "text")
        if "options" in worker_config:
            metadata["human_options"] = worker_config.pop("options")
        metadata["human_instructions"] = worker_config.pop("instructions", "")
        metadata["human_render_target"] = worker_config.pop("render_target", "dialog")
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "vote":
        worker_config["role"] = "vote"
        metadata.update({
            "vote_candidates": worker_config.pop("candidates", []),
            "vote_num_votes": worker_config.pop("num_votes", 3),
            "vote_prompt_template": worker_config.pop("prompt_template", ""),
            "vote_system_prompt": worker_config.pop("system_prompt", ""),
            "vote_temperature": worker_config.pop("temperature", 0.7),
            "vote_strategy": worker_config.pop("vote_strategy", "majority"),
            "vote_parallelism": worker_config.pop("parallelism", 3),
        })
        if "output_json_schema" in worker_config:
            metadata["vote_output_json_schema"] = worker_config.pop("output_json_schema")
        if "vote_config" in worker_config:
            metadata["vote_config"] = worker_config.pop("vote_config")
        if "timeout_seconds" in worker_config:
            metadata["vote_timeout_seconds"] = worker_config.pop("timeout_seconds")
        worker_config["metadata"] = metadata
        return "worker", worker_config

    if node_type == "reduce":
        worker_config["role"] = "reduce"
        metadata["reduce_expression"] = worker_config.pop("reducer", "")
        worker_config["metadata"] = metadata
        return "worker", worker_config

    return node_type, config


def _node_is_input_like(node: dict[str, Any] | None) -> bool:
    if not isinstance(node, dict):
        return False
    node_type = str(node.get("node_type") or "").strip()
    if node_type == "input":
        return True
    if node_type != "worker":
        return False
    metadata = node.get("metadata")
    return isinstance(metadata, dict) and "input_variables" in metadata


def _ensure_input_variable_output_port(
    node: dict[str, Any],
    *,
    port_name: str,
    node_id: str,
) -> str | None:
    """Add a missing named output port for an input-like node when unambiguous."""
    if not _node_is_input_like(node):
        return None

    normalized_port = str(port_name or "").strip()
    if (
        not normalized_port
        or normalized_port == "input"
        or not _INPUT_VARIABLE_NAME_RE.fullmatch(normalized_port)
    ):
        return None

    output_ports = node.setdefault("output_ports", [])
    existing_output_names = {
        str(port.get("name") or "").strip()
        for port in output_ports
        if isinstance(port, dict)
    }
    if normalized_port in existing_output_names:
        return None

    if str(node.get("node_type") or "").strip() == "input":
        variables = node.setdefault("variables", [])
    else:
        metadata = node.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            node["metadata"] = metadata
        variables = metadata.setdefault("input_variables", [])

    existing_variable_names = set()
    if isinstance(variables, list):
        for item in variables:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item or "").strip()
            if name:
                existing_variable_names.add(name)
    else:
        variables = []
        if str(node.get("node_type") or "").strip() == "input":
            node["variables"] = variables
        else:
            node.setdefault("metadata", {})["input_variables"] = variables

    if normalized_port not in existing_variable_names:
        variables.append({
            "name": normalized_port,
            "type": "string",
            "default": "",
        })

    output_ports.append({"name": normalized_port, "schema": {}})
    return (
        f"Auto-added input variable/output port '{normalized_port}' on input node "
        f"'{node_id}' because a later edge referenced it."
    )


# ---------------------------------------------------------------------------
# Default ports by node type
# ---------------------------------------------------------------------------


def _default_ports(
    node_type: str, config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return default (input_ports, output_ports) for a node type.

    For ``gate`` nodes, the output ports depend on ``gate_mode`` in *config*.
    """
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
        "worker": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "if_else": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "true", "schema": {}},
                {"name": "false", "schema": {}},
            ],
        ),
        "gate": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "true", "schema": {}}, {"name": "false", "schema": {}}],
        ),
        "gate__while": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "continue", "schema": {}}, {"name": "done", "schema": {}}],
        ),
        "while_loop": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "goal_loop": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "result", "schema": {}},
                {"name": "goal_met", "schema": {}},
                {"name": "iterations", "schema": {}},
                {"name": "best_score", "schema": {}},
            ],
        ),
        "for_each": (
            [{"name": "items", "schema": {}, "required": False}],
            [{"name": "results", "schema": {}}],
        ),
        "parallel_subagents": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "results", "schema": {}}],
        ),
        "orchestrator": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "results", "schema": {}}],
        ),
        "reduce": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "result", "schema": {}}],
        ),
        "router": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "route", "schema": {}}],
        ),
        "human": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "response", "schema": {}}],
        ),
        "human_in_the_loop": (
            [{"name": "input", "schema": {}, "required": False}],
            [{"name": "response", "schema": {}}],
        ),
        "agent_team": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "result", "schema": {}},
                {"name": "agent_contributions", "schema": {}},
                {"name": "consensus_reached", "schema": {}},
                {"name": "total_turns", "schema": {}},
                {"name": "conversation", "schema": {}},
            ],
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
        "reflection": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "principles", "schema": {}},
                {"name": "principle_count", "schema": {}},
                {"name": "source", "schema": {}},
                {"name": "text", "schema": {}},
            ],
        ),
        "vote": (
            [{"name": "input", "schema": {}, "required": False}],
            [
                {"name": "winner", "schema": {}},
                {"name": "winner_model", "schema": {}},
                {"name": "winner_index", "schema": {}},
                {"name": "all_votes", "schema": {}},
                {"name": "consensus_reached", "schema": {}},
                {"name": "vote_count", "schema": {}},
                {"name": "total_cost", "schema": {}},
                {"name": "strategy_used", "schema": {}},
            ],
        ),
    }
    fallback: tuple[list[dict[str, Any]], list[dict[str, Any]]] = (
        [{"name": "input", "schema": {}, "required": False}],
        [{"name": "output", "schema": {}}],
    )
    if node_type == "tool_operator" and config:
        tool_id = config.get("tool_id", "")
        if tool_id in TOOL_PORT_MANIFESTS:
            inputs, outputs = TOOL_PORT_MANIFESTS[tool_id]
            return copy.deepcopy(inputs), copy.deepcopy(outputs)

    if node_type == "input" and config and config.get("variables"):
        out_ports = []
        for var in config["variables"]:
            name = var.get("name", "input") if isinstance(var, dict) else str(var)
            out_ports.append({"name": name, "schema": {}})
        if out_ports:
            names = {p["name"] for p in out_ports}
            # Keep variable-specific outputs while also exposing an aggregate
            # "input" payload for generated plans that wire from input.input.
            if "input" not in names:
                out_ports.insert(0, {"name": "input", "schema": {}})
            return [], copy.deepcopy(out_ports)

    lookup_key = node_type
    if node_type == "gate" and config and config.get("gate_mode") == "while":
        lookup_key = "gate__while"
    inputs, outputs = table.get(lookup_key, fallback)
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
        "worker": {
            "role": "",
            "instruction": "",
            "persona": "",
            "authority": "leaf",
            "model": None,
            "tool_ids": [],
            "code": "",
            "language": "python",
            "sub_workers": {},
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
        "goal_loop": {
            "goal_text": "Reach the target",
            "metric_name": "score",
            "target_value": 1.0,
            "comparison": ">=",
            "max_iterations": 10,
            "evaluator": "llm_judge",
            "success_criteria": None,
            "body_graph": "",
        },
        "for_each": {
            "body_graph": "",
            "parallelism": 1,
            "merge_strategy": "append",
        },
        "parallel_subagents": {
            "branch_graphs": [],
            "parallelism": 1,
            "merge_strategy": "append",
            "input_mappings": {},
            "branch_inputs": {},
        },
        "orchestrator": {
            "teams": {},
            "orchestrator_prompt": "",
            "completion_condition": "all_done",
            "max_iterations": 100,
        },
        "reduce": {
            "reducer": "",
        },
        "router": {
            "model": "claude-sonnet-4-6",
            "route_descriptions": {},
        },
        "human": {
            "prompt": "",
            "timeout_seconds": None,
            "default_action": None,
        },
        "human_in_the_loop": {
            "prompt": "",
            "timeout_seconds": None,
            "default_action": None,
        },
        "agent_team": {
            "agents": {},
            "moderator_prompt": "",
            "turn_strategy": "round_robin",
            "max_turns": 20,
            "completion_condition": "max_turns",
            "shared_context_keys": [],
            "handoff_policy": "explicit",
            "input_mappings": {},
            "agent_inputs": {},
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
        "reflection": {
            "reflection_prompt": "",
            "source": "last_run",
            "source_config": {},
            "output_format": "principles",
            "max_principles": 10,
            "min_confidence": 0.3,
            "dedup_strategy": "embedding_similarity",
        },
        "vote": {
            "candidates": ["claude-sonnet-4-6"],
            "num_votes": 3,
            "prompt_template": "",
            "system_prompt": "",
            "temperature": 0.7,
            "vote_strategy": "majority",
            "parallelism": 3,
        },
        "input": {
            "variables": [],
        },
    }
    return copy.deepcopy(table.get(node_type, {}))


# ---------------------------------------------------------------------------
# Tool-aware port manifests
# ---------------------------------------------------------------------------

TOOL_PORT_MANIFESTS: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {
    "csv_read": (
        [
            {"name": "path", "schema": {}, "required": True},
            {"name": "delimiter", "schema": {}, "required": False},
            {"name": "max_rows", "schema": {}, "required": False},
            {"name": "columns", "schema": {}, "required": False},
            {"name": "encoding", "schema": {}, "required": False},
        ],
        [
            {"name": "headers", "schema": {}},
            {"name": "rows", "schema": {}},
            {"name": "row_count", "schema": {}},
            {"name": "total_rows", "schema": {}},
            {"name": "column_count", "schema": {}},
            {"name": "truncated", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "file_read": (
        [{"name": "path", "schema": {}, "required": True}],
        [{"name": "content", "schema": {}}, {"name": "result", "schema": {}}],
    ),
    "file_write": (
        [
            {"name": "path", "schema": {}, "required": True},
            {"name": "content", "schema": {}, "required": True},
            {"name": "mode", "schema": {}, "required": False},
            {"name": "encoding", "schema": {}, "required": False},
        ],
        [
            {"name": "bytes_written", "schema": {}},
            {"name": "path", "schema": {}},
            {"name": "mode", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "list_directory": (
        [{"name": "path", "schema": {}, "required": False}],
        [{"name": "entries", "schema": {}}, {"name": "result", "schema": {}}],
    ),
    "pdf_read": (
        [{"name": "path", "schema": {}, "required": True}],
        [{"name": "text", "schema": {}}, {"name": "result", "schema": {}}],
    ),
    "http_request": (
        [
            {"name": "url", "schema": {}, "required": True},
            {"name": "method", "schema": {}, "required": False},
            {"name": "headers", "schema": {}, "required": False},
            {"name": "body", "schema": {}, "required": False},
            {"name": "timeout", "schema": {}, "required": False},
        ],
        [
            {"name": "status_code", "schema": {}},
            {"name": "headers", "schema": {}},
            {"name": "body", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "web_fetch": (
        [
            {"name": "url", "schema": {}, "required": True},
            {"name": "timeout", "schema": {}, "required": False},
            {"name": "max_length", "schema": {}, "required": False},
        ],
        [
            {"name": "url", "schema": {}},
            {"name": "text", "schema": {}},
            {"name": "status_code", "schema": {}},
            {"name": "content_type", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "web_search": (
        [
            {"name": "query", "schema": {}, "required": True},
            {"name": "num_results", "schema": {}, "required": False},
            {"name": "search_depth", "schema": {}, "required": False},
            {"name": "allowed_domains", "schema": {}, "required": False},
            {"name": "blocked_domains", "schema": {}, "required": False},
            {"name": "location", "schema": {}, "required": False},
            {"name": "max_provider_searches", "schema": {}, "required": False},
            {"name": "multi_provider", "schema": {}, "required": False},
        ],
        [
            {"name": "results", "schema": {}},
            {"name": "count", "schema": {}},
            {"name": "provider", "schema": {}},
            {"name": "providers", "schema": {}},
            {"name": "provider_failures", "schema": {}},
            {"name": "result", "schema": {}},
        ],
    ),
    "compile_latex": (
        [{"name": "content", "schema": {}, "required": True},
         {"name": "title", "schema": {}, "required": True}],
        [{"name": "result", "schema": {}},
         {"name": "pdf_path", "schema": {}},
         {"name": "compile_log", "schema": {}},
         {"name": "compile_success", "schema": {}}],
    ),
    "save_paper": (
        [{"name": "content", "schema": {}, "required": True},
         {"name": "title", "schema": {}, "required": True},
         {"name": "pdf_path", "schema": {}, "required": False}],
        [{"name": "result", "schema": {}},
         {"name": "tex_path", "schema": {}},
         {"name": "bib_path", "schema": {}},
         {"name": "title", "schema": {}}],
    ),
    "package_submission": (
        [{"name": "title", "schema": {}, "required": True},
         {"name": "tex_path", "schema": {}, "required": True},
         {"name": "bib_path", "schema": {}, "required": True}],
        [{"name": "result", "schema": {}}],
    ),
    "citation_verifier": (
        [{"name": "input", "schema": {}, "required": False}],
        [{"name": "result", "schema": {}}],
    ),
    "check_latex_deps": (
        [{"name": "input", "schema": {}, "required": False}],
        [{"name": "result", "schema": {}}],
    ),
    "rag_index_documents": (
        [{"name": "pdf_dir", "schema": {}, "required": True}],
        [{"name": "collection", "schema": {}}, {"name": "result", "schema": {}}],
    ),
    "search_papers": (
        [{"name": "query", "schema": {}, "required": True}],
        [{"name": "result", "schema": {}}],
    ),
}


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
# Pattern library — reusable multi-node graph shapes
# ---------------------------------------------------------------------------


def _pattern_chain(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Chain of N LLM nodes connected sequentially."""
    count = params.get("count", 3)
    names = params.get("names", [f"Step {i+1}" for i in range(count)])
    prompts = params.get("prompts", ["" for _ in range(count)])
    ops: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        prompt = prompts[i] if i < len(prompts) else ""
        ops.append({
            "op": "add_node",
            "node_type": "llm_operator",
            "name": name,
            "config": {"prompt_template": prompt},
        })
    for i in range(len(names) - 1):
        src_id = _slugify(names[i])
        tgt_id = _slugify(names[i + 1])
        ops.append({
            "op": "add_edge",
            "source_id": src_id,
            "source_port": "text",
            "target_id": tgt_id,
            "target_port": "input",
        })
    return ops


def _pattern_review_loop(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Writer -> Reviewer -> Gate (while) with back-edge to Writer."""
    writer_name = params.get("writer_name", "Writer")
    reviewer_name = params.get("reviewer_name", "Reviewer")
    gate_name = params.get("gate_name", "Review Gate")
    condition = params.get("condition", "needs_revision == True")
    max_iter = params.get("max_iterations", 5)
    return [
        {
            "op": "add_node",
            "node_type": "llm_operator",
            "name": writer_name,
            "config": {"prompt_template": params.get("writer_prompt", "")},
        },
        {
            "op": "add_node",
            "node_type": "llm_operator",
            "name": reviewer_name,
            "config": {"prompt_template": params.get("reviewer_prompt", "")},
        },
        {
            "op": "add_node",
            "node_type": "gate",
            "name": gate_name,
            "config": {
                "gate_mode": "while",
                "condition": condition,
                "max_iterations": max_iter,
            },
        },
        {
            "op": "add_edge",
            "source_id": _slugify(writer_name),
            "source_port": "text",
            "target_id": _slugify(reviewer_name),
            "target_port": "input",
        },
        {
            "op": "add_edge",
            "source_id": _slugify(reviewer_name),
            "source_port": "text",
            "target_id": _slugify(gate_name),
            "target_port": "input",
        },
        {
            "op": "add_edge",
            "edge_type": "control",
            "source_id": _slugify(gate_name),
            "source_port": "continue",
            "target_id": _slugify(writer_name),
            "target_port": "input",
        },
    ]


def _pattern_fan_out(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Source -> ForEach with body LLM -> downstream collector."""
    source_name = params.get("source_name", "Source")
    body_name = params.get("body_name", "Processor")
    body_id = _slugify(body_name)
    return [
        {
            "op": "add_node",
            "node_type": "llm_operator",
            "name": source_name,
            "config": {"prompt_template": params.get("source_prompt", "")},
        },
        {
            "op": "add_node",
            "node_type": "for_each",
            "name": "Fan Out",
            "config": {"parallelism": params.get("parallelism", 1)},
        },
        {
            "op": "add_edge",
            "source_id": _slugify(source_name),
            "source_port": "text",
            "target_id": "fan-out",
            "target_port": "items",
        },
        {
            "op": "replace_body_graph",
            "node_id": "fan-out",
            "operations": [
                {
                    "op": "add_node",
                    "id": body_id,
                    "node_type": "llm_operator",
                    "name": body_name,
                    "config": {
                        "prompt_template": params.get("body_prompt", "{item}"),
                        "input_ports": [
                            {"name": "item", "schema": {}, "required": False}
                        ],
                        "output_ports": [{"name": "text", "schema": {}}],
                    },
                },
            ],
            "entry_ids": [body_id],
            "exit_ids": [body_id],
        },
    ]


def _pattern_rag_qa(params: dict[str, Any]) -> list[dict[str, Any]]:
    """RAG retrieval -> LLM answer node."""
    rag_name = params.get("rag_name", "Knowledge Base")
    answer_name = params.get("answer_name", "Answer Generator")
    collection = params.get("collection", "")
    top_k = params.get("top_k", 5)
    return [
        {
            "op": "add_node",
            "node_type": "rag_operator",
            "name": rag_name,
            "config": {"collection": collection, "top_k": top_k},
        },
        {
            "op": "add_node",
            "node_type": "llm_operator",
            "name": answer_name,
            "config": {
                "prompt_template": params.get(
                    "answer_prompt", "Answer based on: {input}"
                ),
            },
        },
        {
            "op": "add_edge",
            "source_id": _slugify(rag_name),
            "source_port": "chunks",
            "target_id": _slugify(answer_name),
            "target_port": "input",
        },
    ]


def _pattern_data_ingest(params: dict[str, Any]) -> list[dict[str, Any]]:
    """PDF directory → index into RAG collection → retrieval-ready.

    The ``rag_index_documents`` tool handles file discovery internally,
    so no separate ``list_directory`` node is needed.
    """
    input_var = params.get("input_var", "pdf_dir")
    collection = params.get("collection", "literature")
    rag_name = params.get("rag_name", "Literature KB")
    top_k = params.get("top_k", 5)
    return [
        {"op": "add_node", "node_type": "input", "name": "PDF Input",
         "config": {"variables": [
             {"name": input_var, "type": "string", "default": "", "description": "Path to PDF directory"},
             {"name": "topic", "type": "string", "default": "", "description": "Research topic or query for retrieval"},
         ]}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Index Documents",
         "config": {"tool_id": "rag_index_documents", "tool_config": {"collection": collection}}},
        {"op": "add_node", "node_type": "rag_operator", "name": rag_name,
         "config": {"collection": collection, "top_k": top_k}},
        {"op": "add_edge", "source_id": "pdf-input", "source_port": input_var,
         "target_id": "index-documents", "target_port": "pdf_dir"},
        {"op": "add_edge", "source_id": "pdf-input", "source_port": "topic",
         "target_id": _slugify(rag_name), "target_port": "query"},
        {"op": "add_edge", "edge_type": "control", "source_id": "index-documents",
         "source_port": "result", "target_id": _slugify(rag_name), "target_port": "query"},
    ]


def _pattern_data_analysis(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Data file → read → preprocess (code) → LLM summary for methods/results."""
    input_var = params.get("input_var", "data_path")
    return [
        {"op": "add_node", "node_type": "input", "name": "Data Input",
         "config": {"variables": [{"name": input_var, "type": "string", "default": "", "description": "Path to data file(s)"}]}},
        {"op": "add_node", "node_type": "tool_operator", "name": "Read Data",
         "config": {"tool_id": "file_read"}},
        {"op": "add_node", "node_type": "code_operator", "name": "Preprocess Data",
         "config": {"code": "import json\ntry:\n    data = json.loads(input) if isinstance(input, str) else input\nexcept Exception:\n    data = input\nresult = {'summary': str(data)[:2000], 'raw': input}", "language": "python"}},
        {"op": "add_node", "node_type": "llm_operator", "name": "Data Summary",
         "config": {"prompt_template": "Analyze the following dataset and produce a structured summary suitable for the Methods and Results sections of an academic paper.\n\nData:\n{input}\n\nProvide: (1) descriptive statistics, (2) key variables, (3) notable patterns, (4) suggested analyses.", "system_prompt": "You are a quantitative research methods expert."}},
        {"op": "add_edge", "source_id": "data-input", "source_port": input_var,
         "target_id": "read-data", "target_port": "path"},
        {"op": "add_edge", "source_id": "read-data", "source_port": "result",
         "target_id": "preprocess-data", "target_port": "input"},
        {"op": "add_edge", "source_id": "preprocess-data", "source_port": "result",
         "target_id": "data-summary", "target_port": "input"},
    ]


PATTERN_LIBRARY: dict[str, Any] = {
    "chain": _pattern_chain,
    "review_loop": _pattern_review_loop,
    "fan_out": _pattern_fan_out,
    "rag_qa": _pattern_rag_qa,
    "data_ingest": _pattern_data_ingest,
    "data_analysis": _pattern_data_analysis,
}


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

        source_ports = [p["name"] for p in source_node.get("output_ports", [])]
        resolved_source_port = op.source_port
        if resolved_source_port not in source_ports:
            if _node_is_input_like(source_node) and resolved_source_port == "input":
                # Backward-compat: older graphs may have input variables as
                # output ports but no aggregate "input" output.
                source_node.setdefault("output_ports", []).append(
                    {"name": "input", "schema": {}}
                )
                source_ports.append("input")
                msg = (
                    f"Auto-created output port 'input' on input node '{op.source_id}' "
                    "(compat for aggregate input wiring)."
                )
                if diagnostics is not None:
                    diagnostics.append(msg)
                logger.debug(
                    "Auto-created output port 'input' on input node '%s'",
                    op.source_id,
                )
            else:
                repair_message = _ensure_input_variable_output_port(
                    source_node,
                    port_name=resolved_source_port,
                    node_id=op.source_id,
                )
                if repair_message is not None:
                    source_ports = [p["name"] for p in source_node.get("output_ports", [])]
                    if diagnostics is not None:
                        diagnostics.append(repair_message)
                    logger.debug("%s", repair_message)
                else:
                    alias_map = {
                        "for_each": {"item": "results"},
                        "parallel_subagents": {"item": "results"},
                        "orchestrator": {"item": "results"},
                        "if_else": {"branch": "true"},
                    }
                    node_type = str(source_node.get("node_type") or "")
                    aliased_port = alias_map.get(node_type, {}).get(resolved_source_port)
                    if aliased_port and aliased_port in source_ports:
                        if diagnostics is not None:
                            diagnostics.append(
                                f"Normalized source port '{resolved_source_port}' to '{aliased_port}' "
                                f"for {node_type} node '{op.source_id}'."
                            )
                        logger.debug(
                            "Normalized source port '%s' to '%s' for %s node '%s'",
                            resolved_source_port,
                            aliased_port,
                            node_type,
                            op.source_id,
                        )
                        resolved_source_port = aliased_port
                    else:
                        return (
                            f"Source node '{op.source_id}' has no output port '{op.source_port}' "
                            f"(available: {source_ports})"
                        )

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
            if inject_as == "system" and "system_prompt" in node:
                existing = node.get("system_prompt", "")
                if skill_text not in existing:
                    node["system_prompt"] = f"{skill_text}\n\n{existing}" if existing else skill_text
            else:
                existing = node.get("prompt_template", "")
                if skill_text not in existing:
                    node["prompt_template"] = f"{skill_text}\n\n{existing}" if existing else skill_text

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
