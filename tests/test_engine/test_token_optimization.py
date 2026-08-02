"""Tests for Plan 18-1 model fields and token_optimization utilities."""

from __future__ import annotations

import json
import time

import pytest

from dan.engine.events import EventType
from dan.engine.executor import EngineConfig
from dan.engine.token_optimization import (
    ContextSelector,
    PayloadPruner,
    PromptAnalysis,
    PromptAnalyzer,
    SummarizationConfig,
)
from dan.models.context import ContextMode
from dan.models.edges import ContextEdge
from dan.models.nodes import LLMOperator
from dan.models.ports import InputPort, OutputPort


# =========================================================================
# SummarizationConfig
# =========================================================================


class TestSummarizationConfig:
    def test_defaults(self):
        cfg = SummarizationConfig()
        assert cfg.model == ""
        assert cfg.max_summary_tokens == 500
        assert cfg.preserve_structured is True
        assert cfg.persist_to_memory is False

    def test_custom_values(self):
        cfg = SummarizationConfig(
            model="gpt-4o-mini",
            max_summary_tokens=200,
            preserve_structured=False,
            persist_to_memory=True,
        )
        assert cfg.model == "gpt-4o-mini"
        assert cfg.max_summary_tokens == 200
        assert cfg.preserve_structured is False
        assert cfg.persist_to_memory is True

    def test_json_round_trip(self):
        cfg = SummarizationConfig(model="claude-haiku", max_summary_tokens=300)
        restored = SummarizationConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg


# =========================================================================
# PromptAnalyzer
# =========================================================================


class TestPromptAnalyzer:
    def setup_method(self):
        self.analyzer = PromptAnalyzer()

    def test_referenced_variables(self):
        template = "Summarize this {text} about {topic}."
        result = self.analyzer.analyze(template, ["text", "topic"])
        assert set(result.referenced_variables) == {"text", "topic"}
        assert result.unused_variables == []

    def test_unused_variables(self):
        template = "Write about {topic}."
        result = self.analyzer.analyze(template, ["topic", "metadata", "config"])
        assert result.referenced_variables == ["topic"]
        assert set(result.unused_variables) == {"metadata", "config"}

    def test_no_variables(self):
        template = "Just a plain prompt with no variables."
        result = self.analyzer.analyze(template, ["data"])
        assert result.referenced_variables == []
        assert result.unused_variables == ["data"]

    def test_estimated_tokens(self):
        template = "Summarize: {text}"
        result = self.analyzer.analyze(template, ["text"])
        assert result.estimated_template_tokens > 0

    def test_verbose_patterns_detected(self):
        repeated = "Please follow these instructions carefully and do not deviate from them under any circumstances"
        template = f"{repeated}. Now do X. {repeated}."
        result = self.analyzer.analyze(template, [])
        assert len(result.verbose_patterns) >= 1
        assert result.verbose_patterns[0] == repeated

    def test_no_verbose_patterns(self):
        template = "Short prompt {x}."
        result = self.analyzer.analyze(template, ["x"])
        assert result.verbose_patterns == []

    def test_duplicate_var_references_deduped(self):
        template = "First use {name}, then reuse {name} again."
        result = self.analyzer.analyze(template, ["name"])
        assert result.referenced_variables == ["name"]


# =========================================================================
# ContextSelector
# =========================================================================


class TestContextSelector:
    def setup_method(self):
        self.selector = ContextSelector()

    def test_score_declared_dependency_highest(self):
        inputs = {"topic": "machine learning", "metadata": "some metadata"}
        template = "Write about {topic}."
        scores = self.selector.score_inputs(inputs, template)
        assert scores["topic"] > scores["metadata"]

    def test_score_all_referenced(self):
        inputs = {"a": "alpha", "b": "beta"}
        template = "{a} and {b}"
        scores = self.selector.score_inputs(inputs, template)
        assert scores["a"] > 0
        assert scores["b"] > 0

    def test_score_empty_inputs(self):
        scores = self.selector.score_inputs({}, "template {x}")
        assert scores == {}

    def test_score_range(self):
        inputs = {"x": "hello"}
        scores = self.selector.score_inputs(inputs, "{x}")
        assert 0.0 <= scores["x"] <= 1.0

    def test_keyword_overlap(self):
        score = ContextSelector._keyword_overlap("topic is machine learning", "{topic} and {query}")
        assert score > 0.0

    def test_keyword_overlap_no_vars(self):
        assert ContextSelector._keyword_overlap("anything", "no variables here") == 0.0

    def test_recency_score_no_timestamp(self):
        assert ContextSelector._recency_score({}) == 1.0

    def test_recency_score_recent(self):
        score = ContextSelector._recency_score({"timestamp": time.time() - 60})
        assert score > 0.9

    def test_recency_score_old(self):
        one_week_ago = time.time() - 7 * 24 * 3600
        score = ContextSelector._recency_score({"timestamp": one_week_ago})
        assert score <= 0.1

    def test_declared_dependency_present(self):
        assert ContextSelector._declared_dependency("topic", "Write about {topic}") == 1.0

    def test_declared_dependency_absent(self):
        assert ContextSelector._declared_dependency("metadata", "Write about {topic}") == 0.0

    def test_length_penalty_short(self):
        assert ContextSelector._length_penalty("short") == 1.0

    def test_length_penalty_long(self):
        long_text = "x" * 25000
        score = ContextSelector._length_penalty(long_text)
        assert score == 0.0

    def test_select_no_budget(self):
        inputs = {"a": "data_a", "b": "data_b"}
        inline, deferred = self.selector.select(inputs, "{a}", target_tokens=None)
        assert set(inline.keys()) == {"a", "b"}
        assert deferred == {}

    def test_select_with_budget(self):
        inputs = {
            "important": "short",
            "bulk": "x" * 50000,
        }
        inline, deferred = self.selector.select(
            inputs, "Use {important}", target_tokens=100
        )
        assert "important" in inline
        assert "bulk" in deferred

    def test_custom_weights(self):
        selector = ContextSelector(weights={"declared_dependency": 1.0, "keyword_overlap": 0.0})
        inputs = {"a": "a content", "b": "b content"}
        scores = selector.score_inputs(inputs, "{a}")
        assert scores["a"] > scores["b"]


