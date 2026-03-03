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
    BoundaryContract,
    CompactionRule,
    ContextDeclaration,
    ContextProjection,
    FailurePolicy,
    MergeStrategy,
    NodeLocalState,
)
from dan.models.nodes import NodeBase
from dan.models.ports import OutputPort


def _normalize_state_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Accept both full JSON Schema and flat key→schema map; always return flat."""
    if schema is None:
        return None
    if schema.get("type") == "object" and "properties" in schema:
        return schema["properties"]
    return schema


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


class GateNode(NodeBase):
    """Conditional routing gate with branch-specific output ports.

    In if_else mode: evaluates condition, writes all inputs to exactly one
    branch output port (true or false). No cycles.

    In while mode: evaluates condition each iteration. Writes to 'continue'
    port (back-edge to loop start) or 'done' port (forward exit).
    The scheduler handles iteration — this executor runs ONCE per iteration.
    """

    node_type: Literal["gate"] = "gate"
    condition: str = Field(description="Expression evaluated on inputs")
    gate_mode: Literal["if_else", "while"] = Field(
        default="if_else",
        description="if_else: forward-only branching. while: loop gate with back-edges",
    )
    max_iterations: int = Field(
        default=10,
        ge=1,
        description="Max loop iterations (while mode only)",
    )
    state_schema: dict[str, Any] | None = None
    state_defaults: dict[str, Any] | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.output_ports:
            if self.gate_mode == "if_else":
                self.output_ports = [
                    OutputPort(name="true", description="Active when condition is true"),
                    OutputPort(name="false", description="Active when condition is false"),
                ]
            else:
                self.output_ports = [
                    OutputPort(name="continue", description="Loop back — condition still true"),
                    OutputPort(name="done", description="Exit loop — condition is false"),
                ]
        self.state_schema = _normalize_state_schema(self.state_schema)


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
# Validation / handoff node
# ---------------------------------------------------------------------------


class ValidationRule(BaseModel):
    """A single validation rule applied by a ValidatorNode."""

    rule_type: Literal[
        "required_keys",
        "non_empty",
        "schema_conformance",
        "type_check",
        "custom_expression",
    ]
    config: dict[str, Any] = Field(default_factory=dict)


class ValidatorNode(NodeBase):
    """Checks data at agent boundaries and routes to valid/invalid ports.

    Visible on the canvas — not hidden middleware.  Rule evaluation is
    sequential; ``strict_mode`` stops at the first violation.
    """

    node_type: Literal["validator"] = "validator"
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    on_failure: Literal["route", "warn", "halt"] = "route"
    strict_mode: bool = False

    def model_post_init(self, __context: Any) -> None:
        if not self.output_ports:
            self.output_ports = [
                OutputPort(name="valid", description="Passthrough when all rules pass"),
                OutputPort(name="invalid", description="Data + errors when any rule fails"),
            ]


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
    state_schema: dict[str, Any] | None = None
    state_defaults: dict[str, Any] | None = None

    def model_post_init(self, __context: Any) -> None:
        self.state_schema = _normalize_state_schema(self.state_schema)

    # Composite-node contract
    external_input_schema: dict[str, Any] | None = None
    external_output_schema: dict[str, Any] | None = None
    control_state_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for iteration count, stop flags, thresholds",
    )
    local_state: NodeLocalState = Field(default_factory=NodeLocalState)
    # Override NodeBase defaults; same semantics for composite/loop context contract
    read_set: list[ContextDeclaration] = Field(default_factory=list)
    write_set: list[ContextDeclaration] = Field(default_factory=list)
    compaction_rule: CompactionRule | None = None
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    projections: list[ContextProjection] = Field(default_factory=list)
    boundary_contract: BoundaryContract | None = Field(
        default=None, description="Formal boundary contract (Plan 14-2)",
    )


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
    # Override NodeBase; same semantics for composite/loop context contract
    read_set: list[ContextDeclaration] = Field(default_factory=list)
    write_set: list[ContextDeclaration] = Field(default_factory=list)
    compaction_rule: CompactionRule | None = None
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    projections: list[ContextProjection] = Field(default_factory=list)
    boundary_contract: BoundaryContract | None = Field(
        default=None, description="Formal boundary contract (Plan 14-2)",
    )


class ParallelSubagentsNode(NodeBase):
    """Runs multiple sub-graphs concurrently; results merge at fan-in.

    Each branch is a key into ``Graph.sub_graphs``. All branches run
    asynchronously via asyncio.gather; merge_strategy controls fan-in.
    """

    node_type: Literal["parallel_subagents"] = "parallel_subagents"

    branch_graphs: list[str] = Field(
        description="Keys into Graph.sub_graphs; each runs concurrently",
    )
    parallelism: int = Field(default=1, ge=1, description="Max concurrent branches")
    merge_strategy: MergeStrategy = MergeStrategy.APPEND
    reducer: str | None = Field(
        default=None,
        description="Expression over {'inputs': branch_outputs} when merge_strategy=REDUCER",
    )

    input_mappings: dict[str, str] = Field(
        default_factory=dict,
        description="outer_port_name → inner_entry_port_name (shared input to all branches)",
    )
    branch_inputs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="branch_key → {port: value} per-branch overrides",
    )

    # Composite-node contract
    external_input_schema: dict[str, Any] | None = None
    external_output_schema: dict[str, Any] | None = None
    control_state_schema: dict[str, Any] = Field(default_factory=dict)
    local_state: NodeLocalState = Field(default_factory=NodeLocalState)
    read_set: list[ContextDeclaration] = Field(default_factory=list)
    write_set: list[ContextDeclaration] = Field(default_factory=list)
    compaction_rule: CompactionRule | None = None
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    projections: list[ContextProjection] = Field(default_factory=list)
    boundary_contract: BoundaryContract | None = Field(
        default=None, description="Formal boundary contract (Plan 14-2)",
    )


class OrchestratorNode(NodeBase):
    """Async runtime orchestrator — runs concurrently with subgraph teams.

    Unlike ParallelSubagentsNode (fire-and-forget fan-out), the orchestrator
    actively monitors events and can send inputs to teams mid-execution.
    """

    node_type: Literal["orchestrator"] = "orchestrator"

    teams: dict[str, str] = Field(
        default_factory=dict,
        description="Maps team name to sub_graph key; each team runs as a concurrent subgraph",
    )

    orchestrator_prompt: str = Field(
        default="",
        description="System prompt for the LLM orchestrator making routing decisions",
    )
    orchestrator_model: str | None = Field(
        default=None,
        description="LLM model for orchestrator decisions (uses engine default if None)",
    )

    completion_condition: Literal["all_done", "any_done", "orchestrator_halt"] = Field(
        default="all_done",
        description="When to stop: all teams done, any team done, or orchestrator decides",
    )
    max_iterations: int = Field(default=100, ge=1, description="Safety bound on orchestrator loop iterations")
    timeout_seconds: float | None = Field(default=None, description="Overall timeout")

    input_mappings: dict[str, str] = Field(
        default_factory=dict,
        description="outer_port_name → inner_entry_port_name (shared input to all teams)",
    )
    team_inputs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="team_name → {port: value} per-team overrides",
    )

    # Composite-node contract
    external_input_schema: dict[str, Any] | None = None
    external_output_schema: dict[str, Any] | None = None
    control_state_schema: dict[str, Any] = Field(default_factory=dict)
    local_state: NodeLocalState = Field(default_factory=NodeLocalState)
    read_set: list[ContextDeclaration] = Field(default_factory=list)
    write_set: list[ContextDeclaration] = Field(default_factory=list)
    compaction_rule: CompactionRule | None = None
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    projections: list[ContextProjection] = Field(default_factory=list)
    boundary_contract: BoundaryContract | None = Field(
        default=None, description="Formal boundary contract (Plan 14-2)",
    )


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
    boundary_contract: BoundaryContract | None = Field(
        default=None,
        description="Formal boundary contract for context scoping (Plan 14-2)",
    )
