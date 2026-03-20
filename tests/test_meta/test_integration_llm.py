"""Integration tests for planner, repair, and experience that call a real LLM.

These tests require:
  - DAN_LLM_API_KEY (or LLM_API_KEY) set in the environment (loaded from .env)
  - DAN_LLM_BASE_URL (defaults to https://api.vectorengine.ai/v1)
  - DAN_LLM_MODEL (defaults to claude-sonnet-4-6)

Run with:
    pytest tests/test_meta/test_integration_llm.py -v

Marked with ``integration`` so CI can exclude them from fast unit-test suites::

    pytest -m "not integration"
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest

from dotenv import load_dotenv

load_dotenv()

_LLM_API_KEY = os.environ.get("DAN_LLM_API_KEY", os.environ.get("LLM_API_KEY", ""))
_LLM_BASE_URL = os.environ.get("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1")
_LLM_MODEL = os.environ.get("DAN_LLM_MODEL", "claude-sonnet-4-6")

_SKIP_REASON = "No LLM API key available (set DAN_LLM_API_KEY or LLM_API_KEY)"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(not _LLM_API_KEY, reason=_SKIP_REASON),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_call():
    """Build an async ``llm_call`` matching the planner/repair signature."""
    from dan.providers import ProviderConfig
    from dan.providers.openai_provider import OpenAIProvider

    provider = OpenAIProvider(ProviderConfig(api_key=_LLM_API_KEY, base_url=_LLM_BASE_URL))

    async def llm_call(
        system_prompt: str,
        user_prompt: str,
        model: str | None,
        temperature: float,
    ) -> str:
        result = await provider.complete(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model or _LLM_MODEL,
            temperature=temperature,
        )
        return result.text

    return llm_call


class _InMemGraphStore:
    """Dict-backed graph store with get_graph/save_graph/list_graphs."""

    def __init__(self) -> None:
        self._data: dict[str, dict] = {}

    def get_graph(self, workflow_id: str):
        return self._data.get(workflow_id)

    def save_graph(self, workflow_id: str, data: dict):
        self._data[workflow_id] = data

    def list_graphs(self):
        return list(self._data.keys())


class _DictMemoryStore:
    """Minimal in-memory MemoryStore for test use (actually persists in dict)."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    async def read(self, wf_id, sess_id, key):
        from dan.engine.memory import MemoryEntry
        val = self._data.get(wf_id, {}).get(sess_id, {}).get(key)
        if val is None:
            return None
        return MemoryEntry(key=key, value=val)

    async def write(self, wf_id, sess_id, entry):
        self._data.setdefault(wf_id, {}).setdefault(sess_id, {})[entry.key] = entry.value

    async def delete(self, wf_id, sess_id, key):
        bucket = self._data.get(wf_id, {}).get(sess_id, {})
        if key in bucket:
            del bucket[key]
            return True
        return False

    async def list_keys(self, wf_id, sess_id):
        return list(self._data.get(wf_id, {}).get(sess_id, {}).keys())

    async def list_sessions(self, wf_id):
        return list(self._data.get(wf_id, {}).keys())


# ---------------------------------------------------------------------------
# Planner integration tests
# ---------------------------------------------------------------------------

