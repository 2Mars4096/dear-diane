"""Tests for Plan 19-1: Workflow Experience Memory."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from dan.engine.experience import (
    ExperienceIndex,
    ExperienceStore,
    WorkflowExperience,
    consolidate_experience,
    extract_experience_from_graph,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeMemoryEntry:
    def __init__(self, key: str, value: Any, scope: Any = None, source_run_id: str | None = None):
        self.key = key
        self.value = value
        self.scope = scope
        self.source_run_id = source_run_id


class FakeMemoryStore:
    """In-memory mock of MemoryStore protocol."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict[str, Any]]] = {}

    async def read(self, workflow_id: str, session_id: str, key: str) -> Any:
        val = self._data.get(workflow_id, {}).get(session_id, {}).get(key)
        if val is None:
            return None
        return FakeMemoryEntry(key=key, value=val)

    async def write(self, workflow_id: str, session_id: str, entry: Any) -> None:
        self._data.setdefault(workflow_id, {}).setdefault(session_id, {})[entry.key] = entry.value

    async def delete(self, workflow_id: str, session_id: str, key: str) -> bool:
        bucket = self._data.get(workflow_id, {}).get(session_id, {})
        if key in bucket:
            del bucket[key]
            return True
        return False

    async def list_keys(self, workflow_id: str, session_id: str) -> list[str]:
        return list(self._data.get(workflow_id, {}).get(session_id, {}).keys())

    async def read_all(self, workflow_id: str, session_id: str) -> dict[str, Any]:
        return self._data.get(workflow_id, {}).get(session_id, {})


def _make_graph():
    """Build a minimal Graph for testing."""
    from dan.models.graph import Graph

    return Graph.model_validate({
        "version": "dan_graph_v1",
        "metadata": {"name": "Test Workflow", "tags": ["test", "demo"]},
        "nodes": [
            {"id": "n1", "name": "Writer", "node_type": "llm_operator",
             "model": "gpt-4", "prompt_template": "Write something"},
            {"id": "n2", "name": "Fetch", "node_type": "tool_operator",
             "tool_id": "web_search", "tool_config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "n1", "target_node_id": "n2",
             "source_port": "output", "target_port": "input",
             "edge_type": "data"},
        ],
        "entry_points": ["n1"],
        "exit_points": ["n2"],
    })


def _make_snapshots(successes: int = 2, failures: int = 1) -> list[dict[str, Any]]:
    results = []
    for i in range(successes):
        results.append({
            "run_id": f"run-ok-{i}",
            "success": True,
            "started_at": time.time() - 100 + i,
            "finished_at": time.time() - 50 + i,
            "elapsed_seconds": 30.0 + i,
            "total_cost": 0.05 + i * 0.01,
            "node_statuses": {"n1": {"status": "completed"}, "n2": {"status": "completed"}},
        })
    for i in range(failures):
        results.append({
            "run_id": f"run-fail-{i}",
            "success": False,
            "started_at": time.time() - 200,
            "finished_at": time.time() - 150,
            "elapsed_seconds": 10.0,
            "total_cost": 0.02,
            "errors": {"n2": "tool timeout after 30s"},
        })
    return results


# ---------------------------------------------------------------------------
# Tests: WorkflowExperience model
# ---------------------------------------------------------------------------


class TestWorkflowExperience:
    def test_defaults(self):
        exp = WorkflowExperience(workflow_id="wf1")
        assert exp.run_count == 0
        assert exp.success_count == 0
        assert exp.tags == []
        assert exp.processed_run_ids == []
        assert exp.created_at > 0

    def test_serialization_roundtrip(self):
        exp = WorkflowExperience(
            workflow_id="wf2", name="Demo", tags=["a", "b"],
            run_count=10, success_count=8,
        )
        data = exp.model_dump()
        restored = WorkflowExperience.model_validate(data)
        assert restored.workflow_id == "wf2"
        assert restored.tags == ["a", "b"]
        assert restored.success_count == 8


# ---------------------------------------------------------------------------
# Tests: extract_experience_from_graph
# ---------------------------------------------------------------------------


