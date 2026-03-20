"""Tests for dan.loader.decompiler — graph-to-markdown round-trip."""

import json
import pytest
from pathlib import Path

from dan.loader.compiler import compile_workflow
from dan.loader.decompiler import (
    decompile_to_markdown,
    _escape_json_for_flow_string,
    _invert_until_condition,
    _slugify,
)
from dan.models.edges import DataEdge

FIXTURES = Path(__file__).parent.parent / "fixtures" / "markdown"


class TestDecompileSimpleWorkflow:
    def test_creates_expected_files(self, tmp_path):
        r = compile_workflow(FIXTURES / "simple_workflow.md")
        assert r.graph is not None
        dr = decompile_to_markdown(r.graph, tmp_path)
        names = sorted(f.name for f in dr.files)
        assert "workflow.md" in names
        assert "generator.md" in names
        assert "planner.md" in names
        assert "searcher.md" in names

    def test_workflow_has_agents_section(self, tmp_path):
        r = compile_workflow(FIXTURES / "simple_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        wf = (tmp_path / "workflow.md").read_text()
        assert "## Agents" in wf
        assert "[generator]" in wf
        assert "[planner]" in wf
        assert "[searcher]" in wf

    def test_workflow_has_flow_section(self, tmp_path):
        r = compile_workflow(FIXTURES / "simple_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        wf = (tmp_path / "workflow.md").read_text()
        assert "## Flow" in wf
        assert "→" in wf

    def test_agent_file_has_frontmatter(self, tmp_path):
        r = compile_workflow(FIXTURES / "simple_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        gen = (tmp_path / "generator.md").read_text()
        assert "---" in gen
        assert "type: llm" in gen
        assert "model:" in gen

    def test_agent_file_has_ports(self, tmp_path):
        r = compile_workflow(FIXTURES / "simple_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        gen = (tmp_path / "generator.md").read_text()
        assert "> Accepts:" in gen or "> Returns:" in gen

    def test_metadata_preserved(self, tmp_path):
        r = compile_workflow(FIXTURES / "simple_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        wf = (tmp_path / "workflow.md").read_text()
        assert "Simple Chain" in wf
        assert "format_version: 1" in wf


class TestDecompileComplexWorkflow:
    def test_creates_agent_files(self, tmp_path):
        r = compile_workflow(FIXTURES / "complex_workflow.md")
        assert r.graph is not None
        dr = decompile_to_markdown(r.graph, tmp_path)
        names = sorted(f.name for f in dr.files)
        assert "workflow.md" in names
        assert "writer.md" in names
        assert "reviewer.md" in names

    def test_flow_has_each(self, tmp_path):
        r = compile_workflow(FIXTURES / "complex_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        wf = (tmp_path / "workflow.md").read_text()
        assert "each(" in wf

    def test_flow_has_loop(self, tmp_path):
        r = compile_workflow(FIXTURES / "complex_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        wf = (tmp_path / "workflow.md").read_text()
        assert "loop(" in wf

    def test_flow_has_if(self, tmp_path):
        r = compile_workflow(FIXTURES / "complex_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        wf = (tmp_path / "workflow.md").read_text()
        assert "if(" in wf

    def test_context_section(self, tmp_path):
        r = compile_workflow(FIXTURES / "complex_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        wf = (tmp_path / "workflow.md").read_text()
        assert "## Context" in wf
        assert "style_guide" in wf
        assert "bibliography" in wf

    def test_retry_policy_preserved(self, tmp_path):
        r = compile_workflow(FIXTURES / "complex_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        reviewer = (tmp_path / "reviewer.md").read_text()
        assert "---" in reviewer


class TestRoundTripSimple:
    def test_node_types_preserved(self, tmp_path):
        r1 = compile_workflow(FIXTURES / "simple_workflow.md")
        assert r1.graph is not None
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None

        types1 = sorted((n.id, n.node_type) for n in r1.graph.nodes if n.node_type != "input")
        types2 = sorted((n.id, n.node_type) for n in r2.graph.nodes if n.node_type != "input")
        assert types1 == types2

    def test_chain_edges_preserved(self, tmp_path):
        r1 = compile_workflow(FIXTURES / "simple_workflow.md")
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None

        def chain_edges(g):
            return sorted(
                (e.source_node_id, e.target_node_id)
                for e in g.edges
                if isinstance(e, DataEdge)
                and "workflow_inputs" not in (e.source_node_id, e.target_node_id)
            )

        assert chain_edges(r1.graph) == chain_edges(r2.graph)

    def test_metadata_preserved(self, tmp_path):
        r1 = compile_workflow(FIXTURES / "simple_workflow.md")
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None
        assert r1.graph.metadata.name == r2.graph.metadata.name

    def test_tags_preserved(self, tmp_path):
        r1 = compile_workflow(FIXTURES / "simple_workflow.md")
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None
        assert sorted(r1.graph.metadata.tags) == sorted(r2.graph.metadata.tags)


class TestRoundTripComplex:
    def test_node_types_preserved(self, tmp_path):
        r1 = compile_workflow(FIXTURES / "complex_workflow.md")
        assert r1.graph is not None
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None

        types1 = sorted((n.id, n.node_type) for n in r1.graph.nodes if n.node_type != "input")
        types2 = sorted((n.id, n.node_type) for n in r2.graph.nodes if n.node_type != "input")
        assert types1 == types2

    def test_context_preserved(self, tmp_path):
        r1 = compile_workflow(FIXTURES / "complex_workflow.md")
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None
        keys1 = sorted(c.key for c in r1.graph.shared_context)
        keys2 = sorted(c.key for c in r2.graph.shared_context)
        assert keys1 == keys2

    def test_gate_nodes_recreated(self, tmp_path):
        r1 = compile_workflow(FIXTURES / "complex_workflow.md")
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None
        gates1 = sorted(n.id for n in r1.graph.nodes if n.node_type == "gate")
        gates2 = sorted(n.id for n in r2.graph.nodes if n.node_type == "gate")
        assert len(gates1) == len(gates2)

    def test_foreach_recreated(self, tmp_path):
        r1 = compile_workflow(FIXTURES / "complex_workflow.md")
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None
        fe1 = [n for n in r1.graph.nodes if n.node_type == "for_each"]
        fe2 = [n for n in r2.graph.nodes if n.node_type == "for_each"]
        assert len(fe1) == len(fe2)


class TestEscapeJsonForFlowString:
    """Unit tests for _escape_json_for_flow_string — JSON embedding in flow kwargs."""

    def test_escapes_backslash_and_quote(self):
        assert _escape_json_for_flow_string('{"a": "b"}') == '{\\"a\\": \\"b\\"}'

    def test_escapes_backslash_first(self):
        # \\n in JSON becomes \\\\n so outer string parses correctly
        raw = json.dumps({"x": "a\nb"})
        escaped = _escape_json_for_flow_string(raw)
        assert "\\\\" in escaped or escaped.count("\\") >= 2


class TestInvertUntilCondition:
    """Unit tests for _invert_until_condition — strips not (...) for loop round-trip."""

    def test_not_x_returns_x(self):
        assert _invert_until_condition('not (x)') == 'x'

    def test_not_not_x_returns_not_x(self):
        assert _invert_until_condition('not (not (x))') == 'not (x)'

    def test_nested_parens(self):
        assert _invert_until_condition('not (a and b)') == 'a and b'

    def test_complex_condition(self):
        assert _invert_until_condition('not (verdict == \'accept\')') == "verdict == 'accept'"

    def test_no_not_returns_unchanged(self):
        assert _invert_until_condition('x > 0') == 'x > 0'

    def test_unbalanced_parens_returns_unchanged(self):
        """Unclosed parens: return original (compiler should never emit these)."""
        assert _invert_until_condition('not (x') == 'not (x'


class TestRoundTripLoop:
    """Round-trip tests for loop(until, state, defaults) — Plan 7-8 Task 1."""

    def test_loop_until_inversion_preserved(self, tmp_path):
        """md with loop(until: cond) → graph → md' → graph': until semantics preserved."""
        r1 = compile_workflow(FIXTURES / "complex_workflow.md")
        assert r1.graph is not None
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None

        gates1 = [n for n in r1.graph.nodes if getattr(n, "gate_mode", None) == "while"]
        gates2 = [n for n in r2.graph.nodes if getattr(n, "gate_mode", None) == "while"]
        assert len(gates1) == len(gates2) == 1
        # Both should have not (cond) in gate.condition (compiler stores negated)
        assert gates1[0].condition == gates2[0].condition

    def test_loop_state_defaults_roundtrip(self, tmp_path):
        """md with loop(until, state, defaults) → graph → md' → graph': semantic equivalence."""
        r1 = compile_workflow(FIXTURES / "loop_roundtrip_workflow.md")
        assert r1.graph is not None
        decompile_to_markdown(r1.graph, tmp_path)
        r2 = compile_workflow(tmp_path / "workflow.md")
        assert r2.graph is not None

        gates1 = [n for n in r1.graph.nodes if getattr(n, "gate_mode", None) == "while"]
        gates2 = [n for n in r2.graph.nodes if getattr(n, "gate_mode", None) == "while"]
        assert len(gates1) == len(gates2) == 1

        g1, g2 = gates1[0], gates2[0]
        assert g1.condition == g2.condition
        assert getattr(g1, "state_schema", None) == getattr(g2, "state_schema", None)
        assert getattr(g1, "state_defaults", None) == getattr(g2, "state_defaults", None)

        wf = (tmp_path / "workflow.md").read_text()
        assert 'until: "count >= 3"' in wf
        assert "state:" in wf
        assert "defaults:" in wf


class TestFileNaming:
    def test_slugification(self):
        assert _slugify("Section Writer") == "section-writer"
        assert _slugify("idea_generator") == "idea-generator"
        assert _slugify("UPPER CASE") == "upper-case"
        assert _slugify("") == "node"

    def test_no_file_collisions(self, tmp_path):
        r = compile_workflow(FIXTURES / "simple_workflow.md")
        dr = decompile_to_markdown(r.graph, tmp_path)
        names = [f.name for f in dr.files]
        assert len(names) == len(set(names))
