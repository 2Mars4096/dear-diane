"""Concierge stage-to-tier-to-model resolution.

Maps each concierge pipeline stage to a canonical tier name, then resolves
the tier to a concrete model string via the user's (normalized) tier map.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# TODO(telemetry): Aggregating chat turns by concierge tier/stage in /analytics
# requires a separate telemetry follow-up — the current TelemetryEvent.metadata
# fields are not indexed for grouping.  See Plan 31-26 Task 5-3.

CONCIERGE_STAGE_TIERS: dict[str, str] = {
    "classification": "micro",
    "intent_extraction": "reasoning",
    "goal_resolver": "reasoning",
    "file_review": "routine",
    "direct_task": "routine",
    "experience_fallback": "routine",
    "conversation": "routine",
    "conversation_plan": "reasoning",
    "conversation_debug": "reasoning",
    "workflow_build": "reasoning",
}


class ConciergeTierResolver:
    """Resolve concierge pipeline stages to concrete model strings.

    Parameters
    ----------
    tier_map:
        Normalized tier map (canonical tier name → model string).
    fallback_model:
        Model to use when the stage or tier is not found in the map.
    """

    def __init__(self, tier_map: dict[str, str], fallback_model: str) -> None:
        self._tier_map = tier_map
        self._fallback_model = fallback_model

    def resolve_tier(self, stage: str) -> str:
        """Return the canonical tier name for *stage*, or ``"routine"`` if unknown."""
        return CONCIERGE_STAGE_TIERS.get(stage, "routine")

    def resolve_model(self, stage: str) -> str:
        """Return the model string for *stage*.

        Looks up the stage in :data:`CONCIERGE_STAGE_TIERS` to get the tier,
        then the tier in the tier map to get the model.  Falls back to
        *fallback_model* if either lookup misses.
        """
        tier = self.resolve_tier(stage)
        model = self._tier_map.get(tier, self._fallback_model)
        fallback_used = tier not in self._tier_map
        logger.debug(
            "Concierge tier resolution: stage=%s tier=%s model=%s fallback=%s",
            stage, tier, model, fallback_used,
        )
        return model
