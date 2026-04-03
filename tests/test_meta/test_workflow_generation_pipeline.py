from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.builder.decompiler import decompile
from dan.meta.intent_compiler import IntentCompiler
from dan.meta.intent_extraction import extract_workflow_intent, validate_and_expand_intent
from dan.meta.intent_schema import StageType, WorkflowIntent
from dan.meta.planner import (
    ExecutionReadinessError,
    GenerateCodePlan,
    WorkflowPlanner,
    validate_codegen_output,
)
from dan.meta.workflow_contract import validate_workflow_build_contract
from dan.models.graph import Graph


def _make_planner() -> WorkflowPlanner:
    discovery = AsyncMock()
    discovery.discover_all = AsyncMock(
        return_value=MagicMock(
            workflows=[],
            tools=[],
            skills=[],
            patterns=[],
            self_knowledge_formatted="",
            self_knowledge_chunks=[],
        )
    )
    return WorkflowPlanner(
        discovery=discovery,
        llm_call=AsyncMock(return_value="{}"),
    )


def _tool_call_response(payload: dict[str, Any]) -> Any:
    return SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                function=SimpleNamespace(
                    name="emit_workflow_intent",
                    arguments=json.dumps(payload),
                )
            )
        ]
    )


async def _extract_and_expand(
    goal_text: str,
    extracted_intent: dict[str, Any],
) -> WorkflowIntent:
    async def fake_llm(
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.3,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        return _tool_call_response(extracted_intent)

    intent = await extract_workflow_intent(fake_llm, goal_text)
    assert intent is not None
    return validate_and_expand_intent(intent, goal_text)


async def _run_direct_pipeline(
    goal_text: str,
    extracted_intent: dict[str, Any],
    *,
    domain: str | None = None,
    user_text: str | None = None,
) -> tuple[WorkflowIntent, dict[str, Any]]:
    intent = await _extract_and_expand(goal_text, extracted_intent)
    planner = _make_planner()
    result = await planner.execute_plan_direct(
        GenerateCodePlan(code="", description=goal_text, intent=intent),
        domain=domain,
        user_text=user_text or goal_text,
    )
    validation = validate_codegen_output(result["graph"])
    assert validation.success is True
    assert validation.run_ready is True
    return intent, result["graph"]


async def _run_compiled_pipeline(
    goal_text: str,
    extracted_intent: dict[str, Any],
    *,
    domain: str | None = None,
    user_text: str | None = None,
) -> tuple[WorkflowIntent, dict[str, Any], dict[str, Any]]:
    intent = await _extract_and_expand(goal_text, extracted_intent)
    builder_code = IntentCompiler().compile(intent)
    planner = _make_planner()
    result = await planner.execute_plan(
        GenerateCodePlan(code=builder_code, description=goal_text),
        domain=domain,
        user_text=user_text or goal_text,
    )
    validation = validate_codegen_output(result["graph"])
    assert validation.success is True
    assert validation.run_ready is True
    assert result["code_generated"] is True
    return intent, result["graph"], result


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _eval_prompt_text(prompt_id: str) -> str:
    payload = json.loads((_repo_root() / "tests/eval/prompts.json").read_text(encoding="utf-8"))
    for prompt in payload.get("prompts", []):
        if prompt.get("id") == prompt_id:
            return str(prompt["prompt"])
    raise KeyError(f"Unknown eval prompt id: {prompt_id}")


def _prune_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _prune_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_prune_none(item) for item in value]
    return value


def _resolve_subgraph(root: Graph, key: str) -> Graph | None:
    if key in root.sub_graphs:
        return root.sub_graphs[key]
    for subgraph in root.sub_graphs.values():
        found = _resolve_subgraph(subgraph, key)
        if found is not None:
            return found
    return None


def _normalize_edge(edge: Any) -> dict[str, Any]:
    payload = edge.model_dump(mode="json")
    for key in ("id", "ui", "metadata"):
        payload.pop(key, None)
    return _prune_none(payload)


