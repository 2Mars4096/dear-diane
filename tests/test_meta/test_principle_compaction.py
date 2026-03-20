"""Tests for principle compaction (17-2 task 3-4).

Covers:
- Principles above threshold trigger compaction
- Similar principles merged (guidance combined, max confidence, union tags)
- Below threshold: no-op
- _should_merge logic
- _merge_principles produces correct combined principle
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from dan.engine.error_memory import CausalPrinciple, PrincipleStore
from dan.engine.memory import MemoryEntry, MemoryScope


class InMemoryMemoryStore:
    """Simple in-memory memory store matching the MemoryStore protocol."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, str], MemoryEntry] = {}

    async def write(self, workflow_id: str, session_id: str, entry: MemoryEntry) -> None:
        self._data[(workflow_id, session_id, entry.key)] = entry

    async def read(self, workflow_id: str, session_id: str, key: str) -> MemoryEntry | None:
        return self._data.get((workflow_id, session_id, key))

    async def read_all(self, workflow_id: str, session_id: str) -> dict[str, MemoryEntry]:
        return {
            k[2]: v for k, v in self._data.items()
            if k[0] == workflow_id and k[1] == session_id
        }

    async def list_keys(self, workflow_id: str, session_id: str) -> list[str]:
        return [
            k[2] for k in self._data
            if k[0] == workflow_id and k[1] == session_id
        ]

    async def delete(self, workflow_id: str, session_id: str, key: str) -> bool:
        k = (workflow_id, session_id, key)
        if k in self._data:
            del self._data[k]
            return True
        return False


def _make_principle(
    condition: str,
    action: str,
    confidence: float = 0.5,
    tags: list[str] | None = None,
) -> CausalPrinciple:
    return CausalPrinciple(
        condition=condition,
        action=action,
        confidence=confidence,
        tags=tags or ["general"],
    )


# ===========================================================================
# Below threshold: no-op
# ===========================================================================


class TestBelowThresholdNoOp:
    @pytest.mark.asyncio
    async def test_no_compaction_below_threshold(self):
        store = InMemoryMemoryStore()
        ps = PrincipleStore(store)

        principles = [_make_principle(f"cond {i}", f"action {i}") for i in range(10)]
        await ps.store_principles("wf1", principles)

        removed = await ps.compact("wf1", threshold=50)
        assert removed == 0

    @pytest.mark.asyncio
    async def test_no_compaction_at_threshold(self):
        store = InMemoryMemoryStore()
        ps = PrincipleStore(store)

        principles = [_make_principle(f"unique cond {i}", f"unique action {i}") for i in range(50)]
        await ps.store_principles("wf1", principles)

        removed = await ps.compact("wf1", threshold=50)
        assert removed == 0


# ===========================================================================
# Above threshold triggers compaction
# ===========================================================================


class TestCompactionTriggered:
    @pytest.mark.asyncio
    async def test_compaction_above_threshold_with_similar(self):
        """Principles exceeding threshold with similar guidance get merged."""
        store = InMemoryMemoryStore()
        ps = PrincipleStore(store)

        principles = []
        for i in range(30):
            principles.append(_make_principle(
                f"unique condition number {i}",
                f"unique action number {i}",
                tags=["general"],
            ))
        principles.append(_make_principle(
            "retry on timeout failure in node X",
            "add retry logic with exponential backoff",
            confidence=0.6,
            tags=["timeout"],
        ))
        principles.append(_make_principle(
            "retry on timeout failure in node Y",
            "add retry logic with exponential backoff delay",
            confidence=0.8,
            tags=["timeout"],
        ))

        await ps.store_principles("wf1", principles)

        all_before = await ps.load_principles("wf1")
        assert len(all_before) == 32

        removed = await ps.compact("wf1", threshold=20)
        assert removed >= 1

        all_after = await ps.load_principles("wf1")
        assert len(all_after) < len(all_before)


# ===========================================================================
# Merge logic
# ===========================================================================


class TestMergeLogic:
    def test_should_merge_similar_actions(self):
        a = _make_principle("cond A", "add retry logic with backoff")
        b = _make_principle("cond B", "add retry logic with exponential backoff")
        assert PrincipleStore._should_merge(a, b) is True

    def test_should_not_merge_dissimilar(self):
        a = _make_principle("cond A", "add retry logic with backoff")
        b = _make_principle("cond B", "use structured output JSON schema")
        assert PrincipleStore._should_merge(a, b) is False

    def test_merge_combines_guidance(self):
        a = _make_principle("timeout in node A", "add retry logic", confidence=0.6, tags=["timeout"])
        b = _make_principle("timeout in node B", "add backoff delay", confidence=0.8, tags=["retry"])

        merged = PrincipleStore._merge_principles(a, b)
        assert "add retry logic" in merged.action
        assert "add backoff delay" in merged.action
        assert merged.confidence == 0.8
        assert "timeout" in merged.tags
        assert "retry" in merged.tags

    def test_merge_max_confidence(self):
        a = _make_principle("c", "a", confidence=0.3)
        b = _make_principle("c", "a", confidence=0.9)
        merged = PrincipleStore._merge_principles(a, b)
        assert merged.confidence == 0.9

    def test_merge_union_tags(self):
        a = _make_principle("c", "a", tags=["tag1", "tag2"])
        b = _make_principle("c", "a", tags=["tag2", "tag3"])
        merged = PrincipleStore._merge_principles(a, b)
        assert set(merged.tags) == {"tag1", "tag2", "tag3"}

    def test_merge_does_not_duplicate_action(self):
        """If b.action is already in a.action, don't append."""
        a = _make_principle("c", "add retry logic")
        b = _make_principle("c", "add retry logic")
        merged = PrincipleStore._merge_principles(a, b)
        assert merged.action == "add retry logic"

    def test_merge_union_source_ids(self):
        a = CausalPrinciple(
            condition="c", action="a",
            source_run_ids=["r1"], source_node_ids=["n1"],
        )
        b = CausalPrinciple(
            condition="c", action="a different action",
            source_run_ids=["r2"], source_node_ids=["n2"],
        )
        merged = PrincipleStore._merge_principles(a, b)
        assert set(merged.source_run_ids) == {"r1", "r2"}
        assert set(merged.source_node_ids) == {"n1", "n2"}


# ===========================================================================
# Integration: compact reduces count
# ===========================================================================


class TestCompactionIntegration:
    @pytest.mark.asyncio
    async def test_compact_reduces_count(self):
        """After compaction, the principle count should be lower."""
        store = InMemoryMemoryStore()
        ps = PrincipleStore(store)

        principles = []
        for i in range(25):
            principles.append(_make_principle(
                f"condition about retry and backoff {i}",
                f"add retry logic with exponential backoff {i}",
                tags=["retry"],
            ))
        for i in range(10):
            principles.append(_make_principle(
                f"completely unique condition {i}",
                f"completely unique action {i}",
                tags=["unique"],
            ))

        await ps.store_principles("wf1", principles)
        before_count = len(await ps.load_principles("wf1"))
        assert before_count == 35

        removed = await ps.compact("wf1", threshold=20)
        assert removed > 0

        after_count = len(await ps.load_principles("wf1"))
        assert after_count < before_count
