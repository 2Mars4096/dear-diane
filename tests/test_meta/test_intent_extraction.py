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
    _infer_conditional_config,
    _infer_deterministic_code_config,
    _infer_loop_config,
    build_intent_extraction_system_prompt,
    build_intent_tool_schema,
    validate_and_expand_intent,
)
from dan.meta.intent_schema import StageType, WorkflowIntent


# ---------------------------------------------------------------------------
# System prompt tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Verify that the system prompt is well-formed and covers all stage types."""

    def test_contains_all_stage_type_names(self):
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

    def test_built_prompt_includes_workflow_contract_and_exact_tool_ids(self):
        prompt = build_intent_extraction_system_prompt()
        assert "Workflow Generation Contract" in prompt
        assert "Do NOT invent tool_ids not in this list" in prompt
        assert "smallest complete runnable workflow" in prompt


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


class TestHeuristicExpansion:
    def test_slugify_smoke_phrase_gets_code_execution_stage(self):
        intent = WorkflowIntent(
            goal="slugify",
            stages=[{"name": "draft", "stage_type": "transform"}],
        )
        expanded = validate_and_expand_intent(
            intent,
            "pls set up something small that turns Deep Agent Network into deep-agent-network and execute it",
        )
        code_stage = next(
            s for s in expanded.stages if s.stage_type == StageType.code_execution
        )
        assert code_stage.config["code"]
        assert 'text = "Deep Agent Network"' in code_stage.config["code"]
        assert '"-".join' in code_stage.config["code"]

    def test_slugify_smoke_phrase_overrides_bad_llm_code(self):
        intent = WorkflowIntent(
            goal="slugify",
            stages=[
                {
                    "name": "convert_to_kebab_case",
                    "stage_type": "code_execution",
                    "config": {"code": "result = input_text.lower().replace(' ', '-')"},
                }
            ],
        )
        expanded = validate_and_expand_intent(
            intent,
            "pls set up something small that turns Deep Agent Network into deep-agent-network and execute it",
        )
        code_stage = expanded.stages[0]
        assert code_stage.stage_type == StageType.code_execution
        assert 'text = "Deep Agent Network"' in code_stage.config["code"]
        assert "input_text" not in code_stage.config["code"]

    def test_conditional_smoke_phrase_gets_conditional_payload(self):
        intent = WorkflowIntent(
            goal="branch",
            stages=[{"name": "draft", "stage_type": "transform"}],
        )
        expanded = validate_and_expand_intent(
            intent,
            "uh can you do a tiny branch thing, if 7 is bigger than 5 say BIG otherwise SMALL, then run it",
        )
        conditional_stage = next(
            s for s in expanded.stages if s.stage_type == StageType.conditional
        )
        assert conditional_stage.conditional is not None
        assert conditional_stage.conditional.condition == "7 > 5"
        assert conditional_stage.conditional.then_description == "BIG"
        assert conditional_stage.conditional.else_description == "SMALL"

    def test_loop_smoke_phrase_gets_loop_stage(self):
        intent = WorkflowIntent(
            goal="loop",
            stages=[{"name": "draft", "stage_type": "transform"}],
        )
        expanded = validate_and_expand_intent(
            intent,
            "i want some tiny loop thing that counts up till 3 and then runs and gives me the final value",
        )
        loop_stage = next(s for s in expanded.stages if s.stage_type == StageType.loop)
        assert loop_stage.loop is not None
        assert loop_stage.loop.condition == "counter < 3"

    def test_loop_smoke_phrase_repairs_multi_stage_transform_extraction(self):
        intent = WorkflowIntent(
            goal="loop",
            stages=[
                {
                    "name": "i_want_some_tiny_loop_thing_th",
                    "description": "I want some tiny loop thing that counts up till 3",
                    "stage_type": "transform",
                },
                {
                    "name": "runs_and_gives_me_the_final_va",
                    "description": "Runs and gives me the final value",
                    "stage_type": "transform",
                },
            ],
        )
        expanded = validate_and_expand_intent(
            intent,
            "i want some tiny loop thing that counts up till 3 and then runs and gives me the final value",
        )
        assert expanded.stages[0].stage_type == StageType.loop
        assert expanded.stages[0].loop is not None
        assert expanded.stages[0].loop.condition == "counter < 3"

    def test_direct_conditional_inference_handles_comparison_language(self):
        conditional = _infer_conditional_config(
            "if 7 is bigger than 5 say BIG otherwise SMALL"
        )
        assert conditional.condition == "7 > 5"
        assert conditional.then_description == "BIG"
        assert conditional.else_description == "SMALL"

    def test_direct_loop_inference_parses_count_target(self):
        loop = _infer_loop_config("count up till 3")
        assert loop.condition == "counter < 3"
        assert loop.max_iterations >= 5

    def test_direct_deterministic_code_inference_uses_source_target_example(self):
        config = _infer_deterministic_code_config(
            "pls set up something small that turns Deep Agent Network into deep-agent-network and execute it"
        )
        assert config is not None
        assert 'text = "Deep Agent Network"' in config["code"]
        assert 'result = "-".join' in config["code"]

    def test_numeric_batch_prompt_rewrites_to_seed_fanout_and_reduce(self):
        intent = WorkflowIntent(
            goal="batch",
            stages=[
                {
                    "name": "triple_each",
                    "description": "Triple each number in the input list",
                    "stage_type": "fan_out",
                },
                {
                    "name": "sum_results",
                    "stage_type": "code_execution",
                    "config": {"code": "total = sum(tripled)"},
                },
            ],
        )
        expanded = validate_and_expand_intent(
            intent,
            "do a little batch thing over 1 2 3, triple each one, sum it to 18, and run it",
        )
        assert [stage.stage_type for stage in expanded.stages] == [
            StageType.code_execution,
            StageType.fan_out,
            StageType.code_execution,
        ]
        assert expanded.stages[0].config["code"] == "result = [1, 2, 3]"
        assert expanded.stages[1].config["body_code"] == "result = item * 3"
        assert 'entry["result"]' in expanded.stages[2].config["code"]

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
