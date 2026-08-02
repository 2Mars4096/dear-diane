"""Comprehensive tests for the RAG subsystem: embedding, vector stores, executor, indexer."""

from __future__ import annotations

import math
import platform
import pytest

from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.events import EngineEvent
from dan.engine.state import ExecutionState, NodeStatus
from dan.models.graph import Graph
from dan.models.nodes import RAGOperator
from dan.models.ports import InputPort, OutputPort
from dan.providers import CompletionResult
from dan.rag import (
    EmbeddingProvider,
    EmbeddingRegistry,
    EmbeddingResult,
)
from dan.rag.stores import (
    DocumentRecord,
    QueryResult,
    VectorStore,
    VectorStoreConfig,
    VectorStoreFactory,
)
from dan.rag.stores.memory import MemoryVectorStore, _cosine_similarity
from dan.rag.indexer import Indexer
from dan.executors.rag import RAGExecutor

try:
    from dan.rag.stores.faiss_store import FAISSVectorStore, HAS_FAISS
except ImportError:
    HAS_FAISS = False

try:
    from dan.rag.stores.chroma_store import ChromaVectorStore, HAS_CHROMA
except ImportError:
    HAS_CHROMA = False


_FAISS_SUITE_STABLE = not (
    HAS_FAISS and platform.system() == "Darwin" and platform.machine() == "arm64"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockEmbeddingProvider:
    """Deterministic embedding provider for tests.

    Maps each text to a fixed-dimension vector based on hash.
    """

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    async def embed(self, texts: list[str], model: str = "") -> EmbeddingResult:
        vectors = []
        for text in texts:
            h = hash(text) & 0xFFFFFFFF
            raw = [(h >> (i * 4) & 0xF) / 15.0 for i in range(self._dim)]
            norm = math.sqrt(sum(x * x for x in raw)) or 1.0
            vectors.append([x / norm for x in raw])
        return EmbeddingResult(
            vectors=vectors,
            model=model or "mock",
            dimensions=self._dim,
        )


class FailingEmbeddingProvider:
    """Provider that always raises."""

    async def embed(self, texts: list[str], model: str = "") -> EmbeddingResult:
        raise RuntimeError("Embedding service unavailable")


class _FakeCompletionProvider:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        messages: list[dict[str, object]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: object,
    ) -> CompletionResult:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )
        return CompletionResult(text=self.response_text, model=model)


