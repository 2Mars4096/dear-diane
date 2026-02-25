"""Runtime execution state — node status tracking and port data store."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from dan.models.graph import Graph, Edge
from dan.models.edges import DataEdge


class NodeStatus(str, Enum):
    """Lifecycle states for a node during graph execution."""

    PENDING = "pending"
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PortDataStore:
    """Maps (node_id, port_name) -> value for completed node outputs.

    The scheduler writes here after each node finishes; the edge resolver
    reads here to collect inputs for downstream nodes.
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], Any] = {}

    def set(self, node_id: str, port_name: str, value: Any) -> None:
        self._data[(node_id, port_name)] = value

    def get(self, node_id: str, port_name: str) -> Any:
        return self._data[(node_id, port_name)]

    def has(self, node_id: str, port_name: str) -> bool:
        return (node_id, port_name) in self._data

    def get_node_outputs(self, node_id: str) -> dict[str, Any]:
        """Return all port values for a given node."""
        return {
            port: value
            for (nid, port), value in self._data.items()
            if nid == node_id
        }

    def clear_node(self, node_id: str) -> None:
        """Remove all port data for a given node."""
        keys_to_remove = [k for k in self._data if k[0] == node_id]
        for k in keys_to_remove:
            del self._data[k]

    def resolve_inputs(self, node_id: str, graph: Graph) -> dict[str, Any]:
        """Collect all upstream data-edge values destined for *node_id*.

        Returns a dict mapping target_port_name -> value from the
        source node's output port.
        """
        inputs: dict[str, Any] = {}
        for edge in graph.edges_to(node_id):
            if isinstance(edge, DataEdge):
                if self.has(edge.source_node_id, edge.source_port):
                    inputs[edge.target_port] = self.get(
                        edge.source_node_id, edge.source_port
                    )
        return inputs

    def snapshot(self) -> dict[str, Any]:
        """Serialisable snapshot for checkpointing."""
        return {
            f"{nid}::{port}": value
            for (nid, port), value in self._data.items()
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> PortDataStore:
        store = cls()
        for key, value in data.items():
            nid, port = key.split("::", 1)
            store.set(nid, port, value)
        return store


class ExecutionState:
    """Aggregate runtime state for a single graph execution run."""

    def __init__(self, graph: Graph, run_id: str | None = None) -> None:
        self.graph = graph
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.node_statuses: dict[str, NodeStatus] = {
            node.id: NodeStatus.PENDING for node in graph.nodes
        }
        self.port_data = PortDataStore()
        self.node_errors: dict[str, str] = {}
        self.node_metadata: dict[str, dict[str, Any]] = {}

    def mark(self, node_id: str, status: NodeStatus) -> None:
        self.node_statuses[node_id] = status

    def is_complete(self, node_id: str) -> bool:
        return self.node_statuses.get(node_id) == NodeStatus.COMPLETED

    def is_terminal(self, node_id: str) -> bool:
        s = self.node_statuses.get(node_id)
        return s in (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED)

    def all_finished(self) -> bool:
        return all(self.is_terminal(nid) for nid in self.node_statuses)

    def pending_nodes(self) -> list[str]:
        return [
            nid for nid, s in self.node_statuses.items()
            if s == NodeStatus.PENDING
        ]

    def snapshot(self) -> dict[str, Any]:
        """Full serialisable snapshot for checkpointing."""
        return {
            "run_id": self.run_id,
            "node_statuses": {nid: s.value for nid, s in self.node_statuses.items()},
            "port_data": self.port_data.snapshot(),
            "node_errors": dict(self.node_errors),
            "node_metadata": dict(self.node_metadata),
        }

    def restore_from_snapshot(self, snap: dict[str, Any]) -> None:
        self.run_id = snap["run_id"]
        self.node_statuses = {
            nid: NodeStatus(s) for nid, s in snap["node_statuses"].items()
        }
        self.port_data = PortDataStore.from_snapshot(snap["port_data"])
        self.node_errors = snap.get("node_errors", {})
        self.node_metadata = snap.get("node_metadata", {})
