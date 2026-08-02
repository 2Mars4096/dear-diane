"""Tests for Phase 9D — Self-Evolving Orchestrator (Tiers 1–3).

Covers:
  - Tier 1: ErrorRecord, ErrorCategory, extract_error_records, ErrorMemoryIndex,
    ErrorContextProvider, PrincipleStore (17-1)
  - Tier 2: ReflectionNode, ReflectionExecutor (17-2)
  - Tier 3: RuleGenerator, GeneratedRule, RuleLifecycleManager (17-3)
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest

from dan.engine.error_memory import (
    CausalPrinciple,
    ErrorCategory,
    ErrorContextProvider,
    ErrorMemoryIndex,
    ErrorRecord,
    PrincipleStore,
    extract_error_records,
)
from dan.engine.memory_store import FileSystemMemoryStore
from dan.engine.rule_generator import (
    GeneratedRule,
    RuleGenerator,
    RuleLifecycleManager,
)
from dan.executors.reflection import ReflectionExecutor
from dan.models.nodes import ReflectionNode
from dan.rag.stores.memory import MemoryVectorStore


# =====================================================================
# Mock helpers
# =====================================================================


class MockEmbeddingProvider:
    """Returns deterministic fixed-dimension vectors for testing."""

    async def embed(self, texts, model=""):
        vectors = []
        for t in texts:
            h = hashlib.md5(t.encode()).hexdigest()
            vec = [int(h[i : i + 2], 16) / 255.0 for i in range(0, 32, 2)]
            vectors.append(vec)

        class Result:
            pass

        r = Result()
        r.vectors = vectors
        r.dimensions = len(vectors[0]) if vectors else 0
        return r


class MockProvider:
    """Fake LLM provider returning canned text."""

    def __init__(self, response_text: str):
        self._text = response_text

    async def complete(self, messages, model="", temperature=0.7, max_tokens=None):
        class Result:
            pass

        r = Result()
        r.text = self._text
        r.usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        return r


class MockProviderRegistry:
    def __init__(self, provider):
        self._provider = provider

    def resolve(self, model):
        return self._provider


def make_test_principle(**kwargs) -> CausalPrinciple:
    defaults: dict[str, Any] = {
        "condition": "test condition",
        "action": "avoid this mistake",
        "reason": "test reason",
        "confidence": 0.8,
        "tags": ["llm_operator"],
        "workflow_id": "test-wf",
    }
    defaults.update(kwargs)
    return CausalPrinciple(**defaults)


def _make_error(node_id: str = "n1", msg: str = "boom", **kw) -> ErrorRecord:
    defaults: dict[str, Any] = {
        "run_id": "r1",
        "workflow_id": "wf1",
        "node_id": node_id,
        "error_message": msg,
    }
    defaults.update(kw)
    return ErrorRecord(**defaults)


def _make_execution_context(provider_registry=None, run_id="test-run"):
    """Build a minimal ExecutionContext for testing ReflectionExecutor."""
    from dan.engine.context_runtime import (
        ArtifactStore,
        LocalStateManager,
        SharedContextStore,
    )
    from dan.engine.executor import EngineConfig, ExecutionContext
    from dan.engine.state import ExecutionState
    from dan.models.graph import Graph

    graph = Graph(id="test-graph", name="test", nodes=[], edges=[])
    state = ExecutionState(graph)
    return ExecutionContext(
        state=state,
        config=EngineConfig(),
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        provider_registry=provider_registry,
        run_id=run_id,
    )


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def embedding_provider():
    return MockEmbeddingProvider()


@pytest.fixture
def error_index(embedding_provider):
    store = MemoryVectorStore()
    return ErrorMemoryIndex(embedding_provider=embedding_provider, store=store)


@pytest.fixture
def principle_store(tmp_path):
    return PrincipleStore(FileSystemMemoryStore(str(tmp_path / "mem")))


@pytest.fixture
def rule_manager(tmp_path):
    return RuleLifecycleManager(
        base_dir=str(tmp_path / "rules"),
        default_ttl_days=30,
        max_rules_per_workflow=5,
    )


# =====================================================================
# Tier 1 — ErrorRecord model
# =====================================================================


class TestErrorRecord:
    def test_required_fields(self):
        rec = ErrorRecord(
            run_id="r1", workflow_id="wf1", node_id="n1", error_message="boom"
        )
        assert rec.run_id == "r1"
        assert rec.workflow_id == "wf1"
        assert rec.node_id == "n1"
        assert rec.error_message == "boom"

    def test_defaults(self):
        rec = ErrorRecord(
            run_id="r", workflow_id="w", node_id="n", error_message="e"
        )
        assert rec.node_type == ""
        assert rec.error_category == ErrorCategory.UNKNOWN
        assert rec.input_snapshot is None
        assert rec.upstream_node_ids == []
        assert rec.timestamp > 0
        assert rec.severity == "error"

    def test_custom_severity(self):
        rec = ErrorRecord(
            run_id="r",
            workflow_id="w",
            node_id="n",
            error_message="e",
            severity="fatal",
        )
        assert rec.severity == "fatal"


# =====================================================================
# Tier 1 — ErrorCategory enum
# =====================================================================


class TestErrorCategory:
    def test_all_values_accessible(self):
        expected = {
            "llm_failure",
            "tool_failure",
            "validation_failure",
            "timeout",
            "schema_mismatch",
            "condition_failure",
            "unknown",
        }
        assert {e.value for e in ErrorCategory} == expected

    def test_string_enum_identity(self):
        assert ErrorCategory.TOOL_FAILURE == "tool_failure"


# =====================================================================
# Tier 1 — extract_error_records
# =====================================================================


class TestExtractErrorRecords:
    def test_errors_dict_only(self):
        run_record = {
            "run_id": "r1",
            "graph_id": "wf1",
            "result": {"errors": {"node_a": "something broke"}},
            "node_statuses": {
                "node_a": {
                    "node_type": "tool_operator",
                    "inputs": {"x": 1},
                    "upstream_node_ids": ["node_0"],
                }
            },
        }
        records = extract_error_records(run_record)
        assert len(records) == 1
        rec = records[0]
        assert rec.node_id == "node_a"
        assert rec.error_category == ErrorCategory.TOOL_FAILURE
        assert rec.input_snapshot == {"x": 1}
        assert rec.upstream_node_ids == ["node_0"]

    def test_events_only(self):
        run_record = {"run_id": "r1", "graph_id": "wf1"}
        events = [
            {
                "event_type": "node_failed",
                "node_id": "n1",
                "data": {"error": "timeout reached", "node_type": "llm_operator"},
            },
        ]
        records = extract_error_records(run_record, events=events)
        assert len(records) == 1
        assert records[0].error_category == ErrorCategory.LLM_FAILURE
        assert records[0].error_message == "timeout reached"

    def test_both_sources_dedup_keeps_richer(self):
        run_record = {
            "run_id": "r1",
            "graph_id": "wf1",
            "result": {"errors": {"n1": "short"}},
            "node_statuses": {},
        }
        events = [
            {
                "event_type": "node_failed",
                "node_id": "n1",
                "data": {
                    "error": "much longer and more descriptive error message",
                    "node_type": "llm_operator",
                },
            },
        ]
        records = extract_error_records(run_record, events=events)
        assert len(records) == 1
        assert records[0].error_message == "much longer and more descriptive error message"
        assert records[0].node_type == "llm_operator"

    def test_dedup_keeps_errors_dict_when_richer(self):
        run_record = {
            "run_id": "r1",
            "graph_id": "wf1",
            "result": {"errors": {"n1": "very descriptive error from result dict"}},
            "node_statuses": {"n1": {"node_type": "tool_operator"}},
        }
        events = [
            {
                "event_type": "node_failed",
                "node_id": "n1",
                "data": {"error": "short"},
            },
        ]
        records = extract_error_records(run_record, events=events)
        assert len(records) == 1
        assert records[0].error_category == ErrorCategory.TOOL_FAILURE

    def test_classification_tool_operator(self):
        rr = {
            "run_id": "r", "graph_id": "wf",
            "result": {"errors": {"n": "err"}},
            "node_statuses": {"n": {"node_type": "tool_operator"}},
        }
        assert extract_error_records(rr)[0].error_category == ErrorCategory.TOOL_FAILURE

    def test_classification_llm_operator(self):
        rr = {
            "run_id": "r", "graph_id": "wf",
            "result": {"errors": {"n": "err"}},
            "node_statuses": {"n": {"node_type": "llm_operator"}},
        }
        assert extract_error_records(rr)[0].error_category == ErrorCategory.LLM_FAILURE

    def test_classification_gate(self):
        rr = {
            "run_id": "r", "graph_id": "wf",
            "result": {"errors": {"n": "err"}},
            "node_statuses": {"n": {"node_type": "gate"}},
        }
        assert extract_error_records(rr)[0].error_category == ErrorCategory.CONDITION_FAILURE

    def test_classification_validator(self):
        rr = {
            "run_id": "r", "graph_id": "wf",
            "result": {"errors": {"n": "err"}},
            "node_statuses": {"n": {"node_type": "validator"}},
        }
        assert extract_error_records(rr)[0].error_category == ErrorCategory.VALIDATION_FAILURE

    def test_classification_timeout_in_message(self):
        rr = {
            "run_id": "r", "graph_id": "wf",
            "result": {"errors": {"n": "request timeout after 30s"}},
            "node_statuses": {"n": {"node_type": "custom"}},
        }
        assert extract_error_records(rr)[0].error_category == ErrorCategory.TIMEOUT

    def test_classification_schema_in_message(self):
        rr = {
            "run_id": "r", "graph_id": "wf",
            "result": {"errors": {"n": "invalid json response"}},
            "node_statuses": {"n": {"node_type": "custom"}},
        }
        assert extract_error_records(rr)[0].error_category == ErrorCategory.SCHEMA_MISMATCH

    def test_input_snapshot_truncation(self):
        big_input = {"data": "x" * 5000}
        rr = {
            "run_id": "r", "graph_id": "wf",
            "result": {"errors": {"n": "err"}},
            "node_statuses": {"n": {"inputs": big_input}},
        }
        records = extract_error_records(rr, max_snapshot_chars=100)
        snap = records[0].input_snapshot
        assert snap is not None
        assert snap["_truncated"] is True
        assert len(snap["_preview"]) <= 100

    def test_small_snapshot_not_truncated(self):
        rr = {
            "run_id": "r", "graph_id": "wf",
            "result": {"errors": {"n": "err"}},
            "node_statuses": {"n": {"inputs": {"a": 1}}},
        }
        records = extract_error_records(rr)
        assert records[0].input_snapshot == {"a": 1}

    def test_empty_inputs(self):
        assert extract_error_records({}) == []
        assert extract_error_records({"result": {}}, events=[]) == []

    def test_ignores_non_failed_events(self):
        events = [
            {"event_type": "node_started", "node_id": "n1", "data": {}},
            {"event_type": "node_completed", "node_id": "n1", "data": {}},
        ]
        records = extract_error_records({"run_id": "r"}, events=events)
        assert records == []


# =====================================================================
# Tier 1 — ErrorMemoryIndex
# =====================================================================


class TestErrorMemoryIndex:
    @pytest.mark.asyncio
    async def test_index_errors(self, error_index):
        count = await error_index.index_errors("wf1", [_make_error()])
        assert count == 1

    @pytest.mark.asyncio
    async def test_index_empty_list(self, error_index):
        count = await error_index.index_errors("wf1", [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_query_similar_returns_results(self, error_index):
        await error_index.index_errors(
            "wf1",
            [_make_error("n1", "connection timeout"), _make_error("n2", "parse failed")],
        )
        results = await error_index.query_similar("wf1", "timeout issue")
        assert len(results) > 0
        assert "metadata" in results[0]
        assert "score" in results[0]

    @pytest.mark.asyncio
    async def test_query_nonexistent_collection(self, error_index):
        results = await error_index.query_similar("nonexistent", "anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_clear_removes_collection(self, error_index):
        await error_index.index_errors("wf1", [_make_error()])
        await error_index.clear("wf1")
        stats = await error_index.stats("wf1")
        assert stats["exists"] is False
        assert stats["count"] == 0

    @pytest.mark.asyncio
    async def test_clear_nonexistent_is_noop(self, error_index):
        await error_index.clear("ghost")

    @pytest.mark.asyncio
    async def test_stats_before_and_after_index(self, error_index):
        stats = await error_index.stats("wf1")
        assert stats["exists"] is False
        assert stats["count"] == 0

        await error_index.index_errors("wf1", [_make_error()])
        stats = await error_index.stats("wf1")
        assert stats["exists"] is True
        assert stats["count"] == 1
        assert stats["collection"] == "dan_errors_wf1"

    @pytest.mark.asyncio
    async def test_idempotent_reindex(self, error_index):
        err = _make_error("n1", "same error")
        await error_index.index_errors("wf1", [err])
        await error_index.index_errors("wf1", [err])
        stats = await error_index.stats("wf1")
        assert stats["count"] == 1


# =====================================================================
# Tier 1 — ErrorContextProvider
# =====================================================================


class TestErrorContextProvider:
    @pytest.mark.asyncio
    async def test_empty_index_returns_empty_string(self, error_index):
        provider = ErrorContextProvider(error_index)

        class FakeNode:
            id = "n1"
            node_type = "llm_operator"

        text = await provider.get_context(FakeNode(), "do stuff", {}, "wf1")
        assert text == ""

    @pytest.mark.asyncio
    async def test_includes_past_failures_section(self, error_index):
        await error_index.index_errors("wf1", [_make_error("n1", "timeout error")])
        provider = ErrorContextProvider(error_index, top_k=3)

        class FakeNode:
            id = "n1"
            node_type = "llm_operator"

        text = await provider.get_context(
            FakeNode(), "run query", {"input_key": "val"}, "wf1"
        )
        assert "Past Failures" in text

    @pytest.mark.asyncio
    async def test_includes_learned_principles(self, error_index, principle_store):
        await error_index.index_errors("wf1", [_make_error("n1", "oops")])
        p = make_test_principle(condition="data too large", action="use batching")
        await principle_store.store_principles("wf1", [p])

        provider = ErrorContextProvider(
            error_index, principle_store=principle_store, top_k=3
        )

        class FakeNode:
            id = "n1"
            node_type = "llm_operator"

        text = await provider.get_context(FakeNode(), "process data", {}, "wf1")
        assert "Learned Principles" in text
        assert "use batching" in text


# =====================================================================
# Tier 1 — PrincipleStore
# =====================================================================


class TestPrincipleStore:
    @pytest.mark.asyncio
    async def test_store_and_load(self, principle_store):
        p = make_test_principle()
        await principle_store.store_principles("wf1", [p])
        loaded = await principle_store.load_principles("wf1")
        assert len(loaded) == 1
        assert loaded[0].condition == p.condition
        assert loaded[0].action == p.action

    @pytest.mark.asyncio
    async def test_load_empty(self, principle_store):
        loaded = await principle_store.load_principles("wf1")
        assert loaded == []

    @pytest.mark.asyncio
    async def test_filter_by_min_confidence(self, principle_store):
        low = make_test_principle(confidence=0.1)
        high = make_test_principle(confidence=0.9)
        await principle_store.store_principles("wf1", [low, high])
        loaded = await principle_store.load_principles("wf1", min_confidence=0.5)
        assert len(loaded) == 1
        assert loaded[0].confidence >= 0.5

    @pytest.mark.asyncio
    async def test_filter_by_tags(self, principle_store):
        p1 = make_test_principle(tags=["tool"])
        p2 = make_test_principle(tags=["llm"])
        await principle_store.store_principles("wf1", [p1, p2])
        loaded = await principle_store.load_principles("wf1", tags=["llm"])
        assert len(loaded) == 1
        assert "llm" in loaded[0].tags

    @pytest.mark.asyncio
    async def test_delete_principle(self, principle_store):
        p = make_test_principle()
        await principle_store.store_principles("wf1", [p])
        deleted = await principle_store.delete_principle("wf1", p.id)
        assert deleted is True
        assert await principle_store.load_principles("wf1") == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, principle_store):
        deleted = await principle_store.delete_principle("wf1", "no-such-id")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_expire_old_principles(self, principle_store):
        old = make_test_principle()
        old.created_at = time.time() - 100 * 86400
        await principle_store.store_principles("wf1", [old])
        removed = await principle_store.expire_principles("wf1", max_age_days=30)
        assert removed == 1
        assert await principle_store.load_principles("wf1") == []

    @pytest.mark.asyncio
    async def test_expire_keeps_recent(self, principle_store):
        recent = make_test_principle()
        await principle_store.store_principles("wf1", [recent])
        removed = await principle_store.expire_principles("wf1", max_age_days=30)
        assert removed == 0
        assert len(await principle_store.load_principles("wf1")) == 1


# =====================================================================
# Tier 2 — ReflectionNode model
# =====================================================================


class TestReflectionNode:
    def test_fields_and_defaults(self):
        node = ReflectionNode(id="r1", name="reflect")
        assert node.node_type == "reflection"
        assert node.source == "last_run"
        assert node.max_principles == 10
        assert node.min_confidence == 0.3
        assert node.dedup_strategy == "embedding_similarity"
        assert node.output_format == "principles"
        assert node.reflection_prompt == ""
        assert node.reflection_model is None

    def test_custom_fields(self):
        node = ReflectionNode(
            id="r1",
            name="custom",
            source="last_n_runs",
            source_config={"n": 3},
            max_principles=5,
            min_confidence=0.5,
            dedup_strategy="exact_key",
            reflection_model="gpt-4",
        )
        assert node.source == "last_n_runs"
        assert node.source_config == {"n": 3}
        assert node.reflection_model == "gpt-4"
        assert node.max_principles == 5


# =====================================================================
# Tier 2 — ReflectionExecutor
# =====================================================================


class TestReflectionExecutor:
    @pytest.mark.asyncio
    async def test_missing_provider_registry_fails(self):
        executor = ReflectionExecutor()
        node = ReflectionNode(id="r1", name="reflect")
        ctx = _make_execution_context(provider_registry=None)
        result = await executor.execute(node, {}, ctx)
        from dan.engine.state import NodeStatus

        assert result.status == NodeStatus.FAILED
        assert "provider_registry" in result.error

    @pytest.mark.asyncio
    async def test_empty_error_data_returns_completed(self):
        provider = MockProvider("[]")
        registry = MockProviderRegistry(provider)
        executor = ReflectionExecutor()
        node = ReflectionNode(id="r1", name="reflect")
        ctx = _make_execution_context(provider_registry=registry)
        result = await executor.execute(node, {}, ctx)
        from dan.engine.state import NodeStatus

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["principles"] == []
        assert result.outputs["principle_count"] == 0

    @pytest.mark.asyncio
    async def test_source_last_run_extracts_principles(self):
        llm_output = json.dumps(
            [
                {
                    "condition": "data is large",
                    "action": "use batching",
                    "reason": "avoids OOM",
                    "confidence": 0.9,
                    "tags": ["data"],
                }
            ]
        )
        provider = MockProvider(llm_output)
        registry = MockProviderRegistry(provider)
        executor = ReflectionExecutor()
        node = ReflectionNode(id="r1", name="reflect", source="last_run")
        ctx = _make_execution_context(provider_registry=registry)
        inputs = {
            "run_id": "run-1",
            "run_errors": [{"node_id": "n1", "error": "OOM killed"}],
        }
        result = await executor.execute(node, inputs, ctx)
        from dan.engine.state import NodeStatus

        assert result.status == NodeStatus.COMPLETED
        assert result.outputs["principle_count"] == 1
        p = result.outputs["principles"][0]
        assert p["condition"] == "data is large"
        assert p["action"] == "use batching"

    def test_gather_last_run_includes_runtime_repair_context(self):
        executor = ReflectionExecutor()
        text = executor._gather_last_run({
            "run_id": "run-1",
            "run_errors": [{"node_id": "n1", "error": "tool failed"}],
            "runtime_repair_lineage": {
                "n1": {
                    "attempts": [{"kind": "parameter_fix"}],
                    "last_summary": {
                        "cause": "config_failure",
                        "repair_attempted": "parameter_fix",
                        "next_step": "Retry with overlay",
                    },
                }
            },
            "runtime_repair_summaries": {
                "n1": {
                    "cause": "config_failure",
                    "repair_attempted": "parameter_fix",
                }
            },
        })

        assert "Runtime Repair Lineage" in text
        assert "parameter_fix" in text
        assert "config_failure" in text

    @pytest.mark.asyncio
    async def test_source_last_n_runs(self):
        llm_output = json.dumps(
            [
                {
                    "condition": "repeated timeout",
                    "action": "increase timeout",
                    "reason": "default too low",
                    "confidence": 0.7,
                    "tags": [],
                }
            ]
        )
        provider = MockProvider(llm_output)
        registry = MockProviderRegistry(provider)
        executor = ReflectionExecutor()
        node = ReflectionNode(
            id="r1",
            name="reflect",
            source="last_n_runs",
            source_config={"n": 2},
        )
        ctx = _make_execution_context(provider_registry=registry)
        inputs = {
            "runs": [
                {"run_id": "r1", "status": "failed", "errors": [{"node_id": "n", "error": "timeout"}]},
                {"run_id": "r2", "status": "failed", "errors": [{"node_id": "n", "error": "timeout"}]},
            ],
        }
        result = await executor.execute(node, inputs, ctx)
        assert result.outputs["principle_count"] >= 1

    @pytest.mark.asyncio
    async def test_source_error_index(self):
        llm_output = json.dumps(
            [
                {
                    "condition": "api flaky",
                    "action": "add retry",
                    "reason": "transient",
                    "confidence": 0.6,
                    "tags": [],
                }
            ]
        )
        provider = MockProvider(llm_output)
        registry = MockProviderRegistry(provider)
        executor = ReflectionExecutor()
        node = ReflectionNode(
            id="r1",
            name="reflect",
            source="error_index",
            source_config={"query": "api failures"},
        )
        ctx = _make_execution_context(provider_registry=registry)
        inputs = {
            "error_index_results": [
                {"node_id": "n1", "score": 0.9, "error": "connection refused"},
            ],
        }
        result = await executor.execute(node, inputs, ctx)
        from dan.engine.state import NodeStatus

        assert result.status == NodeStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_json_parsing_with_fences(self):
        raw = '```json\n[{"condition":"c","action":"a","reason":"r","confidence":0.8,"tags":[]}]\n```'
        provider = MockProvider(raw)
        registry = MockProviderRegistry(provider)
        executor = ReflectionExecutor()
        node = ReflectionNode(id="r1", name="reflect")
        ctx = _make_execution_context(provider_registry=registry)
        inputs = {"run_errors": [{"node_id": "n1", "error": "x"}]}
        result = await executor.execute(node, inputs, ctx)
        assert result.outputs["principle_count"] == 1
        assert result.outputs["principles"][0]["condition"] == "c"

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty_principles(self):
        provider = MockProvider("this is not json at all")
        registry = MockProviderRegistry(provider)
        executor = ReflectionExecutor()
        node = ReflectionNode(id="r1", name="reflect")
        ctx = _make_execution_context(provider_registry=registry)
        inputs = {"run_errors": [{"node_id": "n1", "error": "x"}]}
        result = await executor.execute(node, inputs, ctx)
        assert result.outputs["principle_count"] == 0
        assert result.outputs["principles"] == []

    @pytest.mark.asyncio
    async def test_confidence_filtering(self):
        llm_output = json.dumps(
            [
                {"condition": "c1", "action": "a1", "reason": "r", "confidence": 0.1, "tags": []},
                {"condition": "c2", "action": "a2", "reason": "r", "confidence": 0.9, "tags": []},
            ]
        )
        provider = MockProvider(llm_output)
        registry = MockProviderRegistry(provider)
        executor = ReflectionExecutor()
        node = ReflectionNode(id="r1", name="reflect", min_confidence=0.5)
        ctx = _make_execution_context(provider_registry=registry)
        inputs = {"run_errors": [{"node_id": "n1", "error": "x"}]}
        result = await executor.execute(node, inputs, ctx)
        assert result.outputs["principle_count"] == 1
        assert result.outputs["principles"][0]["condition"] == "c2"

    @pytest.mark.asyncio
    async def test_deduplication_exact_key(self):
        llm_output = json.dumps(
            [
                {
                    "condition": "same condition",
                    "action": "same action",
                    "reason": "r",
                    "confidence": 0.8,
                    "tags": [],
                }
            ]
        )
        provider = MockProvider(llm_output)
        registry = MockProviderRegistry(provider)
        executor = ReflectionExecutor()
        node = ReflectionNode(id="r1", name="reflect", dedup_strategy="exact_key")
        ctx = _make_execution_context(provider_registry=registry)
        inputs = {
            "run_errors": [{"node_id": "n1", "error": "x"}],
            "existing_principles": [
                {"condition": "same condition", "action": "same action", "confidence": 0.6},
            ],
        }
        result = await executor.execute(node, inputs, ctx)
        assert result.outputs["principle_count"] == 0


# =====================================================================
# Tier 3 — RuleGenerator
# =====================================================================


class TestRuleGenerator:
    def test_negation_becomes_guardrail(self):
        gen = RuleGenerator()
        p = make_test_principle(action="avoid using raw SQL queries")
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert edge.hyperedge_type == "guardrail"
        assert edge.hook == "pre_prompt"

    def test_do_not_negation(self):
        gen = RuleGenerator()
        p = make_test_principle(action="do not call the API without auth")
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert edge.hyperedge_type == "guardrail"

    def test_preference_becomes_skill(self):
        gen = RuleGenerator()
        p = make_test_principle(action="prefer batched API calls over single requests")
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert edge.hyperedge_type == "skill"
        assert edge.hook == "pre_prompt"

    def test_interception_becomes_override(self):
        gen = RuleGenerator()
        p = make_test_principle(action="block requests exceeding 100MB payload")
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert edge.hyperedge_type == "override"
        assert edge.hook == "tool_call"

    def test_default_becomes_skill(self):
        gen = RuleGenerator()
        p = make_test_principle(action="handle edge case carefully")
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert edge.hyperedge_type == "skill"

    def test_content_includes_principle_data(self):
        gen = RuleGenerator()
        p = make_test_principle(
            condition="large input data",
            action="use streaming to process",
            reason="prevents OOM crashes",
        )
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert "large input data" in edge.content
        assert "use streaming to process" in edge.content
        assert "prevents OOM crashes" in edge.content

    def test_attach_to_tags_from_principle_tags(self):
        gen = RuleGenerator()
        p = make_test_principle(tags=["data_processing", "etl"])
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert "data_processing" in edge.attach_to_tags
        assert "etl" in edge.attach_to_tags

    def test_attach_to_node_ids_when_no_tags(self):
        gen = RuleGenerator()
        p = make_test_principle(tags=[], source_node_ids=["node_a", "node_b"])
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert "node_a" in edge.attach_to
        assert "node_b" in edge.attach_to

    def test_fallback_attach_to_type_when_no_tags_or_ids(self):
        gen = RuleGenerator()
        p = make_test_principle(tags=[], source_node_ids=[])
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert "llm_operator" in edge.attach_to_type

    def test_edge_id_derives_from_principle(self):
        gen = RuleGenerator()
        p = make_test_principle()
        edge = gen.generate_rule(p, "wf1")
        assert edge is not None
        assert edge.id.startswith("gen-")
        assert p.id[:8] in edge.id


# =====================================================================
# Tier 3 — GeneratedRule model
# =====================================================================


class TestGeneratedRule:
    def _make_hyperedge(self):
        from dan.models.hyperedges import Hyperedge

        return Hyperedge(
            id="e1",
            name="test",
            hyperedge_type="skill",
            hook="pre_prompt",
            content="content",
            attach_to_tags=["t"],
        )

    def test_effectiveness_score_zero_applies(self):
        rule = GeneratedRule(
            hyperedge=self._make_hyperedge(),
            source_principle_id="p1",
            workflow_id="wf1",
        )
        assert rule.effectiveness_score == 0.0

    def test_effectiveness_score_computed(self):
        rule = GeneratedRule(
            hyperedge=self._make_hyperedge(),
            source_principle_id="p1",
            workflow_id="wf1",
            apply_count=10,
            prevented_count=7,
        )
        assert rule.effectiveness_score == pytest.approx(0.7)

    def test_effectiveness_score_perfect(self):
        rule = GeneratedRule(
            hyperedge=self._make_hyperedge(),
            source_principle_id="p1",
            workflow_id="wf1",
            apply_count=5,
            prevented_count=5,
            recurred_count=0,
        )
        assert rule.effectiveness_score == pytest.approx(1.0)

    def test_default_status(self):
        rule = GeneratedRule(
            hyperedge=self._make_hyperedge(),
            source_principle_id="p1",
            workflow_id="wf1",
        )
        assert rule.status == "active"


# =====================================================================
# Tier 3 — RuleLifecycleManager
# =====================================================================


class TestRuleLifecycleManager:
    def test_create_and_list(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None
        assert rule.status == "active"
        assert rule.expires_at is not None

        rules = rule_manager.list_rules("wf1")
        assert len(rules) == 1
        assert rules[0].rule_id == rule.rule_id

    def test_list_empty_workflow(self, rule_manager):
        assert rule_manager.list_rules("empty-wf") == []

    def test_get_active_hyperedges(self, rule_manager):
        p = make_test_principle()
        rule_manager.create_rule(p, "wf1")
        edges = rule_manager.get_active_hyperedges("wf1")
        assert len(edges) == 1
        from dan.models.hyperedges import Hyperedge

        assert isinstance(edges[0], Hyperedge)

    def test_disable_and_enable(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        assert rule_manager.disable_rule("wf1", rule.rule_id) is True
        assert rule_manager.list_rules("wf1", status="active") == []
        assert len(rule_manager.list_rules("wf1", status="disabled")) == 1

        assert rule_manager.enable_rule("wf1", rule.rule_id) is True
        assert len(rule_manager.list_rules("wf1", status="active")) == 1

    def test_disable_nonexistent_returns_false(self, rule_manager):
        assert rule_manager.disable_rule("wf1", "ghost") is False

    def test_enable_nonexistent_returns_false(self, rule_manager):
        assert rule_manager.enable_rule("wf1", "ghost") is False

    def test_record_application(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        rule_manager.record_application("wf1", rule.rule_id)
        rule_manager.record_application("wf1", rule.rule_id)
        updated = rule_manager.list_rules("wf1")[0]
        assert updated.apply_count == 2

    def test_record_outcome_prevented(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        rule_manager.record_application("wf1", rule.rule_id)
        rule_manager.record_outcome("wf1", rule.rule_id, error_recurred=False)
        updated = rule_manager.list_rules("wf1")[0]
        assert updated.prevented_count == 1
        assert updated.recurred_count == 0

    def test_record_outcome_recurred(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        rule_manager.record_application("wf1", rule.rule_id)
        rule_manager.record_outcome("wf1", rule.rule_id, error_recurred=True)
        updated = rule_manager.list_rules("wf1")[0]
        assert updated.recurred_count == 1
        assert updated.prevented_count == 0

    def test_prune_ineffective(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        for _ in range(6):
            rule_manager.record_application("wf1", rule.rule_id)
            rule_manager.record_outcome("wf1", rule.rule_id, error_recurred=True)

        pruned = rule_manager.prune_ineffective(
            "wf1", min_effectiveness=0.3, min_apply_count=5
        )
        assert rule.rule_id in pruned
        assert rule_manager.list_rules("wf1", status="active") == []

    def test_prune_skips_low_apply_count(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        rule_manager.record_application("wf1", rule.rule_id)
        rule_manager.record_outcome("wf1", rule.rule_id, error_recurred=True)

        pruned = rule_manager.prune_ineffective(
            "wf1", min_effectiveness=0.3, min_apply_count=5
        )
        assert pruned == []

    def test_rollback_disables_rules_after_timestamp(self, rule_manager):
        cutoff = time.time()
        time.sleep(0.02)

        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        disabled = rule_manager.rollback("wf1", before_timestamp=cutoff)
        assert rule.rule_id in disabled
        assert rule_manager.list_rules("wf1", status="active") == []

    def test_rollback_preserves_old_rules(self, rule_manager):
        p_old = make_test_principle(condition="old rule")
        old_rule = rule_manager.create_rule(p_old, "wf1")
        assert old_rule is not None

        time.sleep(0.02)
        cutoff = time.time()
        time.sleep(0.02)

        p_new = make_test_principle(condition="new rule")
        rule_manager.create_rule(p_new, "wf1")

        disabled = rule_manager.rollback("wf1", before_timestamp=cutoff)
        assert old_rule.rule_id not in disabled
        active = rule_manager.list_rules("wf1", status="active")
        assert any(r.rule_id == old_rule.rule_id for r in active)

    def test_max_rules_cap_eviction(self, rule_manager):
        created_ids = []
        for i in range(5):
            p = make_test_principle(condition=f"condition_{i}")
            r = rule_manager.create_rule(p, "wf1")
            assert r is not None
            created_ids.append(r.rule_id)

        assert len(rule_manager.list_rules("wf1")) == 5

        p_extra = make_test_principle(condition="condition_extra")
        r_extra = rule_manager.create_rule(p_extra, "wf1")
        assert r_extra is not None

        rules = rule_manager.list_rules("wf1")
        assert len(rules) <= 5

    def test_stats(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        rule_manager.record_application("wf1", rule.rule_id)
        rule_manager.record_outcome("wf1", rule.rule_id, error_recurred=False)

        s = rule_manager.stats("wf1")
        assert s["total"] == 1
        assert s["active"] == 1
        assert s["avg_effectiveness"] == pytest.approx(1.0)

    def test_stats_empty_workflow(self, rule_manager):
        s = rule_manager.stats("wf1")
        assert s["total"] == 0
        assert s["active"] == 0
        assert s["avg_effectiveness"] == 0.0

    def test_expiry_transitions_on_list(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        loaded = rule_manager.list_rules("wf1")
        loaded[0].expires_at = time.time() - 1
        rule_manager._persist(loaded[0])

        assert rule_manager.list_rules("wf1", status="active") == []
        expired = rule_manager.list_rules("wf1", status="expired")
        assert len(expired) == 1

    def test_create_with_auto_activate_false(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1", auto_activate=False)
        assert rule is not None
        assert rule.status == "pending"
        assert rule_manager.list_rules("wf1", status="active") == []
        assert len(rule_manager.list_rules("wf1", status="pending")) == 1

    def test_delete_rule(self, rule_manager):
        p = make_test_principle()
        rule = rule_manager.create_rule(p, "wf1")
        assert rule is not None

        assert rule_manager.delete_rule("wf1", rule.rule_id) is True
        assert rule_manager.list_rules("wf1") == []

    def test_delete_nonexistent(self, rule_manager):
        assert rule_manager.delete_rule("wf1", "ghost") is False


# =====================================================================
# Phase 9D extensions — repair_level, parameter mutations, event wiring
# =====================================================================


class TestCausalPrincipleExtendedFields:
    """17-2 task 6: graduated repair classification fields."""

    def test_default_repair_level(self):
        p = CausalPrinciple(condition="x", action="y")
        assert p.repair_level == "prompt_fix"

    def test_explicit_repair_level(self):
        for level in ("retry", "prompt_fix", "parameter_fix", "structural_fix", "redesign"):
            p = CausalPrinciple(condition="x", action="y", repair_level=level)
            assert p.repair_level == level

    def test_suggested_parameter_changes_default(self):
        p = CausalPrinciple(condition="x", action="y")
        assert p.suggested_parameter_changes == {}

    def test_suggested_parameter_changes_populated(self):
        changes = {"model": "gpt-4", "temperature": 0.2}
        p = CausalPrinciple(
            condition="x", action="y",
            repair_level="parameter_fix",
            suggested_parameter_changes=changes,
        )
        assert p.suggested_parameter_changes == changes

    def test_structural_description_default(self):
        p = CausalPrinciple(condition="x", action="y")
        assert p.structural_description == ""

    def test_structural_description_populated(self):
        p = CausalPrinciple(
            condition="x", action="y",
            repair_level="structural_fix",
            structural_description="Add a validator node before the API call",
        )
        assert p.structural_description == "Add a validator node before the API call"

    def test_round_trip_serialization(self):
        p = CausalPrinciple(
            condition="cond", action="act", reason="why",
            repair_level="parameter_fix",
            suggested_parameter_changes={"model": "gpt-4"},
            structural_description="",
        )
        dumped = p.model_dump()
        restored = CausalPrinciple.model_validate(dumped)
        assert restored.repair_level == "parameter_fix"
        assert restored.suggested_parameter_changes == {"model": "gpt-4"}


class TestRuleGeneratorSkipsParameterFix:
    """17-3 task 6-1: parameter_fix principles don't produce hyperedges."""

    def test_parameter_fix_returns_none(self):
        gen = RuleGenerator()
        p = make_test_principle(repair_level="parameter_fix")
        result = gen.generate_rule(p, "wf1")
        assert result is None

    def test_prompt_fix_still_generates(self):
        gen = RuleGenerator()
        p = make_test_principle(repair_level="prompt_fix")
        result = gen.generate_rule(p, "wf1")
        assert result is not None

    def test_structural_fix_still_generates(self):
        gen = RuleGenerator()
        p = make_test_principle(repair_level="structural_fix")
        result = gen.generate_rule(p, "wf1")
        assert result is not None


