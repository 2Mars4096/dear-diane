from __future__ import annotations

from dan.builder import workflow
from dan.graph_mutator import GraphMutator, MutationPlan
from dan.meta.intent_compiler import IntentCompiler
from dan.meta.intent_schema import StageIntent, StageType, WorkflowIntent
from dan.meta.workflow_contract import validate_workflow_build_contract
from dan.models.graph import Graph
from dan.models.node_taxonomy import RETAINED_RUNTIME_NODE_TYPES


_ALLOWED_SPECIALIZED_NODE_TYPES = RETAINED_RUNTIME_NODE_TYPES | {"validator"}


def _assert_worker_first_boundary(graph: Graph) -> None:
    for node in graph.nodes:
        assert (
            node.node_type == "worker"
            or node.node_type in _ALLOWED_SPECIALIZED_NODE_TYPES
        ), f"Unexpected non-Worker compute node {node.node_type!r}"


def _assert_run_ready(graph: Graph, workflow_id: str) -> None:
    report = validate_workflow_build_contract(
        graph.model_dump(mode="json"),
        workflow_id=workflow_id,
        apply_repairs=False,
    )
    assert report.validated is True
    assert report.run_ready is True
    assert report.errors == []
    assert report.run_readiness_issues == []


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


def test_worker_first_builder_gate_passes_contract_validation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")

    wf = workflow("worker_first_builder_gate")
    entry = wf.input_node(
        "entry",
        variables=[{"name": "topic", "type": "string", "default": "agents"}],
    )
    retrieve = wf.rag("retrieve", collection="docs", top_k=3)
    summarize = wf.llm(
        "summarize",
        prompt="Summarize the retrieved evidence about {input}",
        model="test-model",
    )
    score = wf.code("score", code="result = {'score': len(str(input))}")
    wf.edge(entry["topic"], retrieve["query"])
    wf.edge(retrieve["chunks"], summarize["input"])
    wf.edge(summarize["text"], score["input"])

    graph = wf.build()

    _assert_worker_first_boundary(graph)
    _assert_run_ready(graph, "worker-first-builder-gate")


def test_worker_first_scoped_worker_gate_passes_contract_validation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")

    wf = workflow("worker_first_scoped_gate")
    with wf.worker_scope(
        "manager",
        role="manager",
        input_mappings={"brief": "draft::brief"},
        output_mappings={"valid": "final_text"},
        parallelism=2,
        merge_strategy="last_write_wins",
        spawn_policy={"max_spawns_per_node": 2},
        output_ports=[{"name": "final_text"}],
    ) as manager:
        draft = manager.code(
            "draft",
            code="result = {'summary': brief}",
            input_ports=[{"name": "brief", "required": False}],
            output_ports=[{"name": "result"}],
        )
        review = manager.worker(
            "review",
            role="validator",
            validation_rules=[{"rule_type": "required_keys", "config": {"keys": ["summary"]}}],
            input_ports=[{"name": "data", "required": False}],
            output_ports=[{"name": "valid"}, {"name": "invalid"}],
        )
        manager.edge(draft["result"], review["data"])

        with manager.sub_worker("research") as research:
            research.code("collect", code="result = {'notes': ['a', 'b']}")

    graph = wf.build()

    _assert_worker_first_boundary(graph)
    _assert_run_ready(graph, "worker-first-scoped-gate")


def test_worker_first_graph_mutator_gate_passes_contract_validation(
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
                    ],
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
                "id": "summarize",
                "node_type": "llm_operator",
                "name": "Summarize",
                "config": {
                    "model": "test-model",
                    "prompt_template": "Summarize the retrieved evidence about {input}",
                },
            },
            {
                "op": "add_node",
                "id": "score",
                "node_type": "code_operator",
                "name": "Score",
                "config": {"code": "result = {'score': len(str(input))}"},
            },
            {
                "op": "add_node",
                "id": "validate",
                "node_type": "validator",
                "name": "Validate",
                "config": {
                    "validation_rules": [
                        {"rule_type": "required_keys", "config": {"keys": ["text"]}},
                    ],
                    "on_failure": "halt",
                    "strict_mode": True,
                },
            },
            {
                "op": "add_edge",
                "source_id": "entry",
                "source_port": "topic",
                "target_id": "retrieve",
                "target_port": "query",
            },
            {
                "op": "add_edge",
                "source_id": "retrieve",
                "source_port": "chunks",
                "target_id": "summarize",
                "target_port": "input",
            },
            {
                "op": "add_edge",
                "source_id": "summarize",
                "source_port": "text",
                "target_id": "validate",
                "target_port": "data",
            },
            {
                "op": "add_edge",
                "source_id": "validate",
                "source_port": "valid",
                "target_id": "score",
                "target_port": "input",
            },
        ],
        description="Worker-first mutation gate validation",
    )

    result = mutator.dry_run(_empty_graph(), plan)
    assert result.success, result.errors
    assert result.new_graph is not None

    graph = Graph.model_validate(result.new_graph)
    _assert_worker_first_boundary(graph)
    _assert_run_ready(graph, "worker-first-mutator-gate")


def test_worker_first_generation_gate_passes_contract_validation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DAN_WORKER_GENERATION", "enabled")

    graph = IntentCompiler().build_graph(
        WorkflowIntent(
            goal="Search, summarize, and score a topic",
            stages=[
                StageIntent(
                    name="search",
                    stage_type=StageType.tool_call,
                    description="Search the web for relevant material",
                    config={"tool_id": "web_search"},
                ),
                StageIntent(
                    name="summarize",
                    stage_type=StageType.transform,
                    description="Summarize the gathered material",
                ),
                StageIntent(
                    name="score",
                    stage_type=StageType.code_execution,
                    description="Score the summary",
                    config={"code": "result = {'score': 0.92}"},
                ),
            ],
        )
    )

    _assert_worker_first_boundary(graph)
    _assert_run_ready(graph, "worker-first-generation-gate")