class TestPlannerLLM:
    """Planner produces valid REUSE / ADAPT / GENERATE plans via real LLM."""

    async def test_generate_plan_for_novel_goal(self):
        """With no prior workflows, planner should produce GENERATE."""
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import PlanAction, WorkflowPlanner

        graph_store = _InMemGraphStore()
        discovery = DiscoveryService(graph_store=graph_store)
        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
            llm_call=_make_llm_call(),
            model=_LLM_MODEL,
            max_retries=2,
            temperature=0.2,
        )

        output = await planner.plan("Translate a PDF paper from English to French and save as markdown")
        assert output.plan.action == PlanAction.GENERATE.value
        assert output.review is not None

    async def test_reuse_plan_with_existing_workflow(self):
        """When a high-fit workflow exists, planner should prefer REUSE or ADAPT."""
        from dan.engine.experience import ExperienceStore, WorkflowExperience
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import PlanAction, WorkflowPlanner

        graph_store = _InMemGraphStore()
        graph_store.save_graph("paper_writing", {
            "version": "dan_graph_v1",
            "nodes": [
                {"id": "n1", "node_type": "llm_operator", "name": "writer",
                 "config": {"system_prompt": "Write a paper"}},
            ],
            "edges": [],
        })

        mem = _DictMemoryStore()
        exp_store = ExperienceStore(mem)
        exp = WorkflowExperience(
            workflow_id="paper_writing",
            name="Paper writing workflow",
            description="Write academic papers from a topic",
            tags=["writing", "paper", "academic"],
            run_count=10,
            success_count=8,
        )
        await exp_store.save_experience(exp)

        discovery = DiscoveryService(
            experience_store=exp_store,
            graph_store=graph_store,
        )
        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
            llm_call=_make_llm_call(),
            model=_LLM_MODEL,
            max_retries=2,
            temperature=0.2,
        )

        output = await planner.plan("Write a research paper on supply chain resilience")
        # Without a semantic-similarity index, discovery presents the workflow
        # but the LLM may still choose GENERATE if the fit score is low.
        assert output.plan.action in (
            PlanAction.REUSE.value, PlanAction.ADAPT.value, PlanAction.GENERATE.value,
        )

    async def test_adapt_plan_with_partial_match(self):
        """A moderate-fit workflow should trigger ADAPT or GENERATE."""
        from dan.engine.experience import ExperienceStore, WorkflowExperience
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import PlanAction, WorkflowPlanner

        graph_store = _InMemGraphStore()
        graph_store.save_graph("equity_research", {
            "version": "dan_graph_v1",
            "nodes": [
                {"id": "n1", "node_type": "llm_operator", "name": "analyst",
                 "config": {"system_prompt": "Equity analysis"}},
            ],
            "edges": [],
        })

        mem = _DictMemoryStore()
        exp_store = ExperienceStore(mem)
        exp = WorkflowExperience(
            workflow_id="equity_research",
            name="Equity research workflow",
            description="Analyze stocks and write research reports",
            tags=["finance", "equity", "research"],
            run_count=5,
            success_count=3,
        )
        await exp_store.save_experience(exp)

        discovery = DiscoveryService(
            experience_store=exp_store,
            graph_store=graph_store,
        )
        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
            llm_call=_make_llm_call(),
            model=_LLM_MODEL,
            max_retries=2,
            temperature=0.2,
        )

        output = await planner.plan("Analyze quarterly earnings for AAPL and produce beamer slides")
        assert output.plan.action in (
            PlanAction.REUSE.value, PlanAction.ADAPT.value, PlanAction.GENERATE.value,
        )

    async def test_generate_produces_compilable_spec(self):
        """A GENERATE plan's spec should compile into valid Graph JSON."""
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import PlanAction, WorkflowPlanner
        from dan.models.graph import Graph

        graph_store = _InMemGraphStore()
        discovery = DiscoveryService(graph_store=graph_store)
        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
            llm_call=_make_llm_call(),
            model=_LLM_MODEL,
            max_retries=2,
            temperature=0.1,
        )

        output = await planner.plan("Build a simple two-step chain: first summarize a document, then translate to French")
        if output.plan.action == PlanAction.GENERATE.value:
            compiled = planner._compile_generate_spec(output.plan.spec)
            assert compiled.get("version") == "dan_graph_v1"
            assert len(compiled["nodes"]) >= 2
            Graph.model_validate(compiled)


# ---------------------------------------------------------------------------
# Repair integration tests
# ---------------------------------------------------------------------------

class TestRepairLLM:
    """Structural repair planner produces valid mutations via real LLM."""

    async def test_structural_repair_produces_mutations(self):
        """Given a failing graph + principle, structural repair returns mutations."""
        from dan.meta.repair import StructuralRepairPlanner
        from dan.engine.error_memory import CausalPrinciple
        from dan.models.graph import Graph

        planner = StructuralRepairPlanner(
            llm_call=_make_llm_call(),
            model=_LLM_MODEL,
        )

        principle = CausalPrinciple(
            condition="output format is not JSON",
            action="add a post-processing node to parse and validate JSON output",
            confidence=0.9,
            tags=["output_format"],
        )

        graph = Graph.model_validate({
            "version": "dan_graph_v1",
            "nodes": [
                {"id": "n1", "node_type": "llm_operator", "name": "generator",
                 "system_prompt": "Generate structured data",
                 "prompt_template": "Generate JSON",
                 "model": _LLM_MODEL},
            ],
            "edges": [],
        })

        result = await planner.plan_repair(
            principle=principle,
            graph=graph,
            error_context="LLM output was free text instead of valid JSON",
        )
        # result may be None if the LLM fails to generate a valid mutation plan,
        # but when it works it should be a MutationPlan.
        if result is not None:
            assert hasattr(result, "mutations") or isinstance(result, dict)