class TestParameterMutation:
    """17-3 task 6-2: ParameterMutation model and validation."""

    def test_basic_creation(self):
        from dan.engine.rule_generator import ParameterMutation
        m = ParameterMutation(
            source_principle_id="p1",
            workflow_id="wf1",
            target_node_id="n1",
            changes={"model": "gpt-4", "temperature": 0.2},
        )
        assert m.workflow_id == "wf1"
        assert m.target_node_id == "n1"
        assert m.changes == {"model": "gpt-4", "temperature": 0.2}

    def test_validate_allowed_fields(self):
        from dan.engine.rule_generator import ParameterMutation
        m = ParameterMutation(
            source_principle_id="p1",
            workflow_id="wf1",
            target_node_id="n1",
            changes={"model": "gpt-4", "temperature": 0.5, "max_tokens": 4096},
        )
        assert m.validate_changes() == []

    def test_reject_topology_fields(self):
        from dan.engine.rule_generator import ParameterMutation
        m = ParameterMutation(
            source_principle_id="p1",
            workflow_id="wf1",
            target_node_id="n1",
            changes={"model": "gpt-4", "nodes": [], "edges": []},
        )
        rejected = m.validate_changes()
        assert "nodes" in rejected
        assert "edges" in rejected
        assert "model" not in rejected

    def test_reject_unknown_fields(self):
        from dan.engine.rule_generator import ParameterMutation
        m = ParameterMutation(
            source_principle_id="p1",
            workflow_id="wf1",
            target_node_id="n1",
            changes={"model": "gpt-4", "random_field": True},
        )
        rejected = m.validate_changes()
        assert "random_field" in rejected

    def test_default_status_active(self):
        from dan.engine.rule_generator import ParameterMutation
        m = ParameterMutation(
            source_principle_id="p1",
            workflow_id="wf1",
            target_node_id="n1",
            changes={"model": "gpt-4"},
        )
        assert m.status == "active"

    def test_round_trip_serialization(self):
        from dan.engine.rule_generator import ParameterMutation
        m = ParameterMutation(
            source_principle_id="p1",
            workflow_id="wf1",
            target_node_id="n1",
            changes={"temperature": 0.3},
        )
        dumped = m.model_dump_json()
        restored = ParameterMutation.model_validate_json(dumped)
        assert restored.changes == {"temperature": 0.3}
        assert restored.target_node_id == "n1"