class _FakeModelGateway:
    def __init__(self, provider: _FakeCompletionProvider) -> None:
        self.provider = provider
        self.calls: list[str] = []

    async def complete(
        self,
        messages: list[dict[str, object]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: object,
    ) -> CompletionResult:
        self.calls.append(model)
        return await self.provider.complete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    def resolve(self, model: str) -> _FakeCompletionProvider:
        self.calls.append(model)
        return self.provider


class _FakeQueryStore:
    def __init__(self, chunks: list[dict[str, object]]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    async def query(self, collection: str, vector: list[float], top_k: int) -> QueryResult:
        self.calls.append(
            {
                "collection": collection,
                "vector": vector,
                "top_k": top_k,
            }
        )
        return QueryResult(chunks=self.chunks)


def _make_context(
    event_log: list[EngineEvent] | None = None,
    embedding_registry: EmbeddingRegistry | None = None,
    config: EngineConfig | None = None,
    provider_registry: object | None = None,
    model_gateway: object | None = None,
) -> ExecutionContext:
    graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
    state = ExecutionState(graph)
    captured = event_log if event_log is not None else []

    async def capture_event(event: EngineEvent) -> None:
        captured.append(event)

    ctx = ExecutionContext(
        state=state,
        config=config or EngineConfig(checkpoint_enabled=False),
        shared_context=SharedContextStore([]),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        event_callback=capture_event,
        run_id="test-run",
    )
    if embedding_registry is not None:
        ctx.embedding_registry = embedding_registry  # type: ignore[attr-defined]
    if provider_registry is not None:
        ctx.provider_registry = provider_registry  # type: ignore[attr-defined]
    if model_gateway is not None:
        ctx.model_gateway = model_gateway  # type: ignore[attr-defined]
    return ctx


def _make_rag_node(
    node_id: str = "rag1",
    collection: str = "docs",
    top_k: int = 3,
    similarity_threshold: float | None = None,
    rerank: bool = False,
    embedding_model: str = "",
    vector_store_config: dict | None = None,
    query_template: str = "{query}",
    include_metadata: bool = True,
) -> RAGOperator:
    return RAGOperator(
        id=node_id,
        name=node_id,
        collection=collection,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
        rerank=rerank,
        embedding_model=embedding_model,
        vector_store_config=vector_store_config or {},
        query_template=query_template,
        include_metadata=include_metadata,
        input_ports=[InputPort(name="query")],
        output_ports=[OutputPort(name="chunks"), OutputPort(name="scores")],
    )


# ===========================================================================
# EmbeddingProvider tests
# ===========================================================================


class TestEmbeddingProvider:
    @pytest.mark.asyncio
    async def test_mock_provider_returns_vectors(self):
        provider = MockEmbeddingProvider(dim=4)
        result = await provider.embed(["hello", "world"], model="test")
        assert len(result.vectors) == 2
        assert result.dimensions == 4
        assert result.model == "test"
        for vec in result.vectors:
            assert len(vec) == 4

    @pytest.mark.asyncio
    async def test_mock_provider_deterministic(self):
        provider = MockEmbeddingProvider()
        r1 = await provider.embed(["hello"], model="test")
        r2 = await provider.embed(["hello"], model="test")
        assert r1.vectors[0] == r2.vectors[0]

    @pytest.mark.asyncio
    async def test_mock_provider_different_texts_different_vectors(self):
        provider = MockEmbeddingProvider()
        result = await provider.embed(["cat", "dog"], model="test")
        assert result.vectors[0] != result.vectors[1]

    @pytest.mark.asyncio
    async def test_batch_embedding(self):
        provider = MockEmbeddingProvider()
        texts = [f"doc_{i}" for i in range(20)]
        result = await provider.embed(texts, model="test")
        assert len(result.vectors) == 20

    @pytest.mark.asyncio
    async def test_failing_provider(self):
        provider = FailingEmbeddingProvider()
        with pytest.raises(RuntimeError, match="unavailable"):
            await provider.embed(["test"], model="test")

    def test_protocol_check(self):
        assert isinstance(MockEmbeddingProvider(), EmbeddingProvider)


# ===========================================================================
# EmbeddingRegistry tests
# ===========================================================================


class TestEmbeddingRegistry:
    def test_register_and_resolve_default(self):
        reg = EmbeddingRegistry()
        provider = MockEmbeddingProvider()
        reg.register("default", provider)
        assert reg.resolve("anything") is provider

    def test_exact_override(self):
        reg = EmbeddingRegistry()
        p1 = MockEmbeddingProvider(dim=4)
        p2 = MockEmbeddingProvider(dim=8)
        reg.register("openai", p1)
        reg.register("local", p2)
        reg.set_model_override("my-custom-model", "local")
        assert reg.resolve("my-custom-model") is p2

    def test_prefix_match(self):
        reg = EmbeddingRegistry()
        p = MockEmbeddingProvider()
        reg.register("openai", p)
        assert reg.resolve("text-embedding-3-small") is p

    def test_no_provider_raises(self):
        reg = EmbeddingRegistry()
        with pytest.raises(KeyError, match="No embedding provider"):
            reg.resolve("unknown-model")

    def test_has_provider(self):
        reg = EmbeddingRegistry()
        assert not reg.has_provider("openai")
        reg.register("openai", MockEmbeddingProvider())
        assert reg.has_provider("openai")

    def test_provider_names(self):
        reg = EmbeddingRegistry()
        reg.register("b", MockEmbeddingProvider())
        reg.register("a", MockEmbeddingProvider())
        assert reg.provider_names() == ["a", "b"]


class TestOptionBEmbeddingConfig:
    def test_engine_builds_embedding_registry_from_config(self, monkeypatch):
        import dan.rag as rag_mod
        from dan.engine.scheduler import Engine
        from dan.providers import ProviderConfig

        provider = MockEmbeddingProvider()

        def _fake_create(name, pconfig, *args, **kwargs):  # noqa: ANN001
            return provider if name == "default" else None

        monkeypatch.setattr(rag_mod, "_create_embedding_provider", _fake_create)

        engine = Engine(EngineConfig(
            checkpoint_enabled=False,
            embedding_providers={"default": ProviderConfig(api_key="test")},
            default_embedding_model="text-embedding-3-small",
        ))

        resolved = engine.embedding_registry.resolve("text-embedding-3-small")
        assert resolved is provider

    def test_execution_context_carries_embedding_registry(self, monkeypatch):
        import dan.rag as rag_mod
        from dan.engine.scheduler import Engine
        from dan.providers import ProviderConfig

        provider = MockEmbeddingProvider()

        def _fake_create(name, pconfig, *args, **kwargs):  # noqa: ANN001
            return provider if name == "default" else None

        monkeypatch.setattr(rag_mod, "_create_embedding_provider", _fake_create)

        engine = Engine(EngineConfig(
            checkpoint_enabled=False,
            embedding_providers={"default": ProviderConfig(api_key="test")},
        ))
        graph = Graph(nodes=[], edges=[], entry_points=[], exit_points=[])
        state = ExecutionState(graph)
        ctx = engine._make_context(
            state,
            SharedContextStore([]),
            ArtifactStore(),
            LocalStateManager(),
            graph,
        )
        assert ctx.embedding_registry is engine.embedding_registry


# ===========================================================================
# MemoryVectorStore tests
# ===========================================================================


class TestMemoryVectorStore:
    @pytest.mark.asyncio
    async def test_create_and_list_collections(self):
        store = MemoryVectorStore()
        await store.create_collection("test", 4)
        assert "test" in await store.list_collections()

    @pytest.mark.asyncio
    async def test_delete_collection(self):
        store = MemoryVectorStore()
        await store.create_collection("test", 4)
        await store.delete_collection("test")
        assert "test" not in await store.list_collections()

    @pytest.mark.asyncio
    async def test_add_and_count(self):
        store = MemoryVectorStore()
        records = [
            DocumentRecord(id="1", text="hello", embedding=[1, 0, 0, 0]),
            DocumentRecord(id="2", text="world", embedding=[0, 1, 0, 0]),
        ]
        await store.add("col", records)
        assert await store.count("col") == 2

    @pytest.mark.asyncio
    async def test_query_returns_sorted_by_score(self):
        store = MemoryVectorStore()
        await store.add("col", [
            DocumentRecord(id="a", text="alpha", embedding=[1, 0, 0]),
            DocumentRecord(id="b", text="beta", embedding=[0.9, 0.1, 0]),
            DocumentRecord(id="c", text="gamma", embedding=[0, 0, 1]),
        ])
        result = await store.query("col", [1, 0, 0], top_k=3)
        assert len(result.chunks) == 3
        assert result.chunks[0]["id"] == "a"
        assert result.chunks[0]["score"] > result.chunks[1]["score"]

    @pytest.mark.asyncio
    async def test_query_with_top_k(self):
        store = MemoryVectorStore()
        await store.add("col", [
            DocumentRecord(id=str(i), text=f"doc{i}", embedding=[float(i), 0, 0])
            for i in range(10)
        ])
        result = await store.query("col", [1, 0, 0], top_k=3)
        assert len(result.chunks) == 3

    @pytest.mark.asyncio
    async def test_query_with_filters(self):
        store = MemoryVectorStore()
        await store.add("col", [
            DocumentRecord(id="1", text="a", embedding=[1, 0], metadata={"type": "A"}),
            DocumentRecord(id="2", text="b", embedding=[1, 0], metadata={"type": "B"}),
            DocumentRecord(id="3", text="c", embedding=[1, 0], metadata={"type": "A"}),
        ])
        result = await store.query("col", [1, 0], top_k=10, filters={"type": "A"})
        assert len(result.chunks) == 2
        assert all(c["metadata"]["type"] == "A" for c in result.chunks)

    @pytest.mark.asyncio
    async def test_delete_by_ids(self):
        store = MemoryVectorStore()
        await store.add("col", [
            DocumentRecord(id="1", text="a", embedding=[1, 0]),
            DocumentRecord(id="2", text="b", embedding=[0, 1]),
        ])
        await store.delete_by_ids("col", ["1"])
        assert await store.count("col") == 1

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing(self):
        store = MemoryVectorStore()
        await store.add("col", [
            DocumentRecord(id="1", text="old", embedding=[1, 0]),
        ])
        await store.add("col", [
            DocumentRecord(id="1", text="new", embedding=[0, 1]),
        ])
        assert await store.count("col") == 1
        result = await store.query("col", [0, 1], top_k=1)
        assert result.chunks[0]["text"] == "new"

    @pytest.mark.asyncio
    async def test_query_empty_collection(self):
        store = MemoryVectorStore()
        await store.create_collection("empty", 4)
        result = await store.query("empty", [1, 0, 0, 0])
        assert result.chunks == []

    @pytest.mark.asyncio
    async def test_query_nonexistent_collection(self):
        store = MemoryVectorStore()
        result = await store.query("nope", [1, 0, 0])
        assert result.chunks == []

    def test_cosine_similarity_identical(self):
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self):
        assert _cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_cosine_similarity_opposite(self):
        assert _cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1.0)

    def test_cosine_similarity_zero_vector(self):
        assert _cosine_similarity([0, 0, 0], [1, 0, 0]) == 0.0

    def test_cosine_similarity_dimension_mismatch(self):
        assert _cosine_similarity([1, 0], [1, 0, 0]) == 0.0

    def test_protocol_check(self):
        assert isinstance(MemoryVectorStore(), VectorStore)