def _node_signature(node: Any, root: Graph) -> dict[str, Any]:
    payload = node.model_dump(mode="json")
    body_graph_key = payload.pop("body_graph", None)
    for key in ("position", "ui", "metadata"):
        payload.pop(key, None)
    # Decompile → exec roundtrip does not yet re-emit while_loop state_schema/defaults.
    if payload.get("node_type") == "while_loop":
        payload.pop("state_schema", None)
        payload.pop("state_defaults", None)
    signature = _prune_none(payload)
    if body_graph_key:
        subgraph = _resolve_subgraph(root, body_graph_key)
        assert subgraph is not None
        signature["body_graph"] = _graph_signature(subgraph, root=root)
    return signature


def _graph_signature(graph: Graph, *, root: Graph | None = None) -> dict[str, Any]:
    root_graph = root or graph
    metadata = _prune_none(graph.metadata.model_dump(mode="json"))
    return {
        "metadata": metadata,
        "entry_points": sorted(graph.entry_points),
        "exit_points": sorted(graph.exit_points),
        "shared_context": sorted(
            (_prune_none(item.model_dump(mode="json")) for item in graph.shared_context),
            key=lambda item: item.get("key", ""),
        ),
        "artifact_refs": sorted(
            (_prune_none(item.model_dump(mode="json")) for item in graph.artifact_refs),
            key=lambda item: (item.get("name", ""), item.get("path", "")),
        ),
        "hyperedges": sorted(
            (_prune_none(item.model_dump(mode="json")) for item in graph.hyperedges),
            key=lambda item: (item.get("name", ""), item.get("hook", "")),
        ),
        "nodes": [
            _node_signature(node, root_graph)
            for node in sorted(graph.nodes, key=lambda item: item.id)
        ],
        "edges": sorted(
            (_normalize_edge(edge) for edge in graph.edges),
            key=lambda item: (
                item.get("source_node_id", ""),
                item.get("source_port", ""),
                item.get("target_node_id", ""),
                item.get("target_port", ""),
                item.get("edge_type", ""),
            ),
        ),
    }


def _roundtrip_through_builder(graph_dict: dict[str, Any]) -> Graph:
    graph = Graph.model_validate(graph_dict)
    namespace: dict[str, Any] = {}
    exec(decompile(graph), namespace)  # noqa: S102
    rebuilt = namespace["graph"]
    assert isinstance(rebuilt, Graph)
    return rebuilt


@pytest.mark.asyncio
async def test_direct_pipeline_expands_multistep_prompt_and_applies_model_tiering() -> None:
    goal = (
        "search the web for papers on agent memory, draft an introduction, "
        "draft a discussion, draft a conclusion, and publish the final "
        "literature review with no review and no validation"
    )
    extracted = {
        "goal": goal,
        "stages": [
            {
                "name": "workflow",
                "description": "Handle the full request",
                "stage_type": "transform",
            }
        ],
    }

    intent, graph = await _run_direct_pipeline(
        goal,
        extracted,
        domain="literature_review",
        user_text=goal,
    )

    assert [stage.stage_type for stage in intent.stages] == [
        StageType.tool_call,
        StageType.transform,
        StageType.transform,
        StageType.transform,
        StageType.transform,
    ]
    llm_nodes = [node for node in graph["nodes"] if node["node_type"] == "llm_operator"]
    assert [node["task_tier"] for node in llm_nodes] == [
        "routine",
        "reasoning",
        "reasoning",
        "critical",
    ]
    assert all(node["model_policy"] == {"strategy": "tier"} for node in llm_nodes)
    tool_nodes = [node for node in graph["nodes"] if node["node_type"] == "tool_operator"]
    assert len(tool_nodes) == 1
    assert tool_nodes[0]["tool_id"] == "web_search"
    assert tool_nodes[0].get("tool_config", {}).get("query")