class TestRuleLifecycleManagerMutations:
    """17-3 tasks 6-3 through 6-5: mutation CRUD and lifecycle."""

    def test_create_mutation_from_parameter_fix(self, rule_manager):
        p = make_test_principle(
            repair_level="parameter_fix",
            suggested_parameter_changes={"model": "gpt-4", "temperature": 0.2},
            source_node_ids=["n1"],
        )
        mutation = rule_manager.create_mutation(p, "wf1")
        assert mutation is not None
        assert mutation.target_node_id == "n1"
        assert mutation.changes == {"model": "gpt-4", "temperature": 0.2}

    def test_create_mutation_skips_non_parameter_fix(self, rule_manager):
        p = make_test_principle(repair_level="prompt_fix")
        mutation = rule_manager.create_mutation(p, "wf1")
        assert mutation is None

    def test_create_mutation_skips_empty_changes(self, rule_manager):
        p = make_test_principle(
            repair_level="parameter_fix",
            suggested_parameter_changes={},
            source_node_ids=["n1"],
        )
        mutation = rule_manager.create_mutation(p, "wf1")
        assert mutation is None

    def test_create_mutation_skips_no_target_nodes(self, rule_manager):
        p = make_test_principle(
            repair_level="parameter_fix",
            suggested_parameter_changes={"model": "gpt-4"},
            source_node_ids=[],
        )
        mutation = rule_manager.create_mutation(p, "wf1")
        assert mutation is None

    def test_create_mutation_rejects_bad_fields(self, rule_manager):
        p = make_test_principle(
            repair_level="parameter_fix",
            suggested_parameter_changes={"nodes": [], "edges": []},
            source_node_ids=["n1"],
        )
        mutation = rule_manager.create_mutation(p, "wf1")
        assert mutation is None

    def test_create_mutation_filters_bad_fields_keeps_good(self, rule_manager):
        p = make_test_principle(
            repair_level="parameter_fix",
            suggested_parameter_changes={"model": "gpt-4", "nodes": []},
            source_node_ids=["n1"],
        )
        mutation = rule_manager.create_mutation(p, "wf1")
        assert mutation is not None
        assert mutation.changes == {"model": "gpt-4"}
        assert "nodes" not in mutation.changes

    def test_get_active_mutations(self, rule_manager):
        p = make_test_principle(
            repair_level="parameter_fix",
            suggested_parameter_changes={"temperature": 0.1},
            source_node_ids=["n1"],
        )
        rule_manager.create_mutation(p, "wf1")
        mutations = rule_manager.get_active_mutations("wf1")
        assert len(mutations) == 1
        assert mutations[0].changes == {"temperature": 0.1}

    def test_get_active_mutations_empty(self, rule_manager):
        assert rule_manager.get_active_mutations("wf1") == []

    def test_expired_mutations_filtered(self, rule_manager):
        p = make_test_principle(
            repair_level="parameter_fix",
            suggested_parameter_changes={"temperature": 0.1},
            source_node_ids=["n1"],
        )
        rule_manager.create_mutation(p, "wf1")
        mutations = rule_manager.get_active_mutations("wf1")
        assert len(mutations) == 1
        mutations[0].expires_at = time.time() - 100
        rule_manager._persist_mutation(mutations[0])
        active = rule_manager.get_active_mutations("wf1")
        assert len(active) == 0


