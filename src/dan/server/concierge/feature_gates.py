from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def engine_feature_enabled(feature_name: str) -> bool:
    """Resolve concierge learning feature gates through the engine tier config."""
    try:
        from dan.engine.learning_tiers import is_feature_enabled
    except Exception:
        logger.debug("Learning-tier feature gate import failed", exc_info=True)
        return False
    return bool(is_feature_enabled(feature_name))
