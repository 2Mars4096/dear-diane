"""Tests for hyperedge support in the builder DSL (skill/rule methods, compilation, decompilation)."""

import pytest

from dan.builder import workflow
from dan.models.hyperedges import Hyperedge


class TestBuilderSkillMethod:
    """wf.skill() stores hyperedge specs and build() produces graph.hyperedges."""

    def test_skill_basic(self):
        wf = workflow("skill_test")
        wf.llm("gen", prompt="Hello {input}")
        wf.skill("code_review", "Review all code for correctness.", attach_globally=True)
        graph = wf.build()

        assert len(graph.hyperedges) == 1
        he = graph.hyperedges[0]
        assert he.name == "code_review"
        assert he.hyperedge_type == "skill"
        assert he.hook == "pre_prompt"
        assert "Review all code" in he.content
        assert he.attach_globally is True

    def test_skill_with_selectors(self):
        wf = workflow("selector_test")
        wf.llm("gen", prompt="Hello {input}")
        wf.skill(
            "targeted",
            "Focus on gen node.",
            attach_to=["gen"],
            attach_to_type=["llm_operator"],
        )
        graph = wf.build()

        he = graph.hyperedges[0]
        assert he.attach_to == ["gen"]
        assert he.attach_to_type == ["llm_operator"]
        assert he.attach_globally is False

    def test_skill_chaining(self):
        wf = workflow("chaining")
        wf.llm("a", prompt="{input}")
        result = wf.skill("s1", "content1", attach_globally=True)
        assert result is wf
        wf.skill("s2", "content2", attach_globally=True)
        graph = wf.build()
        assert len(graph.hyperedges) == 2

    def test_skill_propagate_false(self):
        wf = workflow("no_propagate")
        wf.llm("a", prompt="{input}")
        wf.skill("local", "Local only.", attach_globally=True, propagate=False)
        graph = wf.build()

        assert graph.hyperedges[0].propagate is False


class TestBuilderRuleMethod:
    """wf.rule() stores hyperedge specs with type-specific config."""

    def test_guardrail_rule(self):
        wf = workflow("guard_test")
        wf.llm("gen", prompt="{input}")
        wf.rule(
            "pii_check",
            "guardrail",
            "post_output",
            "Check for PII.",
            severity="error",
            block_on_fail=True,
            attach_globally=True,
        )
        graph = wf.build()

        assert len(graph.hyperedges) == 1
        he = graph.hyperedges[0]
        assert he.name == "pii_check"
        assert he.hyperedge_type == "guardrail"
        assert he.hook == "post_output"
        assert he.config == {"severity": "error", "block_on_fail": True}

    def test_style_rule(self):
        wf = workflow("style_test")
        wf.llm("gen", prompt="{input}")
        wf.rule(
            "formal",
            "style",
            "pre_prompt",
            "Use formal English.",
            attach_to_type=["llm_operator"],
        )
        graph = wf.build()

        he = graph.hyperedges[0]
        assert he.hyperedge_type == "style"
        assert he.hook == "pre_prompt"
        assert he.attach_to_type == ["llm_operator"]
        assert he.config == {}

    def test_override_rule(self):
        wf = workflow("override_test")
        wf.llm("gen", prompt="{input}")
        wf.rule(
            "temp_override",
            "override",
            "pre_prompt",
            '{"temperature": 0.1}',
            attach_to=["gen"],
        )
        graph = wf.build()

        he = graph.hyperedges[0]
        assert he.hyperedge_type == "override"
        assert he.attach_to == ["gen"]

    def test_rule_chaining(self):
        wf = workflow("chain_rules")
        wf.llm("a", prompt="{input}")
        result = wf.rule("r1", "guardrail", "post_output", "Rule 1.", attach_globally=True)
        assert result is wf
        wf.rule("r2", "style", "pre_prompt", "Rule 2.", attach_globally=True)
        graph = wf.build()
        assert len(graph.hyperedges) == 2


class TestBuilderHyperedgeIds:
    """Generated hyperedge IDs are deterministic and unique."""

    def test_deterministic_ids(self):
        wf1 = workflow("det1")
        wf1.llm("a", prompt="{input}")
        wf1.skill("s", "content", attach_globally=True)
        g1 = wf1.build()

        wf2 = workflow("det2")
        wf2.llm("a", prompt="{input}")
        wf2.skill("s", "content", attach_globally=True)
        g2 = wf2.build()

        assert g1.hyperedges[0].id == g2.hyperedges[0].id

    def test_different_content_different_ids(self):
        wf = workflow("diff")
        wf.llm("a", prompt="{input}")
        wf.skill("s1", "content A", attach_globally=True)
        wf.skill("s2", "content B", attach_globally=True)
        graph = wf.build()

        assert graph.hyperedges[0].id != graph.hyperedges[1].id


class TestBuilderMixedNodesAndHyperedges:
    """Build a complete graph with nodes, edges, and hyperedges."""

    def test_full_workflow(self):
        wf = workflow("full")
        a = wf.llm("gen", prompt="Generate: {input}")
        b = wf.llm("refine", prompt="Refine: {gen}")
        a >> b
        wf.skill("review", "Review output.", attach_globally=True)
        wf.rule("safety", "guardrail", "post_output", "No harmful content.",
                severity="error", block_on_fail=True, attach_globally=True)
        graph = wf.build()

        assert len(graph.nodes) == 2
        assert len(graph.edges) >= 1
        assert len(graph.hyperedges) == 2
        names = {he.name for he in graph.hyperedges}
        assert names == {"review", "safety"}


class TestBuilderDecompilerHyperedges:
    """Builder decompiler emits wf.skill() / wf.rule() calls."""

    def test_skill_decompile(self):
        from dan.builder.decompiler import decompile

        wf = workflow("decomp_skill")
        wf.llm("gen", prompt="{input}")
        wf.skill("review", "Review carefully.", attach_globally=True)
        graph = wf.build()

        code = decompile(graph)
        assert "wf.skill(" in code
        assert "'review'" in code
        assert "'Review carefully.'" in code
        assert "attach_globally=True" in code

    def test_rule_decompile(self):
        from dan.builder.decompiler import decompile

        wf = workflow("decomp_rule")
        wf.llm("gen", prompt="{input}")
        wf.rule("guard", "guardrail", "post_output", "No PII.",
                severity="error", block_on_fail=True, attach_globally=True)
        graph = wf.build()

        code = decompile(graph)
        assert "wf.rule(" in code
        assert "'guard'" in code
        assert "'guardrail'" in code
        assert "'post_output'" in code
        assert "severity='error'" in code
        assert "block_on_fail=True" in code

    def test_decompiled_code_is_executable(self):
        from dan.builder.decompiler import decompile

        wf = workflow("exec_test")
        wf.llm("gen", prompt="{input}")
        wf.skill("s", "Be helpful.", attach_globally=True)
        original = wf.build()

        code = decompile(original)
        ns: dict = {}
        exec(code, ns)
        rebuilt = ns["graph"]

        assert len(rebuilt.hyperedges) == len(original.hyperedges)
        assert rebuilt.hyperedges[0].name == original.hyperedges[0].name
        assert rebuilt.hyperedges[0].content == original.hyperedges[0].content