class TestReflectionRepairLevel:
    """17-2 tasks 6-2 and 6-5: reflection prompt repair_level parsing."""

    @pytest.mark.asyncio
    async def test_repair_level_in_parsed_output(self):
        llm_response = json.dumps([{
            "condition": "LLM exceeds token limit",
            "action": "Switch to larger model",
            "reason": "max_tokens too small",
            "confidence": 0.9,
            "tags": ["llm_operator"],
            "repair_level": "parameter_fix",
            "suggested_parameter_changes": {"model": "gpt-4-turbo", "max_tokens": 8192},
        }])
        provider = MockProvider(llm_response)
        registry = MockProviderRegistry(provider)
        context = _make_execution_context(provider_registry=registry)

        node = ReflectionNode(id="ref1", name="Test Reflection", source="last_run")
        inputs = {"run_id": "r1", "run_errors": [{"node_id": "n1", "error": "token limit"}]}
        result = await ReflectionExecutor().execute(node, inputs, context)

        assert result.status.value == "completed"
        principles = result.outputs["principles"]
        assert len(principles) == 1
        assert principles[0]["repair_level"] == "parameter_fix"
        assert principles[0]["suggested_parameter_changes"] == {"model": "gpt-4-turbo", "max_tokens": 8192}

    @pytest.mark.asyncio
    async def test_invalid_repair_level_defaults_to_prompt_fix(self):
        llm_response = json.dumps([{
            "condition": "something", "action": "do this",
            "reason": "because", "confidence": 0.7,
            "tags": [], "repair_level": "invalid_level",
        }])
        provider = MockProvider(llm_response)
        registry = MockProviderRegistry(provider)
        context = _make_execution_context(provider_registry=registry)

        node = ReflectionNode(id="ref1", name="Test", source="last_run")
        inputs = {"run_id": "r1", "run_errors": [{"node_id": "n1", "error": "oops"}]}
        result = await ReflectionExecutor().execute(node, inputs, context)

        principles = result.outputs["principles"]
        assert principles[0]["repair_level"] == "prompt_fix"

    @pytest.mark.asyncio
    async def test_structural_fix_with_description(self):
        llm_response = json.dumps([{
            "condition": "API validation missing",
            "action": "Add validator node",
            "reason": "No validation step",
            "confidence": 0.75,
            "tags": ["validation"],
            "repair_level": "structural_fix",
            "structural_description": "Insert ValidatorNode after API call",
        }])
        provider = MockProvider(llm_response)
        registry = MockProviderRegistry(provider)
        context = _make_execution_context(provider_registry=registry)

        node = ReflectionNode(id="ref1", name="Test", source="last_run")
        inputs = {"run_id": "r1", "run_errors": [{"node_id": "n1", "error": "bad data"}]}
        result = await ReflectionExecutor().execute(node, inputs, context)

        principles = result.outputs["principles"]
        assert principles[0]["repair_level"] == "structural_fix"
        assert principles[0]["structural_description"] == "Insert ValidatorNode after API call"

    @pytest.mark.asyncio
    async def test_missing_repair_level_defaults(self):
        llm_response = json.dumps([{
            "condition": "something", "action": "fix it",
            "reason": "broken", "confidence": 0.6, "tags": [],
        }])
        provider = MockProvider(llm_response)
        registry = MockProviderRegistry(provider)
        context = _make_execution_context(provider_registry=registry)

        node = ReflectionNode(id="ref1", name="Test", source="last_run")
        inputs = {"run_id": "r1", "run_errors": [{"node_id": "n1", "error": "err"}]}
        result = await ReflectionExecutor().execute(node, inputs, context)

        principles = result.outputs["principles"]
        assert principles[0]["repair_level"] == "prompt_fix"
        assert principles[0]["suggested_parameter_changes"] == {}
        assert principles[0]["structural_description"] == ""