# =========================================================================
# PayloadPruner
# =========================================================================


class TestPayloadPruner:
    def test_prune_wildcard_key(self):
        pruner = PayloadPruner(["*.created_at"])
        data = {
            "name": "Alice",
            "created_at": "2024-01-01",
            "nested": {
                "value": 42,
                "created_at": "2024-02-01",
            },
        }
        pruned, count = pruner.prune(data)
        assert "created_at" not in pruned
        assert "created_at" not in pruned["nested"]
        assert pruned["name"] == "Alice"
        assert pruned["nested"]["value"] == 42
        assert count == 2

    def test_prune_exact_path(self):
        pruner = PayloadPruner(["root.internal_id"])
        data = {
            "root": {
                "internal_id": "abc",
                "name": "test",
            },
            "internal_id": "should_stay",
        }
        pruned, count = pruner.prune(data)
        assert "internal_id" not in pruned["root"]
        assert pruned["internal_id"] == "should_stay"
        assert count == 1

    def test_prune_lists(self):
        pruner = PayloadPruner(["*.secret"])
        data = [
            {"name": "a", "secret": "x"},
            {"name": "b", "secret": "y"},
        ]
        pruned, count = pruner.prune(data)
        assert len(pruned) == 2
        assert "secret" not in pruned[0]
        assert "secret" not in pruned[1]
        assert count == 2

    def test_prune_no_matches(self):
        pruner = PayloadPruner(["*.nonexistent"])
        data = {"name": "Alice", "age": 30}
        pruned, count = pruner.prune(data)
        assert pruned == data
        assert count == 0

    def test_prune_empty_patterns(self):
        pruner = PayloadPruner([])
        data = {"a": 1}
        pruned, count = pruner.prune(data)
        assert pruned == data
        assert count == 0

    def test_format_json(self):
        pruner = PayloadPruner([])
        data = {"key": "value", "num": 42}
        result = pruner.format_compact(data, fmt="json")
        parsed = json.loads(result)
        assert parsed == data

    def test_format_compact(self):
        pruner = PayloadPruner([])
        data = {"name": "Alice", "details": {"age": 30, "city": "NYC"}}
        result = pruner.format_compact(data, fmt="compact")
        assert "name: Alice" in result
        assert "details:" in result
        assert "age: 30" in result
        assert "{" not in result
        assert "}" not in result

    def test_format_compact_list(self):
        pruner = PayloadPruner([])
        data = {"items": [1, 2, 3]}
        result = pruner.format_compact(data, fmt="compact")
        assert "items:" in result
        assert "- 1" in result

    def test_format_yaml(self):
        pruner = PayloadPruner([])
        data = {"key": "value"}
        result = pruner.format_compact(data, fmt="yaml")
        assert "key" in result
        assert "value" in result

    def test_format_yaml_fallback(self, monkeypatch):
        """When pyyaml is unavailable, yaml falls back to JSON."""
        import sys

        monkeypatch.setitem(sys.modules, "yaml", None)
        pruner = PayloadPruner([])
        result = pruner.format_compact({"a": 1}, fmt="yaml")
        assert json.loads(result) == {"a": 1}

    def test_prune_deeply_nested(self):
        pruner = PayloadPruner(["*.meta"])
        data = {"a": {"b": {"c": {"meta": "remove_me", "keep": True}}}}
        pruned, count = pruner.prune(data)
        assert "meta" not in pruned["a"]["b"]["c"]
        assert pruned["a"]["b"]["c"]["keep"] is True
        assert count == 1


# =========================================================================
# LLMOperator new fields
# =========================================================================


