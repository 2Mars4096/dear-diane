"""Filesystem-based graph persistence — stores dan_graph_v1 JSON files."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from dan.models.graph import Graph


class GraphStore:
    """CRUD operations for graph JSON files on disk."""

    LAST_OPENED_FILE = ".last_opened"

    def __init__(self, base_dir: str = "./graphs") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _graph_path(self, graph_id: str) -> Path:
        return self.base_dir / f"{graph_id}.json"

    def list_graphs(self) -> list[dict[str, Any]]:
        results = []
        for p in sorted(self.base_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                meta = data.get("metadata", {})
                results.append({
                    "graph_id": p.stem,
                    "name": meta.get("name", p.stem),
                    "description": meta.get("description", ""),
                    "updated_at": meta.get("updated_at"),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return results

    def get_graph(self, graph_id: str) -> dict[str, Any] | None:
        path = self._graph_path(graph_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_graph(self, graph_id: str, data: dict[str, Any]) -> None:
        data.setdefault("metadata", {})
        data["metadata"]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        path = self._graph_path(graph_id)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def create_graph(self, graph_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if data is None:
            graph = Graph()
            graph.metadata.name = graph_id
            data = json.loads(graph.model_dump_json())
        data.setdefault("metadata", {})
        data["metadata"]["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        data["metadata"]["updated_at"] = data["metadata"]["created_at"]
        self.save_graph(graph_id, data)
        return data

    def delete_graph(self, graph_id: str) -> bool:
        path = self._graph_path(graph_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def load_as_model(self, graph_id: str) -> Graph | None:
        data = self.get_graph(graph_id)
        if data is None:
            return None
        return Graph.model_validate(data)

    def get_last_opened(self) -> str | None:
        path = self.base_dir / self.LAST_OPENED_FILE
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
        return None

    def set_last_opened(self, graph_id: str) -> None:
        path = self.base_dir / self.LAST_OPENED_FILE
        path.write_text(graph_id, encoding="utf-8")
