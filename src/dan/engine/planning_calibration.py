"""Planning-time calibration — duration estimation, failure prediction, model preference.

Bridges "learning from the past" into "planning for the future" by providing
calibrated priors for plan construction.
"""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword tables for heuristic classification
# ---------------------------------------------------------------------------

_QUICK_KEYWORDS = [
    "translate", "summarize", "classify", "extract", "format",
    "convert", "rename", "list", "count", "check",
]
_COMPLEX_KEYWORDS = [
    "research", "analyze", "write paper", "full report", "comprehensive",
    "multi-step", "design", "architect", "review and revise", "deep dive",
    "literature review", "systematic",
]

_NODE_FAILURE_RATES: dict[str, tuple[float, str]] = {
    "code_operator": (0.20, "Code nodes fail ~20% of the time — consider adding a validator or retry policy"),
    "tool_operator": (0.15, "Tool nodes fail ~15% of the time — ensure the tool is available and inputs are valid"),
    "llm_operator": (0.05, "LLM nodes rarely fail (~5%) — mostly rate limits or malformed schemas"),
    "rag_operator": (0.10, "RAG nodes fail ~10% — check collection exists and embeddings are indexed"),
    "composite": (0.12, "Composite nodes fail ~12% — failures often cascade from inner nodes"),
}

_NODE_MODEL_PREFS: dict[str, str] = {
    "code_operator": "reasoning",
    "llm_operator": "routine",
    "tool_operator": "micro",
    "rag_operator": "micro",
    "router": "reasoning",
    "reflection": "reasoning",
}

_TASK_PATTERN_PREFS: dict[str, str] = {
    "simple": "micro",
    "translation": "micro",
    "classification": "micro",
    "summarization": "routine",
    "writing": "reasoning",
    "analysis": "reasoning",
    "code_generation": "reasoning",
    "research": "critical",
}


# ---------------------------------------------------------------------------
# Duration Estimator
# ---------------------------------------------------------------------------


class DurationEstimator:
    """Estimate task duration from description and node type."""

    def estimate(
        self,
        task_description: str,
        node_type: str | None = None,
    ) -> tuple[float, float]:
        """Return ``(median_minutes, confidence_interval_minutes)``.

        Uses heuristic keyword classification.  Future: query
        ExperienceStore for similar past tasks.
        """
        desc_lower = task_description.lower()

        if any(kw in desc_lower for kw in _COMPLEX_KEYWORDS):
            return (30.0, 15.0)

        if any(kw in desc_lower for kw in _QUICK_KEYWORDS):
            return (2.0, 1.0)

        if node_type in ("code_operator", "tool_operator"):
            return (5.0, 3.0)

        return (15.0, 8.0)


# ---------------------------------------------------------------------------
# Failure Hotspot Predictor
# ---------------------------------------------------------------------------


class FailureHotspotPredictor:
    """Predict failure probability for a given node type."""

    def predict(self, node_type: str) -> tuple[float, str]:
        """Return ``(failure_probability, advice)``."""
        entry = _NODE_FAILURE_RATES.get(node_type)
        if entry:
            return entry
        return (0.08, f"No specific failure data for '{node_type}' — default estimate ~8%")


# ---------------------------------------------------------------------------
# Model Preference
# ---------------------------------------------------------------------------


class ModelPreference:
    """Recommend a model tier based on node type and task pattern."""

    def recommend(
        self,
        node_type: str,
        task_pattern: str | None = None,
    ) -> str | None:
        """Return recommended model tier, or None if no recommendation."""
        if task_pattern:
            pattern_lower = task_pattern.lower()
            for key, tier in _TASK_PATTERN_PREFS.items():
                if key in pattern_lower:
                    return tier

        return _NODE_MODEL_PREFS.get(node_type)