# ===========================================================================
# FAISSVectorStore tests
# ===========================================================================


@pytest.mark.skipif(
    not HAS_FAISS or not _FAISS_SUITE_STABLE,
    reason="faiss backend unavailable or unstable on this platform",
)
class TestFAISSVectorStore:
    @pytest.mark.asyncio
    async def test_add_and_query(self):
        store = FAISSVectorStore()
        await store.add("col", [
            DocumentRecord(id="a", text="alpha", embedding=[1, 0, 0]),
            DocumentRecord(id="b", text="beta", embedding=[0, 1, 0]),
        ])
        result = await store.query("col", [1, 0, 0], top_k=2)
        assert len(result.chunks) >= 1
        assert result.chunks[0]["id"] == "a"

    @pytest.mark.asyncio
    async def test_count(self):
        store = FAISSVectorStore()
        await store.add("col", [
            DocumentRecord(id="1", text="a", embedding=[1, 0]),
            DocumentRecord(id="2", text="b", embedding=[0, 1]),
        ])
        assert await store.count("col") == 2

    @pytest.mark.asyncio
    async def test_delete_by_ids(self):
        store = FAISSVectorStore()
        await store.add("col", [
            DocumentRecord(id="1", text="a", embedding=[1, 0]),
            DocumentRecord(id="2", text="b", embedding=[0, 1]),
        ])
        await store.delete_by_ids("col", ["1"])
        assert await store.count("col") == 1

    @pytest.mark.asyncio
    async def test_metadata_filtering(self):
        store = FAISSVectorStore()
        await store.add("col", [
            DocumentRecord(id="1", text="a", embedding=[1, 0, 0], metadata={"type": "A"}),
            DocumentRecord(id="2", text="b", embedding=[1, 0, 0], metadata={"type": "B"}),
            DocumentRecord(id="3", text="c", embedding=[1, 0, 0], metadata={"type": "A"}),
        ])
        result = await store.query("col", [1, 0, 0], top_k=10, filters={"type": "A"})
        assert all(c["metadata"]["type"] == "A" for c in result.chunks)

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        store = FAISSVectorStore(persist_directory=str(tmp_path))
        await store.add("col", [
            DocumentRecord(id="1", text="hello", embedding=[1, 0, 0, 0]),
        ])
        store.save()

        store2 = FAISSVectorStore(persist_directory=str(tmp_path))
        store2.load()
        assert await store2.count("col") == 1
        result = await store2.query("col", [1, 0, 0, 0], top_k=1)
        assert result.chunks[0]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_delete_collection(self):
        store = FAISSVectorStore()
        await store.create_collection("test", 4)
        await store.delete_collection("test")
        assert "test" not in await store.list_collections()

    def test_protocol_check(self):
        assert isinstance(FAISSVectorStore(), VectorStore)


