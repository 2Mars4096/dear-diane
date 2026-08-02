"""Tests for checkpoint portal infrastructure — CheckpointData, RerunScope,
staleness detection, downstream computation, and partial rerun execution."""

from __future__ import annotations

import json

import pytest

from dan.engine.checkpoint import (
    CheckpointData,
    FileSystemCheckpointStore,
    RerunScope,
    StalenessResult,
    check_checkpoint_staleness,
    compute_downstream_nodes,
    compute_graph_revision_hash,
    compute_subgraph_node_ids,
)
from dan.engine.executor import EngineConfig
from dan.models.edges import DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import LLMOperator
from dan.server.run_manager import RunManager, RunStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph(
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    entry_points: list[str] | None = None,
    exit_points: list[str] | None = None,
    sub_graphs: dict | None = None,
) -> Graph:
    """Build a simple Graph for testing."""
    node_list = []
    for nd in (nodes or []):
        node_list.append(LLMOperator(
            id=nd["id"],
            name=nd.get("name", nd["id"]),
            model=nd.get("model", "test-model"),
            prompt_template=nd.get("prompt", "Hello"),
        ))
    edge_list = []
    for i, ed in enumerate(edges or []):
        edge_list.append(DataEdge(
            id=ed.get("id", f"e{i}"),
            source_node_id=ed["src"],
            source_port=ed.get("src_port", "result"),
            target_node_id=ed["tgt"],
            target_port=ed.get("tgt_port", "input"),
        ))
    return Graph(
        metadata=GraphMetadata(name="test"),
        nodes=node_list,
        edges=edge_list,
        entry_points=entry_points or [],
        exit_points=exit_points or [],
        sub_graphs=sub_graphs or {},
    )


def _linear_graph() -> Graph:
    """A -> B -> C linear graph."""
    return _make_graph(
        nodes=[{"id": "A"}, {"id": "B"}, {"id": "C"}],
        edges=[
            {"src": "A", "tgt": "B"},
            {"src": "B", "tgt": "C"},
        ],
        entry_points=["A"],
        exit_points=["C"],
    )


def _diamond_graph() -> Graph:
    """A -> B, A -> C, B -> D, C -> D diamond graph."""
    return _make_graph(
        nodes=[{"id": "A"}, {"id": "B"}, {"id": "C"}, {"id": "D"}],
        edges=[
            {"src": "A", "tgt": "B"},
            {"src": "A", "tgt": "C"},
            {"src": "B", "tgt": "D"},
            {"src": "C", "tgt": "D"},
        ],
        entry_points=["A"],
        exit_points=["D"],
    )


# ---------------------------------------------------------------------------
# CheckpointData model
# ---------------------------------------------------------------------------


class TestCheckpointData:
    def test_defaults(self):
        cd = CheckpointData(run_id="run-1")
        assert cd.run_id == "run-1"
        assert cd.graph_id == ""
        assert cd.graph_revision is None
        assert cd.completed_node_ids == []
        assert cd.node_outputs == {}
        assert cd.timestamp > 0

    def test_full_construction(self):
        cd = CheckpointData(
            run_id="run-2",
            graph_id="wf-1",
            graph_revision="abc123",
            completed_node_ids=["A", "B"],
            node_outputs={"A": {"result": "hello"}, "B": {"result": "world"}},
        )
        assert cd.graph_revision == "abc123"
        assert cd.completed_node_ids == ["A", "B"]
        assert cd.node_outputs["A"]["result"] == "hello"

    def test_serialization_roundtrip(self):
        cd = CheckpointData(
            run_id="run-3",
            graph_revision="def456",
            completed_node_ids=["X"],
            node_outputs={"X": {"out": 42}},
        )
        data = cd.model_dump()
        cd2 = CheckpointData(**data)
        assert cd2.run_id == cd.run_id
        assert cd2.graph_revision == cd.graph_revision
        assert cd2.completed_node_ids == cd.completed_node_ids
        assert cd2.node_outputs == cd.node_outputs


# ---------------------------------------------------------------------------
# RerunScope model
# ---------------------------------------------------------------------------


