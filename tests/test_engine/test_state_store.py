"""Tests for Plan 18-3 Task 1 — StateStore abstraction.

Covers:
  - FileSystemStateStore CRUD: write/read/query/list_keys/delete/clear_scope
  - Scope isolation
  - Typed schema serialization round-trips (LoopIterationState, TeamTurnState, NodeExecutionSummary)
  - Query with prefix filtering
  - Path traversal protection
  - NullStateStore no-ops
  - Concurrent writes via asyncio.gather
  - Atomic write safety (no corrupted files from partial writes)
  - EngineConfig backward compatibility with state_store_enabled=False
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from dan.engine.state_store import (
    FileSystemStateStore,
    LoopIterationState,
    NodeExecutionSummary,
    NullStateStore,
    StateStore,
    TeamTurnState,
    _safe_key,
    _safe_segment,
)


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> str:
    return str(tmp_path / "state")


@pytest.fixture
def store(tmp_state_dir: str) -> FileSystemStateStore:
    return FileSystemStateStore(tmp_state_dir)


# =====================================================================
# Path sanitization
# =====================================================================


class TestPathSanitization:
    def test_safe_segment_strips_traversal(self):
        assert ".." not in _safe_segment("../etc/passwd")
        assert "/" not in _safe_segment("scope/with/slashes")
        assert "\\" not in _safe_segment("scope\\with\\backslash")

    def test_safe_segment_rejects_empty(self):
        with pytest.raises(ValueError):
            _safe_segment("")

    def test_safe_segment_rejects_dot_only(self):
        with pytest.raises(ValueError):
            _safe_segment(".")

    def test_safe_key_strips_traversal(self):
        result = _safe_key("../../secret")
        assert ".." not in result
        assert "/" not in result


# =====================================================================
# FileSystemStateStore CRUD
# =====================================================================


class TestFileSystemStateStoreCRUD:
    @pytest.mark.asyncio
    async def test_write_and_read_dict(self, store: FileSystemStateStore):
        await store.write("run-1", "result", {"score": 0.95})
        val = await store.read("run-1", "result")
        assert val == {"score": 0.95}

    @pytest.mark.asyncio
    async def test_read_nonexistent_returns_none(self, store: FileSystemStateStore):
        val = await store.read("run-1", "nonexistent")
        assert val is None

    @pytest.mark.asyncio
    async def test_write_overwrite(self, store: FileSystemStateStore):
        await store.write("s", "k", {"v": 1})
        await store.write("s", "k", {"v": 2})
        val = await store.read("s", "k")
        assert val == {"v": 2}

    @pytest.mark.asyncio
    async def test_list_keys(self, store: FileSystemStateStore):
        await store.write("s1", "alpha", 1)
        await store.write("s1", "beta", 2)
        await store.write("s1", "gamma", 3)
        keys = await store.list_keys("s1")
        assert keys == ["alpha", "beta", "gamma"]

    @pytest.mark.asyncio
    async def test_list_keys_empty_scope(self, store: FileSystemStateStore):
        keys = await store.list_keys("empty-scope")
        assert keys == []

    @pytest.mark.asyncio
    async def test_delete(self, store: FileSystemStateStore):
        await store.write("s", "k", "val")
        await store.delete("s", "k")
        assert await store.read("s", "k") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, store: FileSystemStateStore):
        await store.delete("s", "nonexistent")

    @pytest.mark.asyncio
    async def test_clear_scope(self, store: FileSystemStateStore):
        await store.write("s", "a", 1)
        await store.write("s", "b", 2)
        await store.clear_scope("s")
        assert await store.list_keys("s") == []
        assert await store.read("s", "a") is None

    @pytest.mark.asyncio
    async def test_clear_nonexistent_scope(self, store: FileSystemStateStore):
        await store.clear_scope("does-not-exist")

    @pytest.mark.asyncio
    async def test_write_scalar_types(self, store: FileSystemStateStore):
        await store.write("s", "int_val", 42)
        await store.write("s", "str_val", "hello")
        await store.write("s", "float_val", 3.14)
        await store.write("s", "bool_val", True)
        await store.write("s", "null_val", None)
        await store.write("s", "list_val", [1, 2, 3])

        assert await store.read("s", "int_val") == 42
        assert await store.read("s", "str_val") == "hello"
        assert await store.read("s", "float_val") == 3.14
        assert await store.read("s", "bool_val") is True
        assert await store.read("s", "null_val") is None
        assert await store.read("s", "list_val") == [1, 2, 3]


# =====================================================================
# Scope isolation
# =====================================================================


class TestScopeIsolation:
    @pytest.mark.asyncio
    async def test_scopes_are_isolated(self, store: FileSystemStateStore):
        await store.write("run-1", "key", {"from": "run-1"})
        await store.write("run-2", "key", {"from": "run-2"})

        val1 = await store.read("run-1", "key")
        val2 = await store.read("run-2", "key")

        assert val1 == {"from": "run-1"}
        assert val2 == {"from": "run-2"}

    @pytest.mark.asyncio
    async def test_clear_scope_doesnt_affect_other(self, store: FileSystemStateStore):
        await store.write("run-1", "k", 1)
        await store.write("run-2", "k", 2)
        await store.clear_scope("run-1")

        assert await store.read("run-1", "k") is None
        assert await store.read("run-2", "k") == 2


# =====================================================================
# Typed schema serialization (Pydantic round-trips)
# =====================================================================


class TestTypedSerialization:
    @pytest.mark.asyncio
    async def test_loop_iteration_state(self, store: FileSystemStateStore):
        state = LoopIterationState(
            iteration=3,
            status="completed",
            started_at=1000.0,
            elapsed_seconds=2.5,
            result_summary="Generated 50 rows",
            output_keys=["artifact_1", "artifact_2"],
            error=None,
            token_usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        await store.write("run-1", "iter_3", state)
        raw = await store.read("run-1", "iter_3")
        assert isinstance(raw, dict)
        restored = LoopIterationState.model_validate(raw)
        assert restored.iteration == 3
        assert restored.status == "completed"
        assert restored.started_at == 1000.0
        assert restored.elapsed_seconds == 2.5
        assert restored.result_summary == "Generated 50 rows"
        assert restored.output_keys == ["artifact_1", "artifact_2"]
        assert restored.error is None
        assert restored.token_usage == {"prompt_tokens": 100, "completion_tokens": 50}

    @pytest.mark.asyncio
    async def test_team_turn_state(self, store: FileSystemStateStore):
        state = TeamTurnState(
            turn_number=5,
            agent_id="researcher",
            status="completed",
            started_at=2000.0,
            elapsed_seconds=1.2,
            message_preview="The analysis shows that...",
            handoff_to="writer",
            token_usage={"prompt_tokens": 200, "completion_tokens": 100},
        )
        await store.write("team-1", "turn_5", state)
        raw = await store.read("team-1", "turn_5")
        restored = TeamTurnState.model_validate(raw)
        assert restored.turn_number == 5
        assert restored.agent_id == "researcher"
        assert restored.handoff_to == "writer"
        assert restored.message_preview == "The analysis shows that..."

    @pytest.mark.asyncio
    async def test_node_execution_summary(self, store: FileSystemStateStore):
        state = NodeExecutionSummary(
            node_id="llm-1",
            node_type="llm_operator",
            status="completed",
            started_at=3000.0,
            elapsed_seconds=0.8,
            input_tokens=150,
            output_tokens=75,
            cost=0.003,
            output_preview="The answer is 42.",
            error=None,
        )
        await store.write("exec", "node_llm-1", state)
        raw = await store.read("exec", "node_llm-1")
        restored = NodeExecutionSummary.model_validate(raw)
        assert restored.node_id == "llm-1"
        assert restored.node_type == "llm_operator"
        assert restored.cost == 0.003
        assert restored.input_tokens == 150
        assert restored.output_tokens == 75

    @pytest.mark.asyncio
    async def test_all_three_schemas(self, store: FileSystemStateStore):
        """Write all three schema types, read back, verify."""
        loop = LoopIterationState(iteration=0, status="completed")
        team = TeamTurnState(turn_number=1, agent_id="a1", status="completed")
        node = NodeExecutionSummary(node_id="n1", node_type="llm_operator", status="failed", error="timeout")

        await store.write("run", "loop_0", loop)
        await store.write("run", "team_1", team)
        await store.write("run", "node_n1", node)

        r_loop = LoopIterationState.model_validate(await store.read("run", "loop_0"))
        r_team = TeamTurnState.model_validate(await store.read("run", "team_1"))
        r_node = NodeExecutionSummary.model_validate(await store.read("run", "node_n1"))

        assert r_loop.iteration == 0
        assert r_team.agent_id == "a1"
        assert r_node.error == "timeout"

    @pytest.mark.asyncio
    async def test_extra_fields_preserved(self, store: FileSystemStateStore):
        """ConfigDict(extra='allow') should preserve unknown fields on round-trip."""
        state = LoopIterationState(iteration=1, status="completed", custom_field="extra_value")
        await store.write("s", "k", state)
        raw = await store.read("s", "k")
        restored = LoopIterationState.model_validate(raw)
        assert restored.custom_field == "extra_value"  # type: ignore[attr-defined]


# =====================================================================
# Query with prefix
# =====================================================================


class TestQueryPrefix:
    @pytest.mark.asyncio
    async def test_query_with_prefix(self, store: FileSystemStateStore):
        await store.write("scope", "iter_1", {"i": 1})
        await store.write("scope", "iter_2", {"i": 2})
        await store.write("scope", "iter_3", {"i": 3})
        await store.write("scope", "meta_info", {"type": "meta"})

        result = await store.query("scope", prefix="iter_")
        assert len(result) == 3
        assert all(k.startswith("iter_") for k in result)
        assert "meta_info" not in result

    @pytest.mark.asyncio
    async def test_query_no_prefix_returns_all(self, store: FileSystemStateStore):
        await store.write("s", "a", 1)
        await store.write("s", "b", 2)
        result = await store.query("s")
        assert len(result) == 2
        assert "a" in result and "b" in result

    @pytest.mark.asyncio
    async def test_query_empty_scope(self, store: FileSystemStateStore):
        result = await store.query("nonexistent")
        assert result == {}


# =====================================================================
# Path traversal protection
# =====================================================================


class TestPathTraversal:
    @pytest.mark.asyncio
    async def test_scope_traversal_sanitized(self, store: FileSystemStateStore):
        await store.write("../escape", "key", "bad")
        val = await store.read("../escape", "key")
        assert val == "bad"
        base = Path(store._base)
        assert not any(p.name == ".." for p in base.rglob("*"))

    @pytest.mark.asyncio
    async def test_key_traversal_sanitized(self, store: FileSystemStateStore):
        await store.write("safe", "../../etc/passwd", "bad")
        val = await store.read("safe", "../../etc/passwd")
        assert val == "bad"
        scope_dir = store._scope_dir("safe")
        for f in scope_dir.glob("*.json"):
            assert ".." not in f.name


# =====================================================================
# NullStateStore
# =====================================================================


class TestNullStateStore:
    @pytest.mark.asyncio
    async def test_read_returns_none(self):
        null = NullStateStore()
        assert await null.read("s", "k") is None

    @pytest.mark.asyncio
    async def test_write_is_noop(self):
        null = NullStateStore()
        await null.write("s", "k", {"data": True})
        assert await null.read("s", "k") is None

    @pytest.mark.asyncio
    async def test_query_returns_empty(self):
        null = NullStateStore()
        assert await null.query("s") == {}

    @pytest.mark.asyncio
    async def test_list_keys_returns_empty(self):
        null = NullStateStore()
        assert await null.list_keys("s") == []

    @pytest.mark.asyncio
    async def test_delete_is_noop(self):
        null = NullStateStore()
        await null.delete("s", "k")

    @pytest.mark.asyncio
    async def test_clear_scope_is_noop(self):
        null = NullStateStore()
        await null.clear_scope("s")

    @pytest.mark.asyncio
    async def test_all_ops_no_exceptions(self):
        """Ensure every method can be called without error."""
        null = NullStateStore()
        await null.write("a", "b", "c")
        assert await null.read("a", "b") is None
        assert await null.query("a", "x") == {}
        assert await null.list_keys("a") == []
        await null.delete("a", "b")
        await null.clear_scope("a")


# =====================================================================
# Concurrent writes
# =====================================================================


class TestConcurrentWrites:
    @pytest.mark.asyncio
    async def test_concurrent_writes(self, store: FileSystemStateStore):
        """Write 10 entries concurrently, verify all are persisted."""
        async def write_entry(i: int):
            await store.write("concurrent", f"entry_{i}", {"index": i})

        await asyncio.gather(*(write_entry(i) for i in range(10)))

        keys = await store.list_keys("concurrent")
        assert len(keys) == 10
        for i in range(10):
            val = await store.read("concurrent", f"entry_{i}")
            assert val == {"index": i}


# =====================================================================
# Atomic write safety
# =====================================================================


class TestAtomicWriteSafety:
    def test_no_tmp_files_after_successful_write(self, store: FileSystemStateStore):
        """After a successful write, no .tmp files should remain."""
        asyncio.run(
            store.write("scope", "key", {"data": True})
        )
        scope_dir = store._scope_dir("scope")
        tmp_files = list(scope_dir.glob("*.tmp"))
        assert tmp_files == []

    def test_valid_json_on_disk(self, store: FileSystemStateStore):
        """Written files should contain valid JSON."""
        asyncio.run(
            store.write("scope", "key", {"valid": True})
        )
        path = store._entry_path("scope", "key")
        data = json.loads(path.read_text("utf-8"))
        assert data == {"valid": True}


# =====================================================================
# Pydantic round-trip
# =====================================================================


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_pydantic_roundtrip_loop(self, store: FileSystemStateStore):
        original = LoopIterationState(
            iteration=7,
            status="completed",
            started_at=time.time(),
            elapsed_seconds=3.14,
            result_summary="All good",
            output_keys=["k1"],
            token_usage={"prompt_tokens": 10},
        )
        await store.write("rt", "loop", original)
        raw = await store.read("rt", "loop")
        restored = LoopIterationState.model_validate(raw)
        assert original.model_dump() == restored.model_dump()

    @pytest.mark.asyncio
    async def test_pydantic_roundtrip_team(self, store: FileSystemStateStore):
        original = TeamTurnState(
            turn_number=2,
            agent_id="agent_x",
            status="completed",
            message_preview="Hello world",
        )
        await store.write("rt", "team", original)
        raw = await store.read("rt", "team")
        restored = TeamTurnState.model_validate(raw)
        assert original.model_dump() == restored.model_dump()

    @pytest.mark.asyncio
    async def test_pydantic_roundtrip_node(self, store: FileSystemStateStore):
        original = NodeExecutionSummary(
            node_id="n-42",
            node_type="code_operator",
            status="completed",
            cost=0.001,
        )
        await store.write("rt", "node", original)
        raw = await store.read("rt", "node")
        restored = NodeExecutionSummary.model_validate(raw)
        assert original.model_dump() == restored.model_dump()


# =====================================================================
# Protocol conformance
# =====================================================================


class TestProtocol:
    def test_filesystem_is_state_store(self):
        assert isinstance(FileSystemStateStore(), StateStore)

    def test_null_is_state_store(self):
        assert isinstance(NullStateStore(), StateStore)


# =====================================================================
# EngineConfig backward compat
# =====================================================================


class TestEngineConfigCompat:
    def test_state_store_disabled_by_default(self):
        from dan.engine.executor import EngineConfig

        config = EngineConfig()
        assert config.state_store_enabled is False
        assert config.state_store_dir is None

    def test_state_store_can_be_enabled(self):
        from dan.engine.executor import EngineConfig

        config = EngineConfig(state_store_enabled=True, state_store_dir="/tmp/state")
        assert config.state_store_enabled is True
        assert config.state_store_dir == "/tmp/state"

    def test_disabled_means_null_store(self):
        """When state_store_enabled=False, NullStateStore should be the right choice."""
        from dan.engine.executor import EngineConfig

        config = EngineConfig(state_store_enabled=False)
        if config.state_store_enabled:
            store = FileSystemStateStore(config.state_store_dir or "./state")
        else:
            store = NullStateStore()
        assert isinstance(store, NullStateStore)


# =====================================================================
# Event types exist
# =====================================================================


class TestEventTypes:
    def test_new_event_types_exist(self):
        from dan.engine.events import EventType

        assert EventType.STATE_EXTERNALIZED.value == "state_externalized"
        assert EventType.LOOP_COMPACTION_APPLIED.value == "loop_compaction_applied"
        assert EventType.BUDGET_ADVISORY.value == "budget_advisory"


# =====================================================================
# Exports from __init__
# =====================================================================


class TestExports:
    def test_exports_from_engine_init(self):
        from dan.engine import (
            FileSystemStateStore,
            LoopIterationState,
            NodeExecutionSummary,
            NullStateStore,
            StateStore,
            TeamTurnState,
        )
        assert FileSystemStateStore is not None
        assert NullStateStore is not None
        assert StateStore is not None
        assert LoopIterationState is not None
        assert TeamTurnState is not None
        assert NodeExecutionSummary is not None
