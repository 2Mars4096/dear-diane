"""Shared fixture helpers for snapshot tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _normalize_graph(graph_dict: dict) -> dict:
    """Remove non-deterministic fields for stable comparison."""
    g = json.loads(json.dumps(graph_dict, sort_keys=True))
    for node in g.get("nodes", []):
        node.pop("position", None)
        meta = node.get("metadata", {})
        if isinstance(meta, dict):
            meta.pop("created_at", None)
            meta.pop("updated_at", None)
    meta = g.get("metadata", {})
    if isinstance(meta, dict):
        meta.pop("created_at", None)
        meta.pop("updated_at", None)
    # Recursively normalize sub_graphs
    for sub in g.get("sub_graphs", {}).values():
        if isinstance(sub, dict):
            _normalize_graph(sub)
    return g


def assert_snapshot(name: str, graph_dict: dict, *, update: bool = False) -> None:
    """Compare graph to stored snapshot. Set update=True to regenerate."""
    path = SNAPSHOT_DIR / f"{name}.json"
    normalized = _normalize_graph(graph_dict)
    if update or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(normalized, indent=2, sort_keys=True))
        return
    expected = json.loads(path.read_text())
    assert normalized == expected, (
        f"Snapshot mismatch for {name}. Run with UPDATE_SNAPSHOTS=1 to update."
    )


def update_snapshots() -> bool:
    """Return True if UPDATE_SNAPSHOTS env var is set to regenerate snapshots."""
    return os.environ.get("UPDATE_SNAPSHOTS", "").strip() == "1"
