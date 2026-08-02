"""Tests for generation_stats (29-6 §5) and preference evolution (29-6 §6)."""

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
    Provenance,
)
from dan.engine.generation_stats import (
    GenerationStats,
    load_generation_stats,
    record_generation_outcome,
    save_generation_stats,
)


@pytest.fixture()
def tmp_kernel(tmp_path):
    return MemoryKernel(base_dir=str(tmp_path / "mk"))


# ── GenerationStats unit tests ───────────────────────────────────────────


class TestRecordAndRetrieveStats:
    def test_record_and_retrieve(self):
        stats = GenerationStats()
        stats.record("codegen", pattern="chain", success=True)
        stats.record("intent_compiler", pattern="review_loop", success=False, error_type="validation")
        assert len(stats.outcomes) == 2
        assert stats.outcomes[0].method == "codegen"
        assert stats.outcomes[1].success is False

    def test_max_outcomes_cap(self):
        stats = GenerationStats(max_outcomes=5)
        for i in range(10):
            stats.record("codegen", success=True)
        assert len(stats.outcomes) == 5


class TestPatternSuccessRate:
    def test_known_pattern(self):
        stats = GenerationStats()
        stats.record("codegen", pattern="chain", success=True)
        stats.record("codegen", pattern="chain", success=True)
        stats.record("codegen", pattern="chain", success=False)
        rate = stats.get_pattern_success_rate("chain")
        assert abs(rate - 2 / 3) < 0.01

    def test_unknown_pattern_returns_neutral(self):
        stats = GenerationStats()
        assert stats.get_pattern_success_rate("nonexistent") == 0.5


class TestCommonFailures:
    def test_returns_ranked_failures(self):
        stats = GenerationStats()
        for _ in range(3):
            stats.record("codegen", success=False, error_type="validation")
        for _ in range(5):
            stats.record("codegen", success=False, error_type="no_output")
        stats.record("intent_compiler", success=False, error_type="validation")

        failures = stats.get_common_failures(limit=5)
        assert len(failures) == 3
        assert failures[0]["error_type"] == "no_output"
        assert failures[0]["count"] == 5

    def test_empty_when_all_succeed(self):
        stats = GenerationStats()
        stats.record("codegen", success=True)
        assert stats.get_common_failures() == []


class TestFormatForPrompt:
    def test_non_empty_when_failures_exist(self):
        stats = GenerationStats()
        stats.record("codegen", success=False, error_type="validation")
        text = stats.format_for_prompt()
        assert "Common generation mistakes to avoid:" in text
        assert "validation" in text

    def test_empty_when_no_failures(self):
        stats = GenerationStats()
        stats.record("codegen", success=True)
        assert stats.format_for_prompt() == ""


class TestAdjustReuseScore:
    def test_high_gen_success_reduces_score(self):
        stats = GenerationStats()
        for _ in range(10):
            stats.record("codegen", pattern="chain", success=True)
        adjusted = stats.adjust_reuse_score("chain", 0.7)
        assert adjusted < 0.7

    def test_low_gen_success_boosts_score(self):
        stats = GenerationStats()
        for _ in range(10):
            stats.record("codegen", pattern="chain", success=False, error_type="fail")
        adjusted = stats.adjust_reuse_score("chain", 0.5)
        assert adjusted > 0.5

    def test_no_data_keeps_score(self):
        stats = GenerationStats()
        assert stats.adjust_reuse_score("chain", 0.6) == 0.6


# ── Persistence round-trip ───────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load(self, tmp_kernel):
        stats = GenerationStats()
        stats.record("codegen", pattern="chain", success=True)
        stats.record("codegen", pattern="chain", success=False, error_type="val")
        save_generation_stats(tmp_kernel, stats)

        loaded = load_generation_stats(tmp_kernel)
        assert len(loaded.outcomes) == 2
        assert loaded.outcomes[0].pattern == "chain"

    def test_record_generation_outcome_convenience(self, tmp_kernel):
        record_generation_outcome(tmp_kernel, method="codegen", success=True)
        record_generation_outcome(tmp_kernel, method="codegen", success=False, error_type="no_output")
        loaded = load_generation_stats(tmp_kernel)
        assert len(loaded.outcomes) == 2

    def test_overwrite_existing_stats(self, tmp_kernel):
        record_generation_outcome(tmp_kernel, method="codegen", success=True)
        record_generation_outcome(tmp_kernel, method="codegen", success=True)
        ws_items = tmp_kernel.list_by_type(MemoryType.WORKING_STATE, limit=100)
        gen_items = [i for i in ws_items if "generation_stats" in i.tags]
        assert len(gen_items) == 1


# ── Preference conflict resolution (29-6 §6-3) ──────────────────────────


