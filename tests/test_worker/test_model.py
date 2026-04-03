from __future__ import annotations

import pytest

from dan.models.context import MergeStrategy
from dan.models.control_flow import SpawnPolicy
from dan.models.graph import Graph
from dan.models.legacy import ValidationRule
from dan.models.node_taxonomy import (
    BODY_GRAPH_RUNTIME_NODE_TYPES,
    RUNTIME_NODE_TYPE_MAP,
    RUNTIME_NODE_TYPES,
)
from dan.worker import (
    AuthorityPolicy,
    ControlFlowConfig,
    ContextBindings,
    ExecutionSemantics,
    LLMHints,
    Worker,
    WorkerAuthority,
)


def test_bare_worker_defaults_to_input_and_result_ports() -> None:
    worker = Worker(id="w", name="Worker")

    assert worker.node_type == "worker"
    assert [port.name for port in worker.input_ports] == ["input"]
    assert [port.name for port in worker.output_ports] == ["result"]


def test_worker_round_trip_preserves_refs_and_policies() -> None:
    worker = Worker(
        id="reviewer",
        name="Reviewer",
        role="reviewer",
        instruction="Review carefully",
        authority=WorkerAuthority.DELEGATE,
        model="test-model",
        context=ContextBindings(
            instruction_profile_ref="review_profile",
            memory_policy_ref="memory_default",
            context_bundle_refs=["ops_bundle"],
            provider_policy_ref="economy",
            retry_policy_ref="retry_low",
            toolset_refs=["safe_tools"],
        ),
        authority_policy=AuthorityPolicy(
            max_spawned_workers=2,
            task_tier_cap="routine",
            allow_delegate=True,
            allow_memory_write_scopes=["memory.team"],
            allowed_toolset_refs=["safe_tools"],
        ),
        execution=ExecutionSemantics(
            resource_locks=["team_writer"],
            blocking_mode="exclusive",
            await_subworkers=True,
            parallelism_override=2,
        ),
        llm_hints=LLMHints(
            prompt_template="Review {input}",
            temperature=0.1,
            task_tier="routine",
        ),
        body_graph="review_body",
        sub_workers={"research": "review_research"},
        input_mappings={"draft": "inner_draft"},
        output_mappings={"inner_summary": "summary"},
        parallelism=2,
        merge_strategy=MergeStrategy.LAST_WRITE_WINS,
        spawn_policy=SpawnPolicy(max_spawns_per_node=2, max_total_children=4),
        validation_rules=[ValidationRule(rule_type="required_keys", config={"keys": ["summary"]})],
        input_ports=[{"name": "input", "required": False}],
        output_ports=[{"name": "result"}],
    )

    payload = worker.model_dump(mode="json", exclude_none=False)
    rebuilt = Worker.model_validate(payload)

    assert rebuilt.context is not None
    assert rebuilt.context.instruction_profile_ref == "review_profile"
    assert rebuilt.context.memory_policy_ref == "memory_default"
    assert rebuilt.context.context_bundle_refs == ["ops_bundle"]
    assert rebuilt.authority_policy is not None
    assert rebuilt.authority_policy.allow_memory_write_scopes == ["memory.team"]
    assert rebuilt.authority_policy.allowed_toolset_refs == ["safe_tools"]
    assert rebuilt.execution is not None
    assert rebuilt.execution.resource_locks == ["team_writer"]
    assert rebuilt.llm_hints is not None
    assert rebuilt.llm_hints.prompt_template == "Review {input}"
    assert rebuilt.body_graph == "review_body"
    assert rebuilt.sub_workers == {"research": "review_research"}
    assert rebuilt.input_mappings == {"draft": "inner_draft"}
    assert rebuilt.output_mappings == {"inner_summary": "summary"}
    assert rebuilt.parallelism == 2
    assert rebuilt.merge_strategy == MergeStrategy.LAST_WRITE_WINS
    assert rebuilt.spawn_policy is not None
    assert rebuilt.spawn_policy.max_spawns_per_node == 2
    assert rebuilt.validation_rules == [ValidationRule(rule_type="required_keys", config={"keys": ["summary"]})]


