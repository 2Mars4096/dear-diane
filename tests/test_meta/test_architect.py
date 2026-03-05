"""Tests for Plan 19-7: System Architect Mode."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dan.meta.architect import (
    RoutingConfig,
    SystemArchitect,
    SystemManifest,
    SystemPlan,
    SystemValidationResult,
    WorkflowSpec,
)
from dan.meta.controller import MetaController, MetaControllerConfig, MetaSession
from dan.meta.discovery import DiscoveryResult, ToolInfo
from dan.meta.planner import GeneratePlan, PlannerOutput, PlanReview


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_llm(response: str):
    async def fake_llm(system_prompt: str, user_prompt: str, model: Any = None, temp: float = 0.3) -> str:
        return response
    return fake_llm


class FakeWorkflowPlanner:
    """Planner that returns a canned GeneratePlan."""

    def __init__(self, plan_output: PlannerOutput | None = None) -> None:
        self._output = plan_output or PlannerOutput(
            plan=GeneratePlan(description="test", spec={}),
            review=PlanReview(valid=True),
        )
        self.plan_count = 0

    async def plan(self, goal: str, error_context: str | None = None) -> PlannerOutput:
        self.plan_count += 1
        return self._output

    async def execute_plan(self, plan: Any) -> dict[str, Any]:
        return {"workflow_id": f"wf-{self.plan_count}", "success": True}


class FakeDiscoveryService:
    async def discover_all(self, query: str, top_k: int = 5) -> DiscoveryResult:
        return DiscoveryResult(
            tools=[ToolInfo(tool_id="web_search"), ToolInfo(tool_id="calculator")],
        )


_SYSTEM_PLAN_JSON = json.dumps({
    "name": "data_pipeline",
    "description": "Multi-step data processing",
    "workflows": [
        {
            "name": "ingest",
            "goal": "Ingest raw data",
            "trigger": "manual",
            "outputs": [{"name": "raw_data", "type": "object"}],
            "shared_state_keys": ["raw_data"],
        },
        {
            "name": "transform",
            "goal": "Transform data",
            "trigger": "upstream-output",
            "depends_on": ["ingest"],
            "inputs": [{"name": "raw_data", "type": "object"}],
            "outputs": [{"name": "clean_data", "type": "object"}],
        },
    ],
    "shared_memory_keys": ["raw_data"],
})


# ---------------------------------------------------------------------------
# Tests: WorkflowSpec
# ---------------------------------------------------------------------------


class TestWorkflowSpec:
    def test_construction_defaults(self):
        spec = WorkflowSpec(name="wf1", goal="Do stuff")
        assert spec.name == "wf1"
        assert spec.goal == "Do stuff"
        assert spec.trigger == "manual"
        assert spec.depends_on == []
        assert spec.inputs == []
        assert spec.outputs == []
        assert spec.shared_state_keys == []
        assert spec.required_tools == []
        assert spec.required_skills == []

    def test_trigger_types(self):
        for trigger in ("manual", "schedule", "event", "upstream-output"):
            spec = WorkflowSpec(name="w", goal="g", trigger=trigger)
            assert spec.trigger == trigger

    def test_with_dependencies(self):
        spec = WorkflowSpec(
            name="transform", goal="transform data",
            depends_on=["ingest", "validate"],
        )
        assert spec.depends_on == ["ingest", "validate"]


# ---------------------------------------------------------------------------
# Tests: RoutingConfig
# ---------------------------------------------------------------------------


class TestRoutingConfig:
    def test_construction_defaults(self):
        rc = RoutingConfig()
        assert rc.strategy == "intent_classifier"
        assert rc.intent_model == ""
        assert rc.fallback_workflow == ""
        assert rc.intent_map == {}

    def test_intent_map_populated(self):
        rc = RoutingConfig(
            intent_map={"greeting": "chat_wf", "search": "search_wf"},
            fallback_workflow="default_wf",
        )
        assert rc.intent_map["greeting"] == "chat_wf"
        assert rc.fallback_workflow == "default_wf"


# ---------------------------------------------------------------------------
# Tests: SystemPlan
# ---------------------------------------------------------------------------


class TestSystemPlan:
    def test_construction_defaults(self):
        plan = SystemPlan()
        assert plan.name == ""
        assert plan.description == ""
        assert plan.workflows == []
        assert plan.shared_tools == []
        assert plan.shared_skills == []
        assert plan.shared_memory_keys == []
        assert plan.routing_config is None

    def test_with_workflows_and_routing(self):
        plan = SystemPlan(
            name="system",
            workflows=[WorkflowSpec(name="a", goal="do a")],
            routing_config=RoutingConfig(fallback_workflow="a"),
        )
        assert len(plan.workflows) == 1
        assert plan.routing_config is not None
        assert plan.routing_config.fallback_workflow == "a"


# ---------------------------------------------------------------------------
# Tests: SystemArchitect
# ---------------------------------------------------------------------------


class TestSystemArchitect:
    @pytest.mark.asyncio
    async def test_classify_single(self):
        architect = SystemArchitect(llm_call=_make_fake_llm("single"))
        result = await architect.classify("Summarize this article")
        assert result == "single"

    @pytest.mark.asyncio
    async def test_classify_multi(self):
        architect = SystemArchitect(llm_call=_make_fake_llm("multi"))
        result = await architect.classify("Build a data pipeline with ingestion, transformation, and reporting")
        assert result == "multi"

    @pytest.mark.asyncio
    async def test_classify_extracts_multi_from_verbose(self):
        architect = SystemArchitect(llm_call=_make_fake_llm("This is a multi-workflow system"))
        result = await architect.classify("complex goal")
        assert result == "multi"

    @pytest.mark.asyncio
    async def test_decompose_produces_system_plan(self):
        architect = SystemArchitect(llm_call=_make_fake_llm(_SYSTEM_PLAN_JSON))
        plan = await architect.decompose("build a data pipeline")
        assert isinstance(plan, SystemPlan)
        assert plan.name == "data_pipeline"
        assert len(plan.workflows) == 2

    @pytest.mark.asyncio
    async def test_plan_delegates_single(self):
        call_idx = {"n": 0}
        responses = ["single", "unused"]

        async def sequenced_llm(sp: str, up: str, m: Any = None, t: float = 0.3) -> str:
            call_idx["n"] += 1
            return responses[min(call_idx["n"] - 1, len(responses) - 1)]

        planner = FakeWorkflowPlanner()
        architect = SystemArchitect(planner=planner, llm_call=sequenced_llm)
        result = await architect.plan("simple task")
        assert isinstance(result, PlannerOutput)
        assert planner.plan_count == 1

    @pytest.mark.asyncio
    async def test_plan_decomposes_multi(self):
        call_idx = {"n": 0}
        responses = ["multi", _SYSTEM_PLAN_JSON]

        async def sequenced_llm(sp: str, up: str, m: Any = None, t: float = 0.3) -> str:
            call_idx["n"] += 1
            return responses[min(call_idx["n"] - 1, len(responses) - 1)]

        architect = SystemArchitect(
            planner=FakeWorkflowPlanner(),
            discovery=FakeDiscoveryService(),
            llm_call=sequenced_llm,
        )
        result = await architect.plan("build data pipeline")
        assert isinstance(result, SystemPlan)

    @pytest.mark.asyncio
    async def test_plan_single_no_planner_raises(self):
        architect = SystemArchitect(llm_call=_make_fake_llm("single"))
        with pytest.raises(RuntimeError, match="No WorkflowPlanner"):
            await architect.plan("test")

    def test_validate_system_valid(self):
        plan = SystemPlan(
            workflows=[
                WorkflowSpec(name="a", goal="do a"),
                WorkflowSpec(name="b", goal="do b", depends_on=["a"]),
            ],
        )
        architect = SystemArchitect()
        result = architect.validate_system(plan)
        assert isinstance(result, SystemValidationResult)
        assert result.valid is True

    def test_validate_system_circular_deps(self):
        plan = SystemPlan(
            workflows=[
                WorkflowSpec(name="a", goal="a", depends_on=["b"]),
                WorkflowSpec(name="b", goal="b", depends_on=["a"]),
            ],
        )
        architect = SystemArchitect()
        result = architect.validate_system(plan)
        assert result.valid is False
        assert any("Circular" in e for e in result.errors)

    def test_validate_system_missing_depends_on(self):
        plan = SystemPlan(
            workflows=[
                WorkflowSpec(name="a", goal="a", depends_on=["nonexistent"]),
            ],
        )
        architect = SystemArchitect()
        result = architect.validate_system(plan)
        assert result.valid is False
        assert any("nonexistent" in e for e in result.errors)

    def test_validate_system_interface_mismatch(self):
        plan = SystemPlan(
            workflows=[
                WorkflowSpec(
                    name="producer", goal="produce",
                    outputs=[{"name": "data_out", "type": "object"}],
                ),
                WorkflowSpec(
                    name="consumer", goal="consume",
                    trigger="upstream-output",
                    depends_on=["producer"],
                    inputs=[{"name": "completely_different", "type": "object"}],
                ),
            ],
        )
        architect = SystemArchitect()
        result = architect.validate_system(plan)
        assert any("overlap" in w.lower() or "overlap" in w for w in result.warnings)

    def test_validate_system_untracked_shared_keys(self):
        plan = SystemPlan(
            workflows=[
                WorkflowSpec(name="a", goal="a", shared_state_keys=["untracked_key"]),
            ],
            shared_memory_keys=[],
        )
        architect = SystemArchitect()
        result = architect.validate_system(plan)
        assert any("untracked_key" in w for w in result.warnings)

    def test_validate_system_no_workflows(self):
        plan = SystemPlan(workflows=[])
        architect = SystemArchitect()
        result = architect.validate_system(plan)
        assert result.valid is False
        assert any("no workflows" in e.lower() for e in result.errors)

    @pytest.mark.asyncio
    async def test_build_system_plans_each_workflow(self):
        planner = FakeWorkflowPlanner()
        architect = SystemArchitect(planner=planner)
        plan = SystemPlan(
            workflows=[
                WorkflowSpec(name="a", goal="do a"),
                WorkflowSpec(name="b", goal="do b", depends_on=["a"]),
            ],
        )
        manifest = await architect.build_system(plan)
        assert isinstance(manifest, SystemManifest)
        assert planner.plan_count == 2

    @pytest.mark.asyncio
    async def test_build_system_returns_workflow_ids(self):
        planner = FakeWorkflowPlanner()
        architect = SystemArchitect(planner=planner)
        plan = SystemPlan(
            workflows=[WorkflowSpec(name="only", goal="do it")],
        )
        manifest = await architect.build_system(plan)
        assert len(manifest.workflow_ids) == 1

    @pytest.mark.asyncio
    async def test_build_system_no_planner(self):
        architect = SystemArchitect()
        plan = SystemPlan(
            workflows=[WorkflowSpec(name="a", goal="a")],
        )
        manifest = await architect.build_system(plan)
        assert any("No WorkflowPlanner" in e for e in manifest.validation_errors)


# ---------------------------------------------------------------------------
# Tests: Controller integration fields (19-7)
# ---------------------------------------------------------------------------


class TestControllerSystemIntegration:
    def test_meta_session_system_fields_defaults(self):
        session = MetaSession(goal="test")
        assert session.is_system is False
        assert session.workflow_ids == []
        assert session.run_ids == []
        assert session.system_plan is None
        assert session.system_manifest is None

    def test_meta_controller_config_authoring_defaults(self):
        cfg = MetaControllerConfig()
        assert cfg.enable_tool_authoring is False
        assert cfg.enable_skill_authoring is False
        assert cfg.require_authoring_approval is True
        assert cfg.custom_tools_dir == ""
        assert cfg.custom_skills_dir == ""

    def test_topo_sort_correct_order(self):
        specs = [
            WorkflowSpec(name="c", goal="c", depends_on=["a", "b"]),
            WorkflowSpec(name="a", goal="a"),
            WorkflowSpec(name="b", goal="b", depends_on=["a"]),
        ]
        ordered = MetaController._topo_sort_workflows(specs)
        names = [w.name for w in ordered]
        assert names.index("a") < names.index("b")
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("c")

    def test_topo_sort_no_deps(self):
        specs = [
            WorkflowSpec(name="x", goal="x"),
            WorkflowSpec(name="y", goal="y"),
            WorkflowSpec(name="z", goal="z"),
        ]
        ordered = MetaController._topo_sort_workflows(specs)
        assert len(ordered) == 3
        assert {w.name for w in ordered} == {"x", "y", "z"}

    def test_topo_sort_single_item(self):
        specs = [WorkflowSpec(name="only", goal="only")]
        ordered = MetaController._topo_sort_workflows(specs)
        assert len(ordered) == 1
        assert ordered[0].name == "only"

    def test_topo_sort_handles_cycles_gracefully(self):
        specs = [
            WorkflowSpec(name="a", goal="a", depends_on=["b"]),
            WorkflowSpec(name="b", goal="b", depends_on=["a"]),
        ]
        ordered = MetaController._topo_sort_workflows(specs)
        assert len(ordered) == 2
