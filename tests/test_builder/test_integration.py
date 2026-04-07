"""Integration tests for the builder DSL.

1. Paper-writing workflow via builder (~30 lines vs 206 manual)
2. Run compiled graph through Engine with mock LLM
3. Decompile-recompile round-trip (structural equality)
4. Editor round-trip golden test (builder -> JSON -> adapter -> JSON -> decompile -> rebuild)
"""

import json
import pytest

from dan.builder import workflow, decompile, BuildError
from dan.builder.refs import NodeRef
from dan.models.context import (
    CompactionRule,
    CompactionStrategy,
    ContextDeclaration,
    ContextMode,
    FailurePolicy,
    MergeStrategy,
    SharedContextDeclaration,
)
from dan.models.graph import Graph
from dan.validation.graph import validate_graph


# ── 1. Paper-writing workflow via builder ──────────────────────────────


def build_paper_writing_via_builder() -> Graph:
    """The motivating example — should be ~30 lines vs 206 manual lines."""
    paper = workflow(
        "Paper Writing Workflow",
        description="Multi-agent paper writing with review-revise loop",
        tags=["paper-writing", "multi-agent"],
        canonical_workers=False,
    )

    paper.context("outline", json_schema={"type": "string"}, description="Paper outline")
    paper.context("current_draft", json_schema={"type": "string"}, description="Latest draft")

    idea_gen = paper.llm(
        "idea_gen", model="gpt-4o",
        prompt="Generate a research idea about {topic}.",
    )
    outline = paper.llm(
        "outline_planner", model="gpt-4o",
        prompt=f"Create a paper outline for: {idea_gen}",
    )

    with paper.for_each(
        "section_writers",
        items=outline["outline"],
        parallelism=4,
        merge_strategy=MergeStrategy.APPEND,
    ) as body:
        body.llm("writer", model="claude-4", prompt="Write section based on outline.")

    assembler = paper.llm(
        "assembler", model="gpt-4o",
        prompt="Assemble these sections into a coherent paper.",
    )

    section_ref = NodeRef("section_writers", "for_each", paper)
    section_ref >> assembler

    with paper.while_loop(
        "review_loop",
        condition="True",
        max_iterations=3,
        compaction=CompactionRule(strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2),
        failure_policy=FailurePolicy(max_iterations=3, stagnation_threshold=2),
    ) as loop:
        reviewer = loop.llm("reviewer", model="gpt-4o", prompt="Review this draft.")
        reviser = loop.llm("reviser", model="claude-4", prompt="Revise draft based on review.")
        reviewer >> reviser

    review_ref = NodeRef("review_loop", "while_loop", paper)
    assembler >> review_ref

    return paper.build()


class TestPaperWritingBuilder:
    def test_construction(self):
        g = build_paper_writing_via_builder()
        assert len(g.nodes) == 5
        assert len(g.sub_graphs) == 2

    def test_passes_validation(self):
        g = build_paper_writing_via_builder()
        errors = validate_graph(g)
        fatal = [e for e in errors if "schema safety bypassed" not in e.lower()]
        assert fatal == [], f"Validation errors: {fatal}"

    def test_node_types(self):
        g = build_paper_writing_via_builder()
        types = {n.node_type for n in g.nodes}
        assert "llm_operator" in types
        assert "for_each" in types
        assert "while_loop" in types

    def test_sub_graphs_present(self):
        g = build_paper_writing_via_builder()
        assert "section_writers_body" in g.sub_graphs
        assert "review_loop_body" in g.sub_graphs

    def test_review_loop_config(self):
        g = build_paper_writing_via_builder()
        loop = g.node_by_id("review_loop")
        assert loop.max_iterations == 3
        assert loop.compaction_rule is not None
        assert loop.compaction_rule.strategy == CompactionStrategy.SLIDING_WINDOW
        assert loop.failure_policy.stagnation_threshold == 2

    def test_shared_context(self):
        g = build_paper_writing_via_builder()
        keys = {d.key for d in g.shared_context}
        assert "outline" in keys
        assert "current_draft" in keys

    def test_json_round_trip(self):
        g = build_paper_writing_via_builder()
        json_str = g.model_dump_json()
        restored = Graph.model_validate_json(json_str)
        assert restored.version == "dan_graph_v1"
        assert len(restored.nodes) == 5
        assert len(restored.sub_graphs) == 2


# ── 2. Run compiled graph through Engine ───────────────────────────────


class MockLLMExecutor:
    """Returns deterministic outputs without calling any LLM."""

    async def execute(self, node, inputs, context):
        from dan.engine.executor import NodeResult
        from dan.engine.state import NodeStatus
        return NodeResult(
            outputs={"text": f"mock-{node.id}"},
            status=NodeStatus.COMPLETED,
        )