class TestSchedulerParameterMutations:
    """17-3 task 6-4: parameter mutations applied at runtime."""

    def test_apply_parameter_mutations_patches_node(self):
        from dan.engine.scheduler import Engine
        from dan.models.nodes import LLMOperator
        from dan.models.graph import Graph
        from dan.engine.rule_generator import ParameterMutation

        node = LLMOperator(
            id="n1", name="Test LLM", model="gpt-3.5-turbo",
            prompt_template="Hello {name}", temperature=0.7,
        )
        graph = Graph(nodes=[node], entry_points=["n1"], exit_points=["n1"])

        mutation = ParameterMutation(
            source_principle_id="p1", workflow_id="wf1",
            target_node_id="n1",
            changes={"model": "gpt-4", "temperature": 0.2},
        )

        patched = Engine._apply_parameter_mutations(graph, [mutation])
        patched_node = patched.node_by_id("n1")
        assert patched_node.model == "gpt-4"
        assert patched_node.temperature == 0.2
        # Original is unchanged
        assert node.model == "gpt-3.5-turbo"
        assert node.temperature == 0.7

    def test_apply_mutations_no_targets_returns_same_graph(self):
        from dan.engine.scheduler import Engine
        from dan.models.nodes import LLMOperator
        from dan.models.graph import Graph
        from dan.engine.rule_generator import ParameterMutation

        node = LLMOperator(
            id="n1", name="Test", model="gpt-3.5-turbo",
            prompt_template="Hello",
        )
        graph = Graph(nodes=[node], entry_points=["n1"], exit_points=["n1"])

        mutation = ParameterMutation(
            source_principle_id="p1", workflow_id="wf1",
            target_node_id="nonexistent",
            changes={"model": "gpt-4"},
        )

        result = Engine._apply_parameter_mutations(graph, [mutation])
        assert result is graph

    def test_apply_mutations_skips_unknown_fields(self):
        from dan.engine.scheduler import Engine
        from dan.models.nodes import LLMOperator
        from dan.models.graph import Graph
        from dan.engine.rule_generator import ParameterMutation

        node = LLMOperator(
            id="n1", name="Test", model="gpt-3.5-turbo",
            prompt_template="Hello",
        )
        graph = Graph(nodes=[node], entry_points=["n1"], exit_points=["n1"])

        mutation = ParameterMutation(
            source_principle_id="p1", workflow_id="wf1",
            target_node_id="n1",
            changes={"nonexistent_field": "value"},
        )

        result = Engine._apply_parameter_mutations(graph, [mutation])
        assert result is graph

    def test_original_graph_not_mutated(self):
        from dan.engine.scheduler import Engine
        from dan.models.nodes import LLMOperator
        from dan.models.graph import Graph
        from dan.engine.rule_generator import ParameterMutation

        node = LLMOperator(
            id="n1", name="Test", model="gpt-3.5-turbo",
            prompt_template="Hello", temperature=0.7,
        )
        graph = Graph(nodes=[node], entry_points=["n1"], exit_points=["n1"])

        mutation = ParameterMutation(
            source_principle_id="p1", workflow_id="wf1",
            target_node_id="n1",
            changes={"temperature": 0.1},
        )

        Engine._apply_parameter_mutations(graph, [mutation])
        original_node = graph.node_by_id("n1")
        assert original_node.temperature == 0.7


