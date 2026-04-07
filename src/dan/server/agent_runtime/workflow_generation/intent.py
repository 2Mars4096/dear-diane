"""Intent-extraction helpers for workflow generation."""

from __future__ import annotations

from typing import Any

from dan.providers import get_model_behavior


def intent_tool_choice(provider: Any, model: str) -> str | dict[str, Any]:
    """Prefer deterministic tool-calling for intent extraction when supported."""

    behavior = get_model_behavior(provider, model)
    if behavior.supports_exact_tool_choice:
        return {
            "type": "function",
            "function": {"name": "emit_workflow_intent"},
        }
    if behavior.supports_required_tool_choice:
        return "required"
    return "auto"


__all__ = ["intent_tool_choice"]
