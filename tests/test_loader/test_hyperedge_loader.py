"""Tests for hyperedge loading, compilation, and decompilation via the markdown loader."""

import textwrap
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_workflow_dir(tmp_path: Path) -> Path:
    """Create a minimal workflow directory with agents and hyperedge files."""
    # --- Agent files ---
    (tmp_path / "gen.md").write_text(textwrap.dedent("""\
        ---
        type: llm
        model: gpt-4
        ---
        > Accepts: topic (string)
        > Returns: text (string)

        Generate ideas about {topic}.
    """))
    (tmp_path / "refine.md").write_text(textwrap.dedent("""\
        ---
        type: llm
        model: gpt-4
        ---
        > Accepts: input (string)
        > Returns: text (string)

        Refine: {input}
    """))
    return tmp_path


def _write_workflow(tmp_path: Path, body: str) -> Path:
    wf_path = tmp_path / "workflow.md"
    wf_path.write_text(textwrap.dedent(body))
    return wf_path


class TestLoadHyperedge:
    """load_hyperedge() parses standalone .md hyperedge files."""

    def test_skill_file(self, tmp_path: Path):
        skill = tmp_path / "code_review.md"
        skill.write_text(textwrap.dedent("""\
            ---
            type: skill
            name: code_review
            hook: pre_prompt
            ---
            You are an expert code reviewer. Focus on correctness.
        """))
        from dan.loader.parser import load_hyperedge

        spec = load_hyperedge(skill)
        assert spec.name == "code_review"
        assert spec.hyperedge_type == "skill"
        assert spec.hook == "pre_prompt"
        assert "expert code reviewer" in spec.content
        assert spec.config == {}

    def test_guardrail_file_with_severity(self, tmp_path: Path):
        guard = tmp_path / "pii_filter.md"
        guard.write_text(textwrap.dedent("""\
            ---
            type: guardrail
            name: pii_filter
            hook: post_output
            severity: error
            block_on_fail: true
            ---
            Check output for PII (email, phone, SSN). Block if found.
        """))
        from dan.loader.parser import load_hyperedge

        spec = load_hyperedge(guard)
        assert spec.name == "pii_filter"
        assert spec.hyperedge_type == "guardrail"
        assert spec.hook == "post_output"
        assert spec.config == {"severity": "error", "block_on_fail": True}
        assert "PII" in spec.content

    def test_invalid_type_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.md"
        bad.write_text(textwrap.dedent("""\
            ---
            type: banana
            ---
            content
        """))
        from dan.loader.parser import ParseError, load_hyperedge

        with pytest.raises(ParseError, match="Invalid hyperedge type"):
            load_hyperedge(bad)

    def test_defaults(self, tmp_path: Path):
        minimal = tmp_path / "minimal.md"
        minimal.write_text(textwrap.dedent("""\
            ---
            ---
            Just some content.
        """))
        from dan.loader.parser import load_hyperedge

        spec = load_hyperedge(minimal)
        assert spec.hyperedge_type == "skill"
        assert spec.hook == "pre_prompt"
        assert spec.name == "minimal"
        assert spec.attach_globally is False

    def test_attachment_selectors(self, tmp_path: Path):
        f = tmp_path / "scoped.md"
        f.write_text(textwrap.dedent("""\
            ---
            type: style
            name: formal_tone
            hook: pre_prompt
            attach_to_type: llm_operator
            attach_to_tags:
              - customer-facing
            attach_globally: false
            ---
            Write in a formal tone.
        """))
        from dan.loader.parser import load_hyperedge

        spec = load_hyperedge(f)
        assert spec.attach_to_type == ["llm_operator"]
        assert spec.attach_to_tags == ["customer-facing"]
        assert spec.attach_globally is False


class TestCompileWorkflowWithSkills:
    """compile_workflow with ## Skills section references."""

    def test_skills_section_file_reference(self, tmp_workflow_dir: Path):
        skill_file = tmp_workflow_dir / "tone.md"
        skill_file.write_text(textwrap.dedent("""\
            ---
            type: skill
            name: tone
            hook: pre_prompt
            attach_globally: true
            ---
            Write in a professional tone.
        """))
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: skill_test
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [refine](refine.md)

            ## Flow

            gen → refine

            ## Skills

            - tone.md
        """)
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result.graph is not None, [d.message for d in result.diagnostics if d.level == "error"]
        assert len(result.graph.hyperedges) == 1
        he = result.graph.hyperedges[0]
        assert he.hyperedge_type == "skill"
        assert he.name == "tone"
        assert "professional tone" in he.content
        assert he.attach_globally is True

    def test_inline_skill_definition(self, tmp_workflow_dir: Path):
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: inline_test
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [refine](refine.md)

            ## Flow

            gen → refine

            ## Skills

            - inline: "Always respond in JSON format"
        """)
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result.graph is not None
        assert len(result.graph.hyperedges) == 1
        he = result.graph.hyperedges[0]
        assert he.content == "Always respond in JSON format"
        assert he.hyperedge_type == "skill"
        assert he.attach_globally is True


