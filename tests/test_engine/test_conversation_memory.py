"""Tests for ConversationMemoryStore (Plan 26-3)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dan.engine.conversation_memory import (
    ConversationMemoryStore,
    ConversationSummary,
)


# ---------------------------------------------------------------------------
# ConversationSummary model
# ---------------------------------------------------------------------------

class TestConversationSummary:
    def test_defaults(self):
        s = ConversationSummary(summary="hello")
        assert len(s.id) == 12
        assert s.summary == "hello"
        assert s.workflow_id == ""
        assert s.topic_tags == []
        assert s.timestamp > 0

    def test_unique_ids(self):
        s1 = ConversationSummary(summary="a")
        s2 = ConversationSummary(summary="b")
        assert s1.id != s2.id


# ---------------------------------------------------------------------------
# Empty store
# ---------------------------------------------------------------------------

class TestEmptyStore:
    def test_count_zero(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        assert store.count == 0

    def test_get_recent_empty(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        assert store.get_recent() == []

    def test_search_empty(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        assert store.search_by_keywords(["anything"]) == []

    def test_format_context_block_empty(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        assert store.format_context_block() == ""


# ---------------------------------------------------------------------------
# add_summary + get_recent
# ---------------------------------------------------------------------------

class TestAddAndRetrieve:
    def test_add_single(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        entry = store.add_summary("Discussed supply chain optimization")
        assert store.count == 1
        assert entry.summary == "Discussed supply chain optimization"

    def test_add_with_metadata(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        entry = store.add_summary(
            "Ran equity pipeline",
            workflow_id="eq-research",
            topic_tags=["equity", "backtest"],
        )
        assert entry.workflow_id == "eq-research"
        assert entry.topic_tags == ["equity", "backtest"]

    def test_get_recent_ordering(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        store.add_summary("first")
        store.add_summary("second")
        store.add_summary("third")
        recent = store.get_recent(2)
        assert len(recent) == 2
        assert recent[0].summary == "third"
        assert recent[1].summary == "second"

    def test_get_recent_respects_limit(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        for i in range(10):
            store.add_summary(f"entry {i}")
        assert len(store.get_recent(3)) == 3
        assert len(store.get_recent(20)) == 10


# ---------------------------------------------------------------------------
# max_entries trimming
# ---------------------------------------------------------------------------

class TestMaxEntries:
    def test_trims_beyond_max(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem", max_entries=5)
        for i in range(8):
            store.add_summary(f"entry {i}")
        assert store.count == 5
        assert store.get_recent(1)[0].summary == "entry 7"

    def test_oldest_dropped(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem", max_entries=3)
        store.add_summary("old")
        store.add_summary("mid")
        store.add_summary("new")
        store.add_summary("newest")
        summaries = [e.summary for e in store.get_recent(10)]
        assert "old" not in summaries
        assert "newest" in summaries


# ---------------------------------------------------------------------------
# search_by_keywords
# ---------------------------------------------------------------------------

class TestSearch:
    def test_single_keyword(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        store.add_summary("Built a supply chain optimizer")
        store.add_summary("Discussed ML training pipeline")
        store.add_summary("Reviewed equity backtest results")
        results = store.search_by_keywords(["supply"])
        assert len(results) == 1
        assert "supply" in results[0].summary.lower()

    def test_multiple_keywords_scored(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        store.add_summary("ML model for supply chain demand")
        store.add_summary("Simple hello world test")
        store.add_summary("Supply chain logistics with ML optimization")
        results = store.search_by_keywords(["supply", "ml"], limit=3)
        # Entries matching both keywords should rank higher
        assert len(results) >= 2
        top = results[0]
        assert "supply" in top.summary.lower() or "ml" in top.summary.lower()

    def test_search_includes_tags(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        store.add_summary("General discussion", topic_tags=["finance", "equity"])
        results = store.search_by_keywords(["equity"])
        assert len(results) == 1

    def test_search_case_insensitive(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        store.add_summary("SUPPLY CHAIN analysis done")
        results = store.search_by_keywords(["supply"])
        assert len(results) == 1

    def test_search_no_match(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        store.add_summary("Hello world")
        results = store.search_by_keywords(["quantum"])
        assert len(results) == 0

    def test_search_respects_limit(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        for i in range(10):
            store.add_summary(f"supply chain item {i}")
        results = store.search_by_keywords(["supply"], limit=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# format_context_block
# ---------------------------------------------------------------------------

class TestFormatContextBlock:
    def test_basic_format(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        store.add_summary("Optimized inventory model", workflow_id="inv-opt")
        store.add_summary("Drafted equity report")
        block = store.format_context_block(n=5)
        assert "Recent conversation context:" in block
        assert "Drafted equity report" in block
        assert "Optimized inventory model" in block
        assert "(workflow: inv-opt)" in block

    def test_respects_n(self, tmp_path: Path):
        store = ConversationMemoryStore(base_dir=tmp_path / "mem")
        for i in range(10):
            store.add_summary(f"entry {i}")
        block = store.format_context_block(n=2)
        assert "entry 9" in block
        assert "entry 8" in block
        assert "entry 7" not in block


# ---------------------------------------------------------------------------
# Persistence (save/load round-trip)
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_data_survives_reload(self, tmp_path: Path):
        mem_dir = tmp_path / "mem"
        store1 = ConversationMemoryStore(base_dir=mem_dir)
        store1.add_summary("alpha", workflow_id="wf-1", topic_tags=["tag1"])
        store1.add_summary("beta")

        store2 = ConversationMemoryStore(base_dir=mem_dir)
        assert store2.count == 2
        recent = store2.get_recent(2)
        assert recent[0].summary == "beta"
        assert recent[1].summary == "alpha"
        assert recent[1].workflow_id == "wf-1"
        assert recent[1].topic_tags == ["tag1"]

    def test_corrupt_index_recovers(self, tmp_path: Path):
        mem_dir = tmp_path / "mem"
        mem_dir.mkdir(parents=True)
        (mem_dir / "_index.json").write_text("BROKEN JSON!!!", encoding="utf-8")
        store = ConversationMemoryStore(base_dir=mem_dir)
        assert store.count == 0

    def test_no_tmp_file_after_save(self, tmp_path: Path):
        mem_dir = tmp_path / "mem"
        store = ConversationMemoryStore(base_dir=mem_dir)
        store.add_summary("test")
        assert not (mem_dir / "_index.tmp").exists()

    def test_creates_directory(self, tmp_path: Path):
        mem_dir = tmp_path / "deep" / "nested" / "mem"
        store = ConversationMemoryStore(base_dir=mem_dir)
        assert mem_dir.exists()
