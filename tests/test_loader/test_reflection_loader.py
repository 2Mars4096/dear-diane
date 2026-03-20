"""Tests for reflection node loading from markdown."""

import textwrap
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_workflow_dir(tmp_path: Path) -> Path:
    """Create a minimal workflow directory with a reflection agent."""
    (tmp_path / "analyze.md").write_text(textwrap.dedent("""\
        ---
        type: reflection
        model: claude-sonnet-4
        reflection_prompt: Distill run errors into causal principles.
        source: last_n_runs
        output_format: principles
        max_principles: 8
        ---
        > Accepts: input (string)
        > Returns: principles (string)

        Analyze the provided run history and extract reusable principles.
    """))
    (tmp_path / "gen.md").write_text(textwrap.dedent("""\
        ---
        type: llm
        model: gpt-4
        ---
        > Accepts: topic (string)
        > Returns: text (string)

        Generate: {topic}
    """))
    return tmp_path


def _write_workflow(tmp_path: Path, body: str) -> Path:
    wf_path = tmp_path / "workflow.md"
    wf_path.write_text(textwrap.dedent(body))
    return wf_path


class TestReflectionLoader:
    """Markdown with type: reflection compiles correctly."""

    def test_reflection_agent_compiles(self, tmp_workflow_dir: Path):
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: reflection_test
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [analyze](analyze.md)

            ## Flow

            gen → analyze
        """)
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert not any(d.level == "error" for d in result.diagnostics)
        graph = result.graph
        assert graph is not None

        analyze = graph.node_by_id("analyze")
        assert analyze is not None
        assert analyze.node_type == "reflection"
        assert analyze.reflection_model == "claude-sonnet-4"
        assert "Distill run errors" in analyze.reflection_prompt
        assert analyze.source == "last_n_runs"
        assert analyze.output_format == "principles"
        assert analyze.max_principles == 8

    def test_reflection_minimal(self, tmp_path: Path):
        (tmp_path / "minimal_reflect.md").write_text(textwrap.dedent("""\
            ---
            type: reflection
            ---

            Minimal reflection agent.
        """))
        _write_workflow(tmp_path, """\
            ---
            name: minimal
            format_version: 1
            ---

            ## Agents

            - [minimal_reflect](minimal_reflect.md)

            ## Flow

        """)
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(tmp_path / "workflow.md")
        graph = result.graph
        assert graph is not None
        node = graph.node_by_id("minimal_reflect")
        assert node is not None
        assert node.node_type == "reflection"
        assert node.source == "last_run"
        assert node.output_format == "principles"


class TestReflectionLoaderDecompile:
    """Loader decompile → markdown → recompile round-trip."""

    def test_reflection_decompile_recompile(self, tmp_workflow_dir: Path):
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: roundtrip
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [analyze](analyze.md)

            ## Flow

            gen → analyze
        """)
        from dan.loader.compiler import compile_workflow
        from dan.loader.decompiler import decompile_to_markdown

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result.graph is not None
        graph = result.graph

        out_dir = tmp_workflow_dir / "decompiled"
        decompile_to_markdown(graph, out_dir)

        # Recompile from decompiled markdown
        result2 = compile_workflow(out_dir / "workflow.md")
        assert not any(d.level == "error" for d in result2.diagnostics)
        graph2 = result2.graph
        assert graph2 is not None

        orig = graph.node_by_id("analyze")
        recomp = graph2.node_by_id("analyze")
        assert orig is not None and recomp is not None
        assert recomp.node_type == "reflection"
        assert recomp.reflection_model == orig.reflection_model
        assert recomp.source == orig.source
        assert recomp.max_principles == orig.max_principles