class TestLLMOperatorNewFields:
    def test_defaults_preserve_behavior(self):
        node = LLMOperator(
            id="n1", name="Test", model="gpt-4o", prompt_template="Hello {name}"
        )
        assert node.target_input_tokens is None
        assert node.summarize_inputs is None
        assert node.jit_tool_loading is False
        assert node.agent_context_tools is False
        assert node.prune_fields == []
        assert node.input_format == "json"

    def test_all_new_fields(self):
        node = LLMOperator(
            id="n2",
            name="Smart",
            model="gpt-4o",
            prompt_template="{text}",
            target_input_tokens=8000,
            summarize_inputs=True,
            jit_tool_loading=True,
            agent_context_tools=True,
            prune_fields=["*.created_at", "*.internal_id"],
            input_format="yaml",
        )
        assert node.target_input_tokens == 8000
        assert node.summarize_inputs is True
        assert node.jit_tool_loading is True
        assert node.agent_context_tools is True
        assert len(node.prune_fields) == 2
        assert node.input_format == "yaml"

    def test_summarize_inputs_dict(self):
        node = LLMOperator(
            id="n3",
            name="Summ",
            model="gpt-4o",
            prompt_template="{x}",
            summarize_inputs={"model": "gpt-4o-mini", "max_summary_tokens": 200},
        )
        assert isinstance(node.summarize_inputs, dict)
        assert node.summarize_inputs["model"] == "gpt-4o-mini"

    def test_json_round_trip(self):
        node = LLMOperator(
            id="n4",
            name="RT",
            model="m",
            prompt_template="p",
            target_input_tokens=5000,
            summarize_inputs=True,
            jit_tool_loading=True,
            agent_context_tools=True,
            prune_fields=["*.meta"],
            input_format="compact",
        )
        data = node.model_dump()
        restored = LLMOperator.model_validate(data)
        assert restored == node

    def test_json_string_round_trip(self):
        node = LLMOperator(
            id="n5",
            name="N",
            model="m",
            prompt_template="p",
            target_input_tokens=3000,
            input_format="yaml",
        )
        json_str = node.model_dump_json()
        restored = LLMOperator.model_validate_json(json_str)
        assert restored == node

    def test_backward_compat_no_new_fields(self):
        """Constructing LLMOperator without new fields works identically."""
        node = LLMOperator(id="old", name="Old", model="m", prompt_template="p")
        data = node.model_dump()
        assert data["node_type"] == "llm_operator"
        assert data["target_input_tokens"] is None
        assert data["summarize_inputs"] is None
        assert data["jit_tool_loading"] is False
        assert data["agent_context_tools"] is False
        assert data["prune_fields"] == []
        assert data["input_format"] == "json"


# =========================================================================
# ContextEdge new field
# =========================================================================


class TestContextEdgeNewField:
    def test_default_pass_by_reference(self):
        e = ContextEdge(
            id="e1",
            source_node_id="a",
            source_port="out",
            target_node_id="b",
            target_port="in",
            context_key="k",
            mode=ContextMode.READ,
        )
        assert e.pass_by_reference is False

    def test_pass_by_reference_true(self):
        e = ContextEdge(
            id="e2",
            source_node_id="a",
            source_port="out",
            target_node_id="b",
            target_port="in",
            context_key="k",
            mode=ContextMode.WRITE,
            pass_by_reference=True,
        )
        assert e.pass_by_reference is True

    def test_json_round_trip(self):
        e = ContextEdge(
            id="e3",
            source_node_id="a",
            source_port="o",
            target_node_id="b",
            target_port="i",
            context_key="ctx",
            mode=ContextMode.APPEND,
            pass_by_reference=True,
        )
        restored = ContextEdge.model_validate(e.model_dump())
        assert restored == e
        assert restored.pass_by_reference is True

    def test_backward_compat(self):
        raw = {
            "id": "e4",
            "source_node_id": "a",
            "source_port": "o",
            "target_node_id": "b",
            "target_port": "i",
            "context_key": "k",
            "mode": "read",
        }
        e = ContextEdge.model_validate(raw)
        assert e.pass_by_reference is False


# =========================================================================
# New EventType values
# =========================================================================


class TestNewEventTypes:
    @pytest.mark.parametrize(
        "name,value",
        [
            ("TOKEN_BUDGET_ADVISORY", "budget_advisory"),
            ("CONTEXT_DEFERRED", "context_deferred"),
            ("INPUT_SUMMARIZED", "input_summarized"),
            ("JIT_SCHEMA_LOADED", "jit_schema_loaded"),
            ("PAYLOAD_PRUNED", "payload_pruned"),
            ("CONTEXT_TOOL_CALLED", "context_tool_called"),
        ],
    )
    def test_event_type_exists(self, name, value):
        member = EventType[name]
        assert member.value == value

    def test_all_values_unique(self):
        values = [e.value for e in EventType]
        assert len(values) == len(set(values))


# =========================================================================
# EngineConfig new field
# =========================================================================


class TestEngineConfigTokenBudget:
    def test_default_none(self):
        cfg = EngineConfig()
        assert cfg.token_budget is None

    def test_custom_value(self):
        cfg = EngineConfig(token_budget=50000)
        assert cfg.token_budget == 50000
