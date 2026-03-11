"""Unified adaptation governance — central registry for all adaptation candidates.

Every learning subsystem (prompt optimization, model recommendation, topology
advice, skill refinement, principles) registers candidates here instead of
managing lifecycle independently.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from dan.engine.behavior_store import (
        AdaptableParameterRegistry,
        BehaviorChangeLog,
    )

logger = logging.getLogger(__name__)

AdaptationSource = Literal[
    "prompt_opt", "model_rec", "topology_adv", "skill_ref", "principle",
    "threshold_cal", "intent_discovery", "domain_discovery", "model_tier", "tool_ref",
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

    # 31-22 task 13: audit and linking
    before_value: str | None = None
    after_value: str | None = None
    parameter_key: str | None = None

    # 31-22 task 4-6: outcome tracking
    baseline_quality: float | None = None
    post_adaptation_quality: float | None = None
    measurement_interactions: int = 0
    measurement_target: int = 20

    # 31-22 task 13-4: scope enforcement
    queued: bool = False


class AdaptationRegistry:
    """Central store for pending/applied/rejected adaptations."""

    def __init__(
        self,
        changelog: BehaviorChangeLog | None = None,
        param_registry: AdaptableParameterRegistry | None = None,
    ) -> None:
        self._candidates: dict[str, AdaptationCandidate] = {}
        self._changelog = changelog
        self._param_registry = param_registry
        # (category, key) → active candidate id for scope enforcement
        self._active_scopes: dict[tuple[str, str], str] = {}

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _extract_scope_key(candidate: AdaptationCandidate) -> tuple[str, str] | None:
        if not candidate.parameter_key:
            return None
        parts = candidate.parameter_key.split("/", 1)
        if len(parts) == 2:
            return (parts[0], parts[1])
        return (candidate.parameter_key, candidate.parameter_key)

    def _write_changelog(self, candidate: AdaptationCandidate, action: str) -> None:
        if self._changelog is None:
            return
        from dan.engine.behavior_store import BehaviorChangeEntry

        pk = candidate.parameter_key or ""
        if "/" in pk:
            category, key = pk.split("/", 1)
        else:
            category, key = pk, pk

        self._changelog.append(BehaviorChangeEntry(
            category=category,
            key=key,
            action=action,
            before_summary=candidate.before_value or "",
            after_summary=candidate.after_value or "",
            evidence=candidate.evidence,
            source=candidate.source,
        ))

    def _set_measurement_target(self, candidate: AdaptationCandidate) -> None:
        if self._param_registry is not None and candidate.parameter_key:
            param = self._param_registry.get_by_key(candidate.parameter_key)
            if param is not None:
                candidate.measurement_target = param.min_evidence_count

    def _activate_scope(self, candidate: AdaptationCandidate) -> None:
        scope_key = self._extract_scope_key(candidate)
        if scope_key is not None:
            self._active_scopes[scope_key] = candidate.id

    def _clear_scope_and_unqueue(self, candidate: AdaptationCandidate) -> None:
        scope_key = self._extract_scope_key(candidate)
        if scope_key is None:
            return
        if self._active_scopes.get(scope_key) == candidate.id:
            del self._active_scopes[scope_key]
        for c in self._candidates.values():
            if c.queued and c.status == "pending":
                if self._extract_scope_key(c) == scope_key:
                    c.queued = False
                    logger.debug("Unqueued candidate %s for scope %s", c.id, scope_key)
                    break

    # -- public API ---------------------------------------------------------

    def add(self, candidate: AdaptationCandidate) -> None:
        scope_key = self._extract_scope_key(candidate)
        if scope_key is not None and scope_key in self._active_scopes:
            active_id = self._active_scopes[scope_key]
            active = self._candidates.get(active_id)
            if active is not None and active.status == "applied":
                candidate.queued = True
                logger.debug(
                    "Queuing candidate %s — active adaptation %s for scope %s",
                    candidate.id, active_id, scope_key,
                )
        self._candidates[candidate.id] = candidate

    def list_pending(self) -> list[AdaptationCandidate]:
        return [
            c for c in self._candidates.values()
            if c.status == "pending" and not c.queued
        ]

    def list_applied(self) -> list[AdaptationCandidate]:
        return [c for c in self._candidates.values() if c.status == "applied"]

    def list_queued(self) -> list[AdaptationCandidate]:
        return [c for c in self._candidates.values() if c.queued]

    def approve(self, id: str, baseline_quality: float = 0.0) -> None:
        """Mark an adaptation as applied."""
        c = self._candidates.get(id)
        if c is None:
            raise KeyError(f"Adaptation {id} not found")
        if c.status != "pending":
            raise ValueError(f"Cannot approve adaptation in state '{c.status}'")
        c.status = "applied"
        c.applied_at = datetime.now(timezone.utc)
        c.baseline_quality = baseline_quality
        c.queued = False
        self._set_measurement_target(c)
        self._activate_scope(c)
        self._write_changelog(c, "approve")

    def auto_apply_candidate(self, id: str, baseline_quality: float = 0.0) -> None:
        """Auto-apply a candidate and log the change."""
        c = self._candidates.get(id)
        if c is None:
            raise KeyError(f"Adaptation {id} not found")
        if c.status != "pending":
            raise ValueError(f"Cannot auto-apply adaptation in state '{c.status}'")
        c.status = "applied"
        c.applied_at = datetime.now(timezone.utc)
        c.baseline_quality = baseline_quality
        c.queued = False
        self._set_measurement_target(c)
        self._activate_scope(c)
        self._write_changelog(c, "auto_apply")

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
        self._clear_scope_and_unqueue(c)
        self._write_changelog(c, "rollback")

    def record_post_adaptation_outcome(
        self, id: str, quality_metric: float, interaction_count: int,
    ) -> None:
        """Record quality data after an adaptation has been applied.

        Automatically triggers regression check when enough interactions
        have been observed (measurement_interactions >= measurement_target).
        """
        c = self._candidates.get(id)
        if c is None:
            raise KeyError(f"Adaptation {id} not found")
        c.post_adaptation_quality = quality_metric
        c.measurement_interactions = interaction_count
        if interaction_count >= c.measurement_target:
            baseline = c.baseline_quality if c.baseline_quality is not None else 0.0
            self.check_regression(id, quality_metric, baseline)

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

    def needs_measurement(self) -> list[AdaptationCandidate]:
        """Return applied candidates whose measurement window is incomplete."""
        return [
            c for c in self._candidates.values()
            if c.status == "applied" and c.measurement_interactions < c.measurement_target
        ]

    def complete_measurement(self, id: str) -> None:
        """Finalize the measurement window — log the outcome and update last_outcome."""
        c = self._candidates.get(id)
        if c is None:
            raise KeyError(f"Adaptation {id} not found")
        baseline = c.baseline_quality if c.baseline_quality is not None else 0.0
        post = c.post_adaptation_quality if c.post_adaptation_quality is not None else 0.0
        if baseline > 0:
            delta_pct = (post - baseline) / baseline
            sign = "+" if delta_pct >= 0 else ""
            c.last_outcome = (
                f"Measurement complete: quality {baseline:.3f} → {post:.3f} "
                f"({sign}{delta_pct:.1%})"
            )
        else:
            c.last_outcome = f"Measurement complete: post-adaptation quality {post:.3f}"
        logger.info("Measurement complete for %s: %s", id, c.last_outcome)

    def get(self, id: str) -> AdaptationCandidate | None:
        return self._candidates.get(id)

    def count(self) -> int:
        return len(self._candidates)