class TestPreferenceConflictResolution:
    def test_new_confirmed_archives_old_inferred(self, tmp_kernel):
        old = tmp_kernel.store_preference(
            content="models: drafting -> gpt-4o",
            tags=["models", "drafting"],
        )
        new = tmp_kernel.store_preference(
            content="models: drafting -> claude-sonnet",
            confirmed=True,
            tags=["models", "drafting"],
        )
        old_item = tmp_kernel.get(old.id)
        assert old_item is not None
        assert old_item.lifecycle == MemoryLifecycle.ARCHIVE
        new_item = tmp_kernel.get(new.id)
        assert new_item is not None
        assert new_item.lifecycle != MemoryLifecycle.ARCHIVE

    def test_inferred_does_not_archive_confirmed(self, tmp_kernel):
        confirmed = tmp_kernel.store_preference(
            content="models: coding -> claude-opus",
            confirmed=True,
            tags=["models", "coding"],
        )
        inferred = tmp_kernel.store_preference(
            content="models: coding -> gpt-4o",
            tags=["models", "coding"],
        )
        confirmed_item = tmp_kernel.get(confirmed.id)
        assert confirmed_item is not None
        assert confirmed_item.lifecycle != MemoryLifecycle.ARCHIVE

    def test_inferred_demotes_existing_inferred(self, tmp_kernel):
        old = tmp_kernel.store_preference(
            content="output_format: latex",
            tags=["output_format"],
            importance=0.8,
        )
        new = tmp_kernel.store_preference(
            content="output_format: markdown",
            tags=["output_format"],
        )
        old_item = tmp_kernel.get(old.id)
        assert old_item is not None
        assert old_item.importance < 0.8


# ── Preference surfacing (29-6 §6-4) ─────────────────────────────────────


class TestPreferenceSurfacingInterval:
    def test_surfaces_at_correct_interval(self, tmp_kernel):
        tmp_kernel.store_preference(content="domains: supply chain", importance=0.5)
        result_5 = tmp_kernel.maybe_surface_preferences(session_count=5)
        assert result_5 == []

        result_10 = tmp_kernel.maybe_surface_preferences(session_count=10)
        assert len(result_10) >= 1

    def test_does_not_surface_at_zero(self, tmp_kernel):
        tmp_kernel.store_preference(content="domains: supply chain", importance=0.5)
        assert tmp_kernel.maybe_surface_preferences(session_count=0) == []

    def test_respects_env_override(self, tmp_kernel, monkeypatch):
        monkeypatch.setenv("DAN_PREFERENCE_SURFACE_INTERVAL", "3")
        tmp_kernel.store_preference(content="domains: ML", importance=0.5)
        assert tmp_kernel.maybe_surface_preferences(session_count=3) != []
        assert tmp_kernel.maybe_surface_preferences(session_count=4) == []

    def test_skips_confirmed_preferences(self, tmp_kernel):
        tmp_kernel.store_preference(content="domains: ML", confirmed=True, importance=0.6)
        result = tmp_kernel.maybe_surface_preferences(session_count=10)
        assert all(not item.provenance.confirmed_by_user for item in result)

    def test_skips_low_importance(self, tmp_kernel):
        tmp_kernel.store_preference(content="domains: ML", importance=0.1)
        result = tmp_kernel.maybe_surface_preferences(session_count=10)
        assert result == []


# ── Preference confirm / reject (29-6 §6-5) ─────────────────────────────


class TestPreferenceConfirmBoost:
    def test_confirm_sets_max_importance(self, tmp_kernel):
        pref = tmp_kernel.store_preference(content="models: drafting -> claude", importance=0.5)
        result = tmp_kernel.confirm_preference(pref.id)
        assert result is not None
        assert result.importance == 1.0
        assert result.provenance.confirmed_by_user is True
        assert result.lifecycle == MemoryLifecycle.DURABLE

    def test_confirm_nonexistent_returns_none(self, tmp_kernel):
        assert tmp_kernel.confirm_preference("nonexistent") is None


class TestPreferenceRejectArchive:
    def test_reject_archives_preference(self, tmp_kernel):
        pref = tmp_kernel.store_preference(content="models: coding -> gpt-4o", importance=0.5)
        result = tmp_kernel.reject_preference(pref.id)
        assert result is not None
        assert result.lifecycle == MemoryLifecycle.ARCHIVE
        assert result.metadata.get("rejected_by_user") is True

    def test_reject_nonexistent_returns_none(self, tmp_kernel):
        assert tmp_kernel.reject_preference("nonexistent") is None

    def test_rejected_not_surfaced_again(self, tmp_kernel):
        pref = tmp_kernel.store_preference(content="domains: supply chain", importance=0.5)
        tmp_kernel.reject_preference(pref.id)
        result = tmp_kernel.maybe_surface_preferences(session_count=10)
        assert all(item.id != pref.id for item in result)
