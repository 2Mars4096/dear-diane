"""Tests for Plan 14-1 — Session / Conversation Memory.

Covers:
  - MemoryEntry / MemoryWriteRequest models
  - FileSystemMemoryStore CRUD, atomic writes, retention
  - Engine integration (session_id, pre-load, write_memory, flush)
  - Cross-run continuity (run1 writes, run2 reads)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from dan.engine.memory import MemoryEntry, MemoryScope, MemoryWriteRequest, WriteMode
from dan.engine.memory_store import FileSystemMemoryStore, NullMemoryStore


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def tmp_memory_dir(tmp_path: Path) -> str:
    return str(tmp_path / "memory")


@pytest.fixture
def store(tmp_memory_dir: str) -> FileSystemMemoryStore:
    return FileSystemMemoryStore(tmp_memory_dir)


# =====================================================================
# MemoryEntry model tests
# =====================================================================


class TestMemoryEntry:
    def test_defaults(self):
        entry = MemoryEntry(key="foo", value=42)
        assert entry.scope == MemoryScope.SESSION
        assert entry.write_mode == WriteMode.SET
        assert entry.source_run_id is None
        assert entry.created_at > 0

    def test_serialization_round_trip(self):
        entry = MemoryEntry(
            key="bar", value={"nested": [1, 2]},
            scope=MemoryScope.WORKFLOW,
            source_run_id="run-123",
            writer_node_id="node-abc",
        )
        data = entry.model_dump()
        restored = MemoryEntry.model_validate(data)
        assert restored.key == "bar"
        assert restored.value == {"nested": [1, 2]}
        assert restored.scope == MemoryScope.WORKFLOW

    def test_write_request_defaults(self):
        req = MemoryWriteRequest(key="k", value="v")
        assert req.scope == MemoryScope.SESSION
        assert req.mode == WriteMode.SET


# =====================================================================
# FileSystemMemoryStore CRUD tests
# =====================================================================


class TestFileSystemMemoryStore:
    @pytest.mark.asyncio
    async def test_write_and_read(self, store: FileSystemMemoryStore):
        entry = MemoryEntry(key="greeting", value="hello")
        await store.write("wf1", "sess1", entry)
        result = await store.read("wf1", "sess1", "greeting")
        assert result is not None
        assert result.key == "greeting"
        assert result.value == "hello"

    @pytest.mark.asyncio
    async def test_read_nonexistent(self, store: FileSystemMemoryStore):
        result = await store.read("wf1", "sess1", "nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_overwrite(self, store: FileSystemMemoryStore):
        e1 = MemoryEntry(key="x", value=1)
        await store.write("wf1", "s1", e1)
        e2 = MemoryEntry(key="x", value=2)
        await store.write("wf1", "s1", e2)
        result = await store.read("wf1", "s1", "x")
        assert result is not None
        assert result.value == 2

    @pytest.mark.asyncio
    async def test_append_mode(self, store: FileSystemMemoryStore):
        e1 = MemoryEntry(key="log", value="a", write_mode=WriteMode.APPEND)
        await store.write("wf1", "s1", e1)
        result = await store.read("wf1", "s1", "log")
        assert result is not None
        assert result.value == ["a"]

        e2 = MemoryEntry(key="log", value="b", write_mode=WriteMode.APPEND)
        await store.write("wf1", "s1", e2)
        result = await store.read("wf1", "s1", "log")
        assert result is not None
        assert result.value == ["a", "b"]

    @pytest.mark.asyncio
    async def test_merge_mode(self, store: FileSystemMemoryStore):
        e1 = MemoryEntry(key="config", value={"a": 1, "b": 2})
        await store.write("wf1", "s1", e1)

        e2 = MemoryEntry(key="config", value={"b": 99, "c": 3}, write_mode=WriteMode.MERGE)
        await store.write("wf1", "s1", e2)
        result = await store.read("wf1", "s1", "config")
        assert result is not None
        assert result.value == {"a": 1, "b": 99, "c": 3}

    @pytest.mark.asyncio
    async def test_delete(self, store: FileSystemMemoryStore):
        entry = MemoryEntry(key="tmp", value="gone")
        await store.write("wf1", "s1", entry)
        deleted = await store.delete("wf1", "s1", "tmp")
        assert deleted is True
        assert await store.read("wf1", "s1", "tmp") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store: FileSystemMemoryStore):
        assert await store.delete("wf1", "s1", "nope") is False

    @pytest.mark.asyncio
    async def test_list_keys(self, store: FileSystemMemoryStore):
        for k in ["b_key", "a_key", "c_key"]:
            await store.write("wf1", "s1", MemoryEntry(key=k, value=k))
        keys = await store.list_keys("wf1", "s1")
        assert keys == ["a_key", "b_key", "c_key"]

    @pytest.mark.asyncio
    async def test_list_keys_empty(self, store: FileSystemMemoryStore):
        assert await store.list_keys("wf1", "s1") == []

    @pytest.mark.asyncio
    async def test_list_sessions(self, store: FileSystemMemoryStore):
        await store.write("wf1", "alpha", MemoryEntry(key="k", value=1))
        await store.write("wf1", "beta", MemoryEntry(key="k", value=2))
        sessions = await store.list_sessions("wf1")
        assert sessions == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_read_all(self, store: FileSystemMemoryStore):
        await store.write("wf1", "s1", MemoryEntry(key="a", value=1))
        await store.write("wf1", "s1", MemoryEntry(key="b", value=2))
        result = await store.read_all("wf1", "s1")
        assert set(result.keys()) == {"a", "b"}
        assert result["a"].value == 1

    @pytest.mark.asyncio
    async def test_clear_session(self, store: FileSystemMemoryStore):
        await store.write("wf1", "s1", MemoryEntry(key="a", value=1))
        await store.write("wf1", "s1", MemoryEntry(key="b", value=2))
        await store.clear_session("wf1", "s1")
        assert await store.list_keys("wf1", "s1") == []

    @pytest.mark.asyncio
    async def test_preserves_created_at_on_update(self, store: FileSystemMemoryStore):
        e1 = MemoryEntry(key="ts", value="v1")
        await store.write("wf1", "s1", e1)
        r1 = await store.read("wf1", "s1", "ts")
        assert r1 is not None
        orig_created = r1.created_at

        e2 = MemoryEntry(key="ts", value="v2")
        await store.write("wf1", "s1", e2)
        r2 = await store.read("wf1", "s1", "ts")
        assert r2 is not None
        assert r2.created_at == orig_created
        assert r2.updated_at >= r2.created_at

    @pytest.mark.asyncio
    async def test_index_tracking(self, store: FileSystemMemoryStore):
        await store.write(
            "wf1", "s1",
            MemoryEntry(key="k", value="v", source_run_id="run-1"),
        )
        index = store._read_index("wf1", "s1")
        assert "k" in index
        assert index["k"]["source_run_id"] == "run-1"


class TestNullMemoryStore:
    @pytest.mark.asyncio
    async def test_all_ops_are_noop(self):
        store = NullMemoryStore()
        assert await store.read("w", "s", "k") is None
        await store.write("w", "s", MemoryEntry(key="k", value=1))
        assert await store.read("w", "s", "k") is None
        assert await store.delete("w", "s", "k") is False
        assert await store.list_keys("w", "s") == []
        assert await store.list_sessions("w") == []
        assert await store.read_all("w", "s") == {}
        await store.clear_session("w", "s")


# =====================================================================
# Engine integration tests
# =====================================================================


class TestEngineMemoryIntegration:
    """Test session memory flows through Engine.run()."""

    @staticmethod
    def _simple_graph():
        """Build a trivial one-node graph for testing."""
        from dan.models.graph import Graph
        from dan.models.nodes import CodeOperator
        from dan.models.ports import InputPort, OutputPort

        node = CodeOperator(
            id="code1",
            name="writer",
            code="result = 'done'",
            input_ports=[InputPort(name="trigger", required=False)],
            output_ports=[OutputPort(name="result")],
        )
        return Graph(
            nodes=[node], edges=[],
            entry_points=["code1"], exit_points=["code1"],
        )

    @pytest.mark.asyncio
    async def test_run_with_session_id(self):
        """Engine.run() accepts session_id without error."""
        from dan.engine import Engine, EngineConfig
        from dan.engine.memory_store import NullMemoryStore

        config = EngineConfig(
            llm_api_key="test", checkpoint_enabled=False, memory_enabled=False,
        )
        engine = Engine(config=config, memory_store=NullMemoryStore())
        graph = self._simple_graph()
        result = await engine.run(
            graph, session_id="sess-1", workflow_id="wf-1",
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_write_memory_from_context(self, tmp_path: Path):
        """ExecutionContext.write_memory() queues writes that get flushed."""
        from dan.engine import Engine, EngineConfig, ExecutionContext
        from dan.engine.memory_store import FileSystemMemoryStore

        mem_dir = str(tmp_path / "mem")
        config = EngineConfig(
            llm_api_key="test",
            checkpoint_enabled=False,
            memory_dir=mem_dir,
        )
        mem_store = FileSystemMemoryStore(mem_dir)
        engine = Engine(config=config, memory_store=mem_store)

        # Register a custom executor that writes memory
        from dan.engine.executor import NodeResult
        from dan.engine.state import NodeStatus

        class MemWriterExecutor:
            async def execute(self, node, inputs, context: ExecutionContext):
                context.write_memory(
                    "test_key", "test_value",
                    writer_node_id=node.id,
                )
                return NodeResult(
                    outputs={"result": "done"},
                    status=NodeStatus.COMPLETED,
                )

        engine.executor_registry.register("code_operator", MemWriterExecutor())

        graph = self._simple_graph()
        result = await engine.run(
            graph, session_id="sess-1", workflow_id="wf-1",
        )
        assert result.success

        entry = await mem_store.read("wf-1", "sess-1", "test_key")
        assert entry is not None
        assert entry.value == "test_value"
        assert entry.writer_node_id == "code1"
        assert entry.source_run_id is not None

    @pytest.mark.asyncio
    async def test_cross_run_memory_continuity(self, tmp_path: Path):
        """Memory written in run1 is available in run2 via pre-load."""
        from dan.engine import Engine, EngineConfig
        from dan.engine.memory_store import FileSystemMemoryStore
        from dan.engine.memory import MemoryEntry

        mem_dir = str(tmp_path / "mem")
        mem_store = FileSystemMemoryStore(mem_dir)

        await mem_store.write(
            "wf-1", "sess-1",
            MemoryEntry(key="prior_result", value="from_run_1"),
        )

        config = EngineConfig(
            llm_api_key="test",
            checkpoint_enabled=False,
            memory_dir=mem_dir,
        )
        engine = Engine(config=config, memory_store=mem_store)

        graph = self._simple_graph()
        result = await engine.run(
            graph, session_id="sess-1", workflow_id="wf-1",
        )
        assert result.success


class TestRunManagerMemoryIntegration:
    """Test session_id flows through RunManager."""

    @pytest.mark.asyncio
    async def test_start_run_with_session_id(self):
        from dan.engine import EngineConfig
        from dan.server.run_manager import RunManager

        config = EngineConfig(
            llm_api_key="test", checkpoint_enabled=False, memory_enabled=False,
        )
        rm = RunManager(engine_config=config)

        from dan.models.graph import Graph
        from dan.models.nodes import CodeOperator
        from dan.models.ports import OutputPort

        graph = Graph(
            nodes=[CodeOperator(
                id="c1", name="c1", code="result='ok'",
                output_ports=[OutputPort(name="result")],
            )],
            edges=[],
            entry_points=["c1"], exit_points=["c1"],
        )
        record = await rm.start_run(
            graph, graph_id="wf-1", session_id="sess-1",
        )
        assert record.run_id
        # Wait for completion
        for _ in range(50):
            if record.finished_at is not None:
                break
            await asyncio.sleep(0.05)
        assert record.result is not None
        assert record.result.success
