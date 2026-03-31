"""Filesystem-based graph persistence — stores dan_graph_v1 JSON files."""

from __future__ import annotations

import copy
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dan.migration.gate_migration import maybe_migrate_graph_dict
from dan.meta.workflow_contract import normalize_workflow_id
from dan.models.graph import Graph
from dan.validation.graph import validate_graph

_GRAPH_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")


class GraphSaveValidationError(Exception):
    """Raised when ``save_graph`` rejects invalid JSON under strict validation."""


def _strict_graph_save_enabled() -> bool:
    return os.environ.get("DAN_STRICT_GRAPH_SAVE", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _is_graph_validation_warning(msg: str) -> bool:
    lower = msg.lower()
    return any(p in lower for p in ("warning", "deprecated", "untyped"))


def _validate_graph_id(graph_id: str) -> None:
    """Raise ValueError if graph_id could escape the graphs directory."""
    if not graph_id or not isinstance(graph_id, str):
        raise ValueError("Graph ID must be a non-empty string")
    if ".." in graph_id or "/" in graph_id or "\\" in graph_id:
        raise ValueError(f"Graph ID contains forbidden characters: {graph_id!r}")
    if not _GRAPH_ID_RE.match(graph_id):
        raise ValueError(
            f"Graph ID must match [A-Za-z0-9_][A-Za-z0-9._-]{{0,63}}, got: {graph_id!r}"
        )


class GraphStore:
    """CRUD operations for graph JSON files on disk."""

    LAST_OPENED_FILE = ".last_opened"

    def __init__(self, base_dir: str = "./graphs") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _graph_path(self, graph_id: str) -> Path:
        _validate_graph_id(graph_id)
        return self.base_dir / f"{graph_id}.json"

    def suggest_graph_id(
        self,
        preferred: str | None,
        *,
        fallback: str = "workflow",
        exact: bool = False,
    ) -> str:
        base = normalize_workflow_id(preferred) or normalize_workflow_id(fallback) or "workflow"
        _validate_graph_id(base)
        if exact:
            if self.get_graph(base) is not None:
                raise ValueError(f"Graph '{base}' already exists")
            return base
        candidate = base
        suffix = 2
        while self.get_graph(candidate) is not None:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

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
        _validate_graph_id(graph_id)
        path = self._graph_path(graph_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save_graph(self, graph_id: str, data: dict[str, Any]) -> dict[str, Any]:
        _validate_graph_id(graph_id)
        payload = copy.deepcopy(data)
        if _strict_graph_save_enabled():
            try:
                migrated = maybe_migrate_graph_dict(payload)
                model = Graph.model_validate(migrated)
            except ValidationError as exc:
                raise GraphSaveValidationError(
                    f"Graph schema validation failed: {exc}",
                ) from exc
            raw_errs = validate_graph(model)
            fatal = [e for e in raw_errs if not _is_graph_validation_warning(e)]
            if fatal:
                raise GraphSaveValidationError(
                    "Graph semantic validation failed: " + "; ".join(fatal[:8]),
                )
            payload = json.loads(model.model_dump_json())
        payload.setdefault("metadata", {})
        payload["metadata"]["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        path = self._graph_path(graph_id)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def create_graph(self, graph_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        _validate_graph_id(graph_id)
        if data is None:
            graph = Graph()
            graph.metadata.name = graph_id
            data = json.loads(graph.model_dump_json())
        data.setdefault("metadata", {})
        data["metadata"]["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        data["metadata"]["updated_at"] = data["metadata"]["created_at"]
        return self.save_graph(graph_id, data)

    def fork_graph(
        self,
        source_graph_id: str,
        *,
        new_graph_id: str | None = None,
        new_name: str | None = None,
        data_override: dict[str, Any] | None = None,
        exact_id: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        _validate_graph_id(source_graph_id)
        source = copy.deepcopy(data_override if data_override is not None else self.get_graph(source_graph_id))
        if source is None:
            raise KeyError(f"Graph '{source_graph_id}' not found")

        target_id = self.suggest_graph_id(
            new_graph_id or new_name or f"{source_graph_id}-copy",
            fallback=f"{source_graph_id}-copy",
            exact=exact_id and bool(new_graph_id),
        )

        source.setdefault("metadata", {})
        metadata = dict(source.get("metadata") or {})
        display_name = str(new_name or metadata.get("name") or target_id).strip() or target_id
        metadata["name"] = display_name
        metadata["forked_from"] = source_graph_id
        source["metadata"] = metadata

        saved = self.create_graph(target_id, source)
        return target_id, saved

    def delete_graph(self, graph_id: str) -> bool:
        _validate_graph_id(graph_id)
        path = self._graph_path(graph_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def load_as_model(self, graph_id: str) -> Graph | None:
        _validate_graph_id(graph_id)
        data = self.get_graph(graph_id)
        if data is None:
            return None
        data = maybe_migrate_graph_dict(data)
        return Graph.model_validate(data)

    def get_last_opened(self) -> str | None:
        path = self.base_dir / self.LAST_OPENED_FILE
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
        return None

    def set_last_opened(self, graph_id: str) -> None:
        _validate_graph_id(graph_id)
        path = self.base_dir / self.LAST_OPENED_FILE
        path.write_text(graph_id, encoding="utf-8")