@pytest.mark.parametrize(
    ("variant", "payload"),
    [
        pytest.param(
            "llm",
            {
                "id": "draft",
                "name": "Draft",
                "model": "stub-model",
                "llm_hints": {
                    "prompt_template": "Draft {input}",
                    "system_prompt": "Stay concise.",
                    "temperature": 0.1,
                    "max_tokens": 128,
                    "task_tier": "routine",
                },
                "input_ports": [{"name": "input", "required": False}],
                "output_ports": [{"name": "text"}],
            },
            id="llm",
        ),
        pytest.param(
            "tool",
            {
                "id": "search",
                "name": "Search",
                "tool_ids": ["web_search"],
                "context": {
                    "toolset_refs": ["default_tools"],
                    "inherit_defaults": False,
                },
                "metadata": {"tool_config": {"query": "{input}", "limit": 3}},
                "input_ports": [{"name": "input", "required": False}],
                "output_ports": [{"name": "result"}],
            },
            id="tool",
        ),
        pytest.param(
            "code",
            {
                "id": "score",
                "name": "Score",
                "code": "result = score + 1",
                "language": "python",
                "input_ports": [{"name": "score", "required": True}],
                "output_ports": [{"name": "result"}],
            },
            id="code",
        ),
        pytest.param(
            "composite",
            {
                "id": "review",
                "name": "Review",
                "body_graph": "review_body",
                "sub_workers": {"critic": "review_critic"},
                "input_mappings": {"draft": "entry::draft"},
                "output_mappings": {"summary": "result"},
                "parallelism": 2,
                "merge_strategy": "last_write_wins",
                "spawn_policy": {"max_spawns_per_node": 2},
                "boundary_contract": {
                    "external_input_schema": {"type": "object", "properties": {"draft": {"type": "string"}}},
                    "external_output_schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
                },
                "input_ports": [{"name": "draft", "required": False}],
                "output_ports": [{"name": "summary"}],
            },
            id="composite",
        ),
        pytest.param(
            "gate",
            {
                "id": "route",
                "name": "Route",
                "control_flow": {
                    "condition": "score > 0.5",
                    "gate_mode": "while",
                    "max_iterations": 4,
                    "state_schema": {"type": "object", "properties": {"score": {"type": "number"}}},
                    "state_defaults": {"score": 0.0},
                },
                "input_ports": [{"name": "score", "required": False}],
                "output_ports": [{"name": "continue"}, {"name": "done"}],
            },
            id="gate",
        ),
        pytest.param(
            "validator",
            {
                "id": "validate",
                "name": "Validate",
                "role": "validator",
                "validation_rules": [{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
                "metadata": {"validator_on_failure": "route", "validator_strict_mode": True},
                "input_ports": [{"name": "data", "required": False}],
                "output_ports": [{"name": "valid"}, {"name": "invalid"}],
            },
            id="validator",
        ),
    ],
)
def test_worker_json_round_trip_preserves_variant_payloads(
    variant: str,
    payload: dict[str, object],
) -> None:
    worker = Worker.model_validate(payload)

    serialized = worker.model_dump(mode="json", exclude_none=False)
    rebuilt = Worker.model_validate(serialized)

    assert rebuilt.model_dump(mode="json", exclude_none=False) == serialized, variant


def test_graph_union_deserializes_worker_nodes() -> None:
    graph = Graph.model_validate(
        {
            "nodes": [
                {
                    "node_type": "worker",
                    "id": "w1",
                    "name": "Worker 1",
                    "role": "reviewer",
                    "context": {
                        "instruction_profile_ref": "review_profile",
                    },
                    "authority_policy": {
                        "allow_memory_write_scopes": ["memory.public"],
                    },
                    "execution": {
                        "resource_locks": ["team_writer"],
                    },
                    "input_ports": [{"name": "input", "required": False}],
                    "output_ports": [{"name": "result"}],
                }
            ],
            "edges": [],
            "entry_points": ["w1"],
            "exit_points": ["w1"],
        }
    )

    assert isinstance(graph.nodes[0], Worker)
    assert graph.nodes[0].context is not None
    assert graph.nodes[0].context.instruction_profile_ref == "review_profile"


def test_worker_is_visible_in_runtime_taxonomy_and_body_graph_sets() -> None:
    assert "worker" in RUNTIME_NODE_TYPES
    assert RUNTIME_NODE_TYPE_MAP["worker"] is Worker
    assert "worker" in BODY_GRAPH_RUNTIME_NODE_TYPES


def test_worker_model_json_schema_exposes_context_and_policy_fields() -> None:
    schema = Worker.model_json_schema()
    properties = schema["properties"]

    assert properties["node_type"]["default"] == "worker"
    assert "context" in properties
    assert "authority_policy" in properties
    assert "execution" in properties
    assert "llm_hints" in properties


def test_llm_hints_defaults_are_stable_and_optional() -> None:
    worker = Worker(id="w", name="Worker")
    hints = LLMHints()

    assert worker.llm_hints is None
    assert hints.prompt_template == ""
    assert hints.system_prompt == ""
    assert hints.temperature == 0.7
    assert hints.max_tool_rounds == 10


def test_worker_with_llm_hints_but_no_explicit_model_defaults_to_text_output() -> None:
    worker = Worker(
        id="draft",
        name="Draft",
        llm_hints=LLMHints(prompt_template="Draft {input}"),
    )

    assert [port.name for port in worker.output_ports] == ["text"]


def test_worker_with_control_flow_defaults_to_gate_ports() -> None:
    worker = Worker(
        id="route",
        name="Route",
        control_flow=ControlFlowConfig(condition="score > 0.5"),
    )

    assert [port.name for port in worker.output_ports] == ["true", "false"]
