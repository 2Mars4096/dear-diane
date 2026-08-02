"""Tests for engine/state.py — NodeStatus, PortDataStore, ExecutionState."""

import pytest

from dan.engine.state import ExecutionState, NodeStatus, PortDataStore
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import LLMOperator
from dan.models.ports import InputPort, OutputPort


def _node(nid: str, inputs=None, outputs=None):
    return LLMOperator(
        id=nid,
        name=nid,
        model="test",
        prompt_template="test",
        input_ports=inputs or [],
        output_ports=outputs or [],
    )


class TestPortDataStore:
    def test_set_get(self):
        store = PortDataStore()
        store.set("n1", "out", 42)
        assert store.get("n1", "out") == 42

    def test_has(self):
        store = PortDataStore()
        assert not store.has("n1", "out")
        store.set("n1", "out", "hello")
        assert store.has("n1", "out")

    def test_get_node_outputs(self):
        store = PortDataStore()
        store.set("n1", "a", 1)
        store.set("n1", "b", 2)
        store.set("n2", "c", 3)
        assert store.get_node_outputs("n1") == {"a": 1, "b": 2}

    def test_resolve_inputs(self):
        n1 = _node("n1", outputs=[OutputPort(name="out")])
        n2 = _node("n2", inputs=[InputPort(name="in")])
        edge = DataEdge(
            id="e1",
            source_node_id="n1", source_port="out",
            target_node_id="n2", target_port="in",
        )
        graph = Graph(
            nodes=[n1, n2],
            edges=[edge],
            entry_points=["n1"],
            exit_points=["n2"],
        )

        store = PortDataStore()
        store.set("n1", "out", "hello")
        inputs = store.resolve_inputs("n2", graph)
        assert inputs == {"in": "hello"}

    def test_snapshot_roundtrip(self):
        store = PortDataStore()
        store.set("n1", "out", {"key": "value"})
        store.set("n2", "result", [1, 2, 3])

        snap = store.snapshot()
        restored = PortDataStore.from_snapshot(snap)
        assert restored.get("n1", "out") == {"key": "value"}
        assert restored.get("n2", "result") == [1, 2, 3]


class TestExecutionState:
    def _make_graph(self):
        n1 = _node("n1")
        n2 = _node("n2")
        return Graph(nodes=[n1, n2], entry_points=["n1"], exit_points=["n2"])

    def test_initial_status(self):
        graph = self._make_graph()
        state = ExecutionState(graph)
        assert state.node_statuses["n1"] == NodeStatus.PENDING
        assert state.node_statuses["n2"] == NodeStatus.PENDING

    def test_mark_and_query(self):
        state = ExecutionState(self._make_graph())
        state.mark("n1", NodeStatus.COMPLETED)
        assert state.is_complete("n1")
        assert state.is_terminal("n1")
        assert not state.all_finished()

        state.mark("n2", NodeStatus.FAILED)
        assert state.is_terminal("n2")
        assert state.all_finished()

    def test_pending_nodes(self):
        state = ExecutionState(self._make_graph())
        assert set(state.pending_nodes()) == {"n1", "n2"}
        state.mark("n1", NodeStatus.RUNNING)
        assert state.pending_nodes() == ["n2"]

    def test_snapshot_roundtrip(self):
        graph = self._make_graph()
        state = ExecutionState(graph, run_id="test-run")
        state.mark("n1", NodeStatus.COMPLETED)
        state.port_data.set("n1", "out", "data")
        state.node_errors["n2"] = "some error"

        snap = state.snapshot()
        restored = ExecutionState(graph)
        restored.restore_from_snapshot(snap)

        assert restored.run_id == "test-run"
        assert restored.node_statuses["n1"] == NodeStatus.COMPLETED
        assert restored.port_data.get("n1", "out") == "data"
        assert restored.node_errors["n2"] == "some error"
