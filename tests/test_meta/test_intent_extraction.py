"""Tests for dan.meta.intent_extraction — system prompt, tool schema, few-shot examples.

Also covers COVERAGE_CATALOG and CoverageChecker.describe_coverage().
"""

from __future__ import annotations

import pytest

from dan.meta.intent_compiler import COVERAGE_CATALOG, CoverageChecker, CoverageResult
from dan.meta.intent_extraction import (
    INTENT_EXTRACTION_SYSTEM_PROMPT,
    INTENT_FEW_SHOT_EXAMPLES,
    _STAGE_TYPE_DESCRIPTIONS,
    build_intent_tool_schema,
)
from dan.meta.intent_schema import StageType, WorkflowIntent


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Verify that the system prompt is well-formed and covers all stage types."""

    def test_contains_all_seven_stage_type_names(self):
        for st in StageType:
            assert st.value in INTENT_EXTRACTION_SYSTEM_PROMPT, (
                f"Stage type '{st.value}' missing from system prompt"
            )

    def test_contains_all_stage_type_descriptions(self):
        for desc in _STAGE_TYPE_DESCRIPTIONS.values():
            assert desc in INTENT_EXTRACTION_SYSTEM_PROMPT

    def test_instructs_decompose(self):
        assert "decompose" in INTENT_EXTRACTION_SYSTEM_PROMPT.lower()

    def test_instructs_clarification(self):
        assert "clarification" in INTENT_EXTRACTION_SYSTEM_PROMPT.lower()

    def test_prompt_is_nonempty_and_bounded(self):
        assert len(INTENT_EXTRACTION_SYSTEM_PROMPT) > 100
        words = INTENT_EXTRACTION_SYSTEM_PROMPT.split()
        assert len(words) < 600, f"System prompt too long: {len(words)} words"


# ---------------------------------------------------------------------------
# Tool schema tests
# ---------------------------------------------------------------------------


class TestToolSchema:
    """Verify the function-calling tool definition."""

    def test_schema_has_function_type(self):
        schema = build_intent_tool_schema()
        assert schema["type"] == "function"

    def test_function_name_is_emit_workflow_intent(self):
        schema = build_intent_tool_schema()
        assert schema["function"]["name"] == "emit_workflow_intent"

    def test_parameters_match_workflow_intent_schema(self):
        schema = build_intent_tool_schema()
        params = schema["function"]["parameters"]
        expected = WorkflowIntent.model_json_schema()
        assert params == expected

    def test_schema_parameters_contain_required_fields(self):
        schema = build_intent_tool_schema()
        params = schema["function"]["parameters"]
        props = params.get("properties", {})
        assert "goal" in props
        assert "stages" in props


# ---------------------------------------------------------------------------
# Few-shot example tests
# ---------------------------------------------------------------------------


class TestFewShotExamples:
    """Verify that few-shot examples are valid WorkflowIntent instances."""

    def test_at_least_two_examples(self):
        assert len(INTENT_FEW_SHOT_EXAMPLES) >= 2

    def test_each_example_has_user_and_intent(self):
        for i, ex in enumerate(INTENT_FEW_SHOT_EXAMPLES):
            assert "user" in ex, f"Example {i} missing 'user' key"
            assert "intent" in ex, f"Example {i} missing 'intent' key"

    @pytest.mark.parametrize("idx", range(len(INTENT_FEW_SHOT_EXAMPLES)))
    def test_example_parses_as_workflow_intent(self, idx: int):
        ex = INTENT_FEW_SHOT_EXAMPLES[idx]
        intent = WorkflowIntent(**ex["intent"])
        assert intent.goal
        assert len(intent.stages) >= 1

    def test_paper_writing_example_has_review_loop(self):
        paper = INTENT_FEW_SHOT_EXAMPLES[0]
        intent = WorkflowIntent(**paper["intent"])
        review_stages = [s for s in intent.stages if s.stage_type == StageType.review_loop]
        assert len(review_stages) == 1
        assert review_stages[0].review is not None

    def test_data_analysis_example_has_tool_call(self):
        data = INTENT_FEW_SHOT_EXAMPLES[1]
        intent = WorkflowIntent(**data["intent"])
        tool_stages = [s for s in intent.stages if s.stage_type == StageType.tool_call]
        assert len(tool_stages) >= 1


# ---------------------------------------------------------------------------
# Coverage catalog tests
# ---------------------------------------------------------------------------


class TestCoverageCatalog:
    """Verify the COVERAGE_CATALOG constant."""

    def test_has_at_least_seven_entries(self):
        assert len(COVERAGE_CATALOG) >= 7

    def test_every_entry_has_required_keys(self):
        for name, entry in COVERAGE_CATALOG.items():
            assert "description" in entry, f"'{name}' missing description"
            assert "stage_types" in entry, f"'{name}' missing stage_types"
            assert "composable" in entry, f"'{name}' missing composable"

    def test_all_referenced_stage_types_are_valid(self):
        for name, entry in COVERAGE_CATALOG.items():
            for st in entry["stage_types"]:
                assert isinstance(st, StageType), (
                    f"'{name}' has invalid stage_type: {st}"
                )

    def test_known_patterns_present(self):
        expected = {
            "linear_chain",
            "review_loop",
            "fan_out_fan_in",
            "rag_qa",
            "data_pipeline",
            "tool_augmented",
            "human_gate",
        }
        assert expected.issubset(COVERAGE_CATALOG.keys())


# ---------------------------------------------------------------------------
# CoverageChecker.describe_coverage() tests
# ---------------------------------------------------------------------------


class TestDescribeCoverage:
    """Verify describe_coverage() output."""

    def test_returns_nonempty_string(self):
        result = CoverageChecker().describe_coverage()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_mentions_all_catalog_patterns(self):
        result = CoverageChecker().describe_coverage()
        for name in COVERAGE_CATALOG:
            assert name in result, f"Pattern '{name}' not in describe_coverage()"

    def test_mentions_supported_stage_types(self):
        result = CoverageChecker().describe_coverage()
        for st in StageType:
            assert st.value in result


# ---------------------------------------------------------------------------
# Fail-fast fallback logic tests
# ---------------------------------------------------------------------------


def _stage(name: str, stage_type: str = "transform", **kw) -> dict:
    d: dict = {"name": name, "stage_type": stage_type}
    d.update(kw)
    return d


class TestFailFastFallback:
    """Phase 14 rule: ANY unsupported stage → recommendation='fallback'."""

    def test_all_supported_returns_compile(self):
        intent = WorkflowIntent(
            goal="simple chain",
            stages=[
                {"name": "a", "stage_type": "transform"},
                {"name": "b", "stage_type": "tool_call"},
            ],
        )
        result = CoverageChecker().check(intent)
        assert result.fully_covered is True
        assert result.recommendation == "compile"

    def test_unsupported_stage_returns_compile_passthrough(self):
        """CoverageChecker is now a pass-through — always returns fully_covered=True."""
        checker = CoverageChecker()
        checker.SUPPORTED_TYPES = {StageType.transform}
        intent = WorkflowIntent(
            goal="mixed",
            stages=[
                {"name": "a", "stage_type": "transform"},
                {"name": "b", "stage_type": "tool_call"},
            ],
        )
        result = checker.check(intent, try_compose=False)
        assert result.fully_covered is True
        assert result.recommendation == "compile"

    def test_all_unsupported_returns_compile_passthrough(self):
        """CoverageChecker is now a pass-through — always returns fully_covered=True."""
        checker = CoverageChecker()
        checker.SUPPORTED_TYPES = set()
        intent = WorkflowIntent(
            goal="nothing works",
            stages=[{"name": "a", "stage_type": "transform"}],
        )
        result = checker.check(intent, try_compose=False)
        assert result.fully_covered is True
        assert result.recommendation == "compile"
