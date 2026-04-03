from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import dan.models.control_flow as control_flow_models
import dan.models.legacy as legacy_models
import dan.models.nodes as node_models
from dan.engine.scheduler import _NODE_SLOT_BYPASS_TYPES
from dan.models.control_flow import (
    AgentTeamNode,
    GoalLoopNode,
    OrchestratorNode,
    ParallelSubagentsNode,
)
from dan.models.graph import Graph, Node
from dan.models.node_taxonomy import (
    CANONICAL_AUTHORING_NODE_TYPES,
    CANONICAL_COMPUTE_NODE_TYPES,
    GENERATE_SPEC_NODE_TYPES,
    LEGACY_COMPATIBILITY_NODE_TYPES,
    MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES,
    RETAINED_RUNTIME_NODE_TYPES,
    RUNTIME_NODE_TYPES,
    SUBGRAPH_BEARING_RUNTIME_NODE_TYPES,
)
from dan.registry import NodeTypeRegistry
from dan.validation.graph import (
    _COMPOSITE_NODE_TYPES,
    _KNOWN_NODE_TYPES,
    _check_sub_graph_refs,
)


def _runtime_node_types() -> set[str]:
    union_type = get_args(Node)[0]
    return {
        cls.model_fields["node_type"].default
        for cls in get_args(union_type)
    }


def test_node_type_registry_covers_graph_runtime_union() -> None:
    assert _runtime_node_types() <= set(NodeTypeRegistry.all_types())


def test_node_type_registry_matches_graph_runtime_union_exactly() -> None:
    assert set(NodeTypeRegistry.all_types()) == _runtime_node_types()


def test_validation_known_node_types_cover_graph_runtime_union() -> None:
    assert _runtime_node_types() <= _KNOWN_NODE_TYPES


def test_validation_known_node_types_match_graph_runtime_union_exactly() -> None:
    assert _KNOWN_NODE_TYPES == _runtime_node_types()


def test_validation_composite_node_types_match_runtime_subgraph_bearers() -> None:
    expected = {
        "worker",
        "composite",
        "while_loop",
        "for_each",
        "parallel_subagents",
        "orchestrator",
        "agent_team",
        "goal_loop",
    }
    assert SUBGRAPH_BEARING_RUNTIME_NODE_TYPES == expected
    assert _COMPOSITE_NODE_TYPES == SUBGRAPH_BEARING_RUNTIME_NODE_TYPES


def test_sub_graph_ref_validation_covers_all_runtime_subgraph_primitives() -> None:
    graph = Graph(
        nodes=[
            GoalLoopNode(
                id="goal",
                name="Goal Loop",
                goal_text="Reach target",
                body_graph="missing_goal_body",
            ),
            ParallelSubagentsNode(
                id="parallel",
                name="Parallel",
                branch_graphs=["missing_branch"],
            ),
            OrchestratorNode(
                id="orch",
                name="Orchestrator",
                teams={"team_a": "missing_team_body"},
            ),
            AgentTeamNode(
                id="team",
                name="Agent Team",
                agents={"writer": "missing_agent_body"},
            ),
        ],
        edges=[],
        sub_graphs={},
        entry_points=[],
        exit_points=[],
    )

    errors = _check_sub_graph_refs(graph)

    assert any("goal" in err and "missing_goal_body" in err for err in errors)
    assert any("parallel" in err and "missing_branch" in err for err in errors)
    assert any("orch" in err and "missing_team_body" in err for err in errors)
    assert any("team" in err and "missing_agent_body" in err for err in errors)


def test_scheduler_bypass_types_use_canonical_rag_operator_name() -> None:
    assert "rag_operator" in _NODE_SLOT_BYPASS_TYPES
    assert "rag" not in _NODE_SLOT_BYPASS_TYPES


