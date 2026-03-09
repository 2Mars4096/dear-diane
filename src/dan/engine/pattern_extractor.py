"""Structural pattern extraction from workflow graphs (29-6 §4).

Analyzes successful workflow topologies for recurring sub-structures and
stores them as WORKFLOW_PATTERN memory items for future reuse by the
concierge build path and reuse-decision logic.

Runs inside ``MemoryKernel.run_consolidation()`` when a ``GraphStore`` is
available, so it must stay lightweight.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)

logger = logging.getLogger(__name__)


class PatternExtractor:
    """Analyze workflow graphs for recurring structural patterns (29-6 §4)."""

    def __init__(self, memory_kernel: Any, graph_store: Any = None) -> None:
        self.memory_kernel = memory_kernel
        self.graph_store = graph_store

    # ------------------------------------------------------------------
    # 4-2: Periodic guard
    # ------------------------------------------------------------------

    def should_run(self, threshold: int = 10) -> bool:
        """Return True when enough workflow assets exist to justify extraction."""
        assets = self.memory_kernel.list_by_type(MemoryType.WORKFLOW_ASSET)
        return len(assets) >= threshold

    # ------------------------------------------------------------------
    # 4-2: Main entry point
    # ------------------------------------------------------------------

    def extract_patterns(self) -> list[MemoryItem]:
        """Load all workflows, compute signatures, find common patterns, store them.

        Returns the list of newly stored or updated ``WORKFLOW_PATTERN`` items.
        """
        if self.graph_store is None:
            return []

        graph_listings = self.graph_store.list_graphs()
        if not graph_listings:
            return []

        signatures: dict[str, list[str]] = {}
        for entry in graph_listings:
            graph_id = entry.get("graph_id", "")
            if not graph_id:
                continue
            try:
                graph_dict = self.graph_store.get_graph(graph_id)
            except Exception:
                logger.debug("Failed to load graph %s for pattern extraction", graph_id, exc_info=True)
                continue
            if not isinstance(graph_dict, dict):
                continue
            nodes = graph_dict.get("nodes") or []
            if len(nodes) < 2:
                continue
            sig = self._compute_signature(graph_dict)
            if sig:
                signatures.setdefault(sig, []).append(graph_id)

        common = self._find_common_patterns(signatures)
        stored: list[MemoryItem] = []
        for pattern in common:
            item = self._store_pattern(pattern)
            if item is not None:
                stored.append(item)
        return stored

    # ------------------------------------------------------------------
    # 4-3: Structural comparison — deterministic signature
    # ------------------------------------------------------------------

    def _compute_signature(self, graph_dict: dict) -> str:
        """Produce a topology signature: ordered node_type counts + edge pattern."""
        nodes = graph_dict.get("nodes") or []
        edges = graph_dict.get("edges") or []

        type_counts: Counter[str] = Counter(
            n.get("node_type", "unknown") if isinstance(n, dict)
            else getattr(n, "node_type", "unknown")
            for n in nodes
        )

        node_type_map: dict[str, str] = {}
        for n in nodes:
            nid = n.get("id") if isinstance(n, dict) else getattr(n, "id", "")
            ntype = n.get("node_type", "?") if isinstance(n, dict) else getattr(n, "node_type", "?")
            node_type_map[nid] = ntype

        edge_types: list[str] = []
        for e in edges:
            src = e.get("source_node_id") if isinstance(e, dict) else getattr(e, "source_node_id", "")
            tgt = e.get("target_node_id") if isinstance(e, dict) else getattr(e, "target_node_id", "")
            edge_types.append(f"{node_type_map.get(src, '?')}->{node_type_map.get(tgt, '?')}")

        parts = [f"{t}:{c}" for t, c in sorted(type_counts.items())]
        parts.extend(sorted(edge_types))
        return "|".join(parts)

    # ------------------------------------------------------------------
    # 4-4: Find common patterns
    # ------------------------------------------------------------------

    def _find_common_patterns(self, signatures: dict[str, list[str]]) -> list[dict]:
        """Find signatures that appear in 2+ workflows."""
        patterns = []
        for sig, workflow_ids in signatures.items():
            if len(workflow_ids) >= 2:
                patterns.append({
                    "signature": sig,
                    "workflow_ids": workflow_ids,
                    "occurrence_count": len(workflow_ids),
                })
        return sorted(patterns, key=lambda p: p["occurrence_count"], reverse=True)

    # ------------------------------------------------------------------
    # 4-5 & 4-6: Store as WORKFLOW_PATTERN with related_ids
    # ------------------------------------------------------------------

    def _store_pattern(self, pattern: dict) -> MemoryItem | None:
        """Store a discovered pattern as a WORKFLOW_PATTERN with lifecycle=ACTIVE.

        Idempotent: if the same signature already exists, updates occurrence
        count instead of creating a duplicate.  Links back to source workflows
        via ``related_ids`` (4-6).
        """
        existing = self.memory_kernel.list_by_type(MemoryType.WORKFLOW_PATTERN)
        for item in existing:
            if item.metadata.get("signature") == pattern["signature"]:
                merged_ids = list(item.related_ids or [])
                for wid in pattern["workflow_ids"]:
                    if wid not in merged_ids:
                        merged_ids.append(wid)
                self.memory_kernel.update(
                    item.id,
                    access_count=item.access_count + 1,
                    metadata={
                        **item.metadata,
                        "occurrence_count": pattern["occurrence_count"],
                        "source_workflow_ids": merged_ids[:20],
                    },
                    related_ids=merged_ids[:20],
                )
                return item

        workflow_ids = pattern["workflow_ids"][:20]
        return self.memory_kernel.store(MemoryItem(
            content=(
                f"Structural pattern found in {pattern['occurrence_count']} workflows: "
                f"{pattern['signature'][:200]}"
            ),
            memory_type=MemoryType.WORKFLOW_PATTERN,
            scope=MemoryScope.USER,
            lifecycle=MemoryLifecycle.ACTIVE,
            metadata={
                "signature": pattern["signature"],
                "source_workflow_ids": workflow_ids,
                "occurrence_count": pattern["occurrence_count"],
            },
            related_ids=workflow_ids,
        ))
