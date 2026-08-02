"""Tests for engine/context_runtime.py — SharedContextStore, ArtifactStore, LocalStateManager."""

import pytest

from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.models.context import SharedContextDeclaration


class TestSharedContextStore:
    def _store(self, keys=("outline", "draft")):
        decls = [SharedContextDeclaration(key=k) for k in keys]
        return SharedContextStore(decls)

    def test_write_read(self):
        store = self._store()
        store.write("outline", {"sections": ["intro", "body"]})
        assert store.read("outline") == {"sections": ["intro", "body"]}

    def test_read_missing_returns_none(self):
        store = self._store()
        assert store.read("outline") is None

    def test_undeclared_key_raises(self):
        store = self._store()
        with pytest.raises(KeyError, match="Undeclared"):
            store.read("unknown_key")
        with pytest.raises(KeyError, match="Undeclared"):
            store.write("unknown_key", "x")

    def test_append(self):
        store = self._store()
        store.append("outline", "item1")
        store.append("outline", "item2")
        assert store.read("outline") == ["item1", "item2"]

    def test_append_to_non_list(self):
        store = self._store()
        store.write("outline", "scalar")
        store.append("outline", "extra")
        assert store.read("outline") == ["scalar", "extra"]

    def test_snapshot_restore(self):
        store = self._store()
        store.write("outline", [1, 2, 3])
        snap = store.snapshot()

        store2 = self._store()
        store2.restore(snap)
        assert store2.read("outline") == [1, 2, 3]


class TestArtifactStore:
    def test_store_fetch(self):
        store = ArtifactStore()
        ref = store.store("artifact://draft-v1", "draft content", media_type="text/plain")
        assert store.fetch("artifact://draft-v1") == "draft content"
        assert ref.uri == "artifact://draft-v1"
        assert ref.media_type == "text/plain"

    def test_versioning(self):
        store = ArtifactStore()
        store.store("artifact://doc", "v1")
        store.store("artifact://doc", "v2")
        assert store.fetch("artifact://doc") == "v2"
        assert store.version_count("artifact://doc") == 2

    def test_missing_raises(self):
        store = ArtifactStore()
        with pytest.raises(KeyError):
            store.fetch("artifact://missing")

    def test_has(self):
        store = ArtifactStore()
        assert not store.has("artifact://x")
        store.store("artifact://x", "data")
        assert store.has("artifact://x")

    def test_snapshot_restore(self):
        store = ArtifactStore()
        store.store("artifact://a", {"key": "val"})
        snap = store.snapshot()

        store2 = ArtifactStore()
        store2.restore(snap)
        assert store2.fetch("artifact://a") == {"key": "val"}


class TestLocalStateManager:
    def test_get_scope_creates(self):
        mgr = LocalStateManager()
        scope = mgr.get_scope("loop-1")
        assert scope == {}
        assert mgr.has_scope("loop-1")

    def test_set_scope(self):
        mgr = LocalStateManager()
        mgr.set_scope("loop-1", {"iteration": 3})
        assert mgr.get_scope("loop-1") == {"iteration": 3}

    def test_update_scope(self):
        mgr = LocalStateManager()
        mgr.set_scope("s1", {"a": 1})
        mgr.update_scope("s1", {"b": 2})
        assert mgr.get_scope("s1") == {"a": 1, "b": 2}

    def test_delete_scope(self):
        mgr = LocalStateManager()
        mgr.set_scope("s1", {"x": 1})
        mgr.delete_scope("s1")
        assert not mgr.has_scope("s1")

    def test_snapshot_restore(self):
        mgr = LocalStateManager()
        mgr.set_scope("s1", {"k": "v"})
        snap = mgr.snapshot()

        mgr2 = LocalStateManager()
        mgr2.restore(snap)
        assert mgr2.get_scope("s1") == {"k": "v"}
