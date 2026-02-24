"""Runtime context stores — Layers 2-4 of the four-layer state model.

Layer 1 (edge data) is handled by PortDataStore in state.py.
"""

from __future__ import annotations

import copy
from typing import Any

from dan.models.context import (
    ArtifactRef,
    ContextMode,
    SharedContextDeclaration,
)


class SharedContextStore:
    """Runtime Layer 3 — namespaced key-value blackboard.

    Validates reads/writes against declared shared_context keys and
    enforces ContextMode (read/write/append).
    """

    def __init__(
        self, declarations: list[SharedContextDeclaration] | None = None
    ) -> None:
        self._declarations = {d.key: d for d in (declarations or [])}
        self._store: dict[str, Any] = {}

    @property
    def declared_keys(self) -> set[str]:
        return set(self._declarations)

    def read(self, key: str) -> Any:
        if key not in self._declarations:
            raise KeyError(f"Undeclared shared-context key: '{key}'")
        return self._store.get(key)

    def write(self, key: str, value: Any) -> None:
        if key not in self._declarations:
            raise KeyError(f"Undeclared shared-context key: '{key}'")
        self._store[key] = value

    def append(self, key: str, value: Any) -> None:
        if key not in self._declarations:
            raise KeyError(f"Undeclared shared-context key: '{key}'")
        existing = self._store.get(key)
        if existing is None:
            self._store[key] = [value]
        elif isinstance(existing, list):
            existing.append(value)
        else:
            self._store[key] = [existing, value]

    def has(self, key: str) -> bool:
        return key in self._store

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._store)

    def restore(self, data: dict[str, Any]) -> None:
        self._store = copy.deepcopy(data)


class ArtifactStore:
    """Runtime Layer 4 — immutable artifact storage by URI.

    Each store() call creates a new version; fetch() retrieves the latest.
    Artifacts are stored in-memory for now; a persistent backend can be
    swapped in later.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[tuple[ArtifactRef, Any]]] = {}

    def store(
        self,
        uri: str,
        data: Any,
        media_type: str = "application/octet-stream",
        description: str = "",
        content_hash: str | None = None,
    ) -> ArtifactRef:
        ref = ArtifactRef(
            uri=uri,
            content_hash=content_hash,
            media_type=media_type,
            description=description,
        )
        self._store.setdefault(uri, []).append((ref, data))
        return ref

    def fetch(self, uri: str) -> Any:
        versions = self._store.get(uri)
        if not versions:
            raise KeyError(f"No artifact at URI: '{uri}'")
        return versions[-1][1]

    def fetch_ref(self, uri: str) -> ArtifactRef:
        versions = self._store.get(uri)
        if not versions:
            raise KeyError(f"No artifact at URI: '{uri}'")
        return versions[-1][0]

    def version_count(self, uri: str) -> int:
        return len(self._store.get(uri, []))

    def has(self, uri: str) -> bool:
        return uri in self._store and len(self._store[uri]) > 0

    def snapshot(self) -> dict[str, Any]:
        return {
            uri: [
                {"ref": ref.model_dump(), "data": data}
                for ref, data in versions
            ]
            for uri, versions in self._store.items()
        }

    def restore(self, data: dict[str, Any]) -> None:
        self._store = {}
        for uri, versions in data.items():
            self._store[uri] = [
                (ArtifactRef.model_validate(v["ref"]), v["data"])
                for v in versions
            ]


class LocalStateManager:
    """Runtime Layer 2 — scoped local state for composite/loop nodes.

    Each composite or loop node gets its own scope identified by node_id.
    State is mutable within scope and invisible to the parent graph.
    """

    def __init__(self) -> None:
        self._scopes: dict[str, dict[str, Any]] = {}

    def get_scope(self, scope_id: str) -> dict[str, Any]:
        return self._scopes.setdefault(scope_id, {})

    def set_scope(self, scope_id: str, state: dict[str, Any]) -> None:
        self._scopes[scope_id] = state

    def update_scope(self, scope_id: str, updates: dict[str, Any]) -> None:
        scope = self.get_scope(scope_id)
        scope.update(updates)

    def delete_scope(self, scope_id: str) -> None:
        self._scopes.pop(scope_id, None)

    def has_scope(self, scope_id: str) -> bool:
        return scope_id in self._scopes

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._scopes)

    def restore(self, data: dict[str, dict[str, Any]]) -> None:
        self._scopes = copy.deepcopy(data)