class TestRerunScope:
    def test_downstream_of(self):
        scope = RerunScope(scope_type="downstream_of", target_node_id="B")
        assert scope.scope_type == "downstream_of"
        assert scope.target_node_id == "B"
        assert scope.sub_graph_key is None

    def test_single_node(self):
        scope = RerunScope(scope_type="single_node", target_node_id="A")
        assert scope.scope_type == "single_node"

    def test_subgraph(self):
        scope = RerunScope(scope_type="subgraph", sub_graph_key="loop_body")
        assert scope.scope_type == "subgraph"
        assert scope.sub_graph_key == "loop_body"

    def test_serialization(self):
        scope = RerunScope(scope_type="downstream_of", target_node_id="X")
        data = scope.model_dump()
        scope2 = RerunScope(**data)
        assert scope2.scope_type == scope.scope_type
        assert scope2.target_node_id == scope.target_node_id


# ---------------------------------------------------------------------------
# Graph revision hashing
# ---------------------------------------------------------------------------


class TestGraphRevisionHash:
    def test_deterministic(self):
        g = _linear_graph()
        h1 = compute_graph_revision_hash(g)
        h2 = compute_graph_revision_hash(g)
        assert h1 == h2
        assert len(h1) == 16  # SHA-256 truncated to 16 hex chars

    def test_different_graphs_different_hash(self):
        g1 = _linear_graph()
        g2 = _diamond_graph()
        assert compute_graph_revision_hash(g1) != compute_graph_revision_hash(g2)

    def test_metadata_change_does_not_change_hash(self):
        g1 = _linear_graph()
        h1 = compute_graph_revision_hash(g1)
        g2 = _linear_graph()
        g2.metadata.name = "different_name"
        g2.metadata.description = "something else"
        h2 = compute_graph_revision_hash(g2)
        assert h1 == h2

    def test_accepts_dict(self):
        g = _linear_graph()
        h1 = compute_graph_revision_hash(g)
        g_dict = json.loads(g.model_dump_json())
        h2 = compute_graph_revision_hash(g_dict)
        assert h1 == h2

    def test_edge_change_changes_hash(self):
        g1 = _make_graph(
            nodes=[{"id": "A"}, {"id": "B"}],
            edges=[{"src": "A", "tgt": "B"}],
        )
        g2 = _make_graph(
            nodes=[{"id": "A"}, {"id": "B"}],
            edges=[],
        )
        assert compute_graph_revision_hash(g1) != compute_graph_revision_hash(g2)


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------


class TestStalenessDetection:
    def test_compatible_checkpoint(self):
        g = _linear_graph()
        rev = compute_graph_revision_hash(g)
        result = check_checkpoint_staleness(rev, g, ["A", "B"])
        assert result.compatible is True
        assert result.stale is False
        assert result.missing_nodes == []

    def test_stale_no_revision(self):
        g = _linear_graph()
        result = check_checkpoint_staleness(None, g)
        assert result.compatible is False
        assert result.stale is True

    def test_stale_graph_changed(self):
        g1 = _linear_graph()
        rev = compute_graph_revision_hash(g1)
        g2 = _diamond_graph()
        result = check_checkpoint_staleness(rev, g2, ["A"])
        assert result.compatible is False
        assert result.stale is True

    def test_missing_nodes_detected(self):
        g_old = _make_graph(
            nodes=[{"id": "A"}, {"id": "B"}, {"id": "C"}],
            edges=[{"src": "A", "tgt": "B"}, {"src": "B", "tgt": "C"}],
        )
        rev = compute_graph_revision_hash(g_old)
        # New graph removes node C
        g_new = _make_graph(
            nodes=[{"id": "A"}, {"id": "B"}],
            edges=[{"src": "A", "tgt": "B"}],
        )
        result = check_checkpoint_staleness(rev, g_new, ["A", "B", "C"])
        assert result.stale is True
        assert "C" in result.missing_nodes

    def test_accepts_dict_graph(self):
        g = _linear_graph()
        rev = compute_graph_revision_hash(g)
        g_dict = json.loads(g.model_dump_json())
        result = check_checkpoint_staleness(rev, g_dict)
        assert result.compatible is True


