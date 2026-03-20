from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from dan.engine.scheduler import _NODE_SLOT_BYPASS_TYPES
from dan.models.control_flow import (
    AgentTeamNode,
    GoalLoopNode,
    OrchestratorNode,
    ParallelSubagentsNode,
)
from dan.models.graph import Graph, Node
from dan.models.node_taxonomy import (
    GENERATE_SPEC_NODE_TYPES,
    MARKDOWN_DECOMPILER_SUPPORTED_NODE_TYPES,
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