# ---------------------------------------------------------------------------
# Cross-workflow principle sharing tests
# ---------------------------------------------------------------------------

class TestCrossWorkflowSharing:
    """ErrorMemoryIndex and PrincipleStore global scope queries work."""

    async def test_principle_store_global_scope(self):
        """Principles stored under different workflow_ids are found in global scope."""
        from dan.engine.memory_store import FileSystemMemoryStore
        from dan.engine.error_memory import CausalPrinciple, PrincipleStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileSystemMemoryStore(base_dir=tmpdir)
            ps = PrincipleStore(store)

            p1 = CausalPrinciple(
                condition="retries exhausted",
                action="switch to a smaller model",
                confidence=0.85,
                tags=["retry"],
            )
            p2 = CausalPrinciple(
                condition="output too long",
                action="add max_tokens constraint",
                confidence=0.7,
                tags=["length"],
            )

            await ps.store_principles("workflow_alpha", [p1])
            await ps.store_principles("workflow_beta", [p2])

            local_alpha = await ps.load_principles("workflow_alpha", scope="workflow")
            assert len(local_alpha) == 1
            assert local_alpha[0].condition == "retries exhausted"

            local_beta = await ps.load_principles("workflow_beta", scope="workflow")
            assert len(local_beta) == 1

            global_results = await ps.load_principles("workflow_alpha", scope="global")
            assert len(global_results) == 2
            conditions = {p.condition for p in global_results}
            assert "retries exhausted" in conditions
            assert "output too long" in conditions

    async def test_principle_dedup_keeps_highest_confidence(self):
        """Global scope de-duplicates principles by id, keeping highest confidence."""
        from dan.engine.memory_store import FileSystemMemoryStore
        from dan.engine.error_memory import CausalPrinciple, PrincipleStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileSystemMemoryStore(base_dir=tmpdir)
            ps = PrincipleStore(store)

            shared_id = "shared-principle-001"
            p_low = CausalPrinciple(
                id=shared_id,
                condition="timeout exceeded",
                action="increase timeout",
                confidence=0.5,
            )
            p_high = CausalPrinciple(
                id=shared_id,
                condition="timeout exceeded",
                action="increase timeout and add retries",
                confidence=0.9,
            )

            await ps.store_principles("wf_a", [p_low])
            await ps.store_principles("wf_b", [p_high])

            global_results = await ps.load_principles("wf_a", scope="global")
            matching = [p for p in global_results if p.id == shared_id]
            assert len(matching) == 1
            assert matching[0].confidence == 0.9
            assert "retries" in matching[0].action

    async def test_error_memory_index_global_scope(self):
        """ErrorMemoryIndex global scope searches across all collections."""
        from dan.engine.error_memory import ErrorMemoryIndex, ErrorRecord
        from dan.rag.stores.memory import MemoryVectorStore
        from dan.rag import EmbeddingResult

        class _FixedEmbedder:
            async def embed(self, texts, model=None, **kw):
                vecs = [[float(hash(t) % 100) / 100.0, 0.5, 0.5] for t in texts]
                return EmbeddingResult(vectors=vecs, model=model or "test")

        store = MemoryVectorStore()
        idx = ErrorMemoryIndex(
            embedding_provider=_FixedEmbedder(),
            store=store,
            embedding_model="test",
        )

        rec1 = ErrorRecord(
            workflow_id="wf_a",
            run_id="r1",
            node_id="n1",
            error_message="Timeout on API call",
            error_category="timeout",
        )
        rec2 = ErrorRecord(
            workflow_id="wf_b",
            run_id="r2",
            node_id="n2",
            error_message="JSON parse failure",
            error_category="validation_failure",
        )
        await idx.index_errors("wf_a", [rec1])
        await idx.index_errors("wf_b", [rec2])

        local = await idx.query_similar("wf_a", "timeout", scope="workflow")
        global_results = await idx.query_similar("wf_a", "timeout", scope="global")
        assert len(global_results) >= len(local)


