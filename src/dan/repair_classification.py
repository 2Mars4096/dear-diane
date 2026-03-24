"""Shared repair-level classification helpers.

This module is intentionally neutral so both ``engine`` and ``meta`` can use
the same repair severity logic without importing each other.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any

__all__ = ["RepairClassifier", "RepairLevel"]

_PARAMETER_KEYWORDS = [
    "model", "temperature", "max_tokens", "timeout", "tool_id",
    "tool_config", "retry", "token_limit", "token limit",
]
_STRUCTURAL_KEYWORDS = [
    "add node", "remove node", "rewire", "validation step", "fallback",
    "error handling", "branch", "loop", "review step", "add a check",
]
_REDESIGN_KEYWORDS = [
    "fundamentally wrong", "completely different", "start over",
    "wrong approach", "redesign", "scrap", "start from scratch",
]


class RepairLevel(IntEnum):
    """Graduated severity levels for repair actions."""

    PROMPT = 1
    PARAMETER = 2
    STRUCTURAL = 3
    REDESIGN = 4

    @staticmethod
    def should_escalate(
        current_level: int,
        failure_count: int,
        max_attempts: int = 2,
    ) -> "RepairLevel":
        """Return the next level if the current one has been exhausted."""
        if failure_count >= max_attempts and current_level < RepairLevel.REDESIGN:
            return RepairLevel(current_level + 1)
        return RepairLevel(current_level)


class RepairClassifier:
    """Classify a repair principle into an appropriate ``RepairLevel``."""

    def classify(
        self,
        principle: Any,
        failure_history: list[Any] | None = None,
        max_attempts_per_level: int = 2,
    ) -> RepairLevel:
        """Determine the repair level based on action text and escalation history."""
        repair_level_str = getattr(principle, "repair_level", "prompt_fix")

        if repair_level_str == "parameter_fix":
            base = RepairLevel.PARAMETER
        elif repair_level_str == "structural_fix":
            base = RepairLevel.STRUCTURAL
        elif repair_level_str == "redesign":
            base = RepairLevel.REDESIGN
        elif repair_level_str == "retry":
            base = RepairLevel.PROMPT
        else:
            base = self._classify_from_text(getattr(principle, "action", ""))

        if failure_history:
            level_failures = sum(
                1
                for record in failure_history
                if getattr(record, "repair_level", None) == base
                and getattr(record, "status", None) == "failed"
            )
            base = RepairLevel.should_escalate(base, level_failures, max_attempts_per_level)

        return base

    @staticmethod
    def _classify_from_text(action_text: str) -> RepairLevel:
        lower = action_text.lower()
        if any(keyword in lower for keyword in _REDESIGN_KEYWORDS):
            return RepairLevel.REDESIGN
        if any(keyword in lower for keyword in _STRUCTURAL_KEYWORDS):
            return RepairLevel.STRUCTURAL
        if any(keyword in lower for keyword in _PARAMETER_KEYWORDS):
            return RepairLevel.PARAMETER
        return RepairLevel.PROMPT