class TestEmitLearningEvent:
    """17-4 task 4-1: RunManager._emit_learning_event helper."""

    def test_emit_appends_to_record_events(self):
        from dan.server.run_manager import RunManager, RunRecord

        manager = RunManager()
        record = RunRecord(run_id="r1", graph_id="wf1")

        manager._emit_learning_event(record, "error_memory_indexed", {
            "error_count": 3, "tier": "error_memory",
        })

        assert len(record.events) == 1
        evt = record.events[0]
        assert evt["event_type"] == "error_memory_indexed"
        assert evt["run_id"] == "r1"
        assert evt["data"]["workflow_id"] == "wf1"
        assert evt["data"]["error_count"] == 3

    def test_emit_broadcasts_to_subscribers(self):
        import asyncio
        from dan.server.run_manager import RunManager, RunRecord

        manager = RunManager()
        record = RunRecord(run_id="r1", graph_id="wf1")
        manager._runs["r1"] = record
        queue = manager.subscribe("r1")
        _catchup = queue.get_nowait()

        manager._emit_learning_event(record, "reflection_started", {
            "source_run_id": "r1",
            "reflection_run_id": "reflection-r1",
            "tier": "reflection",
        })

        evt = queue.get_nowait()
        assert evt["event_type"] == "reflection_started"

    def test_emit_with_node_id(self):
        from dan.server.run_manager import RunManager, RunRecord

        manager = RunManager()
        record = RunRecord(run_id="r1", graph_id="wf1")

        manager._emit_learning_event(
            record, "error_memory_retrieved",
            {"context_chars": 500, "tier": "error_memory"},
            node_id="n1",
        )

        assert record.events[0]["node_id"] == "n1"


