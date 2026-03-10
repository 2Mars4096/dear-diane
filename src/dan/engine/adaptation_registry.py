"""Unified adaptation governance — central registry for all adaptation candidates.

Every learning subsystem (prompt optimization, model recommendation, topology
advice, skill refinement, principles) registers candidates here instead of
managing lifecycle independently.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

AdaptationSource = Literal[
    "prompt_opt", "model_rec", "topology_adv", "skill_ref", "principle",
]
AdaptationStatus = Literal["pending", "applied", "rejected", "rolled_back"]


class AdaptationCandidate(BaseModel):
    """A proposed adaptation from any learning subsystem."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    source: AdaptationSource
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    sample_size: int = 0
    scope: str = "global"
    auto_apply: bool = False
    rollback_path: str | None = None
    status: AdaptationStatus = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    applied_at: datetime | None = None
    last_outcome: str | None = None
    description: str = ""


class AdaptationRegistry:
    """Central store for pending/applied/rejected adaptations."""

    def __init__(self) -> None:
        self._candidates: dict[str, AdaptationCandidate] = {}

    def add(self, candidate: AdaptationCandidate) -> None:
        self._candidates[candidate.id] = candidate

    def list_pending(self) -> list[AdaptationCandidate]:
        return [c for c in self._candidates.values() if c.status == "pending"]

    def list_applied(self) -> list[AdaptationCandidate]:
        return [c for c in self._candidates.values() if c.status == "applied"]

    def approve(self, id: str) -> None:
        """Mark an adaptation as applied."""
        c = self._candidates.get(id)
        if c is None:
            raise KeyError(f"Adaptation {id} not found")
        if c.status != "pending":
            raise ValueError(f"Cannot approve adaptation in state '{c.status}'")
        c.status = "applied"
        c.applied_at = datetime.now(timezone.utc)

    def reject(self, id: str) -> None:
        """Mark an adaptation as rejected."""
        c = self._candidates.get(id)
        if c is None:
            raise KeyError(f"Adaptation {id} not found")
        if c.status != "pending":
            raise ValueError(f"Cannot reject adaptation in state '{c.status}'")
        c.status = "rejected"

    def rollback(self, id: str, reason: str) -> None:
        """Roll back a previously applied adaptation."""
        c = self._candidates.get(id)
        if c is None:
            raise KeyError(f"Adaptation {id} not found")
        if c.status != "applied":
            raise ValueError(f"Cannot rollback adaptation in state '{c.status}'")
        c.status = "rolled_back"
        c.last_outcome = reason

    def check_regression(
        self,
        id: str,
        recent_quality: float,
        baseline_quality: float,
        threshold: float = 0.15,
    ) -> bool:
        """Return True if a >threshold quality drop is detected.

        Triggers automatic rollback when regression is confirmed.
        """
        c = self._candidates.get(id)
        if c is None:
            raise KeyError(f"Adaptation {id} not found")

        if baseline_quality <= 0:
            return False

        drop = (baseline_quality - recent_quality) / baseline_quality
        if drop > threshold:
            if c.status == "applied":
                self.rollback(id, f"Regression detected: {drop:.1%} quality drop")
            return True
        return False

    def get(self, id: str) -> AdaptationCandidate | None:
        return self._candidates.get(id)

    def count(self) -> int:
        return len(self._candidates)
