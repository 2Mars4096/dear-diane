"""GraphStore.save_graph strict validation (DAN_STRICT_GRAPH_SAVE)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.server.graph_store import GraphSaveValidationError, GraphStore


def test_save_graph_rejects_invalid_tool_operator(tmp_path: Path) -> None:
    store = GraphStore(base_dir=str(tmp_path))
    bad = {
        "version": "dan_graph_v1",
        "metadata": {"name": "bad"},
        "nodes": [
            {
                "id": "n1",
                "name": "No tool",
                "node_type": "tool_operator",
            },
        ],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "hyperedges": [],
        "shared_context": [],
        "artifact_refs": [],
    }
    with pytest.raises(GraphSaveValidationError, match="schema validation"):
        store.save_graph("bad", bad)
    assert not (tmp_path / "bad.json").exists()


def test_save_graph_accepts_minimal_empty_graph(tmp_path: Path) -> None:
    store = GraphStore(base_dir=str(tmp_path))
    minimal = {"nodes": [], "edges": []}
    saved = store.save_graph("empty", minimal)
    assert saved["version"] == "dan_graph_v1"
    disk = json.loads((tmp_path / "empty.json").read_text(encoding="utf-8"))
    assert disk["nodes"] == []


def test_save_graph_strict_disabled_allows_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_STRICT_GRAPH_SAVE", "false")
    store = GraphStore(base_dir=str(tmp_path))
    bad = {
        "version": "dan_graph_v1",
        "metadata": {},
        "nodes": [{"id": "x", "name": "x", "node_type": "tool_operator"}],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "hyperedges": [],
        "shared_context": [],
        "artifact_refs": [],
    }
    store.save_graph("legacy", bad)
    assert (tmp_path / "legacy.json").exists()
