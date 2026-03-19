"""Checkpointing — persist and restore execution state across runs.

Provides a pluggable CheckpointStore protocol and a default filesystem
implementation that writes JSON snapshots after each scheduling level.

Also defines extended checkpoint metadata (CheckpointData), rerun scopes,
graph revision hashing, and staleness detection for checkpoint-based
partial reruns.
"""

from __future__ import annotations

import hashlib
import json
import asyncio
import time as _time
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Checkpoint store protocol + implementations
# ---------------------------------------------------------------------------


@runtime_checkable
class CheckpointStore(Protocol):
    """Protocol for checkpoint persistence backends."""

    async def save(self, run_id: str, state: dict[str, Any]) -> None: ...

    async def load(self, run_id: str) -> dict[str, Any] | None: ...

    async def list_runs(self) -> list[str]: ...


class FileSystemCheckpointStore:
    """Writes checkpoint JSON to ``{base_dir}/{run_id}/checkpoint.json``."""

    def __init__(self, base_dir: str = "./checkpoints") -> None:
        self.base_dir = Path(base_dir)

    def _run_dir(self, run_id: str) -> Path:
        return self.base_dir / run_id

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        def _write() -> None:
            run_dir = self._run_dir(run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            path = run_dir / "checkpoint.json"
            data = json.dumps(state, indent=2, default=str)
            path.write_text(data, encoding="utf-8")

        await asyncio.to_thread(_write)

    async def load(self, run_id: str) -> dict[str, Any] | None:
        def _read() -> dict[str, Any] | None:
            path = self._run_dir(run_id) / "checkpoint.json"
            if not path.exists():
                return None
            text = path.read_text(encoding="utf-8")
            return json.loads(text)

        return await asyncio.to_thread(_read)

    async def list_runs(self) -> list[str]:
        if not self.base_dir.exists():
            return []
        return sorted(
            d.name
            for d in self.base_dir.iterdir()
            if d.is_dir() and (d / "checkpoint.json").exists()
        )


class NullCheckpointStore:
    """No-op checkpoint store for when checkpointing is disabled."""

    async def save(self, run_id: str, state: dict[str, Any]) -> None:
        pass

    async def load(self, run_id: str) -> dict[str, Any] | None:
        return None

    async def list_runs(self) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# Extended checkpoint metadata model
# ---------------------------------------------------------------------------


class CheckpointData(BaseModel):
    """Extended metadata stored alongside the raw checkpoint dict.

    Populated by the engine's ``_save_checkpoint`` method so that
    checkpoint portals can determine which nodes are reusable, whether
    the graph has changed, and what outputs are available for injection.
    """

    run_id: str
    graph_id: str = ""
    timestamp: float = Field(default_factory=_time.time)

    # Deterministic hash of graph nodes + edges at checkpoint time.
    # Used to detect stale checkpoints when the graph has been edited.
    graph_revision: str | None = None

    # Node IDs that completed before this checkpoint was taken.
    completed_node_ids: list[str] = Field(default_factory=list)

    # Persisted outputs for completed nodes, keyed by node_id.
    # Values are dicts mapping port_name -> value (from PortDataStore).
    node_outputs: dict[str, Any] = Field(default_factory=dict)
    pending_node_ids: list[str] = Field(default_factory=list)
    checkpoint_trigger: str = ""
    effective_run_policy: dict[str, Any] | None = None
    stop_reason: str = ""
    partial: bool = False
    resumable: bool = False
    remaining_node_ids: list[str] = Field(default_factory=list)
    progress: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Rerun scope model
# ---------------------------------------------------------------------------


class RerunScope(BaseModel):
    """Defines which portion of a graph to rerun from a checkpoint.

    - ``downstream_of``: rerun all nodes downstream of (and including)
      the target node. Upstream completed nodes are skipped.
    - ``single_node``: rerun only the target node with its checkpointed
      inputs. All other nodes are skipped.
    - ``subgraph``: rerun all nodes within the named sub-graph.
    """

    scope_type: Literal["downstream_of", "single_node", "subgraph"]
    target_node_id: str | None = None  # for downstream_of and single_node
    sub_graph_key: str | None = None  # for subgraph


# ---------------------------------------------------------------------------
# Graph revision hashing
# ---------------------------------------------------------------------------


def compute_graph_revision_hash(graph) -> str:
    """Compute a deterministic SHA-256 hash of graph nodes + edges.

    Accepts either a ``Graph`` model instance or a plain dict (serialised
    graph). The hash is based on sorted JSON of nodes and edges only —
    metadata, sub_graphs, and hyperedges are excluded so that cosmetic
    changes (name, description, tags) do not invalidate checkpoints.
    """
    if hasattr(graph, "model_dump"):
        graph_dict = json.loads(graph.model_dump_json())
    elif isinstance(graph, dict):
        graph_dict = graph
    else:
        raise TypeError(f"Expected Graph model or dict, got {type(graph)}")

    # Extract only structural components: nodes and edges.
    nodes = graph_dict.get("nodes", [])
    edges = graph_dict.get("edges", [])
    canonical = json.dumps(
        {"nodes": nodes, "edges": edges},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------


class StalenessResult(BaseModel):
    """Result of comparing a checkpoint's graph revision to the current graph."""

    compatible: bool = True
    stale: bool = False
    missing_nodes: list[str] = Field(default_factory=list)
    message: str = ""


def check_checkpoint_staleness(
    checkpoint_revision: str | None,
    current_graph,
    checkpoint_completed_node_ids: list[str] | None = None,
) -> StalenessResult:
    """Compare a checkpoint's graph revision to the current graph.

    Returns a ``StalenessResult`` indicating whether the checkpoint is
    still compatible with the current graph structure.

    Parameters
    ----------
    checkpoint_revision:
        The ``graph_revision`` stored in the checkpoint. If None, the
        checkpoint predates revision tracking and is considered stale.
    current_graph:
        The current ``Graph`` model or dict. Used to compute the current
        revision hash and to check for missing nodes.
    checkpoint_completed_node_ids:
        Node IDs recorded as completed in the checkpoint. Used to detect
        nodes that no longer exist in the current graph.
    """
    if checkpoint_revision is None:
        return StalenessResult(
            compatible=False,
            stale=True,
            message="Checkpoint has no graph revision — created before revision tracking was added.",
        )

    current_revision = compute_graph_revision_hash(current_graph)

    if checkpoint_revision == current_revision:
        return StalenessResult(
            compatible=True,
            stale=False,
            message="Checkpoint is compatible with the current graph.",
        )

    # Revisions differ — check which nodes are missing.
    if hasattr(current_graph, "nodes"):
        current_node_ids = {n.id for n in current_graph.nodes}
    elif isinstance(current_graph, dict):
        current_node_ids = {n.get("id", "") for n in current_graph.get("nodes", [])}
    else:
        current_node_ids = set()

    missing: list[str] = []
    if checkpoint_completed_node_ids:
        missing = [
            nid for nid in checkpoint_completed_node_ids
            if nid not in current_node_ids
        ]

    return StalenessResult(
        compatible=False,
        stale=True,
        missing_nodes=missing,
        message=(
            f"Graph has changed since checkpoint (revision {checkpoint_revision} "
            f"vs current {current_revision}). "
            + (f"{len(missing)} completed node(s) no longer exist in the graph." if missing else "")
        ),
    )


# ---------------------------------------------------------------------------
# Downstream node computation
# ---------------------------------------------------------------------------


def compute_downstream_nodes(
    target_node_id: str,
    graph,
    *,
    include_target: bool = True,
) -> set[str]:
    """Compute all nodes downstream of *target_node_id* via BFS on edges.

    Returns a set of node IDs that depend (directly or transitively) on
    the target node. Includes the target itself if *include_target* is True.
    """
    # Build adjacency: source -> [target, ...]
    adjacency: dict[str, list[str]] = {}
    edges = graph.edges if hasattr(graph, "edges") else []
    for edge in edges:
        src = edge.source_node_id if hasattr(edge, "source_node_id") else edge.get("source_node_id", "")
        tgt = edge.target_node_id if hasattr(edge, "target_node_id") else edge.get("target_node_id", "")
        if src and tgt:
            adjacency.setdefault(src, []).append(tgt)

    # BFS from target
    visited: set[str] = set()
    queue = [target_node_id]
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for child in adjacency.get(current, []):
            if child not in visited:
                queue.append(child)

    if not include_target:
        visited.discard(target_node_id)

    return visited


def compute_subgraph_node_ids(graph, sub_graph_key: str) -> set[str]:
    """Return the set of node IDs belonging to a named sub-graph.

    Looks up ``graph.sub_graphs[sub_graph_key]`` and returns all node
    IDs within it. Returns an empty set if the key does not exist.
    """
    sub_graphs = graph.sub_graphs if hasattr(graph, "sub_graphs") else {}
    if isinstance(sub_graphs, dict):
        sub = sub_graphs.get(sub_graph_key)
        if sub is not None:
            nodes = sub.nodes if hasattr(sub, "nodes") else sub.get("nodes", [])
            return {
                (n.id if hasattr(n, "id") else n.get("id", ""))
                for n in nodes
            }
    return set()