class TestCompileWorkflowWithRules:
    """compile_workflow with ## Rules section + scope overrides."""

    def test_rules_section_with_scope_override(self, tmp_workflow_dir: Path):
        guard_file = tmp_workflow_dir / "safety.md"
        guard_file.write_text(textwrap.dedent("""\
            ---
            type: guardrail
            name: safety
            hook: post_output
            severity: error
            block_on_fail: true
            ---
            Check for unsafe content.
        """))
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: rules_test
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [refine](refine.md)

            ## Flow

            gen → refine

            ## Rules

            - safety.md -> @nodes(gen, refine)
        """)
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result.graph is not None
        assert len(result.graph.hyperedges) == 1
        he = result.graph.hyperedges[0]
        assert he.hyperedge_type == "guardrail"
        assert he.attach_to == ["gen", "refine"]
        assert he.config.get("severity") == "error"

    def test_rules_section_type_scope(self, tmp_workflow_dir: Path):
        style_file = tmp_workflow_dir / "formal.md"
        style_file.write_text(textwrap.dedent("""\
            ---
            type: style
            name: formal
            hook: pre_prompt
            ---
            Use formal English.
        """))
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: type_scope_test
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [refine](refine.md)

            ## Flow

            gen → refine

            ## Rules

            - formal.md -> @type(llm_operator)
        """)
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result.graph is not None
        assert len(result.graph.hyperedges) == 1
        he = result.graph.hyperedges[0]
        assert he.attach_to_type == ["llm_operator"]

    def test_skills_and_rules_combined(self, tmp_workflow_dir: Path):
        (tmp_workflow_dir / "sk.md").write_text(textwrap.dedent("""\
            ---
            type: skill
            name: sk
            hook: pre_prompt
            attach_globally: true
            ---
            Be helpful.
        """))
        (tmp_workflow_dir / "guard.md").write_text(textwrap.dedent("""\
            ---
            type: guardrail
            name: guard
            hook: post_output
            severity: warning
            block_on_fail: false
            ---
            No profanity.
        """))
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: combined
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [refine](refine.md)

            ## Flow

            gen → refine

            ## Skills

            - sk.md

            ## Rules

            - guard.md -> @global
        """)
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result.graph is not None
        assert len(result.graph.hyperedges) == 2
        types = {he.hyperedge_type for he in result.graph.hyperedges}
        assert types == {"skill", "guardrail"}


class TestDecompilerRoundTrip:
    """Decompiler round-trip: graph with hyperedges -> markdown -> graph -> compare."""

    def test_round_trip_preserves_hyperedges(self, tmp_workflow_dir: Path):
        (tmp_workflow_dir / "review.md").write_text(textwrap.dedent("""\
            ---
            type: skill
            name: review
            hook: pre_prompt
            attach_globally: true
            ---
            Review all outputs carefully.
        """))
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: round_trip
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [refine](refine.md)

            ## Flow

            gen → refine

            ## Skills

            - review.md
        """)
        from dan.loader.compiler import compile_workflow
        from dan.loader.decompiler import decompile_to_markdown

        result1 = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result1.graph is not None
        assert len(result1.graph.hyperedges) == 1

        decompile_result = decompile_to_markdown(result1.graph, tmp_workflow_dir / "output")
        wf_files = [f for f in decompile_result.files if f.name == "workflow.md"]
        assert len(wf_files) == 1

        wf_text = wf_files[0].read_text()
        assert "## Skills" in wf_text
        assert "review.md" in wf_text

    def test_inline_round_trip(self, tmp_workflow_dir: Path):
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: inline_rt
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [refine](refine.md)

            ## Flow

            gen → refine

            ## Skills

            - inline: "Be concise"
        """)
        from dan.loader.compiler import compile_workflow
        from dan.loader.decompiler import decompile_to_markdown

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result.graph is not None

        dr = decompile_to_markdown(result.graph, tmp_workflow_dir / "out2")
        wf_text = [f for f in dr.files if f.name == "workflow.md"][0].read_text()
        assert "## Skills" in wf_text
        assert "Be concise" in wf_text

    def test_guardrail_decompiled_under_rules(self, tmp_workflow_dir: Path):
        (tmp_workflow_dir / "safe.md").write_text(textwrap.dedent("""\
            ---
            type: guardrail
            name: safe
            hook: post_output
            severity: warning
            block_on_fail: false
            attach_globally: true
            ---
            Check safety.
        """))
        _write_workflow(tmp_workflow_dir, """\
            ---
            name: guardrail_decomp
            format_version: 1
            ---

            ## Agents

            - [gen](gen.md)
            - [refine](refine.md)

            ## Flow

            gen → refine

            ## Rules

            - safe.md
        """)
        from dan.loader.compiler import compile_workflow
        from dan.loader.decompiler import decompile_to_markdown

        result = compile_workflow(tmp_workflow_dir / "workflow.md")
        assert result.graph is not None

        dr = decompile_to_markdown(result.graph, tmp_workflow_dir / "out3")
        wf_text = [f for f in dr.files if f.name == "workflow.md"][0].read_text()
        assert "## Rules" in wf_text
        assert "safe.md" in wf_text
        assert "## Skills" not in wf_text
