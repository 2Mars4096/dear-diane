"""Tests for recipe distillation system (Plans 36-1 through 36-4).

Covers models, FurnaceSessionStore, IngredientLedger, Corpus, and RecipeCompiler.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dan.engine.memory_kernel import MemoryKernel
from dan.engine.recipe.models import (
    FurnacePhase,
    PaperStatus,
    SourceStatus,
    KnowledgeKind,
    Generality,
    KNOWLEDGE_KIND_TO_MEMORY_TYPE,
    FurnaceSession,
    IngredientRecord,
    IngredientStatus,
    BatchCheckpoint,
    CorpusMetadata,
    AcquisitionSource,
)
from dan.engine.recipe.session_store import FurnaceSessionStore
from dan.engine.recipe.ingredient_ledger import IngredientLedger
from dan.engine.recipe.corpus import (
    CorpusWriter,
    CorpusReader,
    PromotionEngine,
)
from dan.engine.recipe.recipe_compiler import RecipeCompiler


# ---------------------------------------------------------------------------
# TestModels
# ---------------------------------------------------------------------------


class TestModels:
    """Test models and enums."""

    def test_furnace_phase_values(self) -> None:
        phases = list(FurnacePhase)
        assert phases == [
            FurnacePhase.NORMALIZE,
            FurnacePhase.EXTRACT,
            FurnacePhase.AGGREGATE,
            FurnacePhase.INFER,
            FurnacePhase.PROJECT,
        ]

    def test_paper_status_values(self) -> None:
        statuses = list(PaperStatus)
        assert PaperStatus.PENDING in statuses
        assert PaperStatus.INGESTED in statuses
        assert PaperStatus.EXTRACTED in statuses
        assert PaperStatus.SKIPPED in statuses
        assert PaperStatus.DEFERRED in statuses

    def test_knowledge_kind_taxonomy(self) -> None:
        kinds = list(KnowledgeKind)
        assert len(kinds) == 16
        expected = {
            "source_metadata", "source_summary", "claim", "method", "dataset", "measure",
            "terminology", "citation_norm", "rhetorical_move", "question_pattern",
            "association_edge", "taste_signal", "writing_rule", "anti_pattern",
            "evaluation_case", "recipe_snapshot",
        }
        assert {k.value for k in kinds} == expected

    def test_knowledge_kind_to_memory_type_mapping(self) -> None:
        for kind in KnowledgeKind:
            assert kind in KNOWLEDGE_KIND_TO_MEMORY_TYPE
            mt = KNOWLEDGE_KIND_TO_MEMORY_TYPE[kind]
            assert mt in ("FACT", "PREFERENCE", "PRINCIPLE", "EPISODE", "WORKFLOW_ASSET")

    def test_furnace_session_create(self) -> None:
        session = FurnaceSession(
            corpus_id="corpus1",
            recipe_id="recipe1",
            paper_queue={"p1": PaperStatus.PENDING, "p2": PaperStatus.PENDING},
        )
        assert session.corpus_id == "corpus1"
        assert session.recipe_id == "recipe1"
        assert session.current_phase == FurnacePhase.NORMALIZE
        assert session.status == "active"
        assert len(session.session_id) > 0

    def test_furnace_session_papers_by_status(self) -> None:
        session = FurnaceSession(
            corpus_id="c",
            recipe_id="r",
            paper_queue={
                "p1": PaperStatus.PENDING,
                "p2": PaperStatus.INGESTED,
                "p3": PaperStatus.PENDING,
            },
        )
        assert set(session.papers_by_status(PaperStatus.PENDING)) == {"p1", "p3"}
        assert session.papers_by_status(PaperStatus.INGESTED) == ["p2"]

    def test_furnace_session_advance_phase(self) -> None:
        session = FurnaceSession(corpus_id="c", recipe_id="r", paper_queue={})
        assert session.current_phase == FurnacePhase.NORMALIZE
        session.advance_phase()
        assert session.current_phase == FurnacePhase.EXTRACT
        for _ in range(4):
            session.advance_phase()
        assert session.advance_phase() is None
        assert session.current_phase == FurnacePhase.PROJECT

    def test_ingredient_record_defaults(self) -> None:
        rec = IngredientRecord(paper_id="smith2020", title="A Paper")
        assert rec.paper_id == "smith2020"
        assert rec.status == IngredientStatus.ACTIVE
        assert rec.source == AcquisitionSource.MANUAL_IMPORT
        assert rec.authors == []
        assert rec.year is None
        assert rec.added_at > 0
        assert rec.updated_at > 0

    def test_batch_checkpoint_defaults(self) -> None:
        cp = BatchCheckpoint(batch_index=0, recipe_version="0.1.0", phase_completed=FurnacePhase.NORMALIZE)
        assert len(cp.checkpoint_id) == 12
        assert cp.token_usage == 0
        assert cp.cost_usd == 0.0
        assert cp.benchmark_refs == []
        assert cp.created_at > 0

    def test_corpus_metadata_defaults(self) -> None:
        meta = CorpusMetadata()
        assert meta.family == "corpus"
        assert meta.corpus_id == ""
        assert meta.source_id == ""
        assert meta.knowledge_kind == KnowledgeKind.SOURCE_METADATA
        assert meta.generality.value == "paper"
        assert meta.support_count == 1
        assert meta.confidence == 0.5
        assert meta.evidence == []


class TestFurnaceSessionModel:
    def test_session_name_topic_description(self) -> None:
        session = FurnaceSession(
            corpus_id="c",
            recipe_id="r",
            name="My Session",
            topic="Supply Chain",
            description="A test corpus",
            paper_queue={"p1": PaperStatus.PENDING},
        )
        assert session.name == "My Session"
        assert session.topic == "Supply Chain"
        assert session.description == "A test corpus"
        dumped = session.model_dump()
        assert dumped["name"] == "My Session"
        assert dumped["topic"] == "Supply Chain"
        assert dumped["description"] == "A test corpus"
        loaded = FurnaceSession.model_validate(dumped)
        assert loaded.name == session.name
        assert loaded.topic == session.topic
        assert loaded.description == session.description

    def test_session_source_queue_alias(self) -> None:
        session = FurnaceSession(
            corpus_id="c",
            recipe_id="r",
            paper_queue={"p1": PaperStatus.PENDING, "p2": PaperStatus.INGESTED},
        )
        assert session.source_queue is session.paper_queue
        assert session.source_queue == {"p1": PaperStatus.PENDING, "p2": PaperStatus.INGESTED}

    def test_session_sources_by_status(self) -> None:
        session = FurnaceSession(
            corpus_id="c",
            recipe_id="r",
            paper_queue={
                "p1": PaperStatus.PENDING,
                "p2": PaperStatus.INGESTED,
                "p3": PaperStatus.PENDING,
            },
        )
        assert set(session.sources_by_status(PaperStatus.PENDING)) == {"p1", "p3"}
        assert session.sources_by_status(PaperStatus.INGESTED) == ["p2"]

    def test_session_tags_roundtrip(self) -> None:
        session = FurnaceSession(
            corpus_id="c",
            recipe_id="r",
            tags=["baseline", "keep"],
            paper_queue={},
        )
        assert session.tags == ["baseline", "keep"]
        dumped = session.model_dump()
        assert dumped["tags"] == ["baseline", "keep"]
        loaded = FurnaceSession.model_validate(dumped)
        assert loaded.tags == ["baseline", "keep"]

    def test_session_budget_limit(self) -> None:
        session = FurnaceSession(
            corpus_id="c",
            recipe_id="r",
            paper_queue={},
            budget_limit_usd=5.0,
        )
        assert session.budget_limit_usd == 5.0
        dumped = session.model_dump()
        assert dumped["budget_limit_usd"] == 5.0

    def test_source_status_alias(self) -> None:
        assert SourceStatus is PaperStatus
        assert SourceStatus.PENDING == PaperStatus.PENDING


# ---------------------------------------------------------------------------
# TestFurnaceSessionStore
# ---------------------------------------------------------------------------


class TestFurnaceSessionStore:
    """Test FurnaceSessionStore with tmp_path."""

    def test_create_session(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("corpus1", "recipe1", ["p1", "p2", "p3"])
        assert session.corpus_id == "corpus1"
        assert session.recipe_id == "recipe1"
        assert session.paper_queue == {
            "p1": PaperStatus.PENDING,
            "p2": PaperStatus.PENDING,
            "p3": PaperStatus.PENDING,
        }

    def test_create_session_with_tags(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session(
            "corpus1",
            "recipe1",
            ["p1"],
            tags=["baseline", "favorite"],
        )
        assert session.tags == ["baseline", "favorite"]
        reloaded = store.load(session.session_id)
        assert reloaded is not None
        assert reloaded.tags == ["baseline", "favorite"]

    def test_set_tags(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("corpus1", "recipe1", ["p1"])
        updated = store.set_tags(session.session_id, ["baseline", "favorite"])
        assert updated is not None
        assert updated.tags == ["baseline", "favorite"]
        reloaded = store.load(session.session_id)
        assert reloaded is not None
        assert reloaded.tags == ["baseline", "favorite"]
        assert (tmp_path / f"{session.session_id}.json").exists()

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        assert store.load("nonexistent") is None

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("c", "r", ["p1"])
        loaded = store.load(session.session_id)
        assert loaded is not None
        assert loaded.corpus_id == session.corpus_id
        assert loaded.paper_queue == session.paper_queue

    def test_list_sessions_filter_by_corpus(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        store.create_session("corpus_a", "r1", ["p1"])
        store.create_session("corpus_b", "r1", ["p2"])
        store.create_session("corpus_a", "r2", ["p3"])
        all_sessions = store.list_sessions()
        assert len(all_sessions) == 3
        filtered = store.list_sessions(corpus_id="corpus_a")
        assert len(filtered) == 2
        assert all(s.corpus_id == "corpus_a" for s in filtered)

    def test_list_sessions_filter_by_recipe(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        store.create_session("corpus1", "recipe_a", ["p1"])
        store.create_session("corpus1", "recipe_b", ["p2"])
        filtered = store.list_sessions(recipe_id="recipe_a")
        assert len(filtered) == 1
        assert filtered[0].recipe_id == "recipe_a"

    def test_add_checkpoint(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("c", "r", ["p1", "p2"])
        cp = BatchCheckpoint(
            batch_index=0,
            recipe_version="0.1.0",
            phase_completed=FurnacePhase.NORMALIZE,
            paper_ids=["p1", "p2"],
            token_usage=1000,
            cost_usd=0.01,
        )
        updated = store.add_checkpoint(session.session_id, cp)
        assert updated is not None
        assert len(updated.checkpoints) == 1
        assert updated.total_token_usage == 1000
        assert updated.total_cost_usd == 0.01

    def test_update_paper_status(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("c", "r", ["p1", "p2"])
        assert store.update_paper_status(session.session_id, "p1", PaperStatus.INGESTED)
        loaded = store.load(session.session_id)
        assert loaded.paper_queue["p1"] == PaperStatus.INGESTED
        assert store.update_paper_status(session.session_id, "p99", PaperStatus.INGESTED) is False

    def test_add_papers_to_session(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("c", "r", ["p1"])
        updated = store.add_papers(session.session_id, ["p2", "p3"])
        assert updated is not None
        assert "p1" in updated.paper_queue
        assert "p2" in updated.paper_queue
        assert "p3" in updated.paper_queue
        assert updated.paper_queue["p2"] == PaperStatus.PENDING

    def test_resume_session(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("c", "r", ["p1"])
        store.pause_session(session.session_id)
        resumed = store.resume_session(session.session_id)
        assert resumed is not None
        assert resumed.status == "active"

    def test_pause_session(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("c", "r", ["p1"])
        assert store.pause_session(session.session_id)
        loaded = store.load(session.session_id)
        assert loaded.status == "paused"
        assert store.pause_session("nonexistent") is False

    def test_get_resume_point(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("c", "r", ["p1", "p2"])
        store.update_paper_status(session.session_id, "p1", PaperStatus.INGESTED)
        point = store.get_resume_point(session.session_id)
        assert point is not None
        assert point["current_phase"] == "normalize"
        assert point["pending_count"] == 1
        assert "p2" in point["pending_papers"]
        assert point["total_ingested"] == 1

    def test_delete_session(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("c", "r", ["p1"])
        path = tmp_path / f"{session.session_id}.json"
        assert path.exists()
        assert store.delete(session.session_id)
        assert not path.exists()
        assert store.delete(session.session_id) is False


class TestFurnaceSessionStoreUpdates:
    def test_create_session_with_name(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session(
            "corpus1",
            "recipe1",
            ["p1", "p2"],
            name="My Topic Session",
            topic="Supply Chain",
            description="Papers on supply chain optimization",
            target_count=20,
        )
        assert session.name == "My Topic Session"
        assert session.topic == "Supply Chain"
        assert session.description == "Papers on supply chain optimization"
        assert session.metadata.get("target_count") == 20
        loaded = store.load(session.session_id)
        assert loaded is not None
        assert loaded.name == "My Topic Session"

    def test_find_by_name(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        store.create_session("c1", "r1", [], name="Alpha Session", topic="A")
        s2 = store.create_session("c2", "r2", [], name="Beta Project", topic="B")
        store.create_session("c3", "r3", [], name="Gamma", topic="C")
        found = store.find_by_name("beta")
        assert found is not None
        assert found.session_id == s2.session_id
        assert store.find_by_name("BETA") is not None
        assert store.find_by_name("project").session_id == s2.session_id

    def test_find_by_name_not_found(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        store.create_session("c", "r", [], name="Alpha", topic="X")
        assert store.find_by_name("nonexistent") is None
        assert store.find_by_name("zeta") is None

    def test_list_sessions_status_filter(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        s1 = store.create_session("c", "r", ["p1"])
        s2 = store.create_session("c", "r", ["p2"])
        store.resume_session(s2.session_id)
        all_sessions = store.list_sessions()
        assert len(all_sessions) == 2
        active = store.list_sessions(status="active")
        assert len(active) == 1
        assert active[0].session_id == s2.session_id
        paused = store.list_sessions(status="paused")
        assert len(paused) == 1
        assert paused[0].session_id == s1.session_id

    def test_create_session_no_papers(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session(
            "corpus1",
            "recipe1",
            [],
            name="Empty Session",
            topic="T",
        )
        assert session.paper_queue == {}
        assert session.name == "Empty Session"
        loaded = store.load(session.session_id)
        assert loaded is not None
        assert loaded.paper_queue == {}

    def test_create_session_with_variant_metadata(self, tmp_path: Path) -> None:
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session(
            "corpus1",
            "recipe1",
            ["p1"],
            name="Variant Session",
            topic="Supply Chain",
            variant_label="empirical",
            target_count=15,
            metadata={"parent_session_id": "parent-123"},
        )
        assert session.variant_label == "empirical"
        assert session.metadata["parent_session_id"] == "parent-123"
        assert session.metadata["target_count"] == 15
        loaded = store.load(session.session_id)
        assert loaded is not None
        assert loaded.variant_label == "empirical"
        assert loaded.metadata["parent_session_id"] == "parent-123"


# ---------------------------------------------------------------------------
# TestIngredientLedger
# ---------------------------------------------------------------------------


class TestIngredientLedger:
    """Test IngredientLedger with tmp_path."""

    def test_add_ingredient(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        rec = IngredientRecord(paper_id="smith2020", title="A Study")
        ledger.add_ingredient(rec)
        got = ledger.get_ingredient("smith2020")
        assert got is not None
        assert got.title == "A Study"

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        assert ledger.get_ingredient("nonexistent") is None

    def test_list_ingredients_with_status_filter(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        ledger.add_ingredient(IngredientRecord(paper_id="p1", title="T1", status=IngredientStatus.ACTIVE))
        ledger.add_ingredient(IngredientRecord(paper_id="p2", title="T2", status=IngredientStatus.EXCLUDED))
        ledger.add_ingredient(IngredientRecord(paper_id="p3", title="T3", status=IngredientStatus.ACTIVE))
        active = ledger.list_ingredients(IngredientStatus.ACTIVE)
        assert len(active) == 2
        assert {r.paper_id for r in active} == {"p1", "p3"}
        all_items = ledger.list_ingredients()
        assert len(all_items) == 3

    def test_update_status(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        ledger.add_ingredient(IngredientRecord(paper_id="p1", title="T1"))
        assert ledger.update_status("p1", IngredientStatus.EXCLUDED, "off-topic")
        rec = ledger.get_ingredient("p1")
        assert rec.status == IngredientStatus.EXCLUDED
        assert rec.exclusion_reason == "off-topic"
        assert ledger.update_status("p99", IngredientStatus.ACTIVE) is False

    def test_mark_version(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        ledger.add_ingredient(IngredientRecord(paper_id="p1", title="T1"))
        assert ledger.mark_version("p1", "0.1.0")
        rec = ledger.get_ingredient("p1")
        assert "0.1.0" in rec.included_in_versions
        assert ledger.mark_version("p99", "0.1.0") is False

    def test_compute_version_diff(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        ledger.add_ingredient(IngredientRecord(paper_id="p1", title="T1"))
        ledger.add_ingredient(IngredientRecord(paper_id="p2", title="T2"))
        ledger.mark_version("p1", "0.1.0")
        ledger.mark_version("p1", "0.2.0")
        ledger.mark_version("p2", "0.2.0")
        diff = ledger.compute_version_diff("0.1.0", "0.2.0")
        assert diff.from_version == "0.1.0"
        assert diff.to_version == "0.2.0"
        assert "p2" in diff.added_ingredients
        assert "p1" not in diff.added_ingredients
        assert "p1" not in diff.removed_ingredients

    def test_human_readable_output(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        ledger.add_ingredient(
            IngredientRecord(
                paper_id="smith2020",
                title="A Paper",
                authors=["Smith", "Jones"],
                year=2020,
                source=AcquisitionSource.ARXIV,
                included_in_versions=["0.1.0"],
                inclusion_reason="core method",
            )
        )
        out = ledger.to_human_readable()
        assert "## Ingredients" in out
        assert "smith2020" in out
        assert "A Paper" in out
        assert "Smith" in out
        assert "arxiv" in out
        assert "0.1.0" in out
        assert "core method" in out

    def test_machine_manifest(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        ledger.add_ingredient(IngredientRecord(paper_id="p1", title="T1"))
        manifest = ledger.to_machine_manifest()
        assert manifest["corpus_id"] == "corpus1"
        assert manifest["ingredient_count"] == 1
        assert manifest["active_count"] == 1
        assert len(manifest["ingredients"]) == 1
        assert manifest["ingredients"][0]["paper_id"] == "p1"

    def test_delete_ledger(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        ledger.add_ingredient(IngredientRecord(paper_id="p1", title="T1"))
        path = tmp_path / "corpus1.json"
        assert path.exists()
        assert ledger.delete()
        assert not path.exists()
        assert ledger.delete() is False

    def test_list_ingredients_sorted_by_added_at(self, tmp_path: Path) -> None:
        ledger = IngredientLedger("corpus1", base_dir=tmp_path)
        ledger.add_ingredient(IngredientRecord(paper_id="p1", title="First"))
        ledger.add_ingredient(IngredientRecord(paper_id="p2", title="Second"))
        ledger.add_ingredient(IngredientRecord(paper_id="p3", title="Third"))
        items = ledger.list_ingredients()
        assert len(items) == 3
        assert {r.paper_id for r in items} == {"p1", "p2", "p3"}
        assert items[0].added_at <= items[1].added_at <= items[2].added_at


# ---------------------------------------------------------------------------
# TestCorpus
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_kernel(tmp_path: Path) -> MemoryKernel:
    """MemoryKernel with tmp_path for isolation."""
    return MemoryKernel(base_dir=str(tmp_path / "memory"))


@pytest.fixture
def corpus_writer(memory_kernel: MemoryKernel) -> CorpusWriter:
    return CorpusWriter(memory_kernel)


@pytest.fixture
def corpus_reader(memory_kernel: MemoryKernel) -> CorpusReader:
    return CorpusReader(memory_kernel)


class TestCorpus:
    """Test Corpus (writer, reader, promotion engine)."""

    def test_store_knowledge(
        self,
        corpus_writer: CorpusWriter,
        corpus_reader: CorpusReader,
    ) -> None:
        item = corpus_writer.store_knowledge(
            content="Use active voice.",
            knowledge_kind=KnowledgeKind.WRITING_RULE,
            corpus_id="corpus1",
            source_id="smith2020",
            confidence=0.8,
        )
        assert item.id
        assert item.content == "Use active voice."
        items = corpus_reader.query_by_corpus("corpus1")
        assert len(items) == 1
        assert items[0].metadata.get("corpus", {}).get("source_id") == "smith2020"

    def test_store_many(
        self,
        corpus_writer: CorpusWriter,
        corpus_reader: CorpusReader,
    ) -> None:
        items_data = [
            {"content": "Rule 1", "knowledge_kind": "writing_rule"},
            {"content": "Rule 2", "knowledge_kind": "anti_pattern"},
        ]
        results = corpus_writer.store_many(items_data, "corpus1", "p1")
        assert len(results) == 2
        items = corpus_reader.query_by_corpus("corpus1")
        assert len(items) == 2

    def test_query_by_corpus(
        self,
        corpus_writer: CorpusWriter,
        corpus_reader: CorpusReader,
    ) -> None:
        corpus_writer.store_knowledge("C1", KnowledgeKind.CLAIM, "corpus_a", "p1")
        corpus_writer.store_knowledge("C2", KnowledgeKind.CLAIM, "corpus_b", "p2")
        items_a = corpus_reader.query_by_corpus("corpus_a")
        items_b = corpus_reader.query_by_corpus("corpus_b")
        assert len(items_a) == 1
        assert len(items_b) == 1
        assert items_a[0].content == "C1"

    def test_query_by_paper(
        self,
        corpus_writer: CorpusWriter,
        corpus_reader: CorpusReader,
    ) -> None:
        corpus_writer.store_knowledge("K1", KnowledgeKind.CLAIM, "corpus1", "paper_a")
        corpus_writer.store_knowledge("K2", KnowledgeKind.METHOD, "corpus1", "paper_a")
        corpus_writer.store_knowledge("K3", KnowledgeKind.CLAIM, "corpus1", "paper_b")
        items = corpus_reader.query_by_paper("paper_a", "corpus1")
        assert len(items) == 2
        assert {i.content for i in items} == {"K1", "K2"}

    def test_get_domain_patterns(
        self,
        corpus_writer: CorpusWriter,
        corpus_reader: CorpusReader,
    ) -> None:
        corpus_writer.store_knowledge(
            "Domain pattern",
            KnowledgeKind.TERMINOLOGY,
            "corpus1",
            generality=Generality.DOMAIN,
        )
        corpus_writer.store_knowledge(
            "Paper fact",
            KnowledgeKind.CLAIM,
            "corpus1",
            generality=Generality.PAPER,
        )
        patterns = corpus_reader.get_domain_patterns("corpus1")
        assert len(patterns) == 1
        assert patterns[0].content == "Domain pattern"

    def test_retrieve_for_writing(
        self,
        corpus_writer: CorpusWriter,
        corpus_reader: CorpusReader,
    ) -> None:
        corpus_writer.store_knowledge("Rule", KnowledgeKind.WRITING_RULE, "corpus1", importance=0.9)
        items = corpus_reader.retrieve_for_writing("corpus1")
        assert len(items) >= 1
        assert items[0].content == "Rule"

    def test_promote_to_domain(
        self,
        corpus_writer: CorpusWriter,
        corpus_reader: CorpusReader,
    ) -> None:
        content = "recurring pattern in papers"
        for i in range(3):
            corpus_writer.store_knowledge(
                content,
                KnowledgeKind.TERMINOLOGY,
                "corpus1",
                source_id=f"p{i}",
                importance=0.5 + i * 0.1,
            )
        engine = PromotionEngine(corpus_reader, corpus_writer)
        promoted = engine.promote_to_domain("corpus1")
        assert len(promoted) >= 1
        domain_items = corpus_reader.get_domain_patterns("corpus1")
        assert len(domain_items) >= 1

    def test_promote_to_recipe(
        self,
        corpus_writer: CorpusWriter,
        corpus_reader: CorpusReader,
    ) -> None:
        corpus_writer.store_knowledge(
            "Strong recipe rule",
            KnowledgeKind.WRITING_RULE,
            "corpus1",
            generality=Generality.DOMAIN,
            confidence=0.85,
        )
        engine = PromotionEngine(corpus_reader, corpus_writer)
        promoted = engine.promote_to_recipe("corpus1")
        assert len(promoted) >= 1
        recipe_items = corpus_reader.get_recipe_knowledge("corpus1")
        assert len(recipe_items) >= 1


# ---------------------------------------------------------------------------
# TestRecipeCompiler
# ---------------------------------------------------------------------------


@pytest.fixture
def compiler_fixture(tmp_path: Path) -> tuple[RecipeCompiler, CorpusReader, IngredientLedger]:
    """Set up MemoryKernel, Corpus, IngredientLedger, and RecipeCompiler with test data."""
    kernel = MemoryKernel(base_dir=str(tmp_path / "memory"))
    writer = CorpusWriter(kernel)
    reader = CorpusReader(kernel)
    ledger = IngredientLedger("corpus1", base_dir=tmp_path / "ledgers")

    writer.store_knowledge(
        "Use active voice.",
        KnowledgeKind.WRITING_RULE,
        "corpus1",
        source_id="p1",
        generality="domain",
    )
    writer.store_knowledge(
        "Avoid passive constructions.",
        KnowledgeKind.ANTI_PATTERN,
        "corpus1",
        source_id="p1",
    )
    writer.store_knowledge(
        "Supply chain optimization",
        KnowledgeKind.TERMINOLOGY,
        "corpus1",
        source_id="p1",
    )

    ledger.add_ingredient(
        IngredientRecord(
            paper_id="smith2020",
            title="Supply Chain Methods",
            authors=["Smith"],
            year=2020,
            included_in_versions=["0.1.0"],
        )
    )

    compiler = RecipeCompiler("corpus1", "recipe1", reader, ledger)
    return compiler, reader, ledger


class TestRecipeCompiler:
    """Test RecipeCompiler."""

    def test_compile_recipe_md_basic(self, compiler_fixture: tuple) -> None:
        compiler, _, _ = compiler_fixture
        md = compiler.compile_recipe_md(version="0.1.0")
        assert "recipe_id: recipe1" in md
        assert "corpus_id: corpus1" in md
        assert "version: 0.1.0" in md
        assert "## Ingredients" in md
        assert "## Domain Thesis" in md
        assert "## Core Concepts" in md
        assert "## Writing Rules" in md
        assert "## Anti-Patterns" in md

    def test_compile_recipe_md_with_session(
        self,
        compiler_fixture: tuple,
        tmp_path: Path,
    ) -> None:
        compiler, _, _ = compiler_fixture
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("corpus1", "recipe1", ["p1"])
        store.add_checkpoint(
            session.session_id,
            BatchCheckpoint(
                batch_index=0,
                recipe_version="0.1.0",
                phase_completed=FurnacePhase.NORMALIZE,
                paper_ids=["p1"],
                token_usage=500,
                cost_usd=0.005,
                changes_from_previous="Initial batch",
            ),
        )
        session = store.load(session.session_id)
        md = compiler.compile_recipe_md(session=session, version="0.1.0")
        assert "Training History" in md
        assert "Checkpoints" in md
        assert "Batch 0" in md
        assert "500" in md
        assert "Initial batch" in md

    def test_compile_skill_md(self, compiler_fixture: tuple) -> None:
        compiler, _, _ = compiler_fixture
        md = compiler.compile_skill_md(version="0.1.0")
        assert "skill_id: recipe1-skill" in md
        assert "## Writing Rules" in md
        assert "## Anti-Patterns" in md
        assert "## Core Concepts" in md
        assert "Use active voice" in md
        assert "Avoid passive constructions" in md
        assert "Supply chain optimization" in md

    def test_frontmatter_format(self, compiler_fixture: tuple) -> None:
        compiler, _, _ = compiler_fixture
        md = compiler.compile_recipe_md(version="1.2.3")
        lines = md.split("\n")
        assert lines[0] == "---"
        assert "recipe_id: recipe1" in md
        assert "version: 1.2.3" in md
        assert "ingredient_count:" in md
        assert "created_at:" in md
        assert "---" in md

    def test_bump_version_patch(self, compiler_fixture: tuple) -> None:
        compiler, _, _ = compiler_fixture
        assert compiler.bump_version("1.2.3", "patch") == "1.2.4"
        assert compiler.bump_version("0.0.9", "patch") == "0.0.10"

    def test_bump_version_minor(self, compiler_fixture: tuple) -> None:
        compiler, _, _ = compiler_fixture
        assert compiler.bump_version("1.2.3", "minor") == "1.3.0"
        assert compiler.bump_version("0.9.0", "minor") == "0.10.0"

    def test_bump_version_major(self, compiler_fixture: tuple) -> None:
        compiler, _, _ = compiler_fixture
        assert compiler.bump_version("1.2.3", "major") == "2.0.0"
        assert compiler.bump_version("0.1.0", "major") == "1.0.0"

    def test_create_recipe_version(
        self,
        compiler_fixture: tuple,
        tmp_path: Path,
    ) -> None:
        compiler, _, ledger = compiler_fixture
        store = FurnaceSessionStore(base_dir=tmp_path)
        session = store.create_session("corpus1", "recipe1", ["p1"])
        rv = compiler.create_recipe_version(
            "0.1.0",
            session=session,
            checkpoint_id="cp1",
            benchmark_results={"accuracy": 0.95},
        )
        assert rv.version == "0.1.0"
        assert rv.recipe_id == "recipe1"
        assert rv.corpus_id == "corpus1"
        assert rv.checkpoint_id == "cp1"
        assert rv.ingredient_count == 1
        assert rv.benchmark_results == {"accuracy": 0.95}

    def test_change_log_includes_version_diffs(
        self,
        compiler_fixture: tuple,
        tmp_path: Path,
    ) -> None:
        compiler, _, ledger = compiler_fixture
        ledger.add_ingredient(IngredientRecord(paper_id="p2", title="New Paper"))
        ledger.mark_version("smith2020", "0.1.0")
        ledger.mark_version("p2", "0.2.0")
        ledger.mark_version("smith2020", "0.2.0")
        ledger.compute_version_diff("0.1.0", "0.2.0")
        md = compiler.compile_recipe_md(version="0.2.0")
        assert "## Change Log" in md
        assert "0.1.0 → 0.2.0" in md
        assert "p2" in md or "Added" in md
