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
    FeedbackSelector,
    MergeStrategy,
    NodeLocalState,
)
from dan.models.legacy import (
    HumanInTheLoopNode,
    HumanNode,
    InputNode,
    InputVariable,
    ReduceNode,
    RouterNode,
    ValidationRule,
    ValidatorNode,
    VoteConfig,
    VoteNode,
)
from dan.models.nodes import NodeBase
from dan.models.ports import InputPort, OutputPort


def _normalize_state_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Accept both full JSON Schema and flat key→schema map; always return flat."""
    if schema is None:
        return None
    if schema.get("type") == "object" and "properties" in schema:
        return schema["properties"]
    return schema


class SpawnPolicy(BaseModel):
    """Limits and budget metadata for runtime child workflow expansion."""

    max_child_depth: int | None = Field(default=4, ge=1)
    max_spawns_per_node: int | None = Field(default=8, ge=1)
    max_total_children: int | None = Field(default=32, ge=1)
    timeout_seconds: float | None = None
    budget_share: float | None = Field(default=None, ge=0.0)


class DynamicExpansionSpec(BaseModel):
    """Validated runtime expansion request owned by the engine."""

    mode: Literal["sub_graph", "template_branch", "workflow_ref"] = "sub_graph"
    ref: str
    boundary_contract: BoundaryContract | None = None
    spawn_policy: SpawnPolicy = Field(default_factory=SpawnPolicy)


class ChildWorkflowCall(BaseModel):
    """Typed child-workflow invocation descriptor."""

    spec: DynamicExpansionSpec
    inputs: dict[str, Any] = Field(default_factory=dict)
    parent_node_id: str
    call_id: str
    source: Literal["engine", "runtime_repair", "user", "concierge"] = "engine"


class ChildResultEnvelope(BaseModel):
    """Typed child result returned to the parent workflow."""

    status: Literal["completed", "failed", "partial"] = "completed"
    outputs: dict[str, Any] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    run_id: str = ""
    parent_run_id: str = ""
    parent_node_id: str = ""
    template_key: str = ""
    layer_path: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


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
    # -- 16-4: Feedback selector for while-mode loops --------------------------
    feedback_selector: FeedbackSelector | None = Field(
        default=None,
        description="Filters which body outputs feed back to the next iteration (while mode only)",
    )
    artifact_ports: list[str] | None = Field(
        default=None,
        description="Ports excluded from feedback and accumulated as side-effect artifacts",
    )

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
    # -- 16-4: Feedback selector for while loops --------------------------------
    feedback_selector: FeedbackSelector | None = Field(
        default=None,
        description="Filters which body outputs feed back to the next iteration",
    )
    artifact_ports: list[str] | None = Field(
        default=None,
        description="Ports excluded from feedback and accumulated as side-effect artifacts",
    )
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
    model_policy: Any | None = Field(
        default=None,
        description="Policy-driven model selection (ModelPolicy from dan.providers.model_policy)",
    )
    # -- 18-5: Task-level model tiering ----------------------------------------
    task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = Field(
        default=None,
        description="Explicit task tier override. Bypasses automatic scoring.",
    )

    completion_condition: Literal["all_done", "any_done", "orchestrator_halt"] = Field(
        default="all_done",
        description="When to stop: all teams done, any team done, or orchestrator decides",
    )
    max_iterations: int = Field(default=100, ge=1, description="Safety bound on orchestrator loop iterations")
    # -- 16-5: Async loop design -----------------------------------------------
    max_llm_calls: int = Field(default=50, ge=1, description="Safety bound on orchestrator LLM invocations")
    timeout_seconds: float | None = Field(default=None, description="Overall timeout")

    input_mappings: dict[str, str] = Field(
        default_factory=dict,
        description="outer_port_name → inner_entry_port_name (shared input to all teams)",
    )
    team_inputs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="team_name → {port: value} per-team overrides",
    )
    team_expansions: dict[str, DynamicExpansionSpec] = Field(
        default_factory=dict,
        description=(
            "Optional team_name → validated child-workflow expansion. "
            "When present, the team dispatches through the engine-owned "
            "child workflow primitive instead of a plain in-graph subgraph call."
        ),
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


# ---------------------------------------------------------------------------
# 16-1: Agent team — group-chat style multi-agent coordination
# ---------------------------------------------------------------------------


class TeamMessage(BaseModel):
    """A single message in a team conversation."""

    sender: str
    recipients: list[str] = Field(default_factory=lambda: ["all"])
    content: str
    message_type: Literal["message", "handoff", "result", "question"] = "message"
    metadata: dict[str, Any] = Field(default_factory=dict)
    turn_number: int = 0


class HandoffRequest(BaseModel):
    """Structured work-transfer between team agents."""

    source_agent: str
    target_agent: str
    reason: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    handoff_type: Literal["transfer", "consult"] = "transfer"


class TeamConversation(BaseModel):
    """Accumulated conversation state for an agent team execution."""

    messages: list[TeamMessage] = Field(default_factory=list)
    active_agent: str | None = None
    turn_count: int = 0
    handoff_log: list[HandoffRequest] = Field(default_factory=list)


class AgentTeamNode(NodeBase):
    """Group-chat style multi-agent coordination.

    Agents within a team can address each other via ``@``-routing, hand off
    work, and share a conversational context that accumulates across turns.
    """

    node_type: Literal["agent_team"] = "agent_team"

    agents: dict[str, str] = Field(
        default_factory=dict,
        description="Maps agent name → sub_graph key; each agent runs as a sub-graph",
    )
    moderator_prompt: str = Field(
        default="",
        description="System prompt for the moderator LLM that manages turn order",
    )
    moderator_model: str | None = Field(
        default=None,
        description="LLM model for moderator turn decisions (uses engine default if None)",
    )
    model_policy: Any | None = Field(
        default=None,
        description="Policy-driven model selection",
    )
    # -- 18-5: Task-level model tiering ----------------------------------------
    task_tier: Literal["micro", "routine", "reasoning", "critical"] | None = Field(
        default=None,
        description="Explicit task tier override. Bypasses automatic scoring.",
    )
    turn_strategy: Literal["round_robin", "moderator", "free_form", "sequential"] = Field(
        default="round_robin",
        description="How turns are assigned among agents",
    )
    max_turns: int = Field(default=20, ge=1, description="Safety bound on conversation turns")
    completion_condition: Literal["consensus", "moderator_halt", "max_turns", "all_responded"] = Field(
        default="max_turns",
        description="When the team conversation ends",
    )
    timeout_seconds: float | None = None
    shared_context_keys: list[str] = Field(
        default_factory=list,
        description="Context keys visible to all team agents",
    )
    handoff_policy: Literal["explicit", "any", "moderator_only"] = Field(
        default="explicit",
        description="Who can initiate handoffs between agents",
    )

    input_mappings: dict[str, str] = Field(
        default_factory=dict,
        description="outer_port → inner_entry_port (shared input to all agents)",
    )
    agent_inputs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="agent_name → {port: value} per-agent overrides",
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
    boundary_contract: BoundaryContract | None = None


class GoalLoopNode(NodeBase):
    """Iterates a body sub-graph until a goal metric is satisfied or limits hit.

    Reuses the ``GoalSpec`` contract from the concierge goal loop.
    ``body_graph`` points to a sub-graph in ``Graph.sub_graphs``.
    """

    node_type: Literal["goal_loop"] = "goal_loop"

    goal_text: str = Field(description="Natural language description of the goal")
    metric_name: str = Field(default="score", description="Metric key to evaluate against")
    target_value: float = Field(default=1.0, description="Target threshold")
    comparison: Literal[">=", "<=", "==", ">", "<"] = ">="
    max_iterations: int = Field(default=10, ge=1)
    evaluator: str = Field(
        default="llm_judge",
        description="Evaluation mode: llm_judge | script | test_suite | custom",
    )
    success_criteria: str | None = Field(
        default=None,
        description="Optional expression evaluated on iteration output to check success",
    )
    body_graph: str = Field(description="Key into Graph.sub_graphs")

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
