"""Tests for Plan 29-6 §9: Skill effectiveness tracking, promotion, and refinement."""

from __future__ import annotations

import os
import tempfile

import pytest

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)
from dan.engine.skill_tracker import (
    SkillEffectivenessTracker,
    SkillPromoter,
    SkillRefiner,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _enable_skill_learning(monkeypatch):
    """Enable the skill learning env gate for all tests."""
    monkeypatch.setenv("DAN_SKILL_LEARNING", "1")


@pytest.fixture()
def mk(tmp_path):
    """Fresh in-memory MemoryKernel backed by a temp directory."""
    return MemoryKernel(base_dir=str(tmp_path / "mem"))


# ---------------------------------------------------------------------------
# SkillEffectivenessTracker
# ---------------------------------------------------------------------------


class TestSkillEffectivenessTracker:
    """9-1, 9-2, 9-3: record, compute effectiveness, classify skills."""

    def test_record_execution_stores_episode(self, mk):
        tracker = SkillEffectivenessTracker(mk)
        item = tracker.record_execution(
            node_id="n1",
            skills_applied=["skill_a"],
            outcome="success",
            quality_score=0.9,
        )
        assert item is not None
        assert item.memory_type == MemoryType.EPISODE
        assert item.metadata["tracker"] == "skill_effectiveness"
        assert item.metadata["skills_applied"] == ["skill_a"]
        assert item.metadata["success"] is True

    def test_record_execution_disabled(self, mk, monkeypatch):
        monkeypatch.setenv("DAN_SKILL_LEARNING", "0")
        tracker = SkillEffectivenessTracker(mk)
        assert tracker.record_execution("n1", ["s"], "success") is None

    def test_compute_effectiveness_insufficient_data(self, mk):
        tracker = SkillEffectivenessTracker(mk)
        for i in range(5):
            tracker.record_execution("n1", ["skill_a"], "success", quality_score=0.8)
        result = tracker.compute_effectiveness("skill_a", min_runs=20)
        assert result is None

    def test_compute_effectiveness_with_enough_data(self, mk):
        tracker = SkillEffectivenessTracker(mk)

        for i in range(15):
            tracker.record_execution(
                "n1", ["skill_a"], "success", quality_score=0.9,
            )
        for i in range(5):
            tracker.record_execution(
                "n1", ["skill_a"], "failure", quality_score=0.2,
            )

        for i in range(10):
            tracker.record_execution(
                "n1", [], "success", quality_score=0.6,
            )
        for i in range(10):
            tracker.record_execution(
                "n1", [], "failure", quality_score=0.3,
            )

        result = tracker.compute_effectiveness("skill_a", min_runs=20)
        assert result is not None
        assert result["skill"] == "skill_a"
        assert result["with_skill"]["runs"] == 20
        assert result["without_skill"]["runs"] == 20
        assert result["with_skill"]["success_rate"] == pytest.approx(0.75)
        assert result["without_skill"]["success_rate"] == pytest.approx(0.5)
        assert result["delta"] == pytest.approx(0.25)
        assert result["significant"] is True

    def test_get_ineffective_skills(self, mk):
        tracker = SkillEffectivenessTracker(mk)

        for i in range(12):
            tracker.record_execution("n1", ["bad_skill"], "success", quality_score=0.5)
        for i in range(8):
            tracker.record_execution("n1", ["bad_skill"], "failure", quality_score=0.2)

        for i in range(12):
            tracker.record_execution("n1", [], "success", quality_score=0.6)
        for i in range(8):
            tracker.record_execution("n1", [], "failure", quality_score=0.3)

        result = tracker.get_ineffective_skills(threshold=0.05)
        assert len(result) == 1
        assert result[0]["skill"] == "bad_skill"
        assert result[0]["delta"] == pytest.approx(0.0)

    def test_get_effective_skills(self, mk):
        tracker = SkillEffectivenessTracker(mk)

        for i in range(18):
            tracker.record_execution("n1", ["good_skill"], "success", quality_score=0.9)
        for i in range(2):
            tracker.record_execution("n1", ["good_skill"], "failure", quality_score=0.3)

        for i in range(10):
            tracker.record_execution("n1", [], "success", quality_score=0.5)
        for i in range(10):
            tracker.record_execution("n1", [], "failure", quality_score=0.3)

        result = tracker.get_effective_skills(threshold=0.1)
        assert len(result) == 1
        assert result[0]["skill"] == "good_skill"
        assert result[0]["delta"] > 0.1


# ---------------------------------------------------------------------------
# SkillPromoter
# ---------------------------------------------------------------------------


class TestSkillPromoter:
    """9-4: principle → skill promotion."""

    def _store_principle(self, mk, confidence=0.9, access_count=15, promoted=False):
        meta = {"confidence": confidence}
        if promoted:
            meta["promoted_to_skill"] = True
        item = mk.store(MemoryItem(
            content="Always validate JSON output before downstream processing",
            memory_type=MemoryType.PRINCIPLE,
            scope=MemoryScope.GLOBAL,
            lifecycle=MemoryLifecycle.DURABLE,
            access_count=access_count,
            metadata=meta,
        ))
        return item

    def test_get_promotion_candidates(self, mk):
        promoter = SkillPromoter(mk)
        p1 = self._store_principle(mk, confidence=0.9, access_count=15)
        p2 = self._store_principle(mk, confidence=0.5, access_count=15)
        p3 = self._store_principle(mk, confidence=0.9, access_count=3)
        p4 = self._store_principle(mk, confidence=0.9, access_count=15, promoted=True)

        candidates = promoter.get_promotion_candidates(
            confidence_threshold=0.8, min_applications=10,
        )
        assert len(candidates) == 1
        assert candidates[0].id == p1.id

    def test_promote_creates_workflow_pattern(self, mk):
        promoter = SkillPromoter(mk)
        principle = self._store_principle(mk)

        spec = promoter.promote(principle)
        assert spec["name"].startswith("auto_")
        assert spec["source_principle_id"] == principle.id
        assert "[Auto-generated from principle]" in spec["content"]

        patterns = mk.list_by_type(MemoryType.WORKFLOW_PATTERN)
        skill_patterns = [p for p in patterns if p.metadata.get("is_skill")]
        assert len(skill_patterns) == 1
        assert skill_patterns[0].metadata["source_principle_id"] == principle.id

    def test_promote_marks_principle(self, mk):
        promoter = SkillPromoter(mk)
        principle = self._store_principle(mk)

        promoter.promote(principle)

        updated = mk.get(principle.id)
        assert updated is not None
        assert updated.metadata.get("promoted_to_skill") is True

    def test_double_promotion_prevented(self, mk):
        promoter = SkillPromoter(mk)
        principle = self._store_principle(mk, confidence=0.9, access_count=15)

        promoter.promote(principle)
        candidates = promoter.get_promotion_candidates()
        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# SkillRefiner
# ---------------------------------------------------------------------------


class TestSkillRefiner:
    """9-5, 9-6, 9-7: refinement proposals and lineage."""

    def test_propose_refinement_not_significant(self, mk):
        refiner = SkillRefiner(mk)
        result = refiner.propose_refinement(
            "some_skill",
            {"significant": False, "delta": 0.01},
        )
        assert result is None

    def test_propose_refinement_none_effectiveness(self, mk):
        refiner = SkillRefiner(mk)
        assert refiner.propose_refinement("x", None) is None
        assert refiner.propose_refinement("x", {}) is None

    def test_propose_refinement_negative_delta(self, mk):
        """Skill hurts outcomes → propose a scoping refinement."""
        mk.store(MemoryItem(
            content="Original skill text for testing",
            memory_type=MemoryType.WORKFLOW_PATTERN,
            scope=MemoryScope.GLOBAL,
            metadata={
                "is_skill": True,
                "skill_spec": {"name": "test_skill"},
            },
        ))

        refiner = SkillRefiner(mk)
        eff = {
            "significant": True,
            "delta": -0.1,
            "quality_delta": -0.05,
            "with_skill": {"avg_retries": 1.0, "success_rate": 0.5},
            "without_skill": {"avg_retries": 0.8, "success_rate": 0.6},
        }
        result = refiner.propose_refinement("test_skill", eff)
        assert result is not None
        assert "Original skill text for testing" in result["original"]
        assert "Apply this guidance only when clearly relevant" in result["refined"]
        assert "decreases success rate" in result["rationale"]

    def test_propose_refinement_retry_increase(self, mk):
        """Skill improves success but increases retries → add format emphasis."""
        mk.store(MemoryItem(
            content="Use structured output for all responses",
            memory_type=MemoryType.WORKFLOW_PATTERN,
            scope=MemoryScope.GLOBAL,
            metadata={
                "is_skill": True,
                "skill_spec": {"name": "format_skill"},
            },
        ))

        refiner = SkillRefiner(mk)
        eff = {
            "significant": True,
            "delta": 0.15,
            "quality_delta": 0.05,
            "with_skill": {"avg_retries": 2.5, "success_rate": 0.85},
            "without_skill": {"avg_retries": 1.0, "success_rate": 0.70},
        }
        result = refiner.propose_refinement("format_skill", eff)
        assert result is not None
        assert "Follow the output format strictly" in result["refined"]

    def test_propose_refinement_no_skill_text(self, mk):
        refiner = SkillRefiner(mk)
        eff = {"significant": True, "delta": -0.1, "quality_delta": 0,
               "with_skill": {"avg_retries": 1}, "without_skill": {"avg_retries": 1}}
        assert refiner.propose_refinement("nonexistent_skill", eff) is None

    def test_store_refinement_with_lineage(self, mk):
        """Refined skill links to original via related_ids."""
        original = mk.store(MemoryItem(
            content="Original skill",
            memory_type=MemoryType.WORKFLOW_PATTERN,
            scope=MemoryScope.GLOBAL,
            metadata={"is_skill": True, "skill_spec": {"name": "orig"}},
        ))

        refiner = SkillRefiner(mk)
        refinement = {
            "original": "Original skill",
            "refined": "Original skill\n\nIMPORTANT: Be precise.",
            "rationale": "test rationale",
        }
        item = refiner.store_refinement(
            "orig", refinement, original_skill_id=original.id,
        )
        assert item.memory_type == MemoryType.WORKFLOW_PATTERN
        assert item.lifecycle == MemoryLifecycle.ACTIVE
        assert original.id in item.related_ids
        assert item.metadata["original_skill_id"] == original.id
        assert item.metadata["refinement_rationale"] == "test rationale"

    def test_store_refinement_without_original_id(self, mk):
        refiner = SkillRefiner(mk)
        refinement = {
            "original": "text",
            "refined": "text refined",
            "rationale": "reason",
        }
        item = refiner.store_refinement("s", refinement)
        assert item.related_ids == []
        assert item.metadata["original_skill_id"] is None

    def test_chained_lineage(self, mk):
        """Refined → further refined keeps the chain."""
        original = mk.store(MemoryItem(
            content="v1", memory_type=MemoryType.WORKFLOW_PATTERN,
            scope=MemoryScope.GLOBAL,
            metadata={"is_skill": True, "skill_spec": {"name": "chain"}},
        ))

        refiner = SkillRefiner(mk)

        v2 = refiner.store_refinement(
            "chain",
            {"original": "v1", "refined": "v2", "rationale": "first pass"},
            original_skill_id=original.id,
        )
        assert original.id in v2.related_ids

        v3 = refiner.store_refinement(
            "chain",
            {"original": "v2", "refined": "v3", "rationale": "second pass"},
            original_skill_id=v2.id,
        )
        assert v2.id in v3.related_ids
        assert v3.metadata["original_skill_id"] == v2.id
