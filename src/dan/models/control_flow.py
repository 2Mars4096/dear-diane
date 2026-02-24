"""Control-flow and composite node types.

Control-flow nodes that manage sub-graphs (WhileLoop, ForEach,
CompositeNode) carry the full composite-node contract:
  external_input/output_schema, control_state, local_state,
  read_set/write_set, compaction_rule, projections, failure_policy.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.models.context import (
    CompactionRule,
    ContextDeclaration,
    ContextProjection,
    FailurePolicy,
    MergeStrategy,
    NodeLocalState,
)
from dan.models.nodes import NodeBase


# ---------------------------------------------------------------------------
# Input node — pre-run configuration surface for workflow inputs
# ---------------------------------------------------------------------------


class InputVariable(BaseModel):
    """A single typed variable declared on an InputNode."""

    name: str
    type: Literal["string", "number", "boolean"] = "string"
    default: Any = None
    description: str = ""


class InputNode(NodeBase):
    """Visual entry point for workflow inputs.

    Each variable becomes an output port whose value is provided
    before the run starts (on the canvas UI or via the API).
    """

    node_type: Literal["input"] = "input"
    variables: list[InputVariable] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Simple control-flow (no sub-graph)
# ---------------------------------------------------------------------------


class IfElseNode(NodeBase):
    """Routes data to one of two branches based on a condition.

    The condition is an expression evaluated against upstream data.
    The node produces output on exactly one of two branches — by
    convention the output ports are named ``true`` and ``false``.
    """

    node_type: Literal["if_else"] = "if_else"
    condition: str = Field(description="Expression evaluated on the incoming data")


class ReduceNode(NodeBase):
    """Aggregates results from parallel fan-out branches."""

    node_type: Literal["reduce"] = "reduce"
    reducer: str = Field(
        description="Expression or function name that combines branch outputs"
    )


class RouterNode(NodeBase):
    """LLM-powered dynamic routing.

    The model reads the incoming data and decides which named route to
    activate.  ``route_descriptions`` maps route names to natural-language
    descriptions the LLM uses to choose.
    """

    node_type: Literal["router"] = "router"
    model: str
    route_descriptions: dict[str, str] = Field(
        default_factory=dict,
        description="route_name → natural-language description",
    )


class HumanInTheLoopNode(NodeBase):
    """Pauses execution and waits for human input before resuming."""

    node_type: Literal["human_in_the_loop"] = "human_in_the_loop"
    prompt: str = ""
    timeout_seconds: float | None = None
    default_action: str | None = None


# ---------------------------------------------------------------------------
# Sub-graph-bearing control-flow — full composite-node contract
# ---------------------------------------------------------------------------


class WhileLoopNode(NodeBase):
    """Repeats a sub-graph until a condition is met or limits are hit.

    ``body_graph`` is the name of a sub-graph stored in the parent
    Graph's ``sub_graphs`` dict.
    """

    node_type: Literal["while_loop"] = "while_loop"

    condition: str = Field(description="Expression re-evaluated after each iteration")
    body_graph: str = Field(description="Key into Graph.sub_graphs")
    max_iterations: int = 10

    # Composite-node contract
    external_input_schema: dict[str, Any] | None = None
    external_output_schema: dict[str, Any] | None = None
    control_state_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for iteration count, stop flags, thresholds",
    )
    local_state: NodeLocalState = Field(default_factory=NodeLocalState)
    read_set: list[ContextDeclaration] = Field(default_factory=list)
    write_set: list[ContextDeclaration] = Field(default_factory=list)
    compaction_rule: CompactionRule | None = None
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    projections: list[ContextProjection] = Field(default_factory=list)


class ForEachNode(NodeBase):
    """Fans out a sub-graph over each item in a list, optionally in parallel.

    ``merge_strategy`` controls how branch results are reconciled.
    """

    node_type: Literal["for_each"] = "for_each"

    body_graph: str = Field(description="Key into Graph.sub_graphs")
    parallelism: int = Field(default=1, ge=1, description="Max concurrent branches")
    merge_strategy: MergeStrategy = MergeStrategy.APPEND

    # Composite-node contract
    external_input_schema: dict[str, Any] | None = None
    external_output_schema: dict[str, Any] | None = None
    control_state_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for branch count, completion tracking",
    )
    local_state: NodeLocalState = Field(default_factory=NodeLocalState)
    read_set: list[ContextDeclaration] = Field(default_factory=list)
    write_set: list[ContextDeclaration] = Field(default_factory=list)
    compaction_rule: CompactionRule | None = None
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    projections: list[ContextProjection] = Field(default_factory=list)


class CompositeNode(NodeBase):
    """Wraps a named sub-graph as a single reusable block.

    Outer ports map to inner entry/exit ports through ``input_mappings``
    and ``output_mappings``.
    """

    node_type: Literal["composite"] = "composite"

    body_graph: str = Field(description="Key into Graph.sub_graphs")
    is_blackbox: bool = Field(
        default=False,
        description="When true, node is opaque — no drill-in or sub-graph preview (used for marketplace/imported blocks)",
    )
    input_mappings: dict[str, str] = Field(
        default_factory=dict,
        description="outer_port_name → inner_entry_port_name",
    )
    output_mappings: dict[str, str] = Field(
        default_factory=dict,
        description="inner_exit_port_name → outer_port_name",
    )

    # Composite-node contract
    external_input_schema: dict[str, Any] | None = None
    external_output_schema: dict[str, Any] | None = None
    control_state_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for internal control state",
    )
    local_state: NodeLocalState = Field(default_factory=NodeLocalState)
    read_set: list[ContextDeclaration] = Field(default_factory=list)
    write_set: list[ContextDeclaration] = Field(default_factory=list)
    compaction_rule: CompactionRule | None = None
    projections: list[ContextProjection] = Field(default_factory=list)
