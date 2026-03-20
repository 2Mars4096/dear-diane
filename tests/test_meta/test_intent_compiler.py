"""Tests for Plan 32-2: Intent Compiler Expansion."""

from __future__ import annotations

import pytest

from dan.meta.intent_compiler import (
    COVERAGE_CATALOG,
    DOMAIN_PATTERN_PREFERENCES,
    IntentCompiler,
    _infer_tool_id,
)
from dan.meta.intent_schema import (
    ReviewRequirement,
    StageIntent,
    StageType,
    WorkflowIntent,
)


def _make_intent(stages: list[StageIntent], goal: str = "test") -> WorkflowIntent:
    return WorkflowIntent(goal=goal, stages=stages)


def _make_stage(name: str, stage_type: StageType, **kw) -> StageIntent:
    return StageIntent(name=name, stage_type=stage_type, **kw)


# ---------------------------------------------------------------------------
# Coverage catalog
# ---------------------------------------------------------------------------


class TestCoverageCatalog:
    def test_has_12_entries(self):
        assert len(COVERAGE_CATALOG) == 12

    def test_all_entries_have_required_fields(self):
        for name, entry in COVERAGE_CATALOG.items():
            assert "description" in entry, f"{name} missing description"
            assert "stage_types" in entry, f"{name} missing stage_types"
            assert "composable" in entry, f"{name} missing composable"

    def test_original_7_entries_preserved(self):
        original = [
            "linear_chain",
            "review_loop",
            "fan_out_fan_in",
            "rag_qa",
            "data_pipeline",
            "tool_augmented",
            "human_gate",
        ]
        for name in original:
            assert name in COVERAGE_CATALOG, f"Original pattern {name} missing"

    def test_new_5_entries_present(self):
        new = [
            "comparison",
            "research_review",
            "document_pipeline",
            "multi_source_merge",
            "conditional_branch",
        ]
        for name in new:
            assert name in COVERAGE_CATALOG, f"New pattern {name} missing"


# ---------------------------------------------------------------------------
# Intent compiler
# ---------------------------------------------------------------------------


class TestIntentCompiler:
    def test_compile_basic_transform(self):
        compiler = IntentCompiler()
        intent = _make_intent([
            _make_stage("step1", StageType.transform, description="Do something"),
        ])
        code = compiler.compile(intent)
        assert "wf.build()" in code
        assert 'wf.llm(' in code

    def test_compile_accepts_domain_kwarg(self):
        compiler = IntentCompiler()
        intent = _make_intent([
            _make_stage("step1", StageType.transform, description="Do something"),
        ])
        code = compiler.compile(intent, domain="data_analysis")
        assert "wf.build()" in code

    def test_compile_composed_chain_then_review(self):
        compiler = IntentCompiler()
        intent = _make_intent([
            _make_stage("research", StageType.transform, description="Research topic"),
            _make_stage("write", StageType.transform, description="Write report"),
            _make_stage(
                "review",
                StageType.review_loop,
                description="Review draft",
                review=ReviewRequirement(reviewer_prompt="Check quality"),
            ),
        ])
        code = compiler.compile_composed(intent, ["linear_chain", "review_loop"])
        assert "wf.chain" in code or "wf.llm" in code
        assert "wf.build()" in code
        assert "review_loop" in code or "while_loop" in code

    def test_compile_composed_single_pattern(self):
        compiler = IntentCompiler()
        intent = _make_intent([
            _make_stage(
                "review",
                StageType.review_loop,
                description="Quality check",
                review=ReviewRequirement(reviewer_prompt="Check it"),
            ),
        ])
        code = compiler.compile_composed(intent, ["review_loop"])
        assert "wf.review_loop" in code
        assert "wf.build()" in code

    def test_compile_composed_fan_out(self):
        compiler = IntentCompiler()
        intent = _make_intent([
            _make_stage(
                "process_items",
                StageType.fan_out,
                description="Process each item",
                parallelism=4,
            ),
        ])
        code = compiler.compile_composed(intent, ["fan_out_fan_in"])
        assert "for_each" in code
        assert "parallelism=4" in code

    def test_compile_composed_fallback_stages(self):
        compiler = IntentCompiler()
        intent = _make_intent([
            _make_stage("search", StageType.tool_call, description="Search web"),
            _make_stage("analyze", StageType.transform, description="Analyze results"),
        ])
        code = compiler.compile_composed(intent, ["tool_augmented"])
        assert "wf.build()" in code

    def test_compile_composed_with_conditional(self):
        from dan.meta.intent_schema import ConditionalRequirement
        compiler = IntentCompiler()
        intent = _make_intent([
            _make_stage("s1", StageType.transform),
            _make_stage("s2", StageType.conditional, conditional=ConditionalRequirement(condition="x > 0")),
            _make_stage("s3", StageType.transform),
            _make_stage("s4", StageType.transform),
        ])
        code = compiler.compile_composed(intent, ["linear_chain", "conditional_branch", "linear_chain"])
        assert "s1 >> s2_gate" in code
        assert "s2_then >> seg_2" in code
        assert "s2_else >> seg_2" in code

    def test_partition_stages_single_type_greedy(self):
        compiler = IntentCompiler()
        stages = [
            _make_stage("a", StageType.transform),
            _make_stage("b", StageType.transform),
            _make_stage("c", StageType.transform),
        ]
        result = compiler._partition_stages(stages, ["linear_chain"])
        assert len(result) == 1
        assert len(result[0][1]) == 3

    def test_partition_stages_multi_pattern(self):
        compiler = IntentCompiler()
        stages = [
            _make_stage("a", StageType.transform),
            _make_stage("b", StageType.transform),
            _make_stage(
                "c",
                StageType.review_loop,
                review=ReviewRequirement(),
            ),
        ]
        result = compiler._partition_stages(stages, ["linear_chain", "review_loop"])
        assert len(result) == 2
        assert result[0][0] == "linear_chain"
        assert result[1][0] == "review_loop"


# ---------------------------------------------------------------------------
# Domain preferences
# ---------------------------------------------------------------------------


class TestDomainPreferences:
    def test_known_domains_present(self):
        expected = [
            "paper_rendering",
            "literature_review",
            "equity_research",
            "data_analysis",
            "code_generation",
        ]
        for domain in expected:
            assert domain in DOMAIN_PATTERN_PREFERENCES

    def test_preferences_reference_valid_patterns(self):
        for domain, prefs in DOMAIN_PATTERN_PREFERENCES.items():
            for p in prefs:
                assert p in COVERAGE_CATALOG, (
                    f"{domain} references unknown pattern {p}"
                )

    def test_preferences_are_nonempty(self):
        for domain, prefs in DOMAIN_PATTERN_PREFERENCES.items():
            assert len(prefs) > 0, f"{domain} has empty preferences"


def test_infer_tool_id_prefers_web_search_for_research_language() -> None:
    assert _infer_tool_id("Research competitors", "Search the web for recent updates") == "web_search"


def test_infer_tool_id_maps_browse_language_to_web_search() -> None:
    assert _infer_tool_id("Browse sources", "Browse online documentation and summarize it") == "web_search"


def test_infer_tool_id_falls_back_to_llm_operator_when_no_tool_keywords_match() -> None:
    assert _infer_tool_id("Think deeply", "Reason about the tradeoffs") == "llm_operator"
