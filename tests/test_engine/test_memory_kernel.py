"""Tests for Plan 29-1: Unified Memory Kernel.

Covers MemoryItem model, MemoryKernel CRUD, per-type ranking, policy-based
retrieval, adapter layer, integration pipeline, and consolidation lifecycle.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
    Provenance,
    RetrievalPolicy,
    ScoredMemoryItem,
    classify_task_type,
    _rank_preference,
    _rank_fact,
    _rank_workflow_asset,
    _rank_episode,
    _rank_working_state,
)
from dan.engine.memory_adapters import (
    ConversationAdapter,
    ExperienceAdapter,
    ProfileAdapter,
)
from dan.engine.user_profile import UserProfile, load_user_profile


class _FakeEmbeddingResult:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors


class _FakeEmbeddingProvider:
    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping

    async def embed(self, texts: list[str], model: str = "") -> _FakeEmbeddingResult:
        return _FakeEmbeddingResult([self._mapping[text] for text in texts])


@pytest.fixture()
def kernel(tmp_path):
    return MemoryKernel(base_dir=str(tmp_path / "memory"))


# ===================================================================
# 8-1: MemoryItem model and MemoryKernel CRUD
# ===================================================================


class TestMemoryItemModel:
    def test_memory_item_defaults(self):
        item = MemoryItem(content="hello", memory_type=MemoryType.FACT)
        assert len(item.id) == 16
        assert item.content == "hello"
        assert item.scope == MemoryScope.USER
        assert item.lifecycle == MemoryLifecycle.ACTIVE
        assert 0.0 <= item.importance <= 1.0
        assert item.created_at > 0
        assert item.updated_at > 0
        assert item.last_accessed > 0
        assert item.access_count == 0
        assert item.tags == []
        assert item.related_ids == []
        assert item.embedding is None
        assert item.metadata == {}

    def test_memory_item_types(self):
        expected = {
            "fact", "preference", "workflow_pattern", "workflow_asset",
            "failure_pattern", "principle", "episode", "working_state",
        }
        assert {t.value for t in MemoryType} == expected
        assert len(MemoryType) == 8

    def test_unique_ids(self):
        items = [MemoryItem(content=f"item {i}", memory_type=MemoryType.FACT) for i in range(20)]
        ids = {item.id for item in items}
        assert len(ids) == 20


class TestKernelCRUD:
    def test_store_and_retrieve_by_id(self, kernel):
        item = kernel.store_fact("The sky is blue")
        assert kernel.count == 1
        fetched = kernel.get(item.id)
        assert fetched is not None
        assert fetched.content == "The sky is blue"
        assert fetched.memory_type == MemoryType.FACT

    def test_store_persists_to_disk(self, tmp_path):
        base = str(tmp_path / "mem")
        k1 = MemoryKernel(base_dir=base)
        k1.store_fact("persisted")
        k2 = MemoryKernel(base_dir=base)
        assert k2.count == 1
        items = k2.list_by_type(MemoryType.FACT)
        assert items[0].content == "persisted"

    def test_store_replays_from_journal_without_snapshot(self, tmp_path):
        base = str(tmp_path / "mem")
        k1 = MemoryKernel(base_dir=base)
        item = k1.store_fact("journal only")

        reloaded = MemoryKernel(base_dir=base)
        fetched = reloaded.get(item.id)

        assert fetched is not None
        assert fetched.content == "journal only"
        assert fetched.memory_type == MemoryType.FACT

    def test_update_changes_fields(self, kernel):
        item = kernel.store_fact("original", importance=0.3)
        old_updated = item.updated_at
        time.sleep(0.01)
        updated = kernel.update(item.id, content="modified", importance=0.9)
        assert updated is not None
        assert updated.content == "modified"
        assert updated.importance == 0.9
        assert updated.updated_at >= old_updated

    def test_update_nonexistent_returns_none(self, kernel):
        assert kernel.update("does_not_exist", content="x") is None

    def test_delete_soft(self, kernel):
        item = kernel.store_fact("to soft-delete")
        assert kernel.delete(item.id) is True
        fetched = kernel.get(item.id)
        assert fetched is not None
        assert fetched.lifecycle == MemoryLifecycle.ARCHIVE

    def test_delete_hard(self, kernel):
        item = kernel.store_fact("to hard-delete")
        assert kernel.delete(item.id, hard=True) is True
        assert kernel.get(item.id) is None
        assert kernel.count == 0

    def test_delete_nonexistent(self, kernel):
        assert kernel.delete("nope") is False
        assert kernel.delete("nope", hard=True) is False

    def test_list_by_type_filters_correctly(self, kernel):
        kernel.store_fact("fact-1")
        kernel.store_fact("fact-2")
        kernel.store_preference("pref-1")
        kernel.store_episode("ep-1")

        facts = kernel.list_by_type(MemoryType.FACT)
        assert len(facts) == 2
        assert all(f.memory_type == MemoryType.FACT for f in facts)

        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        assert len(prefs) == 1

        episodes = kernel.list_by_type(MemoryType.EPISODE)
        assert len(episodes) == 1

    def test_list_by_type_excludes_archived(self, kernel):
        item = kernel.store_fact("will archive")
        kernel.delete(item.id)  # soft-delete → ARCHIVE
        assert kernel.list_by_type(MemoryType.FACT) == []
        archived = kernel.list_by_type(MemoryType.FACT, lifecycle=MemoryLifecycle.ARCHIVE)
        assert len(archived) == 1

    def test_list_by_type_scope_filter(self, kernel):
        kernel.store_fact("user-fact", scope=MemoryScope.USER)
        kernel.store_fact("session-fact", scope=MemoryScope.SESSION)
        user_facts = kernel.list_by_type(MemoryType.FACT, scope=MemoryScope.USER)
        assert len(user_facts) == 1
        assert user_facts[0].content == "user-fact"

    def test_store_many(self, kernel):
        items = [
            MemoryItem(content=f"batch-{i}", memory_type=MemoryType.FACT)
            for i in range(5)
        ]
        stored = kernel.store_many(items)
        assert len(stored) == 5
        assert kernel.count == 5

    def test_convenience_store_methods(self, kernel):
        f = kernel.store_fact("a fact")
        assert f.memory_type == MemoryType.FACT

        p = kernel.store_preference("a pref", confirmed=True)
        assert p.memory_type == MemoryType.PREFERENCE
        assert p.provenance.confirmed_by_user is True

        e = kernel.store_episode("an episode")
        assert e.memory_type == MemoryType.EPISODE

        pr = kernel.store_principle("a principle", confidence=0.8)
        assert pr.memory_type == MemoryType.PRINCIPLE
        assert pr.metadata["confidence"] == 0.8

        wa = kernel.store_workflow_asset("asset", workflow_id="wf-1", success_rate=0.9)
        assert wa.memory_type == MemoryType.WORKFLOW_ASSET
        assert wa.metadata["success_rate"] == 0.9

        fp = kernel.store_failure_pattern("failure")
        assert fp.memory_type == MemoryType.FAILURE_PATTERN

    def test_stats(self, kernel):
        kernel.store_fact("f1")
        kernel.store_preference("p1")
        s = kernel.stats()
        assert s["total"] == 2
        assert s["by_type"]["fact"] == 1
        assert s["by_type"]["preference"] == 1
        assert "storage_path" in s


# ===================================================================
# 8-2: Per-type ranking (TypedRanker)
# ===================================================================


class TestTypedRanking:
    def test_preference_ranking_favors_confirmed(self):
        confirmed = MemoryItem(
            content="dark mode",
            memory_type=MemoryType.PREFERENCE,
            provenance=Provenance(confirmed_by_user=True),
        )
        unconfirmed = MemoryItem(
            content="dark mode",
            memory_type=MemoryType.PREFERENCE,
            provenance=Provenance(confirmed_by_user=False),
        )
        assert _rank_preference(confirmed, "settings") > _rank_preference(unconfirmed, "settings")

    def test_fact_ranking_favors_confidence(self):
        high = MemoryItem(content="fact", memory_type=MemoryType.FACT, importance=0.9)
        low = MemoryItem(content="fact", memory_type=MemoryType.FACT, importance=0.1)
        assert _rank_fact(high, "query") > _rank_fact(low, "query")

    def test_workflow_asset_ranking_favors_success_rate(self):
        high_sr = MemoryItem(
            content="pipeline asset",
            memory_type=MemoryType.WORKFLOW_ASSET,
            metadata={"success_rate": 0.95},
        )
        low_sr = MemoryItem(
            content="pipeline asset",
            memory_type=MemoryType.WORKFLOW_ASSET,
            metadata={"success_rate": 0.1},
        )
        assert _rank_workflow_asset(high_sr, "pipeline") > _rank_workflow_asset(low_sr, "pipeline")

    def test_episode_ranking_favors_recency(self):
        now = time.time()
        recent = MemoryItem(
            content="episode",
            memory_type=MemoryType.EPISODE,
            last_accessed=now,
        )
        old = MemoryItem(
            content="episode",
            memory_type=MemoryType.EPISODE,
            last_accessed=now - 90 * 86400,
        )
        assert _rank_episode(recent, "query") > _rank_episode(old, "query")

    def test_working_state_always_highest(self):
        ws = MemoryItem(content="state", memory_type=MemoryType.WORKING_STATE)
        assert _rank_working_state(ws, "anything") == 1.0


# ===================================================================
# 8-3: RetrievalPolicy budget allocation
# ===================================================================


class TestRetrievalPolicy:
    def _populate(self, kernel: MemoryKernel) -> None:
        for i in range(3):
            kernel.store_fact(f"fact {i}")
            kernel.store_preference(f"pref {i}")
            kernel.store_episode(f"episode {i}")
            kernel.store_workflow_asset(f"pipeline asset {i}", workflow_id=f"wf-{i}", success_rate=0.8)
            kernel.store_failure_pattern(f"error pattern {i}")
            kernel.store_principle(f"principle {i}", confidence=0.7)
            kernel.store(MemoryItem(
                content=f"working state {i}",
                memory_type=MemoryType.WORKING_STATE,
            ))

    def test_retrieve_by_task_workflow_build(self, kernel):
        self._populate(kernel)
        results = kernel.retrieve_by_task("build a data pipeline", task_type="workflow_build")
        types_present = {r.item.memory_type for r in results}
        assert MemoryType.WORKFLOW_ASSET in types_present
        assert MemoryType.WORKING_STATE in types_present

    def test_retrieve_by_task_factual_answer(self, kernel):
        self._populate(kernel)
        results = kernel.retrieve_by_task("what is the capital", task_type="factual_answer")
        types_present = {r.item.memory_type for r in results}
        assert MemoryType.FACT in types_present
        assert MemoryType.EPISODE in types_present

    def test_retrieve_by_task_general_conversation(self, kernel):
        self._populate(kernel)
        results = kernel.retrieve_by_task("hello there", task_type="general_conversation")
        types_present = {r.item.memory_type for r in results}
        assert MemoryType.WORKING_STATE in types_present
        assert MemoryType.PREFERENCE in types_present

    def test_retrieve_respects_limit(self, kernel):
        for i in range(20):
            kernel.store_fact(f"fact number {i}")
        results = kernel.retrieve("find facts", policy="general_conversation", limit=5)
        assert len(results) <= 5

    def test_retrieve_excludes_archived(self, kernel):
        item = kernel.store_fact("archived fact")
        kernel.delete(item.id)  # soft-delete → ARCHIVE
        results = kernel.retrieve("archived fact", policy="factual_answer")
        ids = {r.item.id for r in results}
        assert item.id not in ids

    def test_retrieve_updates_access_metadata(self, kernel):
        item = kernel.store_fact("accessed fact")
        old_count = item.access_count
        kernel.retrieve("accessed fact", policy="factual_answer")
        fetched = kernel.get(item.id)
        assert fetched.access_count > old_count

    def test_retrieve_by_task_auto_classifies(self, kernel):
        """Without explicit task_type, classify_task_type determines policy."""
        self._populate(kernel)
        results = kernel.retrieve_by_task("build a workflow")
        assert len(results) > 0
        types_present = {r.item.memory_type for r in results}
        assert MemoryType.WORKFLOW_ASSET in types_present

    def test_retrieve_scored_items_sorted_descending(self, kernel):
        self._populate(kernel)
        results = kernel.retrieve_by_task("pipeline", task_type="workflow_build")
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_retrieve_uses_vector_hits_for_semantic_candidates(self, tmp_path):
        provider = _FakeEmbeddingProvider({
            "draft the quarterly recap": [1.0, 0.0],
        })
        kernel = MemoryKernel(
            base_dir=str(tmp_path / "memory"),
            embedding_provider=provider,
        )
        semantic = kernel.store_workflow_asset(
            "Prepare the investor update",
            workflow_id="wf-semantic",
            success_rate=0.5,
            embedding=[1.0, 0.0],
        )
        lexical = kernel.store_workflow_asset(
            "Draft the quarterly recap with slides",
            workflow_id="wf-lexical",
            success_rate=0.5,
            embedding=[0.0, 1.0],
        )

        results = kernel.retrieve_by_task(
            "draft the quarterly recap",
            task_type="workflow_build",
        )
        asset_ids = [r.item.id for r in results if r.item.memory_type == MemoryType.WORKFLOW_ASSET]

        assert semantic.id in asset_ids
        assert lexical.id in asset_ids
        assert asset_ids.index(semantic.id) < asset_ids.index(lexical.id)


# ===================================================================
# 8-4: Adapter layer
# ===================================================================


class TestAdapterLayer:
    def test_profile_adapter_imports_preferences(self, kernel):
        profile = SimpleNamespace(
            preferred_models={"coding": "gpt-4", "chat": "claude-3"},
            preferred_output_format="json",
            common_domains=["ML", "NLP"],
            search_dirs=["/data/projects"],
        )
        count = ProfileAdapter.import_profile(profile, kernel)
        assert count > 0

        prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        assert len(prefs) >= 2  # 2 model prefs + 1 output format
        assert any("gpt-4" in p.content for p in prefs)

        facts = kernel.list_by_type(MemoryType.FACT)
        assert len(facts) >= 2  # 2 domains + 1 search_dir
        assert any("ML" in f.content for f in facts)

    def test_conversation_adapter_imports_episodes(self, kernel):
        now = time.time()
        summaries = [
            SimpleNamespace(
                id="conv-001",
                summary="Discussed data pipeline optimization",
                timestamp=now - 3600,
                topic_tags=["pipeline", "optimization"],
                workflow_id="wf-opt",
            ),
            SimpleNamespace(
                id="conv-002",
                summary="Explored NLP models",
                timestamp=now - 7200,
                topic_tags=["nlp"],
                workflow_id=None,
            ),
        ]
        conv_store = SimpleNamespace(get_recent=lambda n: summaries)
        count = ConversationAdapter.import_all(conv_store, kernel)
        assert count == 2

        episodes = kernel.list_by_type(MemoryType.EPISODE)
        assert len(episodes) == 2
        assert any("pipeline" in e.content for e in episodes)

    def test_experience_adapter_imports_workflow_assets(self):
        exp = SimpleNamespace(
            workflow_id="wf-test",
            name="Test Workflow",
            description="A test workflow",
            tags=["test"],
            node_types_used=["llm", "tool"],
            tools_used=["web_search"],
            run_count=10,
            success_count=8,
            avg_elapsed_seconds=5.0,
            avg_total_cost=0.01,
            success_patterns=["good input"],
            failure_patterns=["bad input"],
            created_at=time.time() - 86400,
            updated_at=time.time(),
        )
        item = ExperienceAdapter.to_memory_item(exp)
        assert item.memory_type == MemoryType.WORKFLOW_ASSET
        assert "Test Workflow" in item.content
        assert item.metadata["success_rate"] == 0.8
        assert item.metadata["workflow_id"] == "wf-test"


# ===================================================================
# 8-5: Integration: store → retrieve with policy → verify ranking
# ===================================================================


class TestFullRetrievalPipeline:
    def test_full_retrieval_pipeline(self, kernel):
        kernel.store_fact("Python is a programming language")
        kernel.store_preference("Prefers dark mode", confirmed=True)
        kernel.store_episode("Built a data pipeline yesterday")
        kernel.store_workflow_asset(
            "data pipeline: fetch → transform → load",
            workflow_id="wf-etl",
            success_rate=0.9,
        )
        kernel.store_workflow_asset(
            "simple pipeline: read → write",
            workflow_id="wf-simple",
            success_rate=0.3,
        )
        kernel.store_failure_pattern("pipeline timeout on large datasets")

        results = kernel.retrieve_by_task(
            "build a data pipeline",
            task_type="workflow_build",
        )

        assert len(results) > 0
        types = [r.item.memory_type for r in results]
        assert MemoryType.WORKFLOW_ASSET in types

        wa_results = [r for r in results if r.item.memory_type == MemoryType.WORKFLOW_ASSET]
        if len(wa_results) >= 2:
            assert wa_results[0].score >= wa_results[1].score

    def test_keyword_overlap_boosts_relevance(self, kernel):
        kernel.store_workflow_asset("email notification sender", workflow_id="wf-email", success_rate=0.5)
        kernel.store_workflow_asset("data pipeline builder", workflow_id="wf-data", success_rate=0.5)

        results = kernel.retrieve_by_task("build a data pipeline", task_type="workflow_build")
        wa_results = [r for r in results if r.item.memory_type == MemoryType.WORKFLOW_ASSET]

        if len(wa_results) >= 2:
            data_result = next((r for r in wa_results if "data" in r.item.content), None)
            email_result = next((r for r in wa_results if "email" in r.item.content), None)
            if data_result and email_result:
                assert data_result.score >= email_result.score


# ===================================================================
# 8-6: Consolidation lifecycle
# ===================================================================


class TestConsolidationLifecycle:
    def test_promote_to_durable(self, kernel):
        kernel.store_fact("old fact")
        kernel.store_preference("old pref")
        promoted = kernel.promote_to_durable(age_hours=0)
        assert promoted == 2

        for item in kernel.list_by_type(MemoryType.FACT, lifecycle=MemoryLifecycle.DURABLE):
            assert item.lifecycle == MemoryLifecycle.DURABLE

    def test_promote_skips_already_durable(self, kernel):
        item = kernel.store_fact("fact")
        kernel.update(item.id, lifecycle=MemoryLifecycle.DURABLE)
        promoted = kernel.promote_to_durable(age_hours=0)
        assert promoted == 0

    def test_archive_stale(self, kernel):
        item = kernel.store_fact("stale fact")
        kernel.update(item.id, lifecycle=MemoryLifecycle.DURABLE)
        archived = kernel.archive_stale(stale_days=0)
        assert archived == 1
        fetched = kernel.get(item.id)
        assert fetched.lifecycle == MemoryLifecycle.ARCHIVE

    def test_archive_skips_active(self, kernel):
        kernel.store_fact("active fact")
        archived = kernel.archive_stale(stale_days=0)
        assert archived == 0

    def test_apply_decay(self, kernel):
        item = kernel.store_fact("decayable", importance=0.8)
        old_importance = item.importance
        decayed = kernel.apply_decay(decay_days=0, decay_factor=0.5)
        assert decayed == 1
        fetched = kernel.get(item.id)
        assert fetched.importance < old_importance
        assert fetched.importance == pytest.approx(old_importance * 0.5)

    def test_apply_decay_skips_archived(self, kernel):
        item = kernel.store_fact("archived", importance=0.8)
        kernel.delete(item.id)  # soft-delete → ARCHIVE
        decayed = kernel.apply_decay(decay_days=0)
        assert decayed == 0

    def test_apply_decay_skips_low_importance(self, kernel):
        kernel.store_fact("very low", importance=0.04)
        decayed = kernel.apply_decay(decay_days=0)
        assert decayed == 0

    def test_classify_task_type(self):
        assert classify_task_type("build a workflow") == "workflow_build"
        assert classify_task_type("create a pipeline") == "workflow_build"
        assert classify_task_type("what is X") == "factual_answer"
        assert classify_task_type("explain how this works") == "factual_answer"
        assert classify_task_type("fix this error") == "workflow_repair"
        assert classify_task_type("debug the failing test") == "workflow_repair"
        assert classify_task_type("hello") == "general_conversation"
        assert classify_task_type("thanks") == "general_conversation"

    def test_classify_task_type_active_build_override(self):
        assert classify_task_type("hello", has_active_build=True) == "workflow_build"

    def test_full_lifecycle_promote_then_archive(self, kernel):
        """ACTIVE → promote → DURABLE → archive → ARCHIVE."""
        item = kernel.store_fact("lifecycle test")
        assert item.lifecycle == MemoryLifecycle.ACTIVE

        kernel.promote_to_durable(age_hours=0)
        fetched = kernel.get(item.id)
        assert fetched.lifecycle == MemoryLifecycle.DURABLE

        kernel.archive_stale(stale_days=0)
        fetched = kernel.get(item.id)
        assert fetched.lifecycle == MemoryLifecycle.ARCHIVE

    def test_increment_workflow_asset_usage(self, kernel):
        kernel.store_workflow_asset("asset", workflow_id="wf-track", success_rate=0.0)
        assert kernel.increment_workflow_asset_usage("wf-track", success=True) is True
        assert kernel.increment_workflow_asset_usage("wf-track", success=False) is True

        items = kernel.list_by_type(MemoryType.WORKFLOW_ASSET)
        item = items[0]
        assert item.metadata["reuse_count"] == 2
        assert item.metadata["reuse_success_count"] == 1
        assert item.metadata["success_rate"] == 0.5

    def test_increment_nonexistent_workflow(self, kernel):
        assert kernel.increment_workflow_asset_usage("no-such-wf", success=True) is False


# ---------------------------------------------------------------------------
# Dual-write mode (29-1 §5-7)
# ---------------------------------------------------------------------------

class TestDualWriteMode:
    def test_dual_write_disabled_by_default(self, tmp_path):
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        assert kernel._dual_write is None

    def test_dual_write_episode_to_conversation_memory(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DAN_MEMORY_DUAL_WRITE", "1")
        added = []
        conversation_memory = SimpleNamespace(
            add_summary=lambda summary, workflow_id="", topic_tags=None: added.append(
                {"summary": summary, "workflow_id": workflow_id, "topic_tags": topic_tags or []}
            )
        )
        from dan.engine.memory_kernel import DualWriteAdapter
        adapter = DualWriteAdapter(conversation_memory=conversation_memory)
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"), dual_write_adapter=adapter)
        kernel.store_episode("User asked about stocks")
        assert len(added) == 1
        assert "stocks" in added[0]["summary"]

    def test_dual_write_preference_to_user_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DAN_MEMORY_DUAL_WRITE", "1")
        monkeypatch.setenv("DAN_PROFILE_PATH", str(tmp_path / "profile.json"))
        profile = UserProfile()

        from dan.engine.memory_kernel import DualWriteAdapter
        adapter = DualWriteAdapter(user_profile=profile)
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"), dual_write_adapter=adapter)
        kernel.store_preference("output_format: markdown")
        loaded = load_user_profile()
        assert loaded.preferred_output_format == "markdown"

    def test_dual_write_fact_to_user_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DAN_MEMORY_DUAL_WRITE", "1")
        monkeypatch.setenv("DAN_PROFILE_PATH", str(tmp_path / "profile.json"))
        profile = UserProfile()

        from dan.engine.memory_kernel import DualWriteAdapter
        adapter = DualWriteAdapter(user_profile=profile)
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"), dual_write_adapter=adapter)
        kernel.store_fact(
            "papers directory: /tmp/papers",
            tags=["search_dir"],
            metadata={"path": "/tmp/papers", "is_directory": True},
        )
        loaded = load_user_profile()
        assert loaded.search_dirs == ["/tmp/papers"]

    def test_dual_write_workflow_asset_to_experience_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DAN_MEMORY_DUAL_WRITE", "1")
        recorded = []

        class FakeExpStore:
            async def save_experience(self, experience):
                recorded.append(experience)

        from dan.engine.memory_kernel import DualWriteAdapter
        adapter = DualWriteAdapter(experience_store=FakeExpStore())
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"), dual_write_adapter=adapter)
        kernel.store_workflow_asset("equity pipeline", workflow_id="wf-123")
        assert len(recorded) == 1
        assert recorded[0].workflow_id == "wf-123"

    def test_dual_write_store_many(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DAN_MEMORY_DUAL_WRITE", "1")
        added = []
        conversation_memory = SimpleNamespace(add=lambda content: added.append(content))
        from dan.engine.memory_kernel import DualWriteAdapter
        adapter = DualWriteAdapter(conversation_memory=conversation_memory)
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"), dual_write_adapter=adapter)
        items = [
            MemoryItem(content="episode 1", memory_type=MemoryType.EPISODE),
            MemoryItem(content="episode 2", memory_type=MemoryType.EPISODE),
        ]
        kernel.store_many(items)
        assert len(added) == 2

    def test_dual_write_failure_is_silent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DAN_MEMORY_DUAL_WRITE", "1")

        class BrokenProfile:
            def set(self, key, value):
                raise RuntimeError("Profile write failed")

        from dan.engine.memory_kernel import DualWriteAdapter
        adapter = DualWriteAdapter(user_profile=BrokenProfile())
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"), dual_write_adapter=adapter)
        item = kernel.store_fact("This should not raise")
        assert kernel.get(item.id) is not None

    def test_dual_write_not_invoked_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DAN_MEMORY_DUAL_WRITE", raising=False)
        call_count = {"n": 0}

        class SpyAdapter:
            def write(self, item):
                call_count["n"] += 1

        from dan.engine.memory_kernel import DualWriteAdapter
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"), dual_write_adapter=SpyAdapter())
        kernel.store_fact("test")
        assert call_count["n"] == 0