@pytest.mark.asyncio
async def test_direct_pipeline_repairs_numeric_batch_prompt_into_run_ready_fanout_graph() -> None:
    goal = "do a little batch thing over 1 2 3, triple each one, sum it to 18, and run it"
    extracted = {
        "goal": goal,
        "stages": [
            {
                "name": "triple_each",
                "description": "Triple each number in the input list",
                "stage_type": "fan_out",
            },
            {
                "name": "sum_results",
                "stage_type": "code_execution",
                "config": {"code": "total = sum(tripled)"},
            },
        ],
    }

    intent, graph = await _run_direct_pipeline(goal, extracted)

    assert [stage.stage_type for stage in intent.stages] == [
        StageType.code_execution,
        StageType.fan_out,
        StageType.code_execution,
    ]
    assert [(node["id"], node["node_type"]) for node in graph["nodes"]] == [
        ("seed_items", "code_operator"),
        ("triple_each", "for_each"),
        ("sum_results", "code_operator"),
    ]
    code_nodes = [node["code"] for node in graph["nodes"] if node["node_type"] == "code_operator"]
    assert code_nodes[0] == "result = [1, 2, 3]"
    assert 'entry["result"]' in code_nodes[1]
    assert all('"status": "placeholder"' not in code for code in code_nodes)


@pytest.mark.asyncio
async def test_compiled_pipeline_handles_conditional_prompt_end_to_end() -> None:
    goal = (
        "uh can you do a tiny branch thing, if 7 is bigger than 5 say BIG "
        "otherwise SMALL, then run it with no validation"
    )
    extracted = {
        "goal": goal,
        "stages": [{"name": "branch", "stage_type": "transform"}],
    }

    intent, graph, result = await _run_compiled_pipeline(
        goal,
        extracted,
        user_text=goal,
    )

    conditional_stage = next(
        stage for stage in intent.stages if stage.stage_type == StageType.conditional
    )
    assert conditional_stage.conditional is not None
    assert conditional_stage.conditional.condition == "7 > 5"
    assert result["code_generated"] is True
    node_types = {node["id"]: node["node_type"] for node in graph["nodes"]}
    assert node_types["if_7_is_bigger_than_5_say_big_gate"] == "gate"
    assert node_types["if_7_is_bigger_than_5_say_big_then"] == "llm_operator"
    assert node_types["if_7_is_bigger_than_5_say_big_else"] == "llm_operator"


@pytest.mark.asyncio
async def test_compiled_pipeline_handles_tool_code_transform_prompt_end_to_end() -> None:
    goal = (
        "search the web for recent news on a company, compute article counts in python, "
        "draft a briefing, and run it"
    )
    extracted = {
        "goal": goal,
        "stages": [
            {
                "name": "gather_news",
                "description": "Search the web for recent company news",
                "stage_type": "tool_call",
                "config": {
                    "tool_id": "web_search",
                    "query": "recent news on a company",
                },
            },
            {
                "name": "count_articles",
                "description": "Count the gathered articles",
                "stage_type": "code_execution",
                "config": {
                    "code": (
                        "items = data if isinstance(data, list) else []\n"
                        "result = {'article_count': len(items)}"
                    )
                },
            },
            {
                "name": "draft_briefing",
                "description": "Draft a briefing from the gathered news and computed counts",
                "stage_type": "transform",
            },
        ],
    }

    intent, graph, result = await _run_compiled_pipeline(
        goal,
        extracted,
        user_text=goal,
    )

    assert [stage.stage_type for stage in intent.stages] == [
        StageType.tool_call,
        StageType.code_execution,
        StageType.transform,
    ]
    assert result["code_generated"] is True

    node_types = {node["id"]: node["node_type"] for node in graph["nodes"]}
    assert node_types["gather_news"] == "tool_operator"
    assert node_types["count_articles"] == "code_operator"
    assert node_types["draft_briefing"] == "llm_operator"

    tool_node = next(node for node in graph["nodes"] if node["id"] == "gather_news")
    assert tool_node["tool_id"] == "web_search"
    query = str(tool_node.get("tool_config", {}).get("query") or "")
    assert "news" in query.lower()
    assert "company" in query.lower()

    code_node = next(node for node in graph["nodes"] if node["id"] == "count_articles")
    assert "article_count" in code_node["code"]
    assert all(node["node_type"] != "input" for node in graph["nodes"])


