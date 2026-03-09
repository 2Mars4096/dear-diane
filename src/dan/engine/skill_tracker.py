"""Skill effectiveness tracking, promotion, and refinement (29-6 §9).

Three components:
  - SkillEffectivenessTracker: compares node outcomes with vs. without skills
  - SkillPromoter: promotes high-confidence principles to skill hyperedges
  - SkillRefiner: proposes heuristic refinements for skills based on
    effectiveness data (no LLM calls in v1)

All skill evolution is opt-in and proposed, not auto-applied.
Gated by ``DAN_SKILL_LEARNING=1`` (default off).
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Any

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)

logger = logging.getLogger(__name__)

_ENV_SKILL_LEARNING = "DAN_SKILL_LEARNING"


def _is_enabled() -> bool:
    return os.environ.get(_ENV_SKILL_LEARNING, "0") == "1"


# ===================================================================
# 9-1 / 9-2 / 9-3: SkillEffectivenessTracker
# ===================================================================


class SkillEffectivenessTracker:
    """Compare node outcomes with vs. without each skill/hyperedge."""

    TAG_PREFIX = "skill_effectiveness"

    def __init__(self, memory_kernel: Any) -> None:
        self.memory_kernel = memory_kernel

    def record_execution(
        self,
        node_id: str,
        skills_applied: list[str],
        outcome: str,
        quality_score: float = 0.0,
        retries: int = 0,
        error_category: str = "",
    ) -> MemoryItem | None:
        """Record a node execution with its active skills.

        Each execution is stored as an EPISODE item whose metadata captures
        both the skills that were active and the outcome.  Effectiveness
        is computed later by comparing executions *with* a skill to those
        *without* it.
        """
        if not _is_enabled():
            return None

        success = outcome == "success"
        content = (
            f"Skill execution for node {node_id}: "
            f"skills={skills_applied!r}, outcome={outcome}, "
            f"quality={quality_score:.2f}, retries={retries}"
        )
        return self.memory_kernel.store(
            MemoryItem(
                content=content,
                memory_type=MemoryType.EPISODE,
                scope=MemoryScope.WORKFLOW,
                lifecycle=MemoryLifecycle.ACTIVE,
                tags=[self.TAG_PREFIX, f"node:{node_id}"],
                metadata={
                    "tracker": self.TAG_PREFIX,
                    "node_id": node_id,
                    "skills_applied": skills_applied,
                    "outcome": outcome,
                    "success": success,
                    "quality_score": quality_score,
                    "retries": retries,
                    "error_category": error_category,
                    "recorded_at": time.time(),
                },
            )
        )

    def _get_records(self, limit: int = 500) -> list[dict[str, Any]]:
        """Retrieve all skill effectiveness records from the memory kernel."""
        items = self.memory_kernel.list_by_type(
            MemoryType.EPISODE, scope=MemoryScope.WORKFLOW, limit=limit,
        )
        return [
            item.metadata
            for item in items
            if item.metadata.get("tracker") == self.TAG_PREFIX
        ]

    def compute_effectiveness(
        self, skill_name: str, min_runs: int = 20,
    ) -> dict[str, Any] | None:
        """Compute effectiveness delta for a skill.

        Partitions executions into "with skill" and "without skill" groups,
        then compares success rate and average quality.  Returns ``None``
        when either partition has fewer than ``min_runs`` / 2 samples.
        """
        records = self._get_records()

        with_skill: list[dict] = []
        without_skill: list[dict] = []

        for r in records:
            applied = r.get("skills_applied", [])
            if skill_name in applied:
                with_skill.append(r)
            else:
                without_skill.append(r)

        min_per_group = max(min_runs // 2, 1)
        if len(with_skill) < min_per_group or len(without_skill) < min_per_group:
            return None

        def _stats(group: list[dict]) -> dict[str, Any]:
            n = len(group)
            successes = sum(1 for r in group if r.get("success"))
            avg_quality = sum(r.get("quality_score", 0.0) for r in group) / n
            avg_retries = sum(r.get("retries", 0) for r in group) / n
            return {
                "runs": n,
                "success_rate": successes / n,
                "avg_quality": avg_quality,
                "avg_retries": avg_retries,
            }

        ws = _stats(with_skill)
        wos = _stats(without_skill)

        delta = ws["success_rate"] - wos["success_rate"]
        quality_delta = ws["avg_quality"] - wos["avg_quality"]
        total_runs = ws["runs"] + wos["runs"]

        significant = total_runs >= min_runs and abs(delta) > 0.05

        return {
            "skill": skill_name,
            "with_skill": ws,
            "without_skill": wos,
            "delta": delta,
            "quality_delta": quality_delta,
            "significant": significant,
        }

    def get_ineffective_skills(
        self, threshold: float = 0.05,
    ) -> list[dict[str, Any]]:
        """Return skills where the effectiveness delta is below *threshold*.

        A skill is "ineffective" if it doesn't measurably improve (or even
        hurts) outcomes relative to runs without it.
        """
        records = self._get_records()
        all_skills: set[str] = set()
        for r in records:
            for s in r.get("skills_applied", []):
                all_skills.add(s)

        ineffective: list[dict[str, Any]] = []
        for skill_name in sorted(all_skills):
            eff = self.compute_effectiveness(skill_name)
            if eff is None:
                continue
            if eff["delta"] < threshold:
                ineffective.append(eff)
        return ineffective

    def get_effective_skills(
        self, threshold: float = 0.1,
    ) -> list[dict[str, Any]]:
        """Return skills with significant positive effectiveness."""
        records = self._get_records()
        all_skills: set[str] = set()
        for r in records:
            for s in r.get("skills_applied", []):
                all_skills.add(s)

        effective: list[dict[str, Any]] = []
        for skill_name in sorted(all_skills):
            eff = self.compute_effectiveness(skill_name)
            if eff is None:
                continue
            if eff["significant"] and eff["delta"] >= threshold:
                effective.append(eff)
        return effective


# ===================================================================
# 9-4: SkillPromoter
# ===================================================================


class SkillPromoter:
    """Promote effective principles to skill hyperedges.

    Principles that cross a confidence and usage threshold get converted
    into WORKFLOW_PATTERN items with ``is_skill=True`` metadata so they
    can be injected as hyperedges during execution.
    """

    def __init__(self, memory_kernel: Any) -> None:
        self.memory_kernel = memory_kernel

    def get_promotion_candidates(
        self,
        confidence_threshold: float = 0.8,
        min_applications: int = 10,
    ) -> list[MemoryItem]:
        """Find principles eligible for promotion to skills."""
        principles = self.memory_kernel.list_by_type(MemoryType.PRINCIPLE)
        return [
            p
            for p in principles
            if float(p.metadata.get("confidence", 0)) >= confidence_threshold
            and p.access_count >= min_applications
            and not p.metadata.get("promoted_to_skill")
        ]

    def promote(self, principle: MemoryItem) -> dict[str, Any]:
        """Create a skill from a principle. Returns the new skill spec."""
        skill_content = f"[Auto-generated from principle] {principle.content}"
        skill_name = f"auto_{principle.id[:8]}"
        skill_spec: dict[str, Any] = {
            "name": skill_name,
            "content": skill_content,
            "source_principle_id": principle.id,
            "lifecycle": "active",
            "hook": "pre_prompt",
        }

        self.memory_kernel.store(
            MemoryItem(
                content=skill_content,
                memory_type=MemoryType.WORKFLOW_PATTERN,
                scope=MemoryScope.GLOBAL,
                lifecycle=MemoryLifecycle.ACTIVE,
                metadata={
                    "is_skill": True,
                    "skill_spec": skill_spec,
                    "source_principle_id": principle.id,
                },
            )
        )

        self.memory_kernel.update(
            principle.id,
            metadata={**principle.metadata, "promoted_to_skill": True},
        )
        return skill_spec


# ===================================================================
# 9-5 / 9-6 / 9-7: SkillRefiner
# ===================================================================


class SkillRefiner:
    """Propose refined skill text based on effectiveness analysis.

    Uses heuristic rules (not LLM) for v1.  Refinements are stored as
    candidates with ``lifecycle=ACTIVE`` and linked to the original skill
    via ``related_ids`` for lineage tracking.
    """

    def __init__(self, memory_kernel: Any) -> None:
        self.memory_kernel = memory_kernel

    def propose_refinement(
        self, skill_name: str, effectiveness: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Propose a refined version of a skill.

        Returns ``{"original": str, "refined": str, "rationale": str}``
        or ``None`` if the effectiveness data doesn't warrant a change.
        """
        if not effectiveness or not effectiveness.get("significant"):
            return None

        ws = effectiveness.get("with_skill", {})
        wos = effectiveness.get("without_skill", {})
        delta = effectiveness.get("delta", 0)
        quality_delta = effectiveness.get("quality_delta", 0)

        original_text = self._find_skill_text(skill_name)
        if not original_text:
            return None

        rationale_parts: list[str] = []
        refinements: list[str] = []

        if delta > 0 and ws.get("avg_retries", 0) > wos.get("avg_retries", 0):
            rationale_parts.append(
                f"Skill improves success (+{delta:.0%}) but increases retries "
                f"({ws['avg_retries']:.1f} vs {wos['avg_retries']:.1f})"
            )
            refinements.append(
                "IMPORTANT: Follow the output format strictly on the first attempt."
            )

        if delta < 0:
            rationale_parts.append(
                f"Skill decreases success rate by {abs(delta):.0%}"
            )
            refinements.append(
                "NOTE: Apply this guidance only when clearly relevant to the task."
            )

        if quality_delta > 0.1 and delta >= 0:
            rationale_parts.append(
                f"Skill boosts quality (+{quality_delta:.2f}) — emphasize quality aspects"
            )

        if not refinements:
            return None

        refined_text = original_text + "\n\n" + "\n".join(refinements)
        rationale = "; ".join(rationale_parts)

        return {
            "original": original_text,
            "refined": refined_text,
            "rationale": rationale,
        }

    def store_refinement(
        self,
        skill_name: str,
        refinement: dict[str, Any],
        original_skill_id: str | None = None,
    ) -> MemoryItem:
        """Persist a refinement as a WORKFLOW_PATTERN candidate.

        Links to the original skill via ``related_ids`` for lineage.
        """
        related: list[str] = []
        if original_skill_id:
            related.append(original_skill_id)

        return self.memory_kernel.store(
            MemoryItem(
                content=refinement["refined"],
                memory_type=MemoryType.WORKFLOW_PATTERN,
                scope=MemoryScope.GLOBAL,
                lifecycle=MemoryLifecycle.ACTIVE,
                related_ids=related,
                metadata={
                    "is_skill": True,
                    "skill_name": skill_name,
                    "refinement_rationale": refinement["rationale"],
                    "original_skill_id": original_skill_id,
                    "refined_at": time.time(),
                },
            )
        )

    def _find_skill_text(self, skill_name: str) -> str | None:
        """Look up skill text from the skill library or memory kernel."""
        try:
            from dan.server.skill_library import SKILL_LIBRARY

            if skill_name in SKILL_LIBRARY:
                return SKILL_LIBRARY[skill_name].get("text", "")
        except ImportError:
            pass

        patterns = self.memory_kernel.list_by_type(
            MemoryType.WORKFLOW_PATTERN, limit=200,
        )
        for p in patterns:
            if p.metadata.get("is_skill") and p.metadata.get("skill_spec", {}).get("name") == skill_name:
                return p.content
        return None