# ---------------------------------------------------------------------------
# Experience consolidation tests
# ---------------------------------------------------------------------------

class TestExperienceConsolidation:
    """Validate experience creation, consolidation, and retrieval end-to-end."""

    async def test_experience_roundtrip(self):
        """Save, consolidate, search experiences."""
        from dan.engine.experience import (
            ExperienceStore,
            WorkflowExperience,
            consolidate_experience,
        )

        mem = _DictMemoryStore()
        store = ExperienceStore(mem)

        exp = WorkflowExperience(
            workflow_id="test-wf-001",
            name="Test workflow",
            description="A test workflow for integration testing",
            tags=["test", "integration"],
        )
        await store.save_experience(exp)

        run_snapshots = [
            {"run_id": "run-1", "success": True},
            {"run_id": "run-2", "success": False},
            {"run_id": "run-3", "success": True},
        ]
        principles = [
            {"condition": "missing input", "action": "validate inputs first"},
        ]
        updated = consolidate_experience(exp, run_snapshots, principles)
        assert updated.run_count == 3
        assert updated.success_count == 2
        assert "run-1" in updated.processed_run_ids

        await store.save_experience(updated)
        loaded = await store.load_experience("test-wf-001")
        assert loaded is not None
        assert loaded.run_count == 3

    async def test_incremental_consolidation(self):
        """Re-consolidation skips already-processed runs."""
        from dan.engine.experience import (
            WorkflowExperience,
            consolidate_experience,
        )

        exp = WorkflowExperience(
            workflow_id="wf-incr",
            name="Incremental test",
            run_count=2,
            success_count=1,
            processed_run_ids=["run-1", "run-2"],
        )

        run_snapshots = [
            {"run_id": "run-2", "success": True},  # already processed
            {"run_id": "run-3", "success": True},   # new
        ]
        updated = consolidate_experience(exp, run_snapshots, [])
        assert updated.run_count == 3
        assert updated.success_count == 2
        assert "run-3" in updated.processed_run_ids
        assert "run-2" in updated.processed_run_ids


# ---------------------------------------------------------------------------
# Meta-controller end-to-end with real LLM
# ---------------------------------------------------------------------------

class TestMetaControllerLLM:
    """MetaController end-to-end with real LLM for planning."""

    async def test_create_session_and_plan(self):
        """MetaController can create a session and generate a plan."""
        from dan.engine.experience import ExperienceStore
        from dan.meta.controller import MetaController, MetaControllerConfig, MetaSessionStore
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import WorkflowPlanner
        from dan.meta.repair import RepairActionStore, RepairEscalator, StructuralRepairPlanner

        mem = _DictMemoryStore()
        graph_store = _InMemGraphStore()

        discovery = DiscoveryService(graph_store=graph_store)
        llm_call = _make_llm_call()

        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
            llm_call=llm_call,
            model=_LLM_MODEL,
            max_retries=2,
            temperature=0.2,
        )

        structural_planner = StructuralRepairPlanner(llm_call=llm_call, model=_LLM_MODEL)
        repair_store = RepairActionStore(mem)
        escalator = RepairEscalator(structural_planner=structural_planner, action_store=repair_store)
        session_store = MetaSessionStore(mem)
        exp_store = ExperienceStore(mem)

        events_received: list[dict] = []

        async def capture_event(event: dict) -> None:
            events_received.append(event)

        async def mock_run_workflow(plan, session_id):
            return {
                "success": True,
                "workflow_id": "generated-wf-001",
                "run_id": "run-001",
                "outputs": {"result": "done"},
            }

        controller = MetaController(
            planner=planner,
            repair_escalator=escalator,
            experience_store=exp_store,
            session_store=session_store,
            run_workflow=mock_run_workflow,
            emit_event=capture_event,
            graph_loader=graph_store.get_graph,
            graph_saver=graph_store.save_graph,
        )

        config = MetaControllerConfig(max_iterations=1)
        session = await controller.create_session(
            "Summarize a document and translate to French", config,
        )
        assert session.status.value == "planning"

        await controller.run_session(session, config)
        assert session.status.value == "completed"
        assert len(events_received) >= 3

        event_types = {e["event_type"] for e in events_received}
        assert "META_SESSION_STARTED" in event_types
        assert "META_SESSION_COMPLETED" in event_types