# ---------------------------------------------------------------------------
# Downstream node computation
# ---------------------------------------------------------------------------


class TestDownstreamNodes:
    def test_linear_downstream(self):
        g = _linear_graph()
        ds = compute_downstream_nodes("A", g)
        assert ds == {"A", "B", "C"}

    def test_linear_downstream_from_middle(self):
        g = _linear_graph()
        ds = compute_downstream_nodes("B", g)
        assert ds == {"B", "C"}

    def test_linear_downstream_from_end(self):
        g = _linear_graph()
        ds = compute_downstream_nodes("C", g)
        assert ds == {"C"}

    def test_exclude_target(self):
        g = _linear_graph()
        ds = compute_downstream_nodes("A", g, include_target=False)
        assert ds == {"B", "C"}

    def test_diamond_downstream(self):
        g = _diamond_graph()
        ds = compute_downstream_nodes("A", g)
        assert ds == {"A", "B", "C", "D"}

    def test_diamond_from_branch(self):
        g = _diamond_graph()
        ds = compute_downstream_nodes("B", g)
        assert ds == {"B", "D"}

    def test_nonexistent_node(self):
        g = _linear_graph()
        ds = compute_downstream_nodes("Z", g)
        assert ds == {"Z"}  # just the target itself


# ---------------------------------------------------------------------------
# Subgraph node IDs
# ---------------------------------------------------------------------------


class TestSubgraphNodeIds:
    def test_existing_subgraph(self):
        sub = _make_graph(nodes=[{"id": "s1"}, {"id": "s2"}])
        g = _make_graph(
            nodes=[{"id": "A"}],
            sub_graphs={"body": sub},
        )
        ids = compute_subgraph_node_ids(g, "body")
        assert ids == {"s1", "s2"}

    def test_missing_subgraph(self):
        g = _make_graph(nodes=[{"id": "A"}])
        ids = compute_subgraph_node_ids(g, "nonexistent")
        assert ids == set()


# ---------------------------------------------------------------------------
# RunManager checkpoint info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_checkpoint_info_no_store():
    rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
    info = await rm.get_checkpoint_info("run-123")
    assert info is None


@pytest.mark.asyncio
async def test_list_checkpoint_runs_empty():
    rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
    runs = await rm.list_checkpoint_runs()
    assert runs == []


# ---------------------------------------------------------------------------
# Rerun validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_no_checkpoint_store():
    rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
    g = _linear_graph()
    scope = RerunScope(scope_type="single_node", target_node_id="A")
    with pytest.raises(ValueError, match="No checkpoint store"):
        await rm.rerun_from_checkpoint(g, "wf-1", "run-1", scope)


@pytest.mark.asyncio
async def test_rerun_scope_requires_target():
    scope = RerunScope(scope_type="downstream_of")
    # target_node_id is None — should fail validation in rerun_from_checkpoint
    # but the model itself allows it (validation in the method)
    assert scope.target_node_id is None


@pytest.mark.asyncio
async def test_rerun_scope_subgraph_requires_key():
    scope = RerunScope(scope_type="subgraph")
    assert scope.sub_graph_key is None


# ---------------------------------------------------------------------------
# FileSystemCheckpointStore with extended data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_store_saves_extended_data(tmp_path):
    store = FileSystemCheckpointStore(str(tmp_path))
    cd = CheckpointData(
        run_id="test-run",
        graph_id="wf-1",
        graph_revision="abc123",
        completed_node_ids=["A"],
        node_outputs={"A": {"result": "hello"}},
    )
    state = {
        "state": {"run_id": "test-run", "node_statuses": {"A": "completed"}},
        "checkpoint_data": cd.model_dump(),
    }
    await store.save("test-run", state)
    loaded = await store.load("test-run")
    assert loaded is not None
    assert "checkpoint_data" in loaded
    loaded_cd = CheckpointData(**loaded["checkpoint_data"])
    assert loaded_cd.graph_revision == "abc123"
    assert loaded_cd.completed_node_ids == ["A"]
    assert loaded_cd.node_outputs["A"]["result"] == "hello"
