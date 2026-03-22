from __future__ import annotations

import json
from typing import Any

import pytest

from dan.meta.controller import MetaController, MetaControllerConfig, MetaSession, MetaSessionStatus
from dan.meta.discovery import DiscoveryResult
from dan.meta.intent_extraction import build_intent_extraction_system_prompt, extract_workflow_intent
from dan.meta.planner import GeneratePlan, PlannerOutput, PlanningPromptBuilder, PlanReview


_GOAL_CONTRACT = {
    "goal": "Automate a weekly market scan.",
    "deliverable": "A written summary saved to disk.",
    "constraints": ["Use the existing project folder.", "Keep citations for any claims."],
    "required_action_hints": ["search_web", "write_file"],
    "completion_checks": ["The summary file exists.", "The output mentions cited sources."],
    "next_step": "Plan the workflow before execution.",
}


class CapturingPlanner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def plan(
        self,
        goal: str,
        error_context: str | None = None,
        plan_context: dict[str, Any] | None = None,
    ) -> PlannerOutput:
        self.calls.append({
            "goal": goal,
            "error_context": error_context,
            "plan_context": plan_context,
        })
        return PlannerOutput(
            plan=GeneratePlan(description="market scan", spec={}),
            review=PlanReview(valid=True),
        )


def test_planning_prompt_builder_renders_goal_contract():
    builder = PlanningPromptBuilder()
    prompt = builder.build_user_prompt(
        "Automate a weekly market scan.",
        DiscoveryResult(),
        plan_context={"goal_contract": _GOAL_CONTRACT},
    )

    assert "## Goal Contract" in prompt
    assert "- Goal: Automate a weekly market scan." in prompt
    assert "- Deliverable: A written summary saved to disk." in prompt
    assert "- Completion checks:" in prompt


def test_planning_prompt_builder_includes_workflow_contract() -> None:
    builder = PlanningPromptBuilder()
    prompt = builder.build_system_prompt()

    assert "Workflow Generation Contract" in prompt
    assert "replace_body_graph" in prompt
    assert "Do not introduce new `{{variable}}` placeholders" in prompt


@pytest.mark.asyncio
async def test_meta_controller_passes_goal_contract_to_planner():
    planner = CapturingPlanner()
    controller = MetaController(planner=planner)
    session = MetaSession(
        goal="Automate a weekly market scan.",
        goal_context={"goal_contract": _GOAL_CONTRACT},
    )

    result = await controller.run_session(
        session,
        MetaControllerConfig(max_iterations=1, pause_before_execute=True),
    )

    assert result.status == MetaSessionStatus.PAUSED
    assert planner.calls
    assert planner.calls[0]["plan_context"] == {"goal_contract": _GOAL_CONTRACT}


@pytest.mark.asyncio
async def test_extract_workflow_intent_includes_goal_contract_in_prompt():
    captured: dict[str, Any] = {}

    async def fake_llm(
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.3,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["tools"] = tools
        return json.dumps({
            "goal": "Automate a weekly market scan.",
            "stages": [
                {
                    "name": "research",
                    "description": "Gather weekly market updates.",
                    "outputs": ["research_notes"],
                }
            ],
        })

    intent = await extract_workflow_intent(
        fake_llm,
        "Automate a weekly market scan.",
        goal_contract=_GOAL_CONTRACT,
    )

    assert intent is not None
    assert "Workflow Generation Contract" in captured["system_prompt"]
    assert "Do NOT invent tool_ids not in this list" in captured["system_prompt"]
    assert "## Goal Contract" in captured["user_prompt"]
    assert "Deliverable: A written summary saved to disk." in captured["user_prompt"]
    assert "Completion checks:" in captured["user_prompt"]


def test_build_intent_extraction_system_prompt_includes_contract() -> None:
    prompt = build_intent_extraction_system_prompt()

    assert "Workflow Generation Contract" in prompt
    assert "Do NOT invent tool_ids not in this list" in prompt
    assert "smallest complete runnable workflow" in prompt