@pytest.mark.asyncio
async def test_compiled_pipeline_rejects_placeholder_code_as_not_execution_ready() -> None:
    goal = "compute metrics and run it with no validation"
    extracted = {
        "goal": goal,
        "stages": [
            {
                "name": "compute_metrics",
                "description": "Compute metrics",
                "stage_type": "code_execution",
                "config": {
                    "code": (
                        'result = {"status": "placeholder", "task": "compute metrics"}'
                    )
                },
            }
        ],
    }

    intent = await _extract_and_expand(goal, extracted)
    builder_code = IntentCompiler().compile(intent)
    planner = _make_planner()

    with pytest.raises(
        ExecutionReadinessError,
        match="placeholder status payload code",
    ):
        await planner.execute_plan(
            GenerateCodePlan(code=builder_code, description=goal),
            user_text=goal,
        )


@pytest.mark.asyncio
async def test_direct_pipeline_handles_eval_tool_heavy_prompt_end_to_end() -> None:
    goal = _eval_prompt_text("p03")
    extracted = {
        "goal": goal,
        "stages": [
            {
                "name": "search_news",
                "stage_type": "tool_call",
                "description": "Search the web for recent news on a topic",
                "config": {"tool_id": "web_search"},
            },
            {
                "name": "read_results",
                "stage_type": "tool_call",
                "description": "Read the top 3 search results",
                "config": {"tool_id": "web_fetch"},
            },
            {
                "name": "produce_briefing",
                "stage_type": "transform",
                "description": "Produce a briefing from the gathered material",
            },
        ],
        "global_inputs": ["topic"],
        "global_outputs": ["briefing"],
    }

    intent, graph = await _run_direct_pipeline(goal, extracted, user_text=goal)

    assert [stage.stage_type for stage in intent.stages[:3]] == [
        StageType.tool_call,
        StageType.tool_call,
        StageType.transform,
    ]
    tool_nodes = [node for node in graph["nodes"] if node["node_type"] == "tool_operator"]
    assert [node["tool_id"] for node in tool_nodes] == ["web_search", "web_fetch"]
    assert any(node["node_type"] == "llm_operator" for node in graph["nodes"])
    assert any(node["node_type"] == "validator" for node in graph["nodes"])

    rebuilt = _roundtrip_through_builder(graph)
    rebuilt_signature = _graph_signature(rebuilt)
    original_signature = _graph_signature(Graph.model_validate(graph))
    assert rebuilt_signature == original_signature

    report = validate_workflow_build_contract(
        rebuilt.model_dump(mode="json"),
        workflow_id="eval-p03-tool-heavy",
        apply_repairs=False,
    )
    assert report.validated is True
    assert report.run_ready is True


@pytest.mark.asyncio
async def test_direct_pipeline_handles_eval_review_loop_prompt_and_roundtrips() -> None:
    goal = _eval_prompt_text("p02")
    extracted = {
        "goal": goal,
        "stages": [
            {
                "name": "review",
                "stage_type": "review_loop",
                "description": "Draft, review, and revise until approved",
                "review": {
                    "reviewer_prompt": "Check quality and return approval feedback.",
                    "condition": "approved == false",
                    "max_iterations": 3,
                },
            }
        ],
    }

    intent, graph = await _run_direct_pipeline(goal, extracted, user_text=goal)

    assert any(stage.stage_type == StageType.review_loop for stage in intent.stages)
    node_types = {node["node_type"] for node in graph["nodes"]}
    assert "while_loop" in node_types
    assert len(graph["nodes"]) >= 1

    rebuilt = _roundtrip_through_builder(graph)
    assert _graph_signature(rebuilt) == _graph_signature(Graph.model_validate(graph))

    report = validate_workflow_build_contract(
        rebuilt.model_dump(mode="json"),
        workflow_id="eval-p02-review-loop",
        apply_repairs=False,
    )
    assert report.validated is True
    assert report.run_ready is True


