"""Tests for 18-2 caching layer (memoization + semantic cache)."""

from __future__ import annotations

from typing import Any

import pytest

from dan.engine import Engine, EngineConfig, ExecutorRegistry, NodeResult, NodeStatus
from dan.engine.cache import NodeResultCache, SemanticCache
from dan.engine.events import EventType
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.graph import Graph
from dan.models.nodes import LLMOperator, ToolOperator
from dan.rag import EmbeddingRegistry, EmbeddingResult


class FakeEmbeddingProvider:
    async def embed(self, texts: list[str], model: str) -> EmbeddingResult:
        vectors: list[list[float]] = []
        for t in texts:
            s = sum(ord(c) for c in t)
            vectors.append([float(len(t)), float(s % 997), float((s // 997) % 997)])
        return EmbeddingResult(vectors=vectors, model=model or "fake", dimensions=3)


class TestNodeResultCache:
    def test_cache_key_determinism(self):
        node = ToolOperator(id="n1", name="tool", tool_id="echo", tool_config={"a": 1})
        inputs = {"x": 1, "y": "z"}

        k1 = NodeResultCache.compute_cache_key(node, inputs, policy_signature="p1")
        k2 = NodeResultCache.compute_cache_key(node, inputs, policy_signature="p1")
        k3 = NodeResultCache.compute_cache_key(node, {"x": 2, "y": "z"}, policy_signature="p1")

        assert k1 == k2
        assert k1 != k3

    def test_cache_key_sensitive_to_config(self):
        node_a = ToolOperator(id="n1", name="tool", tool_id="echo", tool_config={"a": 1})
        node_b = ToolOperator(id="n1", name="tool", tool_id="echo", tool_config={"a": 2})
        key_a = NodeResultCache.compute_cache_key(node_a, {"x": 1})
        key_b = NodeResultCache.compute_cache_key(node_b, {"x": 1})
        assert key_a != key_b

    def test_put_lookup_roundtrip(self):
        cache = NodeResultCache(max_size_mb=1, enabled=True)
        key = "k1"
        result = NodeResult(
            outputs={"result": "ok"},
            status=NodeStatus.COMPLETED,
            metadata={"usage": {"total_tokens": 123}, "cost": 0.01},
        )
        cache.put(key, result, ttl=60)
        got, reason = cache.lookup(key)
        assert reason in {"hit", "disk"}
        assert got is not None
        assert got.outputs["result"] == "ok"
        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["tokens_saved"] == 123

    def test_lookup_disabled(self):
        cache = NodeResultCache(enabled=False)
        got, reason = cache.lookup("any")
        assert got is None
        assert reason == "disabled"

    def test_ttl_expiry(self, monkeypatch: pytest.MonkeyPatch):
        import dan.engine.cache as cache_mod

        now = 1000.0
        monkeypatch.setattr(cache_mod.time, "time", lambda: now)
        cache = NodeResultCache(enabled=True)
        cache.put("k", NodeResult(outputs={"x": 1}), ttl=10)

        got, _ = cache.lookup("k")
        assert got is not None

        monkeypatch.setattr(cache_mod.time, "time", lambda: now + 11)
        got2, reason2 = cache.lookup("k")
        assert got2 is None
        assert reason2 == "expired"


@pytest.mark.asyncio
class TestSemanticCache:
    async def test_normalization_equivalence(self):
        reg = EmbeddingRegistry()
        reg.register("default", FakeEmbeddingProvider())
        cache = SemanticCache(embedding_registry=reg)
        assert cache.normalize_query(" Hello,\nWORLD!!! ") == "hello, world"

    async def test_put_get_roundtrip(self):
        reg = EmbeddingRegistry()
        reg.register("default", FakeEmbeddingProvider())
        cache = SemanticCache(embedding_registry=reg, threshold=0.9)

        result = NodeResult(
            outputs={"text": "cached answer"},
            status=NodeStatus.COMPLETED,
            metadata={"usage": {"total_tokens": 88}},
        )
        await cache.put("Hello WORLD!!!", result, model="gpt-4o")

        got = await cache.get("  hello   world  ")
        assert got is not None
        assert got.outputs["text"] == "cached answer"
        stats = cache.stats()
        assert stats["hits"] == 1

    async def test_graceful_degradation_without_embedding_registry(self):
        cache = SemanticCache(embedding_registry=None)
        got = await cache.get("anything")
        assert got is None

    async def test_ttl_expiry(self):
        reg = EmbeddingRegistry()
        reg.register("default", FakeEmbeddingProvider())
        cache = SemanticCache(embedding_registry=reg, ttl_hours=0.0)
        await cache.put("prompt", NodeResult(outputs={"text": "x"}), model="m")
        got = await cache.get("prompt")
        assert got is None


@pytest.mark.asyncio
class TestSchedulerMemoizationIntegration:
    async def test_memoization_skips_second_run_with_persistent_cache(self, tmp_path):
        calls = {"n": 0}

        async def echo_tool() -> dict[str, Any]:
            calls["n"] += 1
            return {"result": "ok"}

        tool_registry = ToolRegistry()
        tool_registry.register("echo", echo_tool)

        executor_registry = ExecutorRegistry()
        executor_registry.register("tool_operator", ToolExecutor(tool_registry))

        events = []

        async def event_cb(event):
            events.append(event)

        cfg = EngineConfig(
            checkpoint_enabled=False,
            memory_enabled=False,
            cache_enabled=True,
            cache_dir=str(tmp_path / "cache"),
        )
        engine = Engine(
            config=cfg,
            executor_registry=executor_registry,
            event_callback=event_cb,
        )
        graph = Graph(
            nodes=[ToolOperator(id="t1", name="tool", tool_id="echo", memoize=True)],
            edges=[],
            entry_points=["t1"],
            exit_points=["t1"],
        )

        r1 = await engine.run(graph)
        r2 = await engine.run(graph)

        assert r1.success and r2.success
        assert calls["n"] == 1
        assert any(e.event_type == EventType.CACHE_HIT for e in events)

    async def test_cache_disabled_bypasses_memoization(self):
        calls = {"n": 0}

        async def echo_tool() -> dict[str, Any]:
            calls["n"] += 1
            return {"result": "ok"}

        tool_registry = ToolRegistry()
        tool_registry.register("echo", echo_tool)
        executor_registry = ExecutorRegistry()
        executor_registry.register("tool_operator", ToolExecutor(tool_registry))

        engine = Engine(
            config=EngineConfig(
                checkpoint_enabled=False,
                memory_enabled=False,
                cache_enabled=False,
            ),
            executor_registry=executor_registry,
        )
        graph = Graph(
            nodes=[ToolOperator(id="t1", name="tool", tool_id="echo", memoize=True)],
            edges=[],
            entry_points=["t1"],
            exit_points=["t1"],
        )

        await engine.run(graph)
        await engine.run(graph)
        assert calls["n"] == 2


class TestPersistentCache:
    """Tests for disk-backed persistent caching (18-2 task 2-3)."""

    def test_persistent_flag_creates_default_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            NodeResultCache, "_DEFAULT_CACHE_DIR", str(tmp_path / "dan_cache")
        )
        cache = NodeResultCache(persistent=True, enabled=True)
        assert cache._cache_dir is not None
        assert cache._cache_dir.exists()

    def test_persistent_write_read_roundtrip(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        cache = NodeResultCache(persistent=True, cache_dir=cache_dir, enabled=True)
        result = NodeResult(
            outputs={"answer": 42},
            status=NodeStatus.COMPLETED,
        )
        cache.put("pk1", result, ttl=300)
        cache._entries.clear()
        cache._current_bytes = 0

        got, reason = cache.lookup("pk1")
        assert got is not None
        assert got.outputs["answer"] == 42
        assert reason == "disk"

    def test_persistent_ttl_expiry_on_disk(self, tmp_path, monkeypatch):
        import dan.engine.cache as cache_mod

        cache_dir = str(tmp_path / "cache")
        now = 1000.0
        monkeypatch.setattr(cache_mod.time, "time", lambda: now)
        cache = NodeResultCache(
            persistent=True, cache_dir=cache_dir, enabled=True, cache_ttl=60,
        )
        cache.put("pk2", NodeResult(outputs={"x": 1}))

        cache._entries.clear()
        cache._current_bytes = 0

        monkeypatch.setattr(cache_mod.time, "time", lambda: now + 61)
        got, reason = cache.lookup("pk2")
        assert got is None
        assert reason == "expired"

    def test_disk_mtime_ttl_check(self, tmp_path, monkeypatch):
        import os

        cache_dir = str(tmp_path / "cache")
        cache = NodeResultCache(
            persistent=True, cache_dir=cache_dir, enabled=True, cache_ttl=10,
        )
        cache.put("mk1", NodeResult(outputs={"v": 1}))

        cache._entries.clear()
        cache._current_bytes = 0

        entry_path = cache._entry_path("mk1")
        assert entry_path is not None
        old_mtime = entry_path.stat().st_mtime - 20
        os.utime(str(entry_path), (old_mtime, old_mtime))

        got, reason = cache.lookup("mk1")
        assert got is None
        assert reason == "miss"

    def test_clear_removes_disk_files(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        cache = NodeResultCache(persistent=True, cache_dir=cache_dir, enabled=True)
        cache.put("c1", NodeResult(outputs={"a": 1}))
        cache.put("c2", NodeResult(outputs={"b": 2}))

        import os

        files_before = list((tmp_path / "cache").glob("*.json"))
        assert len(files_before) == 2

        cache.clear()
        files_after = list((tmp_path / "cache").glob("*.json"))
        assert len(files_after) == 0


class TestMemoryAwareInvalidation:
    """Tests for memory-dependent cache invalidation (18-2 task 2-5)."""

    def test_memory_snapshot_hash_stored_and_checked(self):
        cache = NodeResultCache(enabled=True)
        result = NodeResult(outputs={"x": 1})
        cache.put(
            "mak1",
            result,
            memory_dependency_keys=["key_a", "key_b"],
            memory_snapshot_hash="hash_v1",
        )

        got, reason = cache.lookup("mak1", memory_snapshot_hash="hash_v1")
        assert got is not None
        assert reason == "hit"

    def test_memory_change_invalidates(self):
        cache = NodeResultCache(enabled=True)
        cache.put(
            "mak2",
            NodeResult(outputs={"x": 1}),
            memory_dependency_keys=["key_a"],
            memory_snapshot_hash="hash_v1",
        )

        got, reason = cache.lookup("mak2", memory_snapshot_hash="hash_v2")
        assert got is None
        assert reason == "memory_changed"

    def test_no_dep_keys_skips_memory_check(self):
        cache = NodeResultCache(enabled=True)
        cache.put("mak3", NodeResult(outputs={"x": 1}))

        got, reason = cache.lookup("mak3", memory_snapshot_hash="any_hash")
        assert got is not None

    def test_no_snapshot_skips_memory_check(self):
        cache = NodeResultCache(enabled=True)
        cache.put(
            "mak4",
            NodeResult(outputs={"x": 1}),
            memory_dependency_keys=["k"],
            memory_snapshot_hash="h1",
        )
        got, reason = cache.lookup("mak4")
        assert got is not None

    def test_memory_invalidation_on_disk(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        cache = NodeResultCache(
            persistent=True, cache_dir=cache_dir, enabled=True,
        )
        cache.put(
            "dk1",
            NodeResult(outputs={"y": 2}),
            memory_dependency_keys=["mem_key"],
            memory_snapshot_hash="snap_1",
        )
        cache._entries.clear()
        cache._current_bytes = 0

        got, reason = cache.lookup("dk1", memory_snapshot_hash="snap_2")
        assert got is None
        assert reason == "memory_changed"


class TestModelFields:
    def test_nodebase_cache_fields_roundtrip(self):
        node = ToolOperator(
            id="t1",
            name="tool",
            tool_id="echo",
            memoize=True,
            cache_ttl=30,
        )
        restored = ToolOperator.model_validate(node.model_dump())
        assert restored.memoize is True
        assert restored.cache_ttl == 30

    def test_llm_semantic_cache_field_roundtrip(self):
        node = LLMOperator(
            id="l1",
            name="llm",
            model="gpt-4o",
            prompt_template="hello",
            temperature=0.0,
            semantic_cache=True,
        )
        restored = LLMOperator.model_validate(node.model_dump())
        assert restored.semantic_cache is True
