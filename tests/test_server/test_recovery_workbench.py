"""Tests for Recovery/Debug Workbench endpoints (Plan 13-2).

Covers:
- POST /api/runs/{run_id}/rerun (checkpoint-based partial rerun)
- GET/POST/DELETE /api/test-cases/{workflow_id}/{node_id} (test case CRUD)
- POST /api/test-cases/{workflow_id}/{node_id}/{case_id}/run (run test case)
- GET /api/graphs/{graph_id}/nodes/{node_id}/inputs (variable inspector)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from dan.models.edges import DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import LLMOperator
from dan.models.ports import InputPort, OutputPort
from dan.server.test_cases import NodeTestCase, TestCaseStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph(
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    entry_points: list[str] | None = None,
    exit_points: list[str] | None = None,
) -> Graph:
    node_list = []
    for nd in (nodes or []):
        node_list.append(LLMOperator(
            id=nd["id"],
            name=nd.get("name", nd["id"]),
            model=nd.get("model", "test-model"),
            prompt_template=nd.get("prompt", "Hello"),
            input_ports=[InputPort(name="input", required=True)],
            output_ports=[OutputPort(name="text")],
        ))
    edge_list = []
    for i, ed in enumerate(edges or []):
        edge_list.append(DataEdge(
            id=ed.get("id", f"e{i}"),
            source_node_id=ed["src"],
            source_port=ed.get("src_port", "text"),
            target_node_id=ed["tgt"],
            target_port=ed.get("tgt_port", "input"),
        ))
    return Graph(
        metadata=GraphMetadata(name="test"),
        nodes=node_list,
        edges=edge_list,
        entry_points=entry_points or [],
        exit_points=exit_points or [],
    )


def _linear_graph() -> Graph:
    return _make_graph(
        nodes=[{"id": "A"}, {"id": "B"}, {"id": "C"}],
        edges=[
            {"src": "A", "tgt": "B"},
            {"src": "B", "tgt": "C"},
        ],
        entry_points=["A"],
        exit_points=["C"],
    )


# ---------------------------------------------------------------------------
# Variable Inspector (unit, no server needed)
# ---------------------------------------------------------------------------


class TestVariableInspector:
    def test_returns_upstream_variables(self):
        from dan.server.variable_inspector import compute_upstream_variables

        g = _linear_graph()
        variables = compute_upstream_variables("B", g)
        connected = [v for v in variables if v["connected"]]
        assert len(connected) == 1
        assert connected[0]["source_node_id"] == "A"
        assert connected[0]["variable_name"] == "input"

    def test_no_incoming_edges(self):
        from dan.server.variable_inspector import compute_upstream_variables

        g = _linear_graph()
        variables = compute_upstream_variables("A", g)
        connected = [v for v in variables if v["connected"]]
        assert len(connected) == 0

    def test_unconnected_required_ports_shown(self):
        from dan.server.variable_inspector import compute_upstream_variables

        g = _make_graph(
            nodes=[{"id": "X"}],
            edges=[],
            entry_points=["X"],
        )
        variables = compute_upstream_variables("X", g)
        unconnected = [v for v in variables if not v["connected"]]
        assert len(unconnected) >= 1
        assert unconnected[0]["required"] is True

    def test_nonexistent_node(self):
        from dan.server.variable_inspector import compute_upstream_variables

        g = _linear_graph()
        variables = compute_upstream_variables("MISSING", g)
        assert variables == []


# ---------------------------------------------------------------------------
# Test Case CRUD (unit, via TestCaseStore)
# ---------------------------------------------------------------------------


class TestTestCaseCRUD:
    def test_create_and_get(self, tmp_path: Path):
        store = TestCaseStore(base_dir=tmp_path)
        case = NodeTestCase(
            name="smoke",
            node_id="nodeA",
            inputs={"prompt": "test"},
            expected_outputs={"text": "result"},
        )
        store.save_case("wf1", "nodeA", case)
        result = store.list_cases("wf1", "nodeA")
        assert len(result) == 1
        assert result[0].name == "smoke"

    def test_create_multiple(self, tmp_path: Path):
        store = TestCaseStore(base_dir=tmp_path)
        for i in range(3):
            case = NodeTestCase(name=f"case-{i}", node_id="n1", inputs={"x": i})
            store.save_case("wf1", "n1", case)
        assert len(store.list_cases("wf1", "n1")) == 3

    def test_delete(self, tmp_path: Path):
        store = TestCaseStore(base_dir=tmp_path)
        case = NodeTestCase(name="del-me", node_id="n1", inputs={})
        store.save_case("wf1", "n1", case)
        assert store.delete_case("wf1", "n1", case.id) is True
        assert store.list_cases("wf1", "n1") == []

    def test_delete_nonexistent(self, tmp_path: Path):
        store = TestCaseStore(base_dir=tmp_path)
        assert store.delete_case("wf1", "n1", "nope") is False


# ---------------------------------------------------------------------------
# Checkpoint Rerun (unit, via RunManager)
# ---------------------------------------------------------------------------


class TestCheckpointRerun:
    @pytest.mark.asyncio
    async def test_rerun_no_checkpoint_store(self):
        from dan.engine.checkpoint import RerunScope
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunManager

        rm = RunManager(engine_config=EngineConfig(checkpoint_enabled=False))
        scope = RerunScope(scope_type="single_node", target_node_id="A")
        with pytest.raises(ValueError, match="No checkpoint store"):
            await rm.rerun_from_checkpoint(_linear_graph(), "wf-1", "run-1", scope)

    @pytest.mark.asyncio
    async def test_rerun_scope_downstream_of(self):
        from dan.engine.checkpoint import RerunScope

        scope = RerunScope(scope_type="downstream_of", target_node_id="B")
        assert scope.scope_type == "downstream_of"
        assert scope.target_node_id == "B"

    @pytest.mark.asyncio
    async def test_rerun_scope_single_node(self):
        from dan.engine.checkpoint import RerunScope

        scope = RerunScope(scope_type="single_node", target_node_id="A")
        assert scope.scope_type == "single_node"

    def test_staleness_detection_compatible(self):
        from dan.engine.checkpoint import (
            check_checkpoint_staleness,
            compute_graph_revision_hash,
        )

        g = _linear_graph()
        rev = compute_graph_revision_hash(g)
        result = check_checkpoint_staleness(rev, g, ["A", "B"])
        assert result.compatible is True
        assert result.stale is False

    def test_staleness_detection_stale(self):
        from dan.engine.checkpoint import (
            check_checkpoint_staleness,
            compute_graph_revision_hash,
        )

        g1 = _linear_graph()
        rev = compute_graph_revision_hash(g1)
        g2 = _make_graph(
            nodes=[{"id": "X"}, {"id": "Y"}],
            edges=[{"src": "X", "tgt": "Y"}],
        )
        result = check_checkpoint_staleness(rev, g2)
        assert result.stale is True
        assert result.compatible is False