def test_editor_dannode_literals_match_graph_runtime_union() -> None:
    graph_ts = (
        Path(__file__).resolve().parents[1]
        / "editor"
        / "src"
        / "types"
        / "graph.ts"
    )
    source = graph_ts.read_text(encoding="utf-8")
    ts_node_types = set(re.findall(r'node_type:\s*"([^"]+)"', source))

    assert ts_node_types == _runtime_node_types()


def test_markdown_decompiler_supported_types_are_runtime_types() -> None:
    assert MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES <= _runtime_node_types()


def test_generate_spec_node_types_are_runtime_types() -> None:
    assert set(GENERATE_SPEC_NODE_TYPES) <= RUNTIME_NODE_TYPES


def test_runtime_taxonomy_explicitly_partitions_canonical_retained_and_legacy() -> None:
    assert CANONICAL_COMPUTE_NODE_TYPES == {"worker"}
    assert CANONICAL_COMPUTE_NODE_TYPES.isdisjoint(RETAINED_RUNTIME_NODE_TYPES)
    assert CANONICAL_COMPUTE_NODE_TYPES.isdisjoint(LEGACY_COMPATIBILITY_NODE_TYPES)
    assert RETAINED_RUNTIME_NODE_TYPES.isdisjoint(LEGACY_COMPATIBILITY_NODE_TYPES)
    assert (
        CANONICAL_COMPUTE_NODE_TYPES
        | RETAINED_RUNTIME_NODE_TYPES
        | LEGACY_COMPATIBILITY_NODE_TYPES
    ) == RUNTIME_NODE_TYPES


def test_legacy_module_reexports_existing_runtime_class_objects() -> None:
    assert legacy_models.LLMOperator is node_models.LLMOperator
    assert legacy_models.ToolOperator is node_models.ToolOperator
    assert legacy_models.CodeOperator is node_models.CodeOperator
    assert legacy_models.RAGOperator is node_models.RAGOperator
    assert legacy_models.ReflectionNode is node_models.ReflectionNode
    assert legacy_models.InputNode is control_flow_models.InputNode
    assert legacy_models.RouterNode is control_flow_models.RouterNode
    assert legacy_models.ValidatorNode is control_flow_models.ValidatorNode
    assert legacy_models.HumanNode is control_flow_models.HumanNode
    assert legacy_models.HumanInTheLoopNode is control_flow_models.HumanInTheLoopNode
    assert legacy_models.VoteNode is control_flow_models.VoteNode
    assert legacy_models.ReduceNode is control_flow_models.ReduceNode


def test_graph_runtime_union_accepts_legacy_module_exports() -> None:
    union_type = get_args(Node)[0]
    runtime_classes = set(get_args(union_type))
    for legacy_class in (
        legacy_models.LLMOperator,
        legacy_models.ToolOperator,
        legacy_models.CodeOperator,
        legacy_models.RAGOperator,
        legacy_models.InputNode,
        legacy_models.ReduceNode,
        legacy_models.RouterNode,
        legacy_models.HumanNode,
        legacy_models.ValidatorNode,
        legacy_models.VoteNode,
        legacy_models.ReflectionNode,
    ):
        assert legacy_class in runtime_classes


def test_canonical_authoring_node_types_follow_worker_plus_retained_runtime_policy() -> None:
    expected = tuple(
        node_type
        for node_type in (
            "worker",
            "gate",
            "while_loop",
            "for_each",
            "parallel_subagents",
            "orchestrator",
            "composite",
            "agent_team",
            "goal_loop",
        )
    )
    assert CANONICAL_AUTHORING_NODE_TYPES == expected
    assert set(CANONICAL_AUTHORING_NODE_TYPES) == (
        CANONICAL_COMPUTE_NODE_TYPES | RETAINED_RUNTIME_NODE_TYPES
    )
    assert "if_else" not in CANONICAL_AUTHORING_NODE_TYPES
    assert "human_in_the_loop" not in CANONICAL_AUTHORING_NODE_TYPES
