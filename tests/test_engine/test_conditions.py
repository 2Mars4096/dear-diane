"""Tests for engine/conditions.py — safe expression evaluator."""

import pytest

from dan.engine.conditions import ConditionError, evaluate_condition, evaluate_reducer


class TestEvaluateCondition:
    def test_simple_true(self):
        assert evaluate_condition("x > 5", {"x": 10}) is True

    def test_simple_false(self):
        assert evaluate_condition("x > 5", {"x": 3}) is False

    def test_equality(self):
        assert evaluate_condition("verdict == 'revise'", {"verdict": "revise"}) is True
        assert evaluate_condition("verdict == 'revise'", {"verdict": "accept"}) is False

    def test_compound_condition(self):
        assert evaluate_condition(
            "verdict == 'revise' and iteration < 5",
            {"verdict": "revise", "iteration": 2},
        ) is True

    def test_builtin_len(self):
        assert evaluate_condition("len(items) > 0", {"items": [1, 2]}) is True
        assert evaluate_condition("len(items) > 0", {"items": []}) is False

    def test_builtin_any(self):
        assert evaluate_condition(
            "any(x > 3 for x in values)", {"values": [1, 2, 4]}
        ) is True

    def test_no_builtins_leak(self):
        with pytest.raises(ConditionError):
            evaluate_condition("__import__('os').system('echo hi')", {})

    def test_undefined_variable(self):
        with pytest.raises(ConditionError):
            evaluate_condition("unknown_var > 5", {})

    def test_syntax_error(self):
        with pytest.raises(ConditionError):
            evaluate_condition("x >>>", {"x": 1})

    def test_result_coerced_to_bool(self):
        assert evaluate_condition("count", {"count": 5}) is True
        assert evaluate_condition("count", {"count": 0}) is False
        assert evaluate_condition("name", {"name": ""}) is False
        assert evaluate_condition("name", {"name": "hello"}) is True


class TestEvaluateReducer:
    def test_named_concatenate_joins_strings(self):
        assert evaluate_reducer("concatenate", ["a", "b", "c"]) == "abc"

    def test_named_concat_joins_result_fields(self):
        assert evaluate_reducer(
            "concat",
            [{"result": "alpha"}, {"result": "beta"}],
        ) == "alphabeta"

    def test_expression_reducer_still_works(self):
        assert evaluate_reducer("len(inputs)", [1, 2, 3]) == 3
