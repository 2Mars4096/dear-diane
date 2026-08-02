"""Tests for workflow catalog capability tools (29-4 §4)."""

from __future__ import annotations

import pytest

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capability_handlers import (
    handle_fork_workflow,
    handle_list_my_workflows,
    handle_search_workflows,
    handle_show_workflow,
)


class FakeGraphStore:
    """Minimal in-memory graph store for testing."""

    def __init__(self, graphs: dict[str, dict] | None = None) -> None:
        self._graphs: dict[str, dict] = dict(graphs or {})

    def list_graphs(self) -> list[dict]:
        return [
            {
                "graph_id": gid,
                "name": (g.get("metadata") or {}).get("name", gid),
                "description": (g.get("metadata") or {}).get("description", ""),
            }
            for gid, g in self._graphs.items()
        ]

    def get_graph(self, graph_id: str) -> dict | None:
        return self._graphs.get(graph_id)

    def save_graph(self, graph_id: str, data: dict) -> None:
        self._graphs[graph_id] = data


def _make_ctx(graph_store=None) -> CapabilityContext:
    return CapabilityContext(workflow_id="test", graph_store=graph_store)


SAMPLE_GRAPH = {
    "metadata": {"name": "data-pipeline", "description": "ETL pipeline"},
    "nodes": [{"id": "n1", "node_type": "llm"}],
    "edges": [],
}


# ── fork_workflow ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_workflow():
    store = FakeGraphStore({"dp1": dict(SAMPLE_GRAPH)})
    ctx = _make_ctx(graph_store=store)

    result = await handle_fork_workflow({"workflow_id": "dp1", "new_name": "my-copy"}, ctx)

    assert result.success is True
    assert "my-copy" in result.message
    assert result.data is not None
    new_id = result.data["workflow_id"]
    assert new_id == "my-copy"
    assert result.data["name"] == "my-copy"
    assert result.data["forked_from"] == "dp1"

    forked = store.get_graph(new_id)
    assert forked is not None
    assert forked["metadata"]["name"] == "my-copy"
    assert forked["metadata"]["forked_from"] == "dp1"


@pytest.mark.asyncio
async def test_fork_workflow_default_name():
    store = FakeGraphStore({"dp1": dict(SAMPLE_GRAPH)})
    ctx = _make_ctx(graph_store=store)

    result = await handle_fork_workflow({"workflow_id": "dp1"}, ctx)

    assert result.success is True
    assert result.data["name"] == "dp1 copy"
    assert result.data["workflow_id"] == "dp1-copy"


@pytest.mark.asyncio
async def test_fork_workflow_uses_requested_exact_workflow_id():
    store = FakeGraphStore({"dp1": dict(SAMPLE_GRAPH)})
    ctx = _make_ctx(graph_store=store)

    result = await handle_fork_workflow(
        {"workflow_id": "dp1", "new_name": "Equity Watchlist", "new_workflow_id": "daily-equity-watchlist"},
        ctx,
    )

    assert result.success is True
    assert result.data["workflow_id"] == "daily-equity-watchlist"
    assert store.get_graph("daily-equity-watchlist") is not None


@pytest.mark.asyncio
async def test_fork_workflow_dedupes_name_based_workflow_id():
    store = FakeGraphStore(
        {
            "dp1": dict(SAMPLE_GRAPH),
            "daily-equity-watchlist": dict(SAMPLE_GRAPH),
        }
    )
    ctx = _make_ctx(graph_store=store)

    result = await handle_fork_workflow(
        {"workflow_id": "dp1", "new_name": "Daily Equity Watchlist"},
        ctx,
    )

    assert result.success is True
    assert result.data["workflow_id"] == "daily-equity-watchlist-2"


@pytest.mark.asyncio
async def test_fork_workflow_rejects_colliding_exact_workflow_id():
    store = FakeGraphStore(
        {
            "dp1": dict(SAMPLE_GRAPH),
            "daily-equity-watchlist": dict(SAMPLE_GRAPH),
        }
    )
    ctx = _make_ctx(graph_store=store)

    result = await handle_fork_workflow(
        {"workflow_id": "dp1", "new_workflow_id": "daily-equity-watchlist"},
        ctx,
    )

    assert result.success is False
    assert "already exists" in result.message.lower()


@pytest.mark.asyncio
async def test_fork_workflow_not_found():
    store = FakeGraphStore({})
    ctx = _make_ctx(graph_store=store)

    result = await handle_fork_workflow({"workflow_id": "nonexistent"}, ctx)

    assert result.success is False
    assert "not found" in result.message.lower()


@pytest.mark.asyncio
async def test_fork_workflow_no_graph_store():
    ctx = _make_ctx(graph_store=None)

    result = await handle_fork_workflow({"workflow_id": "dp1"}, ctx)

    assert result.success is False
    assert "not available" in result.message.lower()


@pytest.mark.asyncio
async def test_fork_workflow_empty_id():
    store = FakeGraphStore({"dp1": dict(SAMPLE_GRAPH)})
    ctx = _make_ctx(graph_store=store)

    result = await handle_fork_workflow({"workflow_id": ""}, ctx)

    assert result.success is False
    assert "required" in result.message.lower()


@pytest.mark.asyncio
async def test_fork_preserves_nodes_and_edges():
    graph = {
        "metadata": {"name": "original"},
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [{"source_node_id": "a", "target_node_id": "b"}],
    }
    store = FakeGraphStore({"orig": graph})
    ctx = _make_ctx(graph_store=store)

    result = await handle_fork_workflow({"workflow_id": "orig", "new_name": "copy"}, ctx)

    assert result.success is True
    forked = store.get_graph(result.data["workflow_id"])
    assert len(forked["nodes"]) == 2
    assert len(forked["edges"]) == 1


# ── list / search / show (smoke) ──────────────────────────────────


@pytest.mark.asyncio
async def test_list_my_workflows_empty():
    store = FakeGraphStore({})
    ctx = _make_ctx(graph_store=store)

    result = await handle_list_my_workflows({}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_search_workflows_no_match():
    store = FakeGraphStore({"dp1": dict(SAMPLE_GRAPH)})
    ctx = _make_ctx(graph_store=store)

    result = await handle_search_workflows({"query": "zzz_no_match"}, ctx)
    assert result.success is True


@pytest.mark.asyncio
async def test_show_workflow_not_found():
    store = FakeGraphStore({})
    ctx = _make_ctx(graph_store=store)

    result = await handle_show_workflow({"workflow_id": "missing"}, ctx)
    assert result.success is False