class TestExtractExperience:
    def test_basic_extraction(self):
        graph = _make_graph()
        exp = extract_experience_from_graph(graph)
        assert exp.name == "Test Workflow"
        assert "llm_operator" in exp.node_types_used
        assert "tool_operator" in exp.node_types_used
        assert "web_search" in exp.tools_used
        assert exp.workflow_id == ""
        assert exp.tags == ["test", "demo"]

    def test_description_generated(self):
        graph = _make_graph()
        exp = extract_experience_from_graph(graph)
        assert "2 nodes" in exp.description
        assert "Writer" in exp.description

    def test_rejects_non_graph(self):
        with pytest.raises(TypeError):
            extract_experience_from_graph({"not": "a graph"})


# ---------------------------------------------------------------------------
# Tests: consolidate_experience
# ---------------------------------------------------------------------------


class TestConsolidateExperience:
    def test_basic_consolidation(self):
        base = WorkflowExperience(workflow_id="wf1")
        snaps = _make_snapshots(successes=2, failures=1)
        result = consolidate_experience(base, snaps, [])

        assert result.run_count == 3
        assert result.success_count == 2
        assert result.avg_elapsed_seconds is not None
        assert result.avg_total_cost is not None
        assert result.last_run_at is not None

    def test_failure_patterns_extracted(self):
        base = WorkflowExperience(workflow_id="wf1")
        snaps = _make_snapshots(successes=0, failures=3)
        result = consolidate_experience(base, snaps, [])
        assert len(result.failure_patterns) > 0

    def test_principles_sorted_by_confidence(self):
        base = WorkflowExperience(workflow_id="wf1")
        principles = [
            {"condition": "c1", "action": "a1", "confidence": 0.3},
            {"condition": "c2", "action": "a2", "confidence": 0.9},
            {"condition": "c3", "action": "a3", "confidence": 0.6},
        ]
        result = consolidate_experience(base, [], principles)
        assert result.principles[0]["confidence"] == 0.9

    def test_incremental_update(self):
        base = WorkflowExperience(
            workflow_id="wf1", run_count=5, success_count=4,
            avg_elapsed_seconds=20.0,
        )
        snaps = _make_snapshots(successes=1, failures=0)
        result = consolidate_experience(base, snaps, [])
        assert result.run_count == 6
        assert result.success_count == 5

    def test_deduplicates_previously_processed_runs(self):
        snaps = _make_snapshots(successes=1, failures=0)
        run_id = snaps[0]["run_id"]
        base = WorkflowExperience(
            workflow_id="wf1",
            run_count=1,
            success_count=1,
            processed_run_ids=[run_id],
        )
        result = consolidate_experience(base, snaps, [])
        assert result.run_count == 1
        assert result.success_count == 1
        assert result.processed_run_ids == [run_id]


# ---------------------------------------------------------------------------
# Tests: ExperienceStore
# ---------------------------------------------------------------------------


class TestExperienceStore:
    @pytest.fixture
    def store(self):
        return ExperienceStore(FakeMemoryStore())

    @pytest.mark.asyncio
    async def test_save_and_load(self, store):
        exp = WorkflowExperience(workflow_id="wf1", name="Test")
        await store.save_experience(exp)
        loaded = await store.load_experience("wf1")
        assert loaded is not None
        assert loaded.name == "Test"

    @pytest.mark.asyncio
    async def test_load_missing(self, store):
        assert await store.load_experience("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_experiences(self, store):
        await store.save_experience(WorkflowExperience(workflow_id="a", name="A"))
        await store.save_experience(WorkflowExperience(workflow_id="b", name="B"))
        exps = await store.list_experiences()
        assert len(exps) == 2
        names = {e.name for e in exps}
        assert names == {"A", "B"}

    @pytest.mark.asyncio
    async def test_delete(self, store):
        await store.save_experience(WorkflowExperience(workflow_id="wf1"))
        assert await store.delete_experience("wf1")
        assert await store.load_experience("wf1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store):
        assert not await store.delete_experience("nope")
