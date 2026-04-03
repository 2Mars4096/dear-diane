"""Graph container — the top-level ``dan_graph_v1`` serialisation target.

Legacy compute/compatibility node models are imported from
``dan.models.legacy`` so the runtime union has one explicit compatibility
surface instead of pulling those types from both ``nodes`` and
``control_flow``.
"""

from __future__ import annotations

from typing import Annotated, Any, Union

from pydantic import BaseModel, Field

from dan.models.context import ArtifactRef, SharedContextDeclaration
from dan.models.control_flow import (
    AgentTeamNode,
    CompositeNode,
    ForEachNode,
    GateNode,
    GoalLoopNode,
    IfElseNode,
    OrchestratorNode,
    ParallelSubagentsNode,
    WhileLoopNode,
)
from dan.models.edges import ContextEdge, ControlEdge, DataEdge
from dan.models.hyperedges import Hyperedge
from dan.models.legacy import (
    CodeOperator,
    HumanInTheLoopNode,
    HumanNode,
    InputNode,
    LLMOperator,
    RAGOperator,
    ReduceNode,
    ReflectionNode,
    RouterNode,
    ToolOperator,
    ValidatorNode,
    VoteNode,
)
from dan.worker.model import Worker

# ---------------------------------------------------------------------------
# Discriminated unions — Pydantic resolves the concrete type from JSON
# using the ``node_type`` / ``edge_type`` literal field.
# ---------------------------------------------------------------------------

Node = Annotated[
    Union[
        LLMOperator,
        ToolOperator,
        CodeOperator,
        RAGOperator,
        InputNode,
        IfElseNode,
        GateNode,
        WhileLoopNode,
        ForEachNode,
        ParallelSubagentsNode,
        OrchestratorNode,
        ReduceNode,
        RouterNode,
        HumanNode,
        HumanInTheLoopNode,
        ValidatorNode,
        CompositeNode,
        AgentTeamNode,
        VoteNode,
        ReflectionNode,
        GoalLoopNode,
        Worker,
    ],
    Field(discriminator="node_type"),
]

Edge = Annotated[
    Union[DataEdge, ControlEdge, ContextEdge],
    Field(discriminator="edge_type"),
]


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


class GraphMetadata(BaseModel):
    """Human-readable metadata attached to a graph."""

    name: str = ""
    description: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    tags: list[str] = Field(default_factory=list)


class Graph(BaseModel):
    """The top-level container — serialises to the ``dan_graph_v1`` JSON contract.

    ``sub_graphs`` holds named child graphs referenced by control-flow
    and composite nodes via their ``body_graph`` field.
    """

    version: str = "dan_graph_v1"
    metadata: GraphMetadata = Field(default_factory=GraphMetadata)

    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    sub_graphs: dict[str, "Graph"] = Field(default_factory=dict)
    entry_points: list[str] = Field(default_factory=list, description="Node IDs")
    exit_points: list[str] = Field(default_factory=list, description="Node IDs")

    shared_context: list[SharedContextDeclaration] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    worker_resources: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Named shared bundles and policies resolved by Worker refs "
            "(instruction profiles, toolsets, provider policies, retry policies, …)"
        ),
    )

    hyperedges: list[Hyperedge] = Field(
        default_factory=list,
        description="Graph-level behavior modifiers (skills, guardrails, style rules, overrides)",
    )

    # ------------------------------------------------------------------
    # Convenience helpers (not part of the serialised contract)
    # ------------------------------------------------------------------

    def node_by_id(self, node_id: str) -> Node | None:
        """Look up a node by its ID, or return None."""
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def edges_from(self, node_id: str) -> list[Edge]:
        """Return all edges whose source is *node_id*."""
        return [e for e in self.edges if e.source_node_id == node_id]

    def edges_to(self, node_id: str) -> list[Edge]:
        """Return all edges whose target is *node_id*."""
        return [e for e in self.edges if e.target_node_id == node_id]