# ===========================================================================
# ChromaVectorStore tests
# ===========================================================================


@pytest.mark.skipif(not HAS_CHROMA, reason="chromadb not installed")
class TestChromaVectorStore:
    @pytest.mark.asyncio
    async def test_add_and_query(self):
        store = ChromaVectorStore()
        await store.add("col", [
            DocumentRecord(id="a", text="alpha", embedding=[1, 0, 0], metadata={"source": "test"}),
            DocumentRecord(id="b", text="beta", embedding=[0, 1, 0], metadata={"source": "test"}),
        ])
        result = await store.query("col", [1, 0, 0], top_k=2)
        assert len(result.chunks) >= 1

    @pytest.mark.asyncio
    async def test_count(self):
        store = ChromaVectorStore()
        await store.add("col_count", [
            DocumentRecord(id="1", text="a", embedding=[1, 0], metadata={"source": "test"}),
            DocumentRecord(id="2", text="b", embedding=[0, 1], metadata={"source": "test"}),
        ])
        assert await store.count("col_count") == 2

    @pytest.mark.asyncio
    async def test_delete_by_ids(self):
        store = ChromaVectorStore()
        await store.add("col_del", [
            DocumentRecord(id="1", text="a", embedding=[1, 0], metadata={"source": "test"}),
            DocumentRecord(id="2", text="b", embedding=[0, 1], metadata={"source": "test"}),
        ])
        await store.delete_by_ids("col_del", ["1"])
        assert await store.count("col_del") == 1

    @pytest.mark.asyncio
    async def test_metadata_where_filter(self):
        store = ChromaVectorStore()
        await store.add("col_filter", [
            DocumentRecord(id="1", text="a", embedding=[1, 0, 0], metadata={"type": "A"}),
            DocumentRecord(id="2", text="b", embedding=[1, 0, 0], metadata={"type": "B"}),
        ])
        result = await store.query(
            "col_filter", [1, 0, 0], top_k=10,
            filters={"type": {"$eq": "A"}},
        )
        assert all(c["metadata"]["type"] == "A" for c in result.chunks)

    @pytest.mark.asyncio
    async def test_delete_collection(self):
        store = ChromaVectorStore()
        await store.create_collection("to_delete", 4)
        await store.delete_collection("to_delete")
        assert "to_delete" not in await store.list_collections()

    def test_protocol_check(self):
        assert isinstance(ChromaVectorStore(), VectorStore)


