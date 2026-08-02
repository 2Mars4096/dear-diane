"""Tests for reflection node support in the builder DSL."""

import pytest

from dan.builder import workflow
from dan.models.nodes import ReflectionNode


class TestReflectionBuilderMethod:
    """wf.reflection() creates correct node and compiles to ReflectionNode."""

    def test_reflection_basic(self):
        wf = workflow("reflection_test", canonical_workers=False)
        r = wf.reflection("analyze")
        graph = wf.build()

        node = graph.node_by_id("analyze")
        assert node is not None
        assert isinstance(node, ReflectionNode)
        assert node.node_type == "reflection"
        assert node.reflection_prompt == ""
        assert node.reflection_model is None
        assert node.source == "last_run"
        assert node.output_format == "principles"
        assert node.max_principles == 10
        assert node.min_confidence == 0.3
        assert node.dedup_strategy == "embedding_similarity"

    def test_reflection_with_options(self):
        wf = workflow("reflection_options", canonical_workers=False)
        wf.reflection(
            "analyze",
            reflection_prompt="Distill errors into principles.",
            reflection_model="claude-sonnet-4",
            source="last_n_runs",
            source_config={"n": 3},
            output_format="rules",
            max_principles=5,
            min_confidence=0.5,
            dedup_strategy="exact_key",
            name="Error Analyzer",
            description="Post-run analysis",
        )
        graph = wf.build()

        node = graph.node_by_id("analyze")
        assert node is not None
        assert isinstance(node, ReflectionNode)
        assert node.reflection_prompt == "Distill errors into principles."
        assert node.reflection_model == "claude-sonnet-4"
        assert node.source == "last_n_runs"
        assert node.source_config == {"n": 3}
        assert node.output_format == "rules"
        assert node.max_principles == 5
        assert node.min_confidence == 0.5
        assert node.dedup_strategy == "exact_key"
        assert node.name == "Error Analyzer"
        assert node.description == "Post-run analysis"

    def test_reflection_chaining(self):
        wf = workflow("reflection_chain", canonical_workers=False)
        a = wf.llm("gen", prompt="{input}")
        b = wf.reflection("analyze")
        a >> b
        graph = wf.build()

        assert graph.node_by_id("gen") is not None
        assert graph.node_by_id("analyze") is not None
        edges = [e for e in graph.edges if e.source_node_id == "gen" and e.target_node_id == "analyze"]
        assert len(edges) >= 1


class TestReflectionDecompiler:
    """Builder decompiler emits wf.reflection() and round-trips."""

    def test_reflection_decompile(self):
        from dan.builder.decompiler import decompile

        wf = workflow("decomp_reflection", canonical_workers=False)
        wf.reflection(
            "analyze",
            reflection_prompt="Analyze failures.",
            reflection_model="gpt-4",
        )
        graph = wf.build()

        code = decompile(graph)
        assert "wf.reflection(" in code
        assert "'analyze'" in code
        assert "reflection_prompt=" in code
        assert "reflection_model=" in code

    def test_decompiled_reflection_is_executable(self):
        from dan.builder.decompiler import decompile

        wf = workflow("roundtrip_reflection", canonical_workers=False)
        wf.reflection(
            "analyze",
            reflection_prompt="Distill principles.",
            source="last_n_runs",
            max_principles=7,
        )
        original = wf.build()

        code = decompile(original)
        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        orig_node = original.node_by_id("analyze")
        re_node = rebuilt.node_by_id("analyze")
        assert orig_node is not None and re_node is not None
        assert isinstance(re_node, ReflectionNode)
        assert re_node.reflection_prompt == orig_node.reflection_prompt
        assert re_node.source == orig_node.source
        assert re_node.max_principles == orig_node.max_principles
