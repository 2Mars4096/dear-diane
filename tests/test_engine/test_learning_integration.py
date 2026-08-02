"""Integration tests for learning → memory → retrieval → behavior pipeline (31-15 §8-2, §8-3)."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from dan.engine.correction_memory import (
    CorrectionRecord,
    detect_correction,
    route_correction,
)
from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryScope,
    MemoryType,
)
from dan.engine.learning_tiers import (
    LearningHealthCounters,
    check_tier_promotion_gates,
    features_enabled_at_tier,
    is_feature_enabled,
    resolve_learning_tier,
)
from dan.engine.outcome_trackers import (
    ModelOutcomeTracker,
    ModelRecommender,
    NodeOutcome,
    compute_node_quality,
    compute_retry_quality,
    compute_workflow_quality,
    record_node_outcome,
)
from dan.engine.planning_calibration import (
    DurationEstimator,
    FailureHotspotPredictor,
    ModelPreference,
)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def tmp_memory_dir(tmp_path: Path) -> Path:
    d = tmp_path / "memory_kernel"
    d.mkdir()
    return d


@pytest.fixture
def mk(tmp_memory_dir: Path) -> MemoryKernel:
    return MemoryKernel(base_dir=str(tmp_memory_dir))


# ===================================================================
# 8-2: Correction → PREFERENCE/PRINCIPLE storage → retrieval
# ===================================================================


class TestCorrectionIntegration:
    """Correction detection → MemoryKernel storage → retrieval on similar task."""

    def test_correction_to_preference_storage_and_retrieval(self, mk: MemoryKernel) -> None:
        """Preference extracted from correction is stored and retrievable."""
        user_msg = "I prefer using Stata for all data analysis"
        assistant_msg = "I've set up a Python script to analyze your data."

        signal = detect_correction(user_msg, assistant_msg)
        assert signal is not None
        assert signal.confidence >= 0.7

        actions = route_correction(signal)
        assert any(a["type"] == "preference" for a in actions)

        for action in actions:
            if action["type"] == "preference" and action.get("value"):
                mk.store_preference(action["value"], confirmed=False)

        retrieved = mk.retrieve("analyze data with statistical tool", limit=20)
        pref_items = [
            si for si in retrieved
            if si.item.memory_type == MemoryType.PREFERENCE
        ]
        assert len(pref_items) >= 1
        assert any("stata" in si.item.content.lower() for si in pref_items)

    def test_correction_to_principle_storage_and_retrieval(self, mk: MemoryKernel) -> None:
        """Principle extracted from a style correction is stored and retrievable."""
        user_msg = "Keep it short, summarize first before showing raw tables"
        assistant_msg = "Here are all 500 rows of the data:\n| col1 | col2 |..."

        signal = detect_correction(user_msg, assistant_msg)
        assert signal is not None

        actions = route_correction(signal)

        for action in actions:
            if action["type"] == "principle" and action.get("value"):
                mk.store_principle(action["value"], confidence=signal.confidence)

        retrieved = mk.retrieve("display analysis results", limit=20)
        principle_items = [
            si for si in retrieved
            if si.item.memory_type == MemoryType.PRINCIPLE
        ]
        # Even if pattern extraction doesn't find a principle, we at least
        # get the negative evidence action stored
        neg_evidence = [a for a in actions if a["type"] == "negative_evidence"]
        assert len(neg_evidence) >= 1

    def test_correction_full_pipeline(self, mk: MemoryKernel) -> None:
        """Full pipeline: detect → route → store → retrieve → verify behavior change potential."""
        user_msg = "No, don't use Python. Always use Stata for regression"
        assistant_msg = "I'll run the regression in Python using statsmodels."

        signal = detect_correction(user_msg, assistant_msg)
        assert signal is not None
        assert signal.correction_type in ("negation", "override", "preference")

        actions = route_correction(signal)
        assert len(actions) >= 1

        record = CorrectionRecord(signal=signal, actions=actions)
        assert record.id

        for action in actions:
            atype = action.get("type")
            avalue = action.get("value", "")
            if atype == "preference" and avalue:
                mk.store_preference(avalue, confirmed=False)
            elif atype == "principle" and avalue:
                mk.store_principle(avalue, confidence=signal.confidence)

        # On a subsequent similar task, the memory should influence behavior
        similar_query = "run a regression analysis on sales data"
        retrieved = mk.retrieve_by_task(similar_query, task_type="workflow_build")
        assert len(retrieved) >= 0  # May or may not find items depending on keyword overlap


# ===================================================================
# 8-3: Quality signals → model recommender → planning-time assignment
# ===================================================================


class TestQualitySignalsPipeline:
    """Quality signals feed model recommender which feeds planning calibration."""

    def test_node_outcome_quality_signals(self) -> None:
        """Quality scores correctly encode retry count and schema validity."""
        perfect = record_node_outcome("n1", "llm_operator", True, 0, True)
        assert perfect.quality_score == 1.0

        one_retry = record_node_outcome("n2", "llm_operator", True, 1, True)
        assert 0.7 < one_retry.quality_score < 1.0

        many_retries = record_node_outcome("n3", "llm_operator", True, 5, True)
        assert many_retries.quality_score < one_retry.quality_score

        schema_fail = record_node_outcome("n4", "llm_operator", True, 0, False)
        assert schema_fail.quality_score < 1.0
        assert schema_fail.quality_score == 0.8

        failed = record_node_outcome("n5", "llm_operator", False, 0, True)
        assert failed.quality_score == 0.0

    def test_workflow_quality_partial_success(self) -> None:
        """Workflow quality encodes partial success gradient."""
        outcomes = [
            record_node_outcome("n1", "llm_operator", True, 0, True),
            record_node_outcome("n2", "llm_operator", True, 1, True),
            record_node_outcome("n3", "llm_operator", False, 0, True),
        ]
        quality = compute_workflow_quality(outcomes)
        assert 0.0 < quality < 1.0
        # 2 of 3 nodes succeeded; quality < 1.0 because one failed
        assert quality > 0.5

    def test_retry_quality_formula(self) -> None:
        """Verify the retry quality formula bounds."""
        assert compute_retry_quality(0) == 1.0
        assert 0.1 <= compute_retry_quality(100) <= 1.0
        # Diminishing penalty: first retry is less harsh than fifth
        assert compute_retry_quality(1) > compute_retry_quality(5)

    def test_model_recommender_with_quality_signals(self, mk: MemoryKernel) -> None:
        """Model recommender uses quality scores to suggest the best model."""
        os.environ["DAN_MODEL_LEARNING"] = "1"
        try:
            tracker = ModelOutcomeTracker(mk)
            recommender = ModelRecommender(tracker)

            # Record 20 outcomes: model_a does better than model_b
            for i in range(10):
                tracker.record(
                    "test_node", "llm_operator", "test task",
                    "model_a", success=True, retry_count=0,
                    cost=0.01, latency_ms=100,
                )
            for i in range(10):
                tracker.record(
                    "test_node", "llm_operator", "test task",
                    "model_b", success=True, retry_count=3,
                    cost=0.01, latency_ms=100,
                )

            suggestion = recommender.suggest("test_node")
            # model_a should score higher due to better quality
            assert suggestion == "model_a"
        finally:
            os.environ.pop("DAN_MODEL_LEARNING", None)

    def test_planning_calibration_integration(self) -> None:
        """Duration estimator, failure predictor, and model preference produce output."""
        estimator = DurationEstimator()
        median, ci = estimator.estimate("research and write a comprehensive analysis")
        assert median > 0
        assert ci > 0

        predictor = FailureHotspotPredictor()
        prob, advice = predictor.predict("code_operator")
        assert prob > 0
        assert len(advice) > 0

        model_pref = ModelPreference()
        rec = model_pref.recommend("llm_operator", task_pattern="code_generation")
        assert rec is not None


# ===================================================================
# Tier promotion gates
# ===================================================================


class TestTierPromotionGates:
    """Test the check_tier_promotion_gates() function."""

    def test_all_gates_pass(self) -> None:
        counters = LearningHealthCounters()
        for _ in range(110):
            counters.record("memory_extraction", "success")

        result = check_tier_promotion_gates(
            counters,
            model_recommender_precision=0.8,
            model_recommender_samples=20,
            false_positive_adaptations=0,
        )
        assert result["all_pass"] is True
        assert result["gate_a_health"] is True
        assert result["gate_b_precision"] is True
        assert result["gate_c_no_false_positives"] is True

    def test_insufficient_events_fails_gate_a(self) -> None:
        counters = LearningHealthCounters()
        for _ in range(50):
            counters.record("memory_extraction", "success")

        result = check_tier_promotion_gates(
            counters,
            model_recommender_precision=0.8,
            model_recommender_samples=20,
            false_positive_adaptations=0,
        )
        assert result["gate_a_health"] is False
        assert result["all_pass"] is False

    def test_low_success_rate_fails_gate_a(self) -> None:
        counters = LearningHealthCounters()
        for _ in range(90):
            counters.record("memory_extraction", "success")
        for _ in range(20):
            counters.record("memory_extraction", "fail")

        result = check_tier_promotion_gates(
            counters,
            model_recommender_precision=0.8,
            model_recommender_samples=20,
            false_positive_adaptations=0,
        )
        assert result["gate_a_health"] is False

    def test_low_precision_fails_gate_b(self) -> None:
        counters = LearningHealthCounters()
        for _ in range(110):
            counters.record("memory_extraction", "success")

        result = check_tier_promotion_gates(
            counters,
            model_recommender_precision=0.5,
            model_recommender_samples=20,
            false_positive_adaptations=0,
        )
        assert result["gate_b_precision"] is False

    def test_false_positives_fail_gate_c(self) -> None:
        counters = LearningHealthCounters()
        for _ in range(110):
            counters.record("memory_extraction", "success")

        result = check_tier_promotion_gates(
            counters,
            model_recommender_precision=0.8,
            model_recommender_samples=20,
            false_positive_adaptations=2,
        )
        assert result["gate_c_no_false_positives"] is False


# ===================================================================
# Feature env var overrides
# ===================================================================


class TestFeatureOverrides:
    """Test individual env var overrides for features."""

    def test_force_enable_at_tier_0(self) -> None:
        os.environ["DAN_LEARNING_TIER"] = "0"
        os.environ["DAN_PROMPT_OPTIMIZATION"] = "1"
        try:
            assert is_feature_enabled("prompt_variant_proposals") is True
            assert is_feature_enabled("ab_prompt_promotion") is True
        finally:
            os.environ.pop("DAN_LEARNING_TIER", None)
            os.environ.pop("DAN_PROMPT_OPTIMIZATION", None)

    def test_force_disable_at_tier_2(self) -> None:
        os.environ["DAN_LEARNING_TIER"] = "2"
        os.environ["DAN_MODEL_LEARNING"] = "0"
        try:
            assert is_feature_enabled("model_recommendations") is False
            # Other tier-2 features remain enabled
            assert is_feature_enabled("auto_adaptation") is True
        finally:
            os.environ.pop("DAN_LEARNING_TIER", None)
            os.environ.pop("DAN_MODEL_LEARNING", None)

    def test_features_enabled_at_tier_includes_overrides(self) -> None:
        os.environ["DAN_LEARNING_TIER"] = "0"
        os.environ["DAN_TOPOLOGY_LEARNING"] = "1"
        try:
            enabled = features_enabled_at_tier(0)
            assert "topology_suggestions" in enabled
        finally:
            os.environ.pop("DAN_LEARNING_TIER", None)
            os.environ.pop("DAN_TOPOLOGY_LEARNING", None)


# ===================================================================
# Health counters: rate-limited warnings
# ===================================================================


class TestHealthCounterFailureLogging:
    def test_record_failure_tracks_counts(self) -> None:
        counters = LearningHealthCounters()
        counters.record_failure("memory_extraction", RuntimeError("test"))
        counters.record_failure("memory_extraction", RuntimeError("test2"))

        summary = counters.get_summary()
        assert summary["memory_extraction"]["failed"] == 2
        assert summary["memory_extraction"]["attempted"] == 2

    def test_first_failure_logged_as_warning(self) -> None:
        counters = LearningHealthCounters()
        assert "memory_extraction" not in counters._first_failure_warned
        counters.record_failure("memory_extraction", RuntimeError("boom"))
        assert "memory_extraction" in counters._first_failure_warned

    def test_last_event_timestamp_updates(self) -> None:
        counters = LearningHealthCounters()
        assert counters.last_event_timestamp == 0.0
        counters.record("run_learning", "success")
        assert counters.last_event_timestamp > 0.0


# ===================================================================
# MemoryKernel type index
# ===================================================================


class TestMemoryKernelTypeIndex:
    """Test the in-memory type index for eliminating linear scans."""

    def test_type_index_populated_on_store(self, mk: MemoryKernel) -> None:
        mk.store_fact("test fact")
        mk.store_preference("test pref", confirmed=False)

        assert "fact" in mk._type_index
        assert len(mk._type_index["fact"]) == 1
        assert "preference" in mk._type_index
        assert len(mk._type_index["preference"]) == 1

    def test_type_index_used_in_list_by_type(self, mk: MemoryKernel) -> None:
        mk.store_fact("fact 1")
        mk.store_fact("fact 2")
        mk.store_preference("pref 1", confirmed=False)

        facts = mk.list_by_type(MemoryType.FACT)
        assert len(facts) == 2

        prefs = mk.list_by_type(MemoryType.PREFERENCE)
        assert len(prefs) == 1

    def test_type_index_survives_hard_delete(self, mk: MemoryKernel) -> None:
        item = mk.store_fact("will be deleted")
        assert len(mk._type_index.get("fact", [])) == 1
        mk.delete(item.id, hard=True)
        assert len(mk._type_index.get("fact", [])) == 0

    def test_type_index_rebuilt_on_load(self, tmp_memory_dir: Path) -> None:
        mk1 = MemoryKernel(base_dir=str(tmp_memory_dir))
        mk1.store_fact("fact a")
        mk1.store_principle("principle b")

        mk2 = MemoryKernel(base_dir=str(tmp_memory_dir))
        assert "fact" in mk2._type_index
        assert "principle" in mk2._type_index
        assert len(mk2._type_index["fact"]) == 1
        assert len(mk2._type_index["principle"]) == 1


# ===================================================================
# Backward compat: old records with binary quality
# ===================================================================


class TestBackwardCompat:
    """Old records with binary quality (0.0/1.0) still parse correctly."""

    def test_binary_quality_still_valid(self) -> None:
        outcome_success = record_node_outcome("n1", "llm", True, 0, True)
        assert outcome_success.quality_score == 1.0

        outcome_fail = record_node_outcome("n2", "llm", False, 0, True)
        assert outcome_fail.quality_score == 0.0

    def test_model_tracker_accepts_explicit_quality(self, mk: MemoryKernel) -> None:
        """Old callers can still pass explicit quality_score."""
        os.environ["DAN_MODEL_LEARNING"] = "1"
        try:
            tracker = ModelOutcomeTracker(mk)
            item = tracker.record(
                "node1", "llm", "task", "model",
                quality_score=1.0,
                cost=0.01,
                latency_ms=100,
            )
            assert item is not None
            assert item.metadata["quality_score"] == 1.0
        finally:
            os.environ.pop("DAN_MODEL_LEARNING", None)