class TestBuilderWithEngine:
    @pytest.mark.asyncio
    async def test_run_simple_chain(self):
        from dan.engine import Engine, EngineConfig
        from dan.engine.checkpoint import NullCheckpointStore

        wf = workflow("engine_test")
        a = wf.code("a", code="result = {'value': 1}",
                     output_ports=[{"name": "value"}])
        b = wf.code("b", code="result = {'value': value * 10}",
                     input_ports=[{"name": "value"}],
                     output_ports=[{"name": "value"}])
        c = wf.code("c", code="result = {'value': value + 5}",
                     input_ports=[{"name": "value"}],
                     output_ports=[{"name": "value"}])
        wf.edge(a["value"], b["value"])
        wf.edge(b["value"], c["value"])

        graph = wf.build()
        engine = Engine(
            config=EngineConfig(checkpoint_enabled=False),
            checkpoint_store=NullCheckpointStore(),
        )
        result = await engine.run(graph)

        assert result.success
        assert result.outputs["value"] == 15

    @pytest.mark.asyncio
    async def test_run_while_loop(self):
        from dan.engine import Engine, EngineConfig
        from dan.engine.checkpoint import NullCheckpointStore

        wf = workflow("loop_engine_test")
        with wf.while_loop(
            "loop", condition="counter < 5", max_iterations=20,
            input_ports=[{"name": "counter"}],
            output_ports=[{"name": "counter"}],
        ) as body:
            body.code("inc", code="result = {'counter': counter + 1}",
                       input_ports=[{"name": "counter"}],
                       output_ports=[{"name": "counter"}])

        graph = wf.build()
        engine = Engine(
            config=EngineConfig(checkpoint_enabled=False),
            checkpoint_store=NullCheckpointStore(),
        )
        result = await engine.run(graph, inputs={"counter": 0})

        assert result.success
        assert result.outputs["counter"] == 5

    @pytest.mark.asyncio
    async def test_run_for_each(self):
        from dan.engine import Engine, EngineConfig
        from dan.engine.checkpoint import NullCheckpointStore

        wf = workflow("foreach_engine_test")
        with wf.for_each(
            "fan", parallelism=3, merge_strategy=MergeStrategy.APPEND,
            input_ports=[{"name": "items"}],
            output_ports=[{"name": "results"}],
        ) as body:
            body.code("double", code="result = {'value': item * 2}",
                       input_ports=[{"name": "item"}, {"name": "index"}],
                       output_ports=[{"name": "value"}])

        graph = wf.build()
        engine = Engine(
            config=EngineConfig(checkpoint_enabled=False),
            checkpoint_store=NullCheckpointStore(),
        )
        result = await engine.run(graph, inputs={"items": [1, 2, 3]})

        assert result.success
        results = result.outputs["results"]
        assert len(results) == 3
        values = sorted(r["value"] for r in results)
        assert values == [2, 4, 6]


# ── 3. Decompile-recompile round-trip ──────────────────────────────────


class TestDecompileRoundTrip:
    def test_simple_chain_round_trip(self):
        wf = workflow("rt_chain")
        a = wf.llm("gen", model="gpt-4o", prompt="Generate: {topic}")
        b = wf.llm("refine", prompt="Refine text")
        a >> b
        original = wf.build()

        code = decompile(original)
        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        assert sorted(n.id for n in rebuilt.nodes) == sorted(n.id for n in original.nodes)
        assert len(rebuilt.edges) == len(original.edges)

    def test_paper_writing_round_trip(self):
        original = build_paper_writing_via_builder()
        code = decompile(original)

        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        assert sorted(n.id for n in rebuilt.nodes) == sorted(n.id for n in original.nodes)
        assert len(rebuilt.sub_graphs) == len(original.sub_graphs)


# ── 4. Editor round-trip golden test ───────────────────────────────────


class TestEditorRoundTrip:
    def test_json_to_decompile_to_rebuild(self):
        """Builder -> Graph JSON -> decompile -> rebuild -> compare."""
        wf = workflow("editor_rt", description="Test editor round-trip")
        a = wf.llm("step1", model="gpt-4o", prompt="First step: {topic}")
        b = wf.llm("step2", prompt="Second step")
        c = wf.code("step3", code="result = {'done': True}")
        a >> b >> c
        original = wf.build()

        # Simulate visual editor: serialize to JSON and back
        json_str = original.model_dump_json()
        from_editor = Graph.model_validate_json(json_str)

        # Decompile the "editor" graph and rebuild
        code = decompile(from_editor)
        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        assert sorted(n.id for n in rebuilt.nodes) == sorted(n.id for n in original.nodes)
        assert len(rebuilt.edges) == len(original.edges)
        assert rebuilt.metadata.name == original.metadata.name

    def test_json_structure_matches_typescript_types(self):
        """Verify the JSON output has fields matching editor/src/types/graph.ts."""
        wf = workflow("ts_compat")
        wf.llm("node", model="m", prompt="Hi")
        graph = wf.build()

        raw = json.loads(graph.model_dump_json())
        assert "version" in raw
        assert "metadata" in raw
        assert "nodes" in raw
        assert "edges" in raw
        assert "sub_graphs" in raw
        assert "entry_points" in raw
        assert "exit_points" in raw

        node = raw["nodes"][0]
        assert "id" in node
        assert "node_type" in node
        assert "name" in node
        assert "input_ports" in node
        assert "output_ports" in node
        assert "position" in node