# ===========================================================================
# VectorStoreFactory tests
# ===========================================================================


class TestVectorStoreFactory:
    def test_memory_backend(self):
        store = VectorStoreFactory.create(VectorStoreConfig(backend="memory"))
        assert isinstance(store, MemoryVectorStore)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown vector store backend"):
            VectorStoreFactory.create(VectorStoreConfig(backend="unknown"))


# ===========================================================================
# RAGExecutor tests
# ===========================================================================


class TestRAGExecutor:
    @pytest.mark.asyncio
    async def test_basic_query_flow(self):
        """End-to-end: embed query → search memory store → return chunks."""
        provider = MockEmbeddingProvider(dim=4)
        store = MemoryVectorStore()

        embed_result = await provider.embed(["hello world", "foo bar", "test doc"])
        await store.add("docs", [
            DocumentRecord(id="1", text="hello world", embedding=embed_result.vectors[0]),
            DocumentRecord(id="2", text="foo bar", embedding=embed_result.vectors[1]),
            DocumentRecord(id="3", text="test doc", embedding=embed_result.vectors[2]),
        ])

        node = _make_rag_node(
            collection="docs",
            top_k=2,
            vector_store_config={"backend": "memory", "embedding_provider": provider},
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache[f"memory::docs"] = store

        events: list[EngineEvent] = []
        ctx = _make_context(event_log=events)

        result = await executor.execute(node, {"query": "hello world"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        assert "chunks" in result.outputs
        assert "scores" in result.outputs
        assert len(result.outputs["chunks"]) <= 2

        event_types = [e.event_type.value for e in events]
        assert "retrieval_started" in event_types
        assert "retrieval_completed" in event_types

        _store_cache.pop(f"memory::docs", None)

    @pytest.mark.asyncio
    async def test_similarity_threshold_filtering(self):
        provider = MockEmbeddingProvider(dim=4)
        store = MemoryVectorStore()

        # Add one very similar and one dissimilar doc
        embed_result = await provider.embed(["target query"])
        similar_vec = embed_result.vectors[0]
        dissimilar_vec = [-x for x in similar_vec]

        await store.add("docs", [
            DocumentRecord(id="1", text="similar", embedding=similar_vec),
            DocumentRecord(id="2", text="dissimilar", embedding=dissimilar_vec),
        ])

        node = _make_rag_node(
            collection="docs",
            top_k=10,
            similarity_threshold=0.5,
            vector_store_config={"backend": "memory", "embedding_provider": provider},
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache[f"memory::docs"] = store

        ctx = _make_context()
        result = await executor.execute(node, {"query": "target query"}, ctx)

        assert result.status == NodeStatus.COMPLETED
        for chunk in result.outputs["chunks"]:
            assert chunk["score"] >= 0.5

        _store_cache.pop(f"memory::docs", None)

    @pytest.mark.asyncio
    async def test_no_embedding_provider_fails(self):
        node = _make_rag_node()
        executor = RAGExecutor()
        ctx = _make_context()
        result = await executor.execute(node, {"query": "test"}, ctx)
        assert result.status == NodeStatus.FAILED
        assert "No embedding provider" in (result.error or "")

    @pytest.mark.asyncio
    async def test_embedding_error_fails(self):
        node = _make_rag_node(
            vector_store_config={"embedding_provider": FailingEmbeddingProvider()},
        )
        executor = RAGExecutor()
        ctx = _make_context()
        result = await executor.execute(node, {"query": "test"}, ctx)
        assert result.status == NodeStatus.FAILED
        assert "Embedding failed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_query_template_rendering(self):
        provider = MockEmbeddingProvider(dim=4)
        store = MemoryVectorStore()

        embed_result = await provider.embed(["specific query about AI"])
        await store.add("docs", [
            DocumentRecord(id="1", text="AI doc", embedding=embed_result.vectors[0]),
        ])

        node = _make_rag_node(
            collection="docs",
            query_template="Find documents about {topic} in {domain}",
            vector_store_config={"backend": "memory", "embedding_provider": provider},
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache["memory::docs"] = store

        ctx = _make_context()
        result = await executor.execute(
            node, {"topic": "AI", "domain": "research"}, ctx,
        )
        assert result.status == NodeStatus.COMPLETED

        _store_cache.pop("memory::docs", None)

    @pytest.mark.asyncio
    async def test_query_template_accepts_legacy_double_braces(self):
        provider = MockEmbeddingProvider(dim=4)
        store = MemoryVectorStore()

        embed_result = await provider.embed(["Find documents about AI in research"])
        await store.add("docs", [
            DocumentRecord(id="1", text="AI doc", embedding=embed_result.vectors[0]),
        ])

        node = _make_rag_node(
            collection="docs",
            query_template="Find documents about {{topic}} in {domain}",
            vector_store_config={"backend": "memory", "embedding_provider": provider},
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache["memory::docs"] = store

        events: list[EngineEvent] = []
        ctx = _make_context(event_log=events)
        result = await executor.execute(
            node, {"topic": "AI", "domain": "research"}, ctx,
        )

        assert result.status == NodeStatus.COMPLETED
        retrieval_started = next(
            e for e in events if e.event_type.value == "retrieval_started"
        )
        assert retrieval_started.data["query_preview"] == "Find documents about AI in research"

        _store_cache.pop("memory::docs", None)

    @pytest.mark.asyncio
    async def test_include_metadata_false(self):
        provider = MockEmbeddingProvider(dim=4)
        store = MemoryVectorStore()

        embed_result = await provider.embed(["test"])
        await store.add("docs", [
            DocumentRecord(
                id="1", text="test", embedding=embed_result.vectors[0],
                metadata={"source": "file.txt"},
            ),
        ])

        node = _make_rag_node(
            collection="docs",
            include_metadata=False,
            vector_store_config={"backend": "memory", "embedding_provider": provider},
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache["memory::docs"] = store

        ctx = _make_context()
        result = await executor.execute(node, {"query": "test"}, ctx)
        assert result.status == NodeStatus.COMPLETED
        for chunk in result.outputs["chunks"]:
            assert "metadata" not in chunk

        _store_cache.pop("memory::docs", None)

    @pytest.mark.asyncio
    async def test_embedding_registry_resolution(self):
        provider = MockEmbeddingProvider(dim=4)
        store = MemoryVectorStore()

        embed_result = await provider.embed(["test"])
        await store.add("docs", [
            DocumentRecord(id="1", text="test", embedding=embed_result.vectors[0]),
        ])

        reg = EmbeddingRegistry()
        reg.register("default", provider)

        node = _make_rag_node(
            collection="docs",
            embedding_model="text-embedding-3-small",
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache["memory::docs"] = store

        ctx = _make_context(embedding_registry=reg)
        result = await executor.execute(node, {"query": "test"}, ctx)
        assert result.status == NodeStatus.COMPLETED

        _store_cache.pop("memory::docs", None)

    @pytest.mark.asyncio
    async def test_uses_default_embedding_model_from_engine_config(self):
        provider = MockEmbeddingProvider(dim=4)
        store = MemoryVectorStore()

        embed_result = await provider.embed(["test"], model="custom-embed-v1")
        await store.add("docs", [
            DocumentRecord(id="1", text="test", embedding=embed_result.vectors[0]),
        ])

        reg = EmbeddingRegistry()
        reg.register("default", provider)

        node = _make_rag_node(
            collection="docs",
            embedding_model="",
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache["memory::docs"] = store

        ctx = _make_context(
            embedding_registry=reg,
            config=EngineConfig(
                checkpoint_enabled=False,
                default_embedding_model="custom-embed-v1",
            ),
        )
        result = await executor.execute(node, {"query": "test"}, ctx)
        assert result.status == NodeStatus.COMPLETED
        assert result.metadata.get("embedding_model") == "custom-embed-v1"

        _store_cache.pop("memory::docs", None)

    @pytest.mark.asyncio
    async def test_rerank_uses_shared_completion_provider_gateway(self):
        provider = MockEmbeddingProvider(dim=4)
        completion_provider = _FakeCompletionProvider(
            '[{"index": 2, "score": 0.95}, {"index": 0, "score": 0.75}, {"index": 1, "score": 0.25}]'
        )
        gateway = _FakeModelGateway(completion_provider)
        store = _FakeQueryStore(
            chunks=[
                {"text": "chunk-0", "score": 0.1},
                {"text": "chunk-1", "score": 0.2},
                {"text": "chunk-2", "score": 0.3},
            ]
        )

        node = _make_rag_node(
            collection="docs",
            top_k=2,
            rerank=True,
            vector_store_config={"backend": "memory", "embedding_provider": provider},
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache["memory::docs"] = store

        ctx = _make_context(
            config=EngineConfig(
                checkpoint_enabled=False,
                llm_default_model="gpt-4o-mini",
            ),
            model_gateway=gateway,
        )

        try:
            result = await executor.execute(node, {"query": "test"}, ctx)
        finally:
            _store_cache.pop("memory::docs", None)

        assert result.status == NodeStatus.COMPLETED
        assert [chunk["text"] for chunk in result.outputs["chunks"]] == [
            "chunk-2",
            "chunk-0",
        ]
        assert gateway.calls == ["gpt-4o-mini"]
        assert completion_provider.calls and completion_provider.calls[0]["model"] == "gpt-4o-mini"
        assert store.calls and store.calls[0]["top_k"] == 6

    @pytest.mark.asyncio
    async def test_rerank_degrades_gracefully_without_completion_provider(self):
        provider = MockEmbeddingProvider(dim=4)
        store = _FakeQueryStore(
            chunks=[
                {"text": "chunk-0", "score": 0.1},
                {"text": "chunk-1", "score": 0.2},
                {"text": "chunk-2", "score": 0.3},
            ]
        )

        node = _make_rag_node(
            collection="docs",
            top_k=2,
            rerank=True,
            vector_store_config={"backend": "memory", "embedding_provider": provider},
        )
        executor = RAGExecutor()

        from dan.executors.rag import _store_cache
        _store_cache["memory::docs"] = store

        ctx = _make_context(
            config=EngineConfig(checkpoint_enabled=False, llm_default_model="gpt-4o-mini"),
        )

        try:
            result = await executor.execute(node, {"query": "test"}, ctx)
        finally:
            _store_cache.pop("memory::docs", None)

        assert result.status == NodeStatus.COMPLETED
        assert [chunk["text"] for chunk in result.outputs["chunks"]] == [
            "chunk-0",
            "chunk-1",
        ]
        assert store.calls and store.calls[0]["top_k"] == 6


# ===========================================================================
# Indexer tests
# ===========================================================================


class TestIndexer:
    @pytest.mark.asyncio
    async def test_create_index_with_chunking(self):
        provider = MockEmbeddingProvider(dim=8)
        store = MemoryVectorStore()
        indexer = Indexer(embedding_provider=provider, store=store)

        text = "A" * 500 + " " + "B" * 500
        stats = await indexer.create_index(
            "test_idx",
            documents=[{"id": "doc1", "text": text}],
            chunking_config={"chunk_size": 300, "overlap": 50},
        )

        assert stats["name"] == "test_idx"
        assert stats["count"] == 1
        assert stats["chunks"] > 1
        assert stats["dimensions"] == 8
        assert await store.count("test_idx") == stats["chunks"]

    @pytest.mark.asyncio
    async def test_create_index_empty_documents(self):
        provider = MockEmbeddingProvider()
        store = MemoryVectorStore()
        indexer = Indexer(embedding_provider=provider, store=store)

        stats = await indexer.create_index("empty", documents=[])
        assert stats["count"] == 0
        assert stats["chunks"] == 0

    @pytest.mark.asyncio
    async def test_delete_index(self):
        provider = MockEmbeddingProvider()
        store = MemoryVectorStore()
        indexer = Indexer(embedding_provider=provider, store=store)

        await indexer.create_index(
            "to_del", documents=[{"text": "some text"}],
        )
        assert "to_del" in await indexer.list_indices()
        await indexer.delete_index("to_del")
        assert "to_del" not in await indexer.list_indices()

    @pytest.mark.asyncio
    async def test_add_documents_to_existing(self):
        provider = MockEmbeddingProvider()
        store = MemoryVectorStore()
        indexer = Indexer(embedding_provider=provider, store=store)

        await indexer.create_index("idx", documents=[{"text": "first doc"}])
        count_before = await store.count("idx")

        added = await indexer.add_documents("idx", [{"text": "second doc"}])
        assert added > 0
        assert await store.count("idx") > count_before

    @pytest.mark.asyncio
    async def test_get_index_stats(self):
        provider = MockEmbeddingProvider()
        store = MemoryVectorStore()
        indexer = Indexer(embedding_provider=provider, store=store)

        await indexer.create_index("stats_idx", documents=[{"text": "doc"}])
        stats = await indexer.get_index_stats("stats_idx")
        assert stats["exists"] is True
        assert stats["count"] > 0

    @pytest.mark.asyncio
    async def test_get_stats_nonexistent(self):
        provider = MockEmbeddingProvider()
        indexer = Indexer(embedding_provider=provider)
        stats = await indexer.get_index_stats("nope")
        assert stats["exists"] is False
        assert stats["count"] == 0

    @pytest.mark.asyncio
    async def test_batch_embedding(self):
        """Verify batching works with batch_size=2 and 5 documents."""
        call_count = 0
        original_dim = 4

        class CountingProvider:
            async def embed(self, texts: list[str], model: str = "") -> EmbeddingResult:
                nonlocal call_count
                call_count += 1
                vectors = [[1.0] * original_dim for _ in texts]
                return EmbeddingResult(
                    vectors=vectors, model="counting", dimensions=original_dim,
                )

        indexer = Indexer(
            embedding_provider=CountingProvider(),  # type: ignore[arg-type]
            batch_size=2,
        )
        docs = [{"id": f"d{i}", "text": f"Short text {i}"} for i in range(5)]
        await indexer.create_index("batch_test", documents=docs)

        assert call_count >= 3

    @pytest.mark.asyncio
    async def test_word_chunking(self):
        provider = MockEmbeddingProvider()
        store = MemoryVectorStore()
        indexer = Indexer(embedding_provider=provider, store=store)

        text = " ".join([f"word{i}" for i in range(100)])
        stats = await indexer.create_index(
            "word_idx",
            documents=[{"text": text}],
            chunking_config={"chunk_size": 20, "overlap": 5, "method": "words"},
        )
        assert stats["chunks"] > 1

    @pytest.mark.asyncio
    async def test_metadata_preserved(self):
        provider = MockEmbeddingProvider()
        store = MemoryVectorStore()
        indexer = Indexer(embedding_provider=provider, store=store)

        await indexer.create_index(
            "meta_idx",
            documents=[{"text": "test", "metadata": {"source": "test.txt"}}],
        )
        result = await store.query("meta_idx", [1.0] * 8, top_k=1)
        assert result.chunks[0]["metadata"]["source"] == "test.txt"


# ===========================================================================
# Builder / Decompiler round-trip
# ===========================================================================


class TestBuilderDecompilerRoundTrip:
    def test_rag_node_round_trip(self):
        from dan.builder import workflow, decompile

        wf = workflow("rag_test", canonical_workers=False)
        rag = wf.rag(
            "retriever",
            collection="knowledge_base",
            top_k=10,
            similarity_threshold=0.7,
            embedding_model="text-embedding-3-small",
            query_template="Find info about {topic}",
            include_metadata=True,
        )
        graph = wf.build()

        rag_nodes = [n for n in graph.nodes if getattr(n, "node_type", None) == "rag_operator"]
        assert len(rag_nodes) == 1
        node = rag_nodes[0]
        assert node.collection == "knowledge_base"
        assert node.top_k == 10
        assert node.similarity_threshold == 0.7

        code = decompile(graph)
        assert "wf.rag" in code
        assert "knowledge_base" in code
        assert "top_k=10" in code
        assert "similarity_threshold=0.7" in code

    def test_rag_node_recompile(self):
        from dan.builder import workflow, decompile

        wf = workflow("round_trip", canonical_workers=False)
        wf.rag("ret", collection="docs", top_k=5)
        graph1 = wf.build()
        code = decompile(graph1)

        ns: dict = {}
        exec(code, ns)
        graph2 = ns["graph"]

        nodes1 = {n.id: n for n in graph1.nodes if hasattr(n, "node_type")}
        nodes2 = {n.id: n for n in graph2.nodes if hasattr(n, "node_type")}

        for nid in nodes1:
            if nid in nodes2:
                assert getattr(nodes1[nid], "node_type", None) == getattr(nodes2[nid], "node_type", None)
                if hasattr(nodes1[nid], "collection"):
                    assert nodes1[nid].collection == nodes2[nid].collection
                    assert nodes1[nid].top_k == nodes2[nid].top_k

    def test_rag_chaining_uses_query_input_port(self):
        from dan.builder import workflow
        from dan.models.edges import DataEdge

        wf = workflow("rag_chain", canonical_workers=False)
        src = wf.code("src", code='result = {"query": "hello"}')
        rag = wf.rag("ret", collection="docs")
        src >> rag
        graph = wf.build()

        edges = [e for e in graph.edges if isinstance(e, DataEdge)]
        src_to_rag = [
            e for e in edges
            if e.source_node_id == "src" and e.target_node_id == "ret"
        ]
        assert src_to_rag
        assert src_to_rag[0].target_port == "query"
