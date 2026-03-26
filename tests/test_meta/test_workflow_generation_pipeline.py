from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.meta.intent_compiler import IntentCompiler
from dan.meta.intent_extraction import extract_workflow_intent, validate_and_expand_intent
from dan.meta.intent_schema import StageType, WorkflowIntent
from dan.meta.planner import (
    ExecutionReadinessError,
    GenerateCodePlan,
    WorkflowPlanner,
    validate_codegen_output,
)


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