class TestParameterFixMutationWiring:
    """Verify _persist_reflection_principles creates mutations for parameter_fix."""

    def test_parameter_fix_principle_creates_mutation(self, tmp_path):
        from dan.server.run_manager import RunManager, RunRecord, RunStatus
        from dan.engine.executor import EngineConfig
        from dan.engine.scheduler import RunResult

        config = EngineConfig(
            self_evolving_rules_enabled=True,
            rules_dir=str(tmp_path / "rules"),
        )
        manager = RunManager(engine_config=config)

        reflection_record = RunRecord(
            run_id="reflection-orig1",
            graph_id="wf1",
            status=RunStatus.COMPLETED,
        )
        reflection_record.result = RunResult(
            run_id="reflection-orig1",
            metadata={
                "reflection-auto": {
                    "principles": [
                        {
                            "condition": "timeout on API calls",
                            "action": "increase timeout",
                            "repair_level": "parameter_fix",
                            "suggested_parameter_changes": {"timeout_seconds": 120},
                            "source_node_ids": ["n1"],
                            "confidence": 0.9,
                        }
                    ]
                }
            }
        )

        manager._persist_reflection_principles(reflection_record)

        rlm = manager._get_rule_lifecycle_manager()
        mutations = rlm.get_active_mutations("wf1")
        assert len(mutations) == 1
        assert mutations[0].target_node_id == "n1"
        assert mutations[0].changes == {"timeout_seconds": 120}

        mutation_events = [
            e for e in reflection_record.events
            if e.get("event_type") == "rule_generated"
        ]
        assert len(mutation_events) == 1
        assert mutation_events[0]["data"]["hyperedge_type"] == "parameter_mutation"

    def test_prompt_fix_principle_creates_hyperedge_rule(self, tmp_path):
        from dan.server.run_manager import RunManager, RunRecord
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunStatus
        from dan.engine.scheduler import RunResult

        config = EngineConfig(
            self_evolving_rules_enabled=True,
            rules_dir=str(tmp_path / "rules"),
        )
        manager = RunManager(engine_config=config)

        reflection_record = RunRecord(
            run_id="reflection-orig2",
            graph_id="wf2",
            status=RunStatus.COMPLETED,
        )
        reflection_record.result = RunResult(
            run_id="reflection-orig2",
            metadata={
                "reflection-auto": {
                    "principles": [
                        {
                            "condition": "hallucinated references",
                            "action": "avoid citing papers not in the context",
                            "repair_level": "prompt_fix",
                            "confidence": 0.8,
                            "tags": ["llm_operator"],
                        }
                    ]
                }
            }
        )

        manager._persist_reflection_principles(reflection_record)

        rlm = manager._get_rule_lifecycle_manager()
        rules = rlm.list_rules("wf2", status="active")
        assert len(rules) >= 1

        rule_events = [
            e for e in reflection_record.events
            if e.get("event_type") == "rule_generated"
        ]
        assert len(rule_events) == 1
        assert rule_events[0]["data"]["hyperedge_type"] != "parameter_mutation"


