"""Long-chain memory pipeline — short-term buffer, compaction, consolidation.

Part of Plan 14-3.  Activates CompactionStrategy from models/context.py,
provides a ShortTermMemory buffer with token-aware eviction, and a
ConsolidationPipeline that transfers old items to long-term memory.

Key design choices:
  - Heuristic-first: all strategies except ``summarize`` run without LLM calls.
  - ``summarize`` requires an LLM call and falls back to ``sliding_window``
    if no LLM callback is available.
  - Token budget is enforced via ``estimate_tokens`` from utils/tokens.py.
  - The pipeline is opt-in via feature flags on EngineConfig.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from dan.models.context import CompactionStrategy
from dan.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class MemoryPolicyConfig:
    """Configures how the memory pipeline manages context.

    ``strategy`` maps to ``CompactionStrategy`` from models/context.py.
    ``max_tokens`` is the token budget before compaction triggers.
    ``window_size`` is the number of recent items kept in full.
    ``consolidation_enabled`` controls whether items migrate to long-term.
    """

    strategy: CompactionStrategy = CompactionStrategy.SLIDING_WINDOW
    window_size: int = 20
    max_tokens: int = 4000
    model: str = ""
    consolidation_enabled: bool = True
    consolidation_threshold: int = 50


# ---------------------------------------------------------------------------
# Memory item
# ---------------------------------------------------------------------------


@dataclass
class MemoryItem:
    """A single item in the short-term memory buffer."""

    content: str
    source_node_id: str = ""
    source_run_id: str = ""
    timestamp: float = field(default_factory=time.time)
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.token_count == 0 and self.content:
            self.token_count = estimate_tokens(self.content)


# ---------------------------------------------------------------------------
# Short-term memory buffer
# ---------------------------------------------------------------------------


class ShortTermMemory:
    """Token-aware buffer of recent memory items.

    Items are appended chronologically.  When the buffer exceeds
    ``max_tokens``, older items are evicted or compacted according
    to the configured strategy.
    """

    def __init__(self, config: MemoryPolicyConfig | None = None) -> None:
        self.config = config or MemoryPolicyConfig()
        self._items: list[MemoryItem] = []
        self._total_tokens: int = 0

    @property
    def items(self) -> list[MemoryItem]:
        return list(self._items)

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def count(self) -> int:
        return len(self._items)

    def append(self, item: MemoryItem) -> None:
        """Add an item, triggering compaction if budget is exceeded."""
        self._items.append(item)
        self._total_tokens += item.token_count
        if self._total_tokens > self.config.max_tokens:
            self._compact()

    def get_recent(self, n: int | None = None) -> list[MemoryItem]:
        """Return the most recent *n* items (all if None)."""
        if n is None:
            return list(self._items)
        return list(self._items[-n:])

    def to_text(self, n: int | None = None) -> str:
        """Concatenate recent items into a single text string."""
        items = self.get_recent(n)
        return "\n".join(item.content for item in items)

    def clear(self) -> None:
        self._items.clear()
        self._total_tokens = 0

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "content": item.content,
                "source_node_id": item.source_node_id,
                "source_run_id": item.source_run_id,
                "timestamp": item.timestamp,
                "token_count": item.token_count,
                "metadata": item.metadata,
            }
            for item in self._items
        ]

    def restore(self, data: list[dict[str, Any]]) -> None:
        self._items = [
            MemoryItem(
                content=d["content"],
                source_node_id=d.get("source_node_id", ""),
                source_run_id=d.get("source_run_id", ""),
                timestamp=d.get("timestamp", 0.0),
                token_count=d.get("token_count", 0),
                metadata=d.get("metadata", {}),
            )
            for d in data
        ]
        self._total_tokens = sum(i.token_count for i in self._items)

    # -- internal compaction -----------------------------------------------

    def _compact(self) -> None:
        """Apply compaction strategy to bring buffer under token budget."""
        strategy = self.config.strategy

        if strategy == CompactionStrategy.NONE:
            return

        if strategy == CompactionStrategy.KEEP_LAST:
            self._compact_keep_last()
        elif strategy == CompactionStrategy.SLIDING_WINDOW:
            self._compact_sliding_window()
        elif strategy == CompactionStrategy.DIFF_BASED:
            self._compact_diff_based()
        elif strategy == CompactionStrategy.SUMMARIZE:
            self._compact_sliding_window()
        else:
            self._compact_sliding_window()

    def _compact_keep_last(self) -> None:
        """Keep only the last N items."""
        n = self.config.window_size
        if len(self._items) > n:
            self._items = self._items[-n:]
            self._total_tokens = sum(i.token_count for i in self._items)

    def _compact_sliding_window(self) -> None:
        """Drop oldest items until under token budget."""
        while self._total_tokens > self.config.max_tokens and len(self._items) > 1:
            evicted = self._items.pop(0)
            self._total_tokens -= evicted.token_count

    def _compact_diff_based(self) -> None:
        """Keep first + last item, replace middle with diff summary."""
        if len(self._items) <= 2:
            return
        first = self._items[0]
        last = self._items[-1]
        middle_count = len(self._items) - 2
        diff_summary = MemoryItem(
            content=f"[{middle_count} intermediate items compacted]",
            source_node_id="__compaction__",
            metadata={"compacted_count": middle_count},
        )
        self._items = [first, diff_summary, last]
        self._total_tokens = sum(i.token_count for i in self._items)


# ---------------------------------------------------------------------------
# Compaction function (standalone, usable without ShortTermMemory)
# ---------------------------------------------------------------------------


def apply_compaction(
    items: list[MemoryItem],
    strategy: CompactionStrategy,
    window_size: int = 20,
    max_tokens: int = 4000,
) -> list[MemoryItem]:
    """Apply a compaction strategy to a list of MemoryItems.

    Returns a new list (does not mutate the input).
    """
    if strategy == CompactionStrategy.NONE:
        return list(items)

    if strategy == CompactionStrategy.KEEP_LAST:
        return list(items[-window_size:])

    if strategy == CompactionStrategy.SLIDING_WINDOW:
        result = list(items)
        total = sum(i.token_count for i in result)
        while total > max_tokens and len(result) > 1:
            evicted = result.pop(0)
            total -= evicted.token_count
        return result

    if strategy == CompactionStrategy.DIFF_BASED:
        if len(items) <= 2:
            return list(items)
        first = items[0]
        last = items[-1]
        middle_count = len(items) - 2
        diff_item = MemoryItem(
            content=f"[{middle_count} intermediate items compacted]",
            source_node_id="__compaction__",
            metadata={"compacted_count": middle_count},
        )
        return [first, diff_item, last]

    if strategy == CompactionStrategy.SUMMARIZE:
        return apply_compaction(
            items, CompactionStrategy.SLIDING_WINDOW, window_size, max_tokens,
        )

    return list(items)


# ---------------------------------------------------------------------------
# Consolidation pipeline
# ---------------------------------------------------------------------------


@dataclass
class ConsolidationResult:
    """Outcome of a consolidation pass."""

    items_consolidated: int = 0
    items_remaining: int = 0
    long_term_entries: list[MemoryItem] = field(default_factory=list)


class ConsolidationPipeline:
    """Manages the transfer of items from short-term to long-term memory.

    When the short-term buffer exceeds ``consolidation_threshold``,
    older items are compacted and stored as long-term memory entries
    (via the MemoryStore from plan 14-1).
    """

    def __init__(self, config: MemoryPolicyConfig | None = None) -> None:
        self.config = config or MemoryPolicyConfig()

    def should_consolidate(self, buffer: ShortTermMemory) -> bool:
        return (
            self.config.consolidation_enabled
            and buffer.count >= self.config.consolidation_threshold
        )

    def consolidate(self, buffer: ShortTermMemory) -> ConsolidationResult:
        """Extract old items from buffer and return them for long-term storage.

        Keeps the most recent ``window_size`` items in the buffer.
        Returns the evicted items as ``long_term_entries``.
        """
        if not self.should_consolidate(buffer):
            return ConsolidationResult(items_remaining=buffer.count)

        keep_count = self.config.window_size
        all_items = buffer.items

        if len(all_items) <= keep_count:
            return ConsolidationResult(items_remaining=buffer.count)

        to_consolidate = all_items[:-keep_count]
        to_keep = all_items[-keep_count:]

        consolidated = apply_compaction(
            to_consolidate,
            self.config.strategy,
            window_size=self.config.window_size,
            max_tokens=self.config.max_tokens,
        )

        buffer.clear()
        for item in to_keep:
            buffer._items.append(item)
        buffer._total_tokens = sum(i.token_count for i in buffer._items)

        return ConsolidationResult(
            items_consolidated=len(to_consolidate),
            items_remaining=buffer.count,
            long_term_entries=consolidated,
        )