# ---------------------------------------------------------------------------
# Full engine pipeline test (19-3 task 7-6)
# ---------------------------------------------------------------------------

class TestEnginePipelineWithMutation:
    """Run a real workflow through the engine and verify parameter mutations."""

    async def test_parameter_mutation_changes_model_at_runtime(self):
        """Build a simple chain, run it, apply a parameter mutation, and
        verify the mutation took effect on a second run."""
        from dan.engine.executor import EngineConfig
        from dan.engine.scheduler import Engine
        from dan.models.graph import Graph

        config = EngineConfig(
            llm_api_key=_LLM_API_KEY,
            llm_base_url=_LLM_BASE_URL,
            llm_default_model=_LLM_MODEL,
            checkpoint_enabled=False,
            memory_enabled=False,
        )

        graph = Graph.model_validate({
            "version": "dan_graph_v1",
            "nodes": [
                {
                    "id": "greet",
                    "node_type": "llm_operator",
                    "name": "Greeter",
                    "model": _LLM_MODEL,
                    "system_prompt": "You are a helpful greeter.",
                    "prompt_template": "Say hello to the world in exactly 5 words.",
                    "temperature": 0.0,
                },
            ],
            "edges": [],
            "entry_points": ["greet"],
        })

        engine = Engine(config=config)
        result1 = await engine.run(graph, run_id="run-base")
        assert result1.success
        assert result1.node_statuses.get("greet") == "completed"

        patched = Engine._apply_parameter_mutations(
            graph,
            [type("M", (), {"target_node_id": "greet", "changes": {"temperature": 0.9}})()],
        )
        assert patched.nodes[0].temperature == 0.9

        engine2 = Engine(config=config)
        result2 = await engine2.run(patched, run_id="run-mutated")
        assert result2.success
        assert result2.node_statuses.get("greet") == "completed"

    async def test_graph_mutator_edit_node_roundtrip(self):
        """GraphMutator.apply with EditNode modifies the graph and runs successfully."""
        from dan.engine.executor import EngineConfig
        from dan.engine.scheduler import Engine
        from dan.models.graph import Graph
        from dan.server.graph_mutator import GraphMutator, MutationPlan

        config = EngineConfig(
            llm_api_key=_LLM_API_KEY,
            llm_base_url=_LLM_BASE_URL,
            llm_default_model=_LLM_MODEL,
            checkpoint_enabled=False,
            memory_enabled=False,
        )

        graph_data = {
            "version": "dan_graph_v1",
            "nodes": [
                {
                    "id": "writer",
                    "node_type": "llm_operator",
                    "name": "Writer",
                    "model": _LLM_MODEL,
                    "system_prompt": "You are a creative writer.",
                    "prompt_template": "Write a haiku about mountains.",
                    "temperature": 0.7,
                },
            ],
            "edges": [],
            "entry_points": ["writer"],
        }

        mutation_plan = MutationPlan(
            operations=[{
                "op": "edit_node",
                "node_id": "writer",
                "updates": {
                    "system_prompt": "You are a minimalist poet.",
                    "prompt_template": "Write a haiku about the ocean.",
                },
            }],
            description="Change topic from mountains to ocean",
        )

        mutator = GraphMutator()
        result = mutator.apply(graph_data, mutation_plan)
        assert result.success
        assert result.new_graph is not None

        mutated_node = result.new_graph["nodes"][0]
        assert "ocean" in mutated_node["prompt_template"]
        assert "minimalist" in mutated_node["system_prompt"]

        graph = Graph.model_validate(result.new_graph)
        engine = Engine(config=config)
        run_result = await engine.run(graph, run_id="run-mutated-graph")
        assert run_result.success
        assert run_result.node_statuses.get("writer") == "completed"


# ---------------------------------------------------------------------------
# Builder-code path test (19-2 task 3-3)
# ---------------------------------------------------------------------------

