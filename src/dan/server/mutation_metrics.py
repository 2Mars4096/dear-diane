"""Mutation pipeline quality metrics.

Collects three key acceptance metrics:
- apply_success_rate: fraction of apply-mutation calls that succeed
- post_validate_pass_rate: fraction of successful applies that also pass validate_graph
- avg_user_turns_to_success: average number of chat turns before a successful mutation
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MutationMetrics:
    """In-memory mutation quality metrics collector."""

    _apply_attempts: int = 0
    _apply_successes: int = 0
    _validation_passes: int = 0
    _validation_attempts: int = 0
    _turn_counts: list[int] = field(default_factory=list)
    _last_reset: float = field(default_factory=time.time)

    def record_apply(self, success: bool) -> None:
        self._apply_attempts += 1
        if success:
            self._apply_successes += 1

    def record_validation(self, passed: bool) -> None:
        self._validation_attempts += 1
        if passed:
            self._validation_passes += 1

    def record_turns_to_success(self, turns: int) -> None:
        self._turn_counts.append(turns)

    @property
    def apply_success_rate(self) -> float:
        if self._apply_attempts == 0:
            return 0.0
        return self._apply_successes / self._apply_attempts

    @property
    def post_validate_pass_rate(self) -> float:
        if self._validation_attempts == 0:
            return 0.0
        return self._validation_passes / self._validation_attempts

    @property
    def avg_user_turns_to_success(self) -> float:
        if not self._turn_counts:
            return 0.0
        return sum(self._turn_counts) / len(self._turn_counts)

    def summary(self) -> dict[str, float | int]:
        return {
            "apply_attempts": self._apply_attempts,
            "apply_successes": self._apply_successes,
            "apply_success_rate": round(self.apply_success_rate, 4),
            "validation_attempts": self._validation_attempts,
            "validation_passes": self._validation_passes,
            "post_validate_pass_rate": round(self.post_validate_pass_rate, 4),
            "turn_samples": len(self._turn_counts),
            "avg_user_turns_to_success": round(self.avg_user_turns_to_success, 2),
            "elapsed_seconds": round(time.time() - self._last_reset, 1),
        }

    def reset(self) -> dict[str, float | int]:
        report = self.summary()
        self._apply_attempts = 0
        self._apply_successes = 0
        self._validation_passes = 0
        self._validation_attempts = 0
        self._turn_counts.clear()
        self._last_reset = time.time()
        return report


# Singleton instance
mutation_metrics = MutationMetrics()
