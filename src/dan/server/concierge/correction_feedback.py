from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TurnFeedbackAnalysis:
    """Result of analyzing whether a user turn corrects the last assistant turn."""

    correction_record: Any | None
    actions: list[Any]
    is_positive_outcome: bool


def analyze_turn_feedback(
    *,
    user_message: str,
    previous_assistant_message: str,
    prompt_key: str | None,
    min_confidence: float,
) -> TurnFeedbackAnalysis | None:
    """Map a user follow-up into either success, corrective action, or no-op."""
    try:
        from dan.engine.correction_memory import (
            CorrectionRecord,
            detect_correction,
            route_correction,
        )
    except Exception:
        logger.debug("Correction detector import failed", exc_info=True)
        return None

    signal = detect_correction(user_message, previous_assistant_message)
    if signal is None:
        return TurnFeedbackAnalysis(
            correction_record=None,
            actions=[],
            is_positive_outcome=True,
        )

    if float(getattr(signal, "confidence", 0.0) or 0.0) < min_confidence:
        return None

    actions = route_correction(signal)
    return TurnFeedbackAnalysis(
        correction_record=CorrectionRecord(
            signal=signal,
            actions=actions,
            active_prompt_key=prompt_key,
        ),
        actions=actions,
        is_positive_outcome=False,
    )
