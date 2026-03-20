from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.migration.gate_migration import maybe_migrate_graph_dict
from dan.server.graph_store import GraphStore
from dan.server.routers import adapters as adapters_module


def _legacy_if_else_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "legacy"},
        "nodes": [
            {
                "id": "branch",
                "name": "branch",
                "node_type": "if_else",
                "condition": "True",
            }
        ],
        "edges": [],
        "sub_graphs": {},
        "entry_points": ["branch"],
        "exit_points": ["branch"],
        "shared_context": [],
        "artifact_refs": [],
        "hyperedges": [],
    }


def test_maybe_migrate_graph_dict_respects_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _legacy_if_else_graph()

    monkeypatch.delenv("DAN_GATE_MIGRATION_ENABLED", raising=False)
    untouched = maybe_migrate_graph_dict(graph)
    assert untouched["nodes"][0]["node_type"] == "if_else"

    monkeypatch.setenv("DAN_GATE_MIGRATION_ENABLED", "1")
    migrated = maybe_migrate_graph_dict(graph)
    assert migrated["nodes"][0]["node_type"] == "gate"
    assert migrated["nodes"][0]["gate_mode"] == "if_else"


def test_graph_store_load_as_model_applies_optional_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GraphStore(str(tmp_path / "graphs"))
    store.save_graph("legacy", _legacy_if_else_graph())

    monkeypatch.delenv("DAN_GATE_MIGRATION_ENABLED", raising=False)
    graph = store.load_as_model("legacy")
    assert graph is not None
    assert graph.nodes[0].node_type == "if_else"

    monkeypatch.setenv("DAN_GATE_MIGRATION_ENABLED", "1")
    migrated = store.load_as_model("legacy")
    assert migrated is not None
    assert migrated.nodes[0].node_type == "gate"


def test_adapter_graph_id_load_applies_optional_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GraphStore(str(tmp_path / "graphs"))
    store.save_graph("legacy", _legacy_if_else_graph())

    monkeypatch.setattr(adapters_module, "get_graph_store", lambda: store)

    monkeypatch.delenv("DAN_GATE_MIGRATION_ENABLED", raising=False)
    graph = adapters_module._load_workflow_for_adapter("legacy")
    assert graph.nodes[0].node_type == "if_else"

    monkeypatch.setenv("DAN_GATE_MIGRATION_ENABLED", "1")
    migrated = adapters_module._load_workflow_for_adapter("legacy")
    assert migrated.nodes[0].node_type == "gate"


def test_adapter_json_file_load_applies_optional_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_path = tmp_path / "legacy.json"
    graph_path.write_text(json.dumps(_legacy_if_else_graph()), encoding="utf-8")

    monkeypatch.setattr(adapters_module, "get_graph_store", lambda: GraphStore(str(tmp_path / "graphs")))

    monkeypatch.setenv("DAN_GATE_MIGRATION_ENABLED", "1")
    migrated = adapters_module._load_workflow_for_adapter(str(graph_path))
    assert migrated.nodes[0].node_type == "gate"
