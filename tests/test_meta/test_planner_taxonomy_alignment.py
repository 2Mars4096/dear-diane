from __future__ import annotations

import pytest

from dan.meta.intent_compiler import IntentCompiler
from dan.meta.intent_schema import StageIntent, StageType, WorkflowIntent
from dan.meta.planner import (
    PlanningPromptBuilder,
    WorkflowPlanner,
)
from dan.models.node_taxonomy import BODY_GRAPH_RUNTIME_NODE_TYPES, GENERATE_SPEC_NODE_TYPES
from dan.server.graph_mutator import _node_uses_body_graph


def test_planning_prompt_builder_uses_honest_mutation_and_generate_contract() -> None:
    template = PlanningPromptBuilder().build_system_prompt()

    assert (
        'Each node: {"node_type": "llm_operator"|"tool_operator"|"code_operator"|"gate", "name": "...", "config": {...}}'
        in template
    )
    assert '{"op": "remove_edge", "source_id": "...", "source_port": "...", "target_id": "...", "target_port": "..."}' in template
    assert '"tool_id": "file_read"' in template
    assert '"tool_id": "index_documents"' not in template
    assert "For branching/control-flow edges, always specify explicit ports" in template


def test_generate_spec_node_types_constant_matches_prompt_contract() -> None:
    assert GENERATE_SPEC_NODE_TYPES == (
        "llm_operator",
        "tool_operator",
        "code_operator",
        "gate",
    )


def test_graph_mutator_body_graph_policy_uses_taxonomy_constant() -> None:
    expected = {"while_loop", "for_each", "composite", "goal_loop"}
    assert BODY_GRAPH_RUNTIME_NODE_TYPES == expected
    for node_type in expected:
        assert _node_uses_body_graph(node_type) is True


def test_compile_generate_spec_uses_runtime_default_ports_for_linear_edges() -> None:
    graph = WorkflowPlanner._compile_generate_spec({
        "name": "linear",
        "nodes": [
            {"node_type": "llm_operator", "name": "draft", "config": {"prompt": "Draft"}},
            {"node_type": "llm_operator", "name": "review", "config": {"prompt": "Review"}},
        ],
        "edges": [
            {"source": "draft", "target": "review"},
        ],
    })

    assert graph["edges"] == [
        {
            "id": "e1",
            "edge_type": "data",
            "source_node_id": "draft_1",
            "source_port": "text",
            "target_node_id": "review_2",
            "target_port": "input",
        }
    ]


def test_compile_generate_spec_requires_explicit_gate_source_ports() -> None:
    with pytest.raises(ValueError, match="must specify source_port explicitly"):
        WorkflowPlanner._compile_generate_spec({
            "name": "branch",
            "nodes": [
                {
                    "node_type": "gate",
                    "name": "route",
                    "config": {"condition": "True"},
                },
                {
                    "node_type": "llm_operator",
                    "name": "then_step",
                    "config": {"prompt": "Handle true branch"},
                },
            ],
            "edges": [
                {"source": "route", "target": "then_step"},
            ],
        })


def test_compile_generate_spec_accepts_tool_name_alias_as_tool_id() -> None:
    graph = WorkflowPlanner._compile_generate_spec({
        "name": "alias",
        "nodes": [
            {
                "node_type": "tool_operator",
                "name": "fetch",
                "config": {"tool_name": "file_read"},
            },
        ],
        "edges": [],
    })

    assert graph["nodes"][0]["tool_id"] == "file_read"


def test_unknown_tool_call_stage_falls_back_to_llm_node() -> None:
    intent = WorkflowIntent(
        goal="Investigate an anomaly",
        stages=[
            StageIntent(
                name="Investigate",
                description="Investigate the anomaly deeply",
                stage_type=StageType.tool_call,
            ),
        ],
    )

    graph = IntentCompiler().build_graph(intent)

    assert len(graph.nodes) == 1
    assert graph.nodes[0].node_type == "llm_operator"
