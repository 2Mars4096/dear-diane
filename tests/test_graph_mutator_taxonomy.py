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


def test_graph_mutator_worker_builder_gate_workerizes_simple_compute_nodes(monkeypatch) -> None:
    monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")
    mutator = GraphMutator()
    plan = MutationPlan(
        operations=[
            {
                "op": "add_node",
                "id": "draft",
                "node_type": "llm_operator",
                "name": "Draft",
                "config": {
                    "model": "test-model",
                    "prompt_template": "Write a draft",
                },
            },
            {
                "op": "add_node",
                "id": "fetch",
                "node_type": "tool_operator",
                "name": "Fetch",
                "config": {
                    "tool_id": "pdf_read",
                    "tool_config": {"path": "/tmp/doc.pdf"},
                },
            },
            {
                "op": "add_node",
                "id": "script",
                "node_type": "code_operator",
                "name": "Script",
                "config": {
                    "code": "result = 1",
                },
            },
        ],
        description="Workerize simple compute mutation nodes",
    )

    result = mutator.dry_run(_empty_graph(), plan)

    assert result.success, result.errors
    assert result.new_graph is not None
    Graph.model_validate(result.new_graph)
    assert result.new_graph.get("sub_graphs", {}) == {}

    nodes = {node["id"]: node for node in result.new_graph["nodes"]}

    assert nodes["draft"]["node_type"] == "worker"
    assert nodes["draft"]["llm_hints"]["prompt_template"] == "Write a draft"
    assert [port["name"] for port in nodes["draft"]["output_ports"]] == ["text"]

    assert nodes["fetch"]["node_type"] == "worker"
    assert nodes["fetch"]["tool_ids"] == ["pdf_read"]
    assert nodes["fetch"]["metadata"]["tool_config"] == {"path": "/tmp/doc.pdf"}
    assert [port["name"] for port in nodes["fetch"]["output_ports"]] == ["text", "result"]

    assert nodes["script"]["node_type"] == "worker"
    assert nodes["script"]["code"] == "result = 1"
    assert [port["name"] for port in nodes["script"]["output_ports"]] == ["result"]


def test_graph_mutator_worker_builder_gate_workerizes_extended_compute_aliases(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")
    mutator = GraphMutator()
    plan = MutationPlan(
        operations=[
            {
                "op": "add_node",
                "id": "entry",
                "node_type": "input",
                "name": "Entry",
                "config": {
                    "variables": [
                        {"name": "topic", "type": "string", "default": "agents"},
                    ]
                },
            },
            {
                "op": "add_node",
                "id": "retrieve",
                "node_type": "rag_operator",
                "name": "Retrieve",
                "config": {
                    "collection": "docs",
                    "top_k": 3,
                    "query_template": "{query}",
                },
            },
            {
                "op": "add_node",
                "id": "route",
                "node_type": "router",
                "name": "Route",
                "config": {
                    "model": "test-model",
                    "route_descriptions": {
                        "research": "Do research",
                        "draft": "Draft the answer",
                    },
                },
            },
            {
                "op": "add_node",
                "id": "reflect",
                "node_type": "reflection",
                "name": "Reflect",
                "config": {
                    "reflection_prompt": "Extract principles",
                    "max_principles": 5,
                },
            },
            {
                "op": "add_node",
                "id": "validate",
                "node_type": "validator",
                "name": "Validate",
                "config": {
                    "validation_rules": [
                        {"rule_type": "required_keys", "config": {"keys": ["summary"]}},
                    ],
                    "on_failure": "halt",
                    "strict_mode": True,
                },
            },
            {
                "op": "add_node",
                "id": "approve",
                "node_type": "human",
                "name": "Approve",
                "config": {
                    "prompt": "Approve this draft",
                    "render_mode": "approval",
                },
            },
            {
                "op": "add_node",
                "id": "vote",
                "node_type": "vote",
                "name": "Vote",
                "config": {
                    "candidates": ["a", "b"],
                    "prompt_template": "Pick one",
                },
            },
            {
                "op": "add_node",
                "id": "merge",
                "node_type": "reduce",
                "name": "Merge",
                "config": {
                    "reducer": "append",
                },
            },
        ],
        description="Workerize extended compute mutation nodes",
    )

    result = mutator.dry_run(_empty_graph(), plan)

    assert result.success, result.errors
    assert result.new_graph is not None
    Graph.model_validate(result.new_graph)

    nodes = {node["id"]: node for node in result.new_graph["nodes"]}

    assert nodes["entry"]["node_type"] == "worker"
    assert nodes["entry"]["metadata"]["input_variables"][0]["name"] == "topic"
    assert [port["name"] for port in nodes["entry"]["output_ports"]] == ["input", "topic"]

    assert nodes["retrieve"]["node_type"] == "worker"
    assert nodes["retrieve"]["role"] == "rag"
    assert nodes["retrieve"]["metadata"]["rag_collection"] == "docs"
    assert [port["name"] for port in nodes["retrieve"]["input_ports"]] == ["query"]
    assert [port["name"] for port in nodes["retrieve"]["output_ports"]] == ["chunks", "scores"]

    assert nodes["route"]["node_type"] == "worker"
    assert nodes["route"]["role"] == "router"
    assert nodes["route"]["model"] == "test-model"
    assert nodes["route"]["metadata"]["route_descriptions"] == {
        "research": "Do research",
        "draft": "Draft the answer",
    }
    assert [port["name"] for port in nodes["route"]["input_ports"]] == ["input"]
    assert [port["name"] for port in nodes["route"]["output_ports"]] == ["route"]

    assert nodes["reflect"]["node_type"] == "worker"
    assert nodes["reflect"]["role"] == "reflection"
    assert nodes["reflect"]["metadata"]["reflection_prompt"] == "Extract principles"
    assert nodes["reflect"]["metadata"]["reflection_max_principles"] == 5

    assert nodes["validate"]["node_type"] == "worker"
    assert nodes["validate"]["role"] == "validator"
    assert nodes["validate"]["validation_rules"] == [
        {"rule_type": "required_keys", "config": {"keys": ["summary"]}},
    ]
    assert nodes["validate"]["metadata"]["validator_on_failure"] == "halt"
    assert nodes["validate"]["metadata"]["validator_strict_mode"] is True
    assert [port["name"] for port in nodes["validate"]["input_ports"]] == ["data"]
    assert [port["name"] for port in nodes["validate"]["output_ports"]] == ["valid", "invalid"]

    assert nodes["approve"]["node_type"] == "worker"
    assert nodes["approve"]["role"] == "human"
    assert nodes["approve"]["metadata"]["human_prompt"] == "Approve this draft"
    assert nodes["approve"]["metadata"]["human_render_mode"] == "approval"

    assert nodes["vote"]["node_type"] == "worker"
    assert nodes["vote"]["role"] == "vote"
    assert nodes["vote"]["metadata"]["vote_candidates"] == ["a", "b"]
    assert nodes["vote"]["metadata"]["vote_prompt_template"] == "Pick one"

    assert nodes["merge"]["node_type"] == "worker"
    assert nodes["merge"]["role"] == "reduce"
    assert nodes["merge"]["metadata"]["reduce_expression"] == "append"
