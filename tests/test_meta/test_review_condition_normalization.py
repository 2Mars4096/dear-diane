"""Tests for review-loop condition normalization (plan 33-10, task A)."""

import json
import logging

from dan.meta.intent_compiler import (
    _infer_tool_id,
    _normalize_review_condition,
    _placeholder_code,
)


def test_condition_polarity_flip():
    assert _normalize_review_condition("quality_score >= 8") == "quality_score < 8"


def test_condition_gt_flip():
    assert _normalize_review_condition("quality_score > 7") == "quality_score <= 7"


def test_reversed_threshold_stop_condition_is_negated():
    assert _normalize_review_condition("8 <= quality_score") == "8 > quality_score"


def test_condition_already_correct():
    assert _normalize_review_condition("quality_score < 8") == "quality_score < 8"


def test_or_stop_condition_is_negated_as_a_whole():
    result = _normalize_review_condition("quality_score >= 8 or quality_score >= 9")
    assert result == "quality_score < 8 and quality_score < 9"


def test_and_stop_condition_is_negated_as_a_whole():
    result = _normalize_review_condition("quality_score >= 8 and quality_score >= 9")
    assert result == "quality_score < 8 or quality_score < 9"


def test_unknown_variable_replaced(caplog):
    with caplog.at_level(logging.WARNING):
        result = _normalize_review_condition("content_approved == true")
    assert result == "quality_score < 8"
    assert "unknown variables" in caplog.text.lower()


def test_known_variable_passes():
    result = _normalize_review_condition(
        "quality_score < 9", state_vars={"quality_score"},
    )
    assert result == "quality_score < 9"


def test_feedback_variable_passes():
    result = _normalize_review_condition("feedback != ''")
    assert result == "feedback != ''"


def test_string_literal_condition_preserved():
    result = _normalize_review_condition("feedback == 'approved'")
    assert result == "feedback == 'approved'"


def test_is_not_none_condition_preserved():
    result = _normalize_review_condition("feedback is not None")
    assert result == "feedback is not None"


def test_in_condition_preserved():
    result = _normalize_review_condition("feedback in ('approved', 'accepted')")
    assert result == "feedback in ('approved', 'accepted')"


def test_embedded_comparison_inside_string_literal_preserved():
    result = _normalize_review_condition("feedback == 'quality_score >= 8'")
    assert result == "feedback == 'quality_score >= 8'"


def test_string_literal_placeholder_collision_preserved():
    result = _normalize_review_condition("feedback == '__STR1__' and quality_score < 8")
    assert result == "feedback == '__STR1__' and quality_score < 8"


def test_placeholder_code_escapes_quotes_and_newlines():
    code = _placeholder_code('Analyze the "revenue" column\nand summarize it')
    namespace: dict[str, object] = {}
    exec(code, namespace)

    assert namespace["result"] == {
        "status": "placeholder",
        "task": 'Analyze the "revenue" column\nand summarize it',
    }
    assert json.loads(code.removeprefix("result = "))["task"].startswith("Analyze the")


def test_tool_inference_matches_explicit_file_phrase():
    assert _infer_tool_id("", "read file contents from disk") == "file_read"


def test_tool_inference_does_not_match_profile_as_file():
    assert _infer_tool_id("", "profile customer behavior") == "web_search"


def test_malformed_stop_condition_falls_back_to_default(caplog):
    with caplog.at_level(logging.WARNING):
        result = _normalize_review_condition("quality_score >= 8 or quality_score >=")
    assert result == "quality_score < 8"
    assert "could not be parsed" in caplog.text.lower()
