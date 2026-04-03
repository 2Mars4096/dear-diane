from __future__ import annotations

from dan.linter import LintConfig, RuleSeverity


def test_lint_config_parses_nested_dicts_and_defaults() -> None:
    config = LintConfig.model_validate(
        {
            "structural": {
                "json_schema": {"type": "object"},
                "required_keys": ["summary"],
            },
            "semantic": {"topic_keywords": ["finance"]},
            "intent": {"intent": "Finance summary"},
            "autofix": ["fill_defaults", "retry_with_feedback"],
            "max_retries": 2,
            "retry_budget_ms": 2500,
        }
    )

    assert config.enabled is True
    assert config.severity == RuleSeverity.ERROR
    assert config.structural is not None
    assert config.structural.required_keys == ["summary"]
    assert config.semantic is not None
    assert config.semantic.topic_keywords == ["finance"]
    assert config.intent is not None
    assert config.intent.intent == "Finance summary"
    assert config.retry_budget_ms == 2500


def test_lint_config_allows_disabled_gate() -> None:
    config = LintConfig(enabled=False)

    assert config.enabled is False
    assert config.structural is None
    assert config.semantic is None
    assert config.intent is None
