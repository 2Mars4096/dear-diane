"""Unit tests for PatternExtractor (29-6 §12-4).

Covers structural comparison, sub-graph extraction, and pattern storage.
"""

from __future__ import annotations

from typing import Any

import pytest

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)
from dan.engine.pattern_extractor import PatternExtractor


# ---------------------------------------------------------------------------
# Mock graph store
# ---------------------------------------------------------------------------


class MockGraphStore:
    """In-memory graph store for testing PatternExtractor."""

    def __init__(self, graphs: dict[str, dict[str, Any]]) -> None:
        self._graphs = graphs

    def list_graphs(self) -> list[dict[str, str]]:
        return [{"graph_id": gid} for gid in self._graphs]

    def get_graph(self, graph_id: str) -> dict[str, Any]:
        return self._graphs[graph_id]


@pytest.fixture
def kernel(tmp_path) -> MemoryKernel:
    return MemoryKernel(base_dir=str(tmp_path / "mem"))


def _linear_graph(name: str) -> dict[str, Any]:
    return {
        "metadata": {"name": name},
        "nodes": [
            {"id": "input", "node_type": "input"},
            {"id": "process", "node_type": "llm_operator"},
            {"id": "output", "node_type": "llm_operator"},
        ],
        "edges": [
            {"source_node_id": "input", "target_node_id": "process"},
            {"source_node_id": "process", "target_node_id": "output"},
        ],
    }


def _fan_out_graph(name: str) -> dict[str, Any]:
    return {
        "metadata": {"name": name},
        "nodes": [
            {"id": "input", "node_type": "input"},
            {"id": "branch1", "node_type": "llm_operator"},
            {"id": "branch2", "node_type": "llm_operator"},
        ],
        "edges": [
            {"source_node_id": "input", "target_node_id": "branch1"},
            {"source_node_id": "input", "target_node_id": "branch2"},
        ],
    }


# ---------------------------------------------------------------------------
# should_run
# ---------------------------------------------------------------------------


class TestShouldRun:
    def test_below_threshold(self, kernel: MemoryKernel):
        extractor = PatternExtractor(kernel)
        assert extractor.should_run(threshold=10) is False

    def test_at_threshold(self, kernel: MemoryKernel):
        for i in range(10):
            kernel.store_workflow_asset(f"asset-{i}", workflow_id=f"wf-{i}")
        extractor = PatternExtractor(kernel)
        assert extractor.should_run(threshold=10) is True

    def test_custom_threshold(self, kernel: MemoryKernel):
        for i in range(3):
            kernel.store_workflow_asset(f"asset-{i}", workflow_id=f"wf-{i}")
        extractor = PatternExtractor(kernel)
        assert extractor.should_run(threshold=3) is True
        assert extractor.should_run(threshold=5) is False


# ---------------------------------------------------------------------------
# Signature computation
# ---------------------------------------------------------------------------


class TestSignatureComputation:
    def test_same_topology_same_signature(self, kernel: MemoryKernel):
        ext = PatternExtractor(kernel)
        g1 = _linear_graph("a")
        g2 = _linear_graph("b")
        assert ext._compute_signature(g1) == ext._compute_signature(g2)

    def test_different_topology_different_signature(self, kernel: MemoryKernel):
        ext = PatternExtractor(kernel)
        g1 = _linear_graph("a")
        g2 = _fan_out_graph("b")
        assert ext._compute_signature(g1) != ext._compute_signature(g2)

    def test_empty_graph_empty_signature(self, kernel: MemoryKernel):
        ext = PatternExtractor(kernel)
        sig = ext._compute_signature({"nodes": [], "edges": []})
        assert sig == ""


# ---------------------------------------------------------------------------
# Pattern extraction
# ---------------------------------------------------------------------------


class TestExtractPatterns:
    def test_no_graph_store_returns_empty(self, kernel: MemoryKernel):
        ext = PatternExtractor(kernel, graph_store=None)
        assert ext.extract_patterns() == []

    def test_extracts_common_pattern(self, kernel: MemoryKernel):
        graphs = {f"wf-{i}": _linear_graph(f"analysis-{i}") for i in range(5)}
        store = MockGraphStore(graphs)
        ext = PatternExtractor(kernel, store)
        patterns = ext.extract_patterns()
        assert len(patterns) >= 1
        assert patterns[0].memory_type == MemoryType.WORKFLOW_PATTERN

    def test_no_pattern_from_unique_topologies(self, kernel: MemoryKernel):
        graphs = {
            "wf-0": _linear_graph("a"),
            "wf-1": _fan_out_graph("b"),
        }
        store = MockGraphStore(graphs)
        ext = PatternExtractor(kernel, store)
        patterns = ext.extract_patterns()
        assert len(patterns) == 0

    def test_idempotent_store(self, kernel: MemoryKernel):
        """Running extraction twice with same data shouldn't create duplicates."""
        graphs = {f"wf-{i}": _linear_graph(f"a-{i}") for i in range(3)}
        store = MockGraphStore(graphs)
        ext = PatternExtractor(kernel, store)
        p1 = ext.extract_patterns()
        p2 = ext.extract_patterns()
        all_patterns = kernel.list_by_type(MemoryType.WORKFLOW_PATTERN)
        assert len(all_patterns) == 1
        assert len(p1) == 1
        assert len(p2) == 1

    def test_pattern_has_related_ids(self, kernel: MemoryKernel):
        graphs = {f"wf-{i}": _linear_graph(f"a-{i}") for i in range(3)}
        store = MockGraphStore(graphs)
        ext = PatternExtractor(kernel, store)
        patterns = ext.extract_patterns()
        assert len(patterns[0].related_ids) >= 2

    def test_pattern_lifecycle_is_active(self, kernel: MemoryKernel):
        graphs = {f"wf-{i}": _linear_graph(f"a-{i}") for i in range(3)}
        store = MockGraphStore(graphs)
        ext = PatternExtractor(kernel, store)
        patterns = ext.extract_patterns()
        assert patterns[0].lifecycle == MemoryLifecycle.ACTIVE

    def test_skips_single_node_graphs(self, kernel: MemoryKernel):
        graphs = {
            "wf-0": {"nodes": [{"id": "a", "node_type": "input"}], "edges": []},
            "wf-1": {"nodes": [{"id": "a", "node_type": "input"}], "edges": []},
        }
        store = MockGraphStore(graphs)
        ext = PatternExtractor(kernel, store)
        patterns = ext.extract_patterns()
        assert len(patterns) == 0
