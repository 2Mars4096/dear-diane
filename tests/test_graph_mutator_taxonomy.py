from __future__ import annotations

from typing import get_args

from dan.chat_prompts import NODE_TYPES
from dan.graph_mutator import GraphMutator, MutationPlan
from dan.models.graph import Graph
from dan.models.graph import Node
from dan.server.graph_mutator import _node_uses_body_graph


def _empty_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "test", "description": ""},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "hyperedges": [],
        "shared_context": [],
        "artifact_refs": [],
    }


def _runtime_node_types() -> set[str]:
    union_type = get_args(Node)[0]
    return {
        cls.model_fields["node_type"].default
        for cls in get_args(union_type)
    }


def test_chat_mutation_node_types_follow_documented_runtime_policy() -> None:
    # `if_else` remains a deprecated runtime alias; chat mutation should target
    # canonical runtime forms rather than reintroducing that legacy node kind.
    assert set(NODE_TYPES) == (_runtime_node_types() - {"if_else"})


def test_goal_loop_is_body_graph_capable_for_mutations() -> None:
    assert _node_uses_body_graph("goal_loop") is True


def test_graph_mutator_goal_loop_add_and_replace_body_graph_dry_run_succeeds() -> None:
    mutator = GraphMutator()
    plan = MutationPlan(
        operations=[
            {
                "op": "add_node",
                "id": "goal",
                "node_type": "goal_loop",
                "name": "Goal Loop",
                "config": {"goal_text": "Reach the target"},
            },
            {
                "op": "replace_body_graph",
                "node_id": "goal",
                "operations": [
                    {
                        "op": "add_node",
                        "id": "score_step",
                        "node_type": "llm_operator",
                        "name": "Score Step",
                        "config": {"prompt_template": "Score this draft"},
                    }
                ],
                "entry_ids": ["score_step"],
                "exit_ids": ["score_step"],
            },
        ],
        description="Add goal loop with body",
    )

    result = mutator.dry_run(_empty_graph(), plan)

    assert result.success, result.errors
    assert result.new_graph is not None
    goal_node = next(node for node in result.new_graph["nodes"] if node["id"] == "goal")
    assert goal_node["body_graph"]
    assert goal_node["body_graph"] in result.new_graph["sub_graphs"]
    Graph.model_validate(result.new_graph)


def test_new_runtime_node_defaults_are_schema_valid_for_mutations() -> None:
    mutator = GraphMutator()

    for node_type in ("human", "reflection", "vote", "agent_team"):
        plan = MutationPlan(
            operations=[
                {
                    "op": "add_node",
                    "id": f"{node_type}_1",
                    "node_type": node_type,
                    "name": node_type.replace("_", " ").title(),
                }
            ],
            description=f"Add {node_type}",
        )

        result = mutator.dry_run(_empty_graph(), plan)
        assert result.success, (node_type, result.errors)
        assert result.new_graph is not None
        Graph.model_validate(result.new_graph)