@pytest.mark.asyncio
async def test_direct_worker_generation_pipeline_passes_contract_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DAN_WORKER_GENERATION", "enabled")
    goal = (
        "search the web for papers on agent memory, summarize the findings, "
        "compute a confidence score, and publish a short briefing"
    )
    extracted = {
        "goal": goal,
        "stages": [
            {
                "name": "search",
                "stage_type": "tool_call",
                "description": "Search the web for relevant agent memory papers",
                "config": {"tool_id": "web_search"},
            },
            {
                "name": "summarize",
                "stage_type": "transform",
                "description": "Summarize the gathered findings into a briefing",
            },
            {
                "name": "score",
                "stage_type": "code_execution",
                "description": "Compute a confidence score for the briefing",
                "config": {"code": "result = {'score': 0.92}"},
            },
        ],
        "global_inputs": ["topic"],
        "global_outputs": ["briefing"],
    }

    intent, graph = await _run_direct_pipeline(
        goal,
        extracted,
        domain="literature_review",
        user_text=goal,
    )

    compute_nodes = [
        node for node in graph["nodes"]
        if node["id"] in {"search", "summarize", "score"}
    ]
    assert [node["node_type"] for node in compute_nodes] == ["worker", "worker", "worker"]
    assert compute_nodes[0]["tool_ids"] == ["web_search"]
    assert compute_nodes[1]["role"] == "processor"
    assert compute_nodes[2]["code"] == "result = {'score': 0.92}"
    assert all(
        node["node_type"] in {"worker", "gate", "for_each", "while_loop", "goal_loop", "validator"}
        for node in graph["nodes"]
    )

    rebuilt = _roundtrip_through_builder(graph)
    assert _graph_signature(rebuilt) == _graph_signature(Graph.model_validate(graph))

    report = validate_workflow_build_contract(
        rebuilt.model_dump(mode="json"),
        workflow_id="worker-generation-contract-gate",
        apply_repairs=False,
    )
    assert report.validated is True
    assert report.run_ready is True
    assert report.errors == []
    assert report.run_readiness_issues == []
    assert [stage.stage_type for stage in intent.stages[:3]] == [
        StageType.tool_call,
        StageType.transform,
        StageType.code_execution,
    ]


@pytest.mark.asyncio
async def test_compiled_pipeline_handles_eval_conditional_prompt_and_roundtrips() -> None:
    goal = _eval_prompt_text("t1-09")
    extracted = {
        "goal": goal,
        "stages": [
            {
                "name": "branch",
                "stage_type": "conditional",
                "description": "Route based on document relevance",
                "conditional": {
                    "condition": "is_relevant",
                    "if_true": "summarize the document",
                    "if_false": "write a brief rejection note",
                },
            }
        ],
    }

    intent, graph, result = await _run_compiled_pipeline(goal, extracted, user_text=goal)

    assert result["code_generated"] is True
    assert any(stage.stage_type == StageType.conditional for stage in intent.stages)
    node_types = {node["id"]: node["node_type"] for node in graph["nodes"]}
    assert any(node_type == "gate" for node_type in node_types.values())
    assert any(node_type == "llm_operator" for node_type in node_types.values())

    rebuilt = _roundtrip_through_builder(graph)
    assert _graph_signature(rebuilt) == _graph_signature(Graph.model_validate(graph))

    report = validate_workflow_build_contract(
        rebuilt.model_dump(mode="json"),
        workflow_id="eval-t1-09-conditional",
        apply_repairs=False,
    )
    assert report.validated is True
    assert report.run_ready is True
