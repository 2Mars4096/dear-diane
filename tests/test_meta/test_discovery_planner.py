"""Tests for Plan 19-2: Discovery Service and Workflow Planner."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dan.meta.discovery import (
    DiscoveryResult,
    DiscoveryService,
    PatternInfo,
    SkillInfo,
    ToolInfo,
    WorkflowMatch,
)
from dan.meta.planner import (
    AdaptPlan,
    GeneratePlan,
    PlannerOutput,
    PlanningPromptBuilder,
    PlanReview,
    ReusePlan,
    WorkflowPlanner,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeToolRegistry:
    def registered_ids(self) -> list[str]:
        return ["web_search", "calculator", "file_reader"]


class FakeExperienceIndex:
    def __init__(self, hits: list[tuple[str, float]] | None = None):
        self._hits = hits or []

    async def search_similar(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        return self._hits[:top_k]


class FakeExperienceStore:
    def __init__(self, experiences: dict[str, Any] | None = None):
        self._exps = experiences or {}

    async def load_experience(self, workflow_id: str):
        return self._exps.get(workflow_id)


class FakeExperience:
    def __init__(self, name="", description="", tags=None, run_count=0, success_count=0):
        self.name = name
        self.description = description
        self.tags = tags or []
        self.run_count = run_count
        self.success_count = success_count


class FakeGraphStore:
    def __init__(self, graphs: dict[str, dict] | None = None):
        self._graphs = graphs or {}

    def get_graph(self, graph_id: str) -> dict | None:
        return self._graphs.get(graph_id)


# ---------------------------------------------------------------------------
# Tests: Discovery models
# ---------------------------------------------------------------------------


class TestDiscoveryModels:
    def test_tool_info(self):
        t = ToolInfo(tool_id="web_search", description="Search the web")
        assert t.tool_id == "web_search"

    def test_workflow_match(self):
        m = WorkflowMatch(
            workflow_id="wf1", score=0.85, reuse_fit_score=0.9,
        )
        assert m.reuse_fit_score == 0.9

    def test_discovery_result_empty(self):
        r = DiscoveryResult()
        assert r.tools == []
        assert r.workflows == []


# ---------------------------------------------------------------------------
# Tests: DiscoveryService
# ---------------------------------------------------------------------------


class TestDiscoveryService:
    def test_discover_tools(self):
        svc = DiscoveryService(tool_registry=FakeToolRegistry())
        tools = svc.discover_tools()
        assert len(tools) == 3
        ids = {t.tool_id for t in tools}
        assert "web_search" in ids

    def test_discover_tools_no_registry(self):
        svc = DiscoveryService()
        assert svc.discover_tools() == []

    def test_discover_patterns(self):
        svc = DiscoveryService()
        patterns = svc.discover_patterns()
        names = {p.name for p in patterns}
        assert "chain" in names
        assert "review_loop" in names

    def test_discover_skills(self):
        svc = DiscoveryService()
        skills = svc.discover_skills()
        assert len(skills) > 0

    @pytest.mark.asyncio
    async def test_discover_workflows(self):
        idx = FakeExperienceIndex(hits=[("wf1", 0.85), ("wf2", 0.6)])
        store = FakeExperienceStore(experiences={
            "wf1": FakeExperience(name="Paper Writer", run_count=10, success_count=8),
        })
        svc = DiscoveryService(experience_index=idx, experience_store=store)
        matches = await svc.discover_workflows("write a paper")
        assert len(matches) == 2
        assert matches[0].reuse_fit_score >= matches[1].reuse_fit_score

    @pytest.mark.asyncio
    async def test_discover_workflows_no_index(self):
        svc = DiscoveryService()
        assert await svc.discover_workflows("anything") == []

    @pytest.mark.asyncio
    async def test_discover_all(self):
        svc = DiscoveryService(tool_registry=FakeToolRegistry())
        result = await svc.discover_all("test query")
        assert isinstance(result, DiscoveryResult)
        assert len(result.tools) == 3
        assert len(result.patterns) > 0


# ---------------------------------------------------------------------------
# Tests: PlanningPromptBuilder
# ---------------------------------------------------------------------------


class TestPlanningPromptBuilder:
    def test_system_prompt_not_empty(self):
        builder = PlanningPromptBuilder()
        prompt = builder.build_system_prompt()
        assert "REUSE" in prompt
        assert "ADAPT" in prompt
        assert "GENERATE" in prompt

    def test_user_prompt_includes_goal(self):
        builder = PlanningPromptBuilder()
        prompt = builder.build_user_prompt("Write a research paper", DiscoveryResult())
        assert "Write a research paper" in prompt

    def test_user_prompt_includes_workflows(self):
        builder = PlanningPromptBuilder()
        disco = DiscoveryResult(workflows=[
            WorkflowMatch(workflow_id="wf1", name="Paper", reuse_fit_score=0.9),
        ])
        prompt = builder.build_user_prompt("write paper", disco)
        assert "wf1" in prompt
        assert "0.90" in prompt

    def test_user_prompt_includes_error_context(self):
        builder = PlanningPromptBuilder()
        prompt = builder.build_user_prompt(
            "write paper", DiscoveryResult(), error_context="Tool timeout",
        )
        assert "Tool timeout" in prompt

    def test_system_prompt_contains_few_shot_examples(self):
        builder = PlanningPromptBuilder()
        prompt = builder.build_system_prompt()
        assert "Example 1" in prompt
        assert "Example 2" in prompt
        assert "Example 3" in prompt
        assert "paper_writing" in prompt
        assert "equity_research" in prompt
        assert '"action": "REUSE"' in prompt
        assert '"action": "ADAPT"' in prompt
        assert '"action": "GENERATE"' in prompt

    def test_system_prompt_includes_workflow_contract(self):
        builder = PlanningPromptBuilder()
        prompt = builder.build_system_prompt()
        assert "Workflow Generation Contract" in prompt
        assert "replace_body_graph" in prompt
        assert "Do not introduce new `{{variable}}` placeholders" in prompt


# ---------------------------------------------------------------------------
# Tests: WorkflowPlanner
# ---------------------------------------------------------------------------


class TestWorkflowPlanner:
    @pytest.mark.asyncio
    async def test_plan_reuse(self):
        async def fake_llm(*args) -> str:
            return json.dumps({"action": "REUSE", "workflow_id": "wf1"})

        svc = DiscoveryService(tool_registry=FakeToolRegistry())
        planner = WorkflowPlanner(
            discovery=svc, llm_call=fake_llm,
            graph_store=FakeGraphStore(graphs={"wf1": {"version": "dan_graph_v1"}}),
        )
        output = await planner.plan("reuse an existing workflow")
        assert isinstance(output.plan, ReusePlan)
        assert output.plan.workflow_id == "wf1"
        assert output.review.valid

    @pytest.mark.asyncio
    async def test_plan_adapt(self):
        async def fake_llm(*args) -> str:
            return json.dumps({
                "action": "ADAPT", "workflow_id": "wf1",
                "mutations": [{"op": "edit_node", "node_id": "n1", "config": {"temperature": 0.5}}],
            })

        svc = DiscoveryService(tool_registry=FakeToolRegistry())
        planner = WorkflowPlanner(
            discovery=svc, llm_call=fake_llm,
            graph_store=FakeGraphStore(graphs={"wf1": {"version": "dan_graph_v1"}}),
        )
        output = await planner.plan("adapt workflow")
        assert isinstance(output.plan, AdaptPlan)
        assert len(output.plan.mutations) == 1

    @pytest.mark.asyncio
    async def test_plan_generate(self):
        async def fake_llm(*args) -> str:
            return json.dumps({
                "action": "GENERATE",
                "description": "New workflow",
                "spec": {"nodes": [{"node_type": "llm_operator", "name": "Writer"}]},
            })

        svc = DiscoveryService(tool_registry=FakeToolRegistry())
        planner = WorkflowPlanner(discovery=svc, llm_call=fake_llm)
        output = await planner.plan("generate new workflow")
        assert isinstance(output.plan, GeneratePlan)
        assert output.plan.spec["nodes"]

    @pytest.mark.asyncio
    async def test_plan_no_llm_raises(self):
        svc = DiscoveryService()
        planner = WorkflowPlanner(discovery=svc)
        with pytest.raises(RuntimeError, match="No LLM callable"):
            await planner.plan("test")

    @pytest.mark.asyncio
    async def test_plan_invalid_json_retries(self):
        calls = [0]

        async def flaky_llm(*args) -> str:
            calls[0] += 1
            if calls[0] <= 2:
                return "not valid json"
            return json.dumps({"action": "GENERATE", "description": "test", "spec": {}})

        svc = DiscoveryService()
        planner = WorkflowPlanner(discovery=svc, llm_call=flaky_llm, max_retries=3)
        output = await planner.plan("test")
        assert isinstance(output.plan, GeneratePlan)
        assert calls[0] == 3

    @pytest.mark.asyncio
    async def test_plan_validates_workflow_exists(self):
        async def fake_llm(*args) -> str:
            return json.dumps({"action": "REUSE", "workflow_id": "missing"})

        svc = DiscoveryService()
        planner = WorkflowPlanner(
            discovery=svc, llm_call=fake_llm,
            graph_store=FakeGraphStore(),
        )
        output = await planner.plan("test")
        assert not output.review.valid
        assert any("not found" in e for e in output.review.errors)

    @pytest.mark.asyncio
    async def test_execute_reuse(self):
        svc = DiscoveryService()
        graph_data = {"version": "dan_graph_v1", "nodes": []}
        planner = WorkflowPlanner(
            discovery=svc,
            graph_store=FakeGraphStore(graphs={"wf1": graph_data}),
        )
        result = await planner.execute_plan(ReusePlan(workflow_id="wf1"))
        assert result["workflow_id"] == "wf1"
        assert result["graph"] == graph_data


# ---------------------------------------------------------------------------
# Tests: _compile_generate_spec
# ---------------------------------------------------------------------------


class TestCompileGenerateSpec:
    """19-2 task 7-4: Generate-spec compiler validation."""

    def test_basic_compilation(self):
        spec = {
            "nodes": [
                {"node_type": "llm_operator", "name": "Writer", "config": {"prompt_template": "Write"}},
                {"node_type": "llm_operator", "name": "Editor", "config": {"prompt_template": "Edit"}},
            ],
            "edges": [{"source": "Writer", "target": "Editor"}],
        }
        result = WorkflowPlanner._compile_generate_spec(spec)
        assert result["version"] == "dan_graph_v1"
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["entry_points"]
        assert result["exit_points"]

    def test_compiled_graph_is_valid(self):
        """Compiled output must pass Graph model validation."""
        from dan.models.graph import Graph

        spec = {
            "nodes": [
                {"node_type": "llm_operator", "name": "Step 1", "config": {"prompt_template": "Hello"}},
            ],
        }
        result = WorkflowPlanner._compile_generate_spec(spec)
        graph = Graph.model_validate(result)
        assert graph.version == "dan_graph_v1"
        assert len(graph.nodes) == 1

    def test_node_types_supported(self):
        for nt in ("llm_operator", "tool_operator", "code_operator", "gate"):
            spec = {"nodes": [{"node_type": nt, "name": f"{nt}_node"}]}
            result = WorkflowPlanner._compile_generate_spec(spec)
            assert result["nodes"][0]["node_type"] == nt

    def test_unsupported_node_type_raises(self):
        spec = {"nodes": [{"node_type": "magic_node", "name": "Bad"}]}
        with pytest.raises(ValueError, match="Unsupported"):
            WorkflowPlanner._compile_generate_spec(spec)

    def test_empty_nodes_raises(self):
        spec = {"nodes": []}
        with pytest.raises(ValueError, match="non-empty"):
            WorkflowPlanner._compile_generate_spec(spec)

    def test_edge_name_resolution(self):
        spec = {
            "nodes": [
                {"node_type": "llm_operator", "name": "A"},
                {"node_type": "llm_operator", "name": "B"},
            ],
            "edges": [{"source": "A", "target": "B"}],
        }
        result = WorkflowPlanner._compile_generate_spec(spec)
        edge = result["edges"][0]
        assert edge["source_node_id"]
        assert edge["target_node_id"]
        assert edge["source_node_id"] != edge["target_node_id"]

    def test_passthrough_full_graph(self):
        full = {"version": "dan_graph_v1", "nodes": [], "edges": []}
        assert WorkflowPlanner._compile_generate_spec(full) is full

    def test_source_id_target_id_fallback(self):
        spec = {
            "nodes": [
                {"node_type": "llm_operator", "name": "X", "id": "x1"},
                {"node_type": "llm_operator", "name": "Y", "id": "y1"},
            ],
            "edges": [{"source_id": "x1", "target_id": "y1"}],
        }
        result = WorkflowPlanner._compile_generate_spec(spec)
        edge = result["edges"][0]
        assert edge["source_node_id"] == "x1"
        assert edge["target_node_id"] == "y1"

    def test_tool_operator_defaults(self):
        spec = {"nodes": [{"node_type": "tool_operator", "name": "Tool"}]}
        result = WorkflowPlanner._compile_generate_spec(spec)
        node = result["nodes"][0]
        assert node["tool_id"] == "run_python"

    def test_code_operator_defaults(self):
        spec = {"nodes": [{"node_type": "code_operator", "name": "Code"}]}
        result = WorkflowPlanner._compile_generate_spec(spec)
        node = result["nodes"][0]
        assert node["code"] == "result = inputs"
        assert node["language"] == "python"

    def test_gate_defaults(self):
        spec = {"nodes": [{"node_type": "gate", "name": "Decision"}]}
        result = WorkflowPlanner._compile_generate_spec(spec)
        node = result["nodes"][0]
        assert node["gate_mode"] == "if_else"

    def test_llm_defaults_follow_configured_provider(self, monkeypatch):
        monkeypatch.delenv("DAN_LLM_MODEL", raising=False)
        monkeypatch.delenv("DAN_ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("DAN_GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("DAN_OPENAI_API_KEY", "test-key")

        spec = {"nodes": [{"node_type": "llm_operator", "name": "Writer"}]}
        result = WorkflowPlanner._compile_generate_spec(spec)

        assert result["nodes"][0]["model"] == "gpt-4o"

    def test_model_tier_config_maps_to_runtime_policy(self):
        spec = {
            "nodes": [
                {
                    "node_type": "llm_operator",
                    "name": "Review",
                    "config": {"model_tier": "premium"},
                }
            ]
        }

        result = WorkflowPlanner._compile_generate_spec(spec)

        assert result["nodes"][0]["task_tier"] == "critical"
        assert result["nodes"][0]["model_policy"] == {"strategy": "tier"}