class TestOriginRunEventRouting:
    """Verify reflection events are routed to the originating run record."""

    def test_reflection_completed_emitted_on_origin_run(self, tmp_path):
        from dan.server.run_manager import RunManager, RunRecord
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunStatus
        from dan.engine.scheduler import RunResult

        config = EngineConfig(
            self_evolving_rules_enabled=True,
            rules_dir=str(tmp_path / "rules"),
        )
        manager = RunManager(engine_config=config)

        origin_record = RunRecord(run_id="orig1", graph_id="wf1")
        manager._runs["orig1"] = origin_record

        reflection_record = RunRecord(
            run_id="reflection-orig1",
            graph_id="wf1",
            status=RunStatus.COMPLETED,
        )
        reflection_record.result = RunResult(
            run_id="reflection-orig1",
            metadata={
                "reflection-auto": {
                    "principles": [
                        {
                            "condition": "test",
                            "action": "fix it",
                            "confidence": 0.7,
                        }
                    ]
                }
            }
        )

        manager._persist_reflection_principles(reflection_record)

        origin_events = [
            e for e in origin_record.events
            if e.get("event_type") == "reflection_completed"
        ]
        assert len(origin_events) == 1
        assert origin_events[0]["data"]["reflection_run_id"] == "reflection-orig1"

        reflection_events = [
            e for e in reflection_record.events
            if e.get("event_type") == "reflection_completed"
        ]
        assert len(reflection_events) == 1

    def test_rule_generated_emitted_on_origin_run(self, tmp_path):
        from dan.server.run_manager import RunManager, RunRecord
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunStatus
        from dan.engine.scheduler import RunResult

        config = EngineConfig(
            self_evolving_rules_enabled=True,
            rules_dir=str(tmp_path / "rules"),
        )
        manager = RunManager(engine_config=config)

        origin_record = RunRecord(run_id="orig2", graph_id="wf2")
        manager._runs["orig2"] = origin_record

        reflection_record = RunRecord(
            run_id="reflection-orig2",
            graph_id="wf2",
            status=RunStatus.COMPLETED,
        )
        reflection_record.result = RunResult(
            run_id="reflection-orig2",
            metadata={
                "reflection-auto": {
                    "principles": [
                        {
                            "condition": "slow model",
                            "action": "use faster model",
                            "repair_level": "parameter_fix",
                            "suggested_parameter_changes": {"model": "gpt-4o-mini"},
                            "source_node_ids": ["n1"],
                            "confidence": 0.8,
                        }
                    ]
                }
            }
        )

        manager._persist_reflection_principles(reflection_record)

        origin_rule_events = [
            e for e in origin_record.events
            if e.get("event_type") == "rule_generated"
        ]
        assert len(origin_rule_events) == 1
        assert origin_rule_events[0]["data"]["hyperedge_type"] == "parameter_mutation"

    def test_no_origin_record_does_not_crash(self, tmp_path):
        from dan.server.run_manager import RunManager, RunRecord
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunStatus
        from dan.engine.scheduler import RunResult

        config = EngineConfig(
            self_evolving_rules_enabled=True,
            rules_dir=str(tmp_path / "rules"),
        )
        manager = RunManager(engine_config=config)

        reflection_record = RunRecord(
            run_id="reflection-nonexistent",
            graph_id="wf3",
            status=RunStatus.COMPLETED,
        )
        reflection_record.result = RunResult(
            run_id="reflection-nonexistent",
            metadata={
                "reflection-auto": {
                    "principles": [{"condition": "x", "action": "y", "confidence": 0.5}]
                }
            }
        )

        manager._persist_reflection_principles(reflection_record)
        assert len([
            e for e in reflection_record.events
            if e.get("event_type") == "reflection_completed"
        ]) == 1


class TestEffectivenessScoreFreshness:
    """Verify effectiveness events use reloaded (fresh) rule data."""

    def test_effectiveness_event_uses_fresh_counts(self, tmp_path):
        from dan.server.run_manager import RunManager, RunRecord
        from dan.engine.executor import EngineConfig
        from dan.server.run_manager import RunStatus
        from dan.engine.scheduler import RunResult

        config = EngineConfig(
            self_evolving_rules_enabled=True,
            rules_dir=str(tmp_path / "rules"),
        )
        manager = RunManager(engine_config=config)

        principle = make_test_principle(tags=["llm_operator"])
        rlm = manager._get_rule_lifecycle_manager()
        rule = rlm.create_rule(principle, "wf1")
        assert rule is not None
        rule.hyperedge.attach_to = ["n1"]
        rlm._persist(rule)

        record = RunRecord(
            run_id="run1", graph_id="wf1", status=RunStatus.COMPLETED,
        )
        record.result = RunResult(run_id="run1", errors={"n1": "failed"})

        manager._track_rule_effectiveness(record)

        eff_events = [
            e for e in record.events
            if e.get("event_type") == "rule_effectiveness_update"
        ]
        assert len(eff_events) == 1
        assert eff_events[0]["data"]["apply_count"] == 1
        assert isinstance(eff_events[0]["data"]["effectiveness_score"], float)


class TestRuleDisabledEvent:
    """Verify RULE_DISABLED event is emitted from the API path."""

    def test_emit_rule_lifecycle_event_basic(self):
        from dan.server.run_manager import RunManager, RunRecord

        manager = RunManager()
        record = RunRecord(run_id="r1", graph_id="wf1")
        manager._runs["r1"] = record

        manager.emit_rule_lifecycle_event("wf1", "rule_disabled", {
            "rule_id": "rule-123",
            "reason": "manual_api",
        })

        disabled_events = [
            e for e in record.events
            if e.get("event_type") == "rule_disabled"
        ]
        assert len(disabled_events) == 1
        assert disabled_events[0]["data"]["rule_id"] == "rule-123"
        assert disabled_events[0]["data"]["reason"] == "manual_api"

    def test_emit_rule_lifecycle_event_no_matching_run(self):
        from dan.server.run_manager import RunManager

        manager = RunManager()
        manager.emit_rule_lifecycle_event("wf-none", "rule_disabled", {
            "rule_id": "r1",
            "reason": "manual_api",
        })


class TestSchedulerRlmGuard:
    """Verify scheduler doesn't crash if rlm init fails."""

    def test_rlm_none_skips_mutations(self):
        from dan.engine.scheduler import Engine
        from dan.engine.executor import EngineConfig
        from dan.models.graph import Graph

        graph = Graph(id="g1", name="test", nodes=[], edges=[])
        config = EngineConfig(self_evolving_rules_enabled=True)
        engine = Engine(config=config)
        assert engine is not None


class TestLLMExecutorErrorMemoryEvent:
    """17-4 task 1-2: ERROR_MEMORY_RETRIEVED event from LLMExecutor."""

    @pytest.mark.asyncio
    async def test_error_memory_retrieved_event_emitted(self):
        from dan.engine.executor import EngineConfig
        from dan.models.nodes import LLMOperator

        emitted_events: list[dict] = []

        class MockErrorProvider:
            async def get_context(self, node, prompt, inputs, workflow_id, **kwargs):
                return "Past error: node n1 failed with timeout"

        llm_response = "Hello world"
        provider = MockProvider(llm_response)
        registry = MockProviderRegistry(provider)

        config = EngineConfig(error_memory_enabled=True)
        context = _make_execution_context(provider_registry=registry)
        context.config = config
        context._workflow_id = "wf1"
        context._error_context_provider = MockErrorProvider()

        original_emit = context.emit_event
        async def capture_emit(event_type, node_id, node_type=None, data=None):
            emitted_events.append({"event_type": event_type, "node_id": node_id, "data": data})
            await original_emit(event_type, node_id, node_type, data)
        context.emit_event = capture_emit

        node = LLMOperator(
            id="n1", name="Test LLM", model="test-model",
            prompt_template="Hello {name}",
        )
        from dan.executors.llm import LLMExecutor
        executor = LLMExecutor()
        result = await executor.execute(node, {"name": "world"}, context)

        error_mem_events = [e for e in emitted_events if e["event_type"] == "error_memory_retrieved"]
        assert len(error_mem_events) == 1
        assert error_mem_events[0]["data"]["tier"] == "error_memory"
        assert error_mem_events[0]["data"]["context_chars"] > 0
