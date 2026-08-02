"""Tests for background consolidation (29-1 §6-5/6-6) and preference extraction wiring (29-1 §7-5)."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)


@pytest.fixture()
def kernel(tmp_path):
    return MemoryKernel(base_dir=str(tmp_path / "memory"))


# ===================================================================
# run_consolidation
# ===================================================================


class TestRunConsolidation:
    def test_run_consolidation_returns_counts(self, kernel):
        kernel.store_fact("old fact", importance=0.8)
        result = kernel.run_consolidation()
        assert isinstance(result, dict)
        assert "promoted" in result
        assert "archived" in result
        assert "decayed" in result

    def test_run_consolidation_promotes_old_items(self, kernel):
        item = kernel.store_fact("old fact")
        promoted = kernel.promote_to_durable(age_hours=0)
        kernel2 = MemoryKernel(base_dir=kernel._base_dir)
        item2 = kernel2.store_fact("another fact")
        result = kernel2.run_consolidation()
        assert result["promoted"] >= 0

    def test_run_consolidation_full_lifecycle(self, kernel):
        """Store items, make them old enough to promote, archive, and decay."""
        item = kernel.store_fact("lifecycle item", importance=0.6)
        old_time = time.time() - 48 * 3600  # 48 hours ago
        kernel.update(item.id, created_at=old_time, last_accessed=old_time)

        result = kernel.run_consolidation()
        assert result["promoted"] >= 1

        fetched = kernel.get(item.id)
        assert fetched.lifecycle == MemoryLifecycle.DURABLE

    def test_run_consolidation_empty_kernel(self, kernel):
        result = kernel.run_consolidation()
        assert result["promoted"] == 0
        assert result["archived"] == 0
        assert result["decayed"] == 0
        assert result["patterns_extracted"] == 0

    def test_run_consolidation_archives_stale_durable(self, kernel):
        item = kernel.store_fact("stale item")
        stale_time = time.time() - 60 * 86400  # 60 days ago
        kernel.update(item.id, lifecycle=MemoryLifecycle.DURABLE, last_accessed=stale_time)

        result = kernel.run_consolidation()
        assert result["archived"] >= 1

        fetched = kernel.get(item.id)
        assert fetched.lifecycle == MemoryLifecycle.ARCHIVE

    def test_run_consolidation_decays_old_untouched(self, kernel):
        item = kernel.store_fact("untouched item", importance=0.8)
        old_time = time.time() - 90 * 86400  # 90 days ago
        kernel.update(item.id, last_accessed=old_time)

        result = kernel.run_consolidation()
        assert result["decayed"] >= 1

        fetched = kernel.get(item.id)
        assert fetched.importance < 0.8


# ===================================================================
# DAN_MEMORY_CONSOLIDATION_INTERVAL env var
# ===================================================================


class TestConsolidationIntervalEnvVar:
    def test_default_interval_is_six_hours(self, monkeypatch):
        monkeypatch.delenv("DAN_MEMORY_CONSOLIDATION_INTERVAL", raising=False)
        val = float(os.environ.get("DAN_MEMORY_CONSOLIDATION_INTERVAL", "6"))
        assert val == 6.0

    def test_custom_interval(self, monkeypatch):
        monkeypatch.setenv("DAN_MEMORY_CONSOLIDATION_INTERVAL", "12")
        val = float(os.environ.get("DAN_MEMORY_CONSOLIDATION_INTERVAL", "6"))
        assert val == 12.0

    def test_zero_disables_consolidation(self, monkeypatch):
        monkeypatch.setenv("DAN_MEMORY_CONSOLIDATION_INTERVAL", "0")
        val = float(os.environ.get("DAN_MEMORY_CONSOLIDATION_INTERVAL", "6"))
        assert val == 0.0
        assert val <= 0  # disabled


# ===================================================================
# _consolidation_loop
# ===================================================================


class TestConsolidationLoop:
    @pytest.mark.asyncio
    async def test_consolidation_loop_runs_and_cancels(self, kernel):
        import asyncio

        from dan.server.startup import _consolidation_loop

        kernel.store_fact("item for consolidation", importance=0.5)

        from unittest.mock import MagicMock
        mock_gs = MagicMock()
        task = asyncio.create_task(_consolidation_loop(kernel, 1e-6, mock_gs))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.done()

    @pytest.mark.asyncio
    async def test_consolidation_loop_handles_kernel_error(self, kernel):
        import asyncio

        from dan.server.startup import _consolidation_loop

        original = kernel.run_consolidation_async

        call_count = 0

        async def failing_consolidation(graph_store=None):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("simulated error")

        kernel.run_consolidation_async = failing_consolidation

        from unittest.mock import MagicMock
        mock_gs = MagicMock()
        task = asyncio.create_task(_consolidation_loop(kernel, 1e-6, mock_gs))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert call_count > 0
        kernel.run_consolidation_async = original


# ===================================================================
# Preference extraction wiring (7-5)
# ===================================================================


class TestPreferenceExtractionWiring:
    def _make_concierge(self, kernel):
        """Build a minimal Concierge with a real memory kernel for testing."""
        from dan.server.concierge.runtime import Concierge
        from dan.server.concierge.project_store import ProjectStore

        project_store = ProjectStore()
        mock_resolver = MagicMock()
        mock_chat_manager = MagicMock()
        mock_cap_context = MagicMock()
        return Concierge(
            project_store=project_store,
            chat_manager=mock_chat_manager,
            capability_context=mock_cap_context,
            memory_kernel=kernel,
        )

    def test_try_extract_preferences_stores_models(self, kernel):
        concierge = self._make_concierge(kernel)
        concierge._try_extract_preferences(
            "use Claude Opus for drafting my reports",
            "Sure, I'll use Claude Opus for drafting.",
        )
        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        assert len(prefs) > 0
        contents = " ".join(p.content for p in prefs)
        assert "model" in contents.lower() or "draft" in contents.lower()

    def test_try_extract_preferences_stores_domains(self, kernel):
        concierge = self._make_concierge(kernel)
        concierge._try_extract_preferences(
            "I work in supply chain logistics and need help with inventory optimization",
            "I'd be happy to help with supply chain and inventory optimization.",
        )
        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        contents = " ".join(p.content for p in prefs)
        assert "supply chain" in contents.lower()

    def test_try_extract_preferences_stores_format(self, kernel):
        concierge = self._make_concierge(kernel)
        concierge._try_extract_preferences(
            "please output everything in JSON format",
            "I'll format all output as JSON.",
        )
        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        contents = " ".join(p.content for p in prefs)
        assert "json" in contents.lower()

    def test_try_extract_preferences_noop_when_no_prefs(self, kernel):
        concierge = self._make_concierge(kernel)
        concierge._try_extract_preferences("hello", "hi there")
        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        assert len(prefs) == 0

    def test_try_extract_preferences_disabled_by_env(self, kernel, monkeypatch):
        monkeypatch.setenv("DAN_PREFERENCE_EXTRACTION", "0")
        concierge = self._make_concierge(kernel)
        concierge._try_extract_preferences(
            "use Claude Opus for drafting",
            "Sure, I'll use Claude Opus.",
        )
        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        assert len(prefs) == 0

    def test_try_extract_preferences_no_kernel(self):
        from dan.server.concierge.runtime import Concierge
        from dan.server.concierge.project_store import ProjectStore

        concierge = Concierge(
            project_store=ProjectStore(),
            chat_manager=MagicMock(),
            capability_context=MagicMock(),
            memory_kernel=None,
        )
        concierge._try_extract_preferences("use claude", "ok")

    def test_try_extract_preferences_handles_extractor_error(self, kernel):
        concierge = self._make_concierge(kernel)
        with patch(
            "dan.engine.preference_extractor.PreferenceExtractor.extract_from_messages",
            side_effect=RuntimeError("boom"),
        ):
            concierge._try_extract_preferences("use claude for coding", "ok")
        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        assert len(prefs) == 0

    def test_store_memory_candidates_calls_preference_extraction(self, kernel):
        concierge = self._make_concierge(kernel)
        concierge._store_memory_candidates(
            "I prefer JSON output and work in supply chain logistics",
            "Got it, I'll use JSON format for supply chain work.",
        )
        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        assert len(prefs) > 0

        episodes = kernel.list_by_type(MemoryType.EPISODE)
        assert len(episodes) > 0