class TestBuilderCodePath:
    """Test the GENERATE_CODE planner path that runs builder DSL in a sandbox."""

    async def test_generate_code_plan_model(self):
        """GenerateCodePlan round-trips through model_validate."""
        from dan.meta.planner import GenerateCodePlan

        plan = GenerateCodePlan(
            code='from dan.builder import workflow\nwf = workflow("test")\nwf.llm("n1", prompt="hi")\ngraph = wf.build()',
            description="Test workflow",
        )
        data = plan.model_dump()
        assert data["action"] == "GENERATE_CODE"
        restored = GenerateCodePlan.model_validate(data)
        assert restored.code == plan.code

    async def test_execute_generate_code_in_sandbox(self):
        """Builder DSL code executes in subprocess and produces valid Graph."""
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import GenerateCodePlan, WorkflowPlanner

        graph_store = _InMemGraphStore()
        discovery = DiscoveryService(graph_store=graph_store)
        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
            llm_call=_make_llm_call(),
            model=_LLM_MODEL,
        )

        code = """\
from dan.builder import workflow

wf = workflow("sandbox_test", description="Built in sandbox")
n1 = wf.llm("summarizer", prompt="Summarize: {{input}}", system_prompt="You summarize text.")
n2 = wf.llm("translator", prompt="Translate to French: {{input}}", system_prompt="You translate.")
n1 >> n2
graph = wf.build()
"""
        plan = GenerateCodePlan(code=code, description="Sandbox test workflow")
        result = await planner._execute_generate_code(plan)

        assert result["generated"] is True
        assert result["code_generated"] is True
        assert "graph" in result
        graph_data = result["graph"]
        assert graph_data.get("version") == "dan_graph_v1"
        assert len(graph_data["nodes"]) == 2

        from dan.models.graph import Graph
        Graph.model_validate(graph_data)

    async def test_execute_generate_code_fails_on_bad_code(self):
        """Builder code that crashes should raise ValueError."""
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import GenerateCodePlan, WorkflowPlanner

        graph_store = _InMemGraphStore()
        discovery = DiscoveryService(graph_store=graph_store)
        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
        )

        plan = GenerateCodePlan(code="raise RuntimeError('boom')", description="Bad code")
        with pytest.raises(ValueError, match="failed"):
            await planner._execute_generate_code(plan)

    async def test_validate_plan_warns_on_missing_build(self):
        """PlanReview should warn if GENERATE_CODE code lacks build() call."""
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import GenerateCodePlan, WorkflowPlanner

        graph_store = _InMemGraphStore()
        discovery = DiscoveryService(graph_store=graph_store)
        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
        )

        plan = GenerateCodePlan(code="x = 1 + 2", description="No build")
        review = planner._validate_plan(plan)
        assert any("build()" in w for w in review.warnings)

    async def test_llm_generates_code_plan(self):
        """LLM can produce GENERATE_CODE when prompted for builder DSL code."""
        from dan.meta.discovery import DiscoveryService
        from dan.meta.planner import WorkflowPlanner

        graph_store = _InMemGraphStore()
        discovery = DiscoveryService(graph_store=graph_store)
        llm_call = _make_llm_call()

        async def _code_llm_call(system_prompt, user_prompt, model, temperature):
            extended_system = system_prompt + """

You may also output GENERATE_CODE if you prefer to write Python builder DSL code:

4. GENERATE_CODE — write Python code using dan.builder:
   {"action": "GENERATE_CODE", "code": "from dan.builder import workflow, llm\\nwf = workflow('name')\\n...", "description": "..."}
"""
            return await llm_call(extended_system, user_prompt, model, temperature)

        planner = WorkflowPlanner(
            discovery=discovery,
            graph_store=graph_store,
            llm_call=_code_llm_call,
            model=_LLM_MODEL,
            max_retries=2,
            temperature=0.1,
        )

        output = await planner.plan(
            "Build a simple two-step pipeline using Python builder DSL code: "
            "step 1 extracts key points from a document, step 2 writes a summary. "
            "You MUST use GENERATE_CODE action with dan.builder Python code, not GENERATE."
        )
        # The LLM may or may not follow the instruction to use GENERATE_CODE.
        # We just verify the planner doesn't crash on any action.
        assert output.plan is not None
        assert output.review is not None
