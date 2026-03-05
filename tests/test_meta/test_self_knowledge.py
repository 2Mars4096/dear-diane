"""Tests for Plan 19-5: Self-Knowledge RAG."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from dan.rag import EmbeddingResult
from dan.rag.stores import VectorStoreConfig
from dan.rag.stores.memory import MemoryVectorStore
from dan.meta.self_knowledge import (
    COLLECTION_NAME,
    RetrievedChunk,
    SelfKnowledgeIndex,
    _split_markdown_sections,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIM = 8


def _deterministic_vector(text: str) -> list[float]:
    """Hash text into a fixed-dimension unit-ish vector for reproducible tests."""
    digest = hashlib.sha256(text.encode()).digest()
    raw = [b / 255.0 for b in digest[:_DIM]]
    norm = sum(x * x for x in raw) ** 0.5 or 1.0
    return [x / norm for x in raw]


class FakeEmbeddingProvider:
    """Returns deterministic vectors based on text content hash."""

    def __init__(self) -> None:
        self.call_count = 0

    async def embed(self, texts: list[str], model: str = "") -> EmbeddingResult:
        self.call_count += 1
        vectors = [_deterministic_vector(t) for t in texts]
        return EmbeddingResult(vectors=vectors, model=model or "fake", dimensions=_DIM)


class FakeToolRegistry:
    """Minimal tool registry for index_tool_schemas tests."""

    def __init__(self, tools: dict[str, dict] | None = None) -> None:
        self._tools = tools or {}

    def registered_ids(self) -> list[str]:
        return list(self._tools)

    def get_metadata(self, tool_id: str) -> dict | None:
        return self._tools.get(tool_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def embedding_provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
def memory_store() -> MemoryVectorStore:
    return MemoryVectorStore()


@pytest.fixture
def index(embedding_provider: FakeEmbeddingProvider, memory_store: MemoryVectorStore) -> SelfKnowledgeIndex:
    return SelfKnowledgeIndex(
        embedding_provider=embedding_provider,
        store=memory_store,
        token_budget=4000,
    )


def _write_md(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests: RetrievedChunk
# ---------------------------------------------------------------------------


class TestRetrievedChunk:
    def test_basic_construction(self):
        chunk = RetrievedChunk(
            text="hello",
            source_file="api.md",
            section_title="Overview",
            doc_type="api_reference",
            score=0.95,
        )
        assert chunk.text == "hello"
        assert chunk.source_file == "api.md"
        assert chunk.section_title == "Overview"
        assert chunk.doc_type == "api_reference"
        assert chunk.score == 0.95

    def test_default_score(self):
        chunk = RetrievedChunk(
            text="x", source_file="f", section_title="s", doc_type="example",
        )
        assert chunk.score == 0.0


# ---------------------------------------------------------------------------
# Tests: SelfKnowledgeIndex
# ---------------------------------------------------------------------------


class TestSelfKnowledgeIndex:
    @pytest.mark.asyncio
    async def test_index_docs_simple(self, index: SelfKnowledgeIndex, tmp_path: Path):
        md = _write_md(tmp_path, "api-guide.md", "## Overview\nSome text\n\n## Nodes\nNode info")
        result = await index.index_docs([md])
        assert result["files_indexed"] == 1
        assert result["chunks"] >= 2

    @pytest.mark.asyncio
    async def test_index_docs_chunking_section_titles(self, tmp_path: Path):
        content = "## Alpha\nContent A\n\n## Beta\nContent B\n\n## Gamma\nContent C"
        sections = _split_markdown_sections(content, "test.md", "api_reference")
        titles = [s["metadata"]["section_title"] for s in sections]
        assert "Alpha" in titles
        assert "Beta" in titles
        assert "Gamma" in titles

    @pytest.mark.asyncio
    async def test_index_docs_preamble_chunk(self, tmp_path: Path):
        content = "Preamble text before any header.\n\n## First\nBody"
        sections = _split_markdown_sections(content, "test.md", "api_reference")
        assert any(s["metadata"]["section_title"] == "(preamble)" for s in sections)

    @pytest.mark.asyncio
    async def test_index_docs_skips_missing(self, index: SelfKnowledgeIndex, tmp_path: Path):
        missing = tmp_path / "nonexistent.md"
        result = await index.index_docs([missing])
        assert result["files_indexed"] == 0
        assert result["chunks"] == 0

    @pytest.mark.asyncio
    async def test_index_docs_skips_empty(self, index: SelfKnowledgeIndex, tmp_path: Path):
        empty = _write_md(tmp_path, "empty.md", "   \n  ")
        result = await index.index_docs([empty])
        assert result["files_indexed"] == 0

    @pytest.mark.asyncio
    async def test_index_tool_schemas(self, index: SelfKnowledgeIndex):
        registry = FakeToolRegistry(tools={
            "web_search": {
                "description": "Search the web",
                "category": "retrieval",
                "parameters": {"query": {"type": "string", "description": "query"}},
                "returns": "dict",
                "examples": [{"input": {"query": "hello"}}],
            },
        })
        result = await index.index_tool_schemas(tool_registry=registry)
        assert result["tools_indexed"] == 1
        assert result["chunks"] >= 1

    @pytest.mark.asyncio
    async def test_index_tool_schemas_empty_registry(self, index: SelfKnowledgeIndex):
        registry = FakeToolRegistry(tools={})
        result = await index.index_tool_schemas(tool_registry=registry)
        assert result["tools_indexed"] == 0
        assert result["chunks"] == 0

    @pytest.mark.asyncio
    async def test_index_examples(self, index: SelfKnowledgeIndex, tmp_path: Path):
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "demo.py").write_text("print('hello')", encoding="utf-8")
        (examples_dir / "guide.md").write_text("# Guide\nSteps here", encoding="utf-8")
        (examples_dir / "data.json").write_text("{}", encoding="utf-8")

        result = await index.index_examples(examples_dir)
        assert result["files_indexed"] == 2
        assert result["chunks"] >= 2

    @pytest.mark.asyncio
    async def test_index_examples_nonexistent_dir(self, index: SelfKnowledgeIndex, tmp_path: Path):
        result = await index.index_examples(tmp_path / "missing_dir")
        assert result["files_indexed"] == 0

    @pytest.mark.asyncio
    async def test_index_examples_skips_empty_files(self, index: SelfKnowledgeIndex, tmp_path: Path):
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "empty.py").write_text("", encoding="utf-8")
        result = await index.index_examples(examples_dir)
        assert result["files_indexed"] == 0

    @pytest.mark.asyncio
    async def test_retrieve_returns_chunks(self, index: SelfKnowledgeIndex, tmp_path: Path):
        md = _write_md(tmp_path, "api-guide.md", "## Builder DSL\nUse WorkflowBuilder to create graphs.")
        await index.index_docs([md])
        chunks = await index.retrieve("builder DSL", top_k=5)
        assert len(chunks) > 0
        assert all(isinstance(c, RetrievedChunk) for c in chunks)

    @pytest.mark.asyncio
    async def test_retrieve_fields(self, index: SelfKnowledgeIndex, tmp_path: Path):
        md = _write_md(tmp_path, "architecture.md", "## Architecture\nLayout info")
        await index.index_docs([md])
        chunks = await index.retrieve("architecture", top_k=3)
        assert len(chunks) > 0
        chunk = chunks[0]
        assert chunk.source_file != ""
        assert chunk.section_title != ""
        assert chunk.doc_type != ""
        assert isinstance(chunk.score, float)

    @pytest.mark.asyncio
    async def test_retrieve_respects_top_k(self, index: SelfKnowledgeIndex, tmp_path: Path):
        md = _write_md(
            tmp_path,
            "big-api-guide.md",
            "\n\n".join(f"## Section {i}\nContent for section {i} " * 10 for i in range(20)),
        )
        await index.index_docs([md])
        chunks = await index.retrieve("section", top_k=3)
        assert len(chunks) <= 3

    @pytest.mark.asyncio
    async def test_retrieve_empty_index(self, index: SelfKnowledgeIndex):
        chunks = await index.retrieve("anything")
        assert chunks == []

    @pytest.mark.asyncio
    async def test_refresh_incremental_unchanged(
        self, embedding_provider: FakeEmbeddingProvider, memory_store: MemoryVectorStore, tmp_path: Path,
    ):
        idx = SelfKnowledgeIndex(
            embedding_provider=embedding_provider, store=memory_store,
        )
        md = _write_md(tmp_path, "api-guide.md", "## API\nStuff here")
        await idx.index_docs([md])

        registry = FakeToolRegistry(tools={
            "test_tool": {
                "description": "test", "category": "custom",
                "parameters": {}, "returns": "dict", "examples": [],
            },
        })

        calls_before = embedding_provider.call_count
        await idx.refresh(doc_paths=[md], tool_registry=registry)
        calls_after = embedding_provider.call_count
        # Only the tool schema re-index should have called embed, not the unchanged doc
        assert calls_after - calls_before == 1  # tool schemas only

    @pytest.mark.asyncio
    async def test_refresh_detects_changed_file(
        self, embedding_provider: FakeEmbeddingProvider, memory_store: MemoryVectorStore, tmp_path: Path,
    ):
        idx = SelfKnowledgeIndex(
            embedding_provider=embedding_provider, store=memory_store,
        )
        md = _write_md(tmp_path, "api-guide.md", "## Version 1\nOld content")
        await idx.index_docs([md])

        md.write_text("## Version 2\nNew content", encoding="utf-8")

        registry = FakeToolRegistry(tools={})
        result = await idx.refresh(doc_paths=[md], tool_registry=registry)
        assert result["docs_refreshed"] == 1

    def test_format_for_prompt_basic(self, index: SelfKnowledgeIndex):
        chunks = [
            RetrievedChunk(
                text="Builder API details",
                source_file="api.md",
                section_title="Builder",
                doc_type="api_reference",
                score=0.9,
            ),
        ]
        result = index.format_for_prompt(chunks)
        assert "## DAN API Reference" in result
        assert "[Source: api.md > Builder]" in result
        assert "Builder API details" in result

    def test_format_for_prompt_respects_token_budget(self):
        idx = SelfKnowledgeIndex(
            embedding_provider=FakeEmbeddingProvider(),
            token_budget=50,
        )
        chunks = [
            RetrievedChunk(
                text="A" * 500,
                source_file=f"f{i}.md",
                section_title=f"S{i}",
                doc_type="api_reference",
                score=0.9 - i * 0.1,
            )
            for i in range(10)
        ]
        result = idx.format_for_prompt(chunks)
        assert len(result) < 500 * 10

    def test_format_for_prompt_empty_chunks(self, index: SelfKnowledgeIndex):
        assert index.format_for_prompt([]) == ""

    def test_constructor_with_store_config(self, embedding_provider: FakeEmbeddingProvider):
        config = VectorStoreConfig(backend="memory")
        idx = SelfKnowledgeIndex(
            embedding_provider=embedding_provider,
            store_config=config,
        )
        assert idx.store is not None

    def test_constructor_with_store_instance(
        self, embedding_provider: FakeEmbeddingProvider, memory_store: MemoryVectorStore,
    ):
        idx = SelfKnowledgeIndex(
            embedding_provider=embedding_provider,
            store=memory_store,
        )
        assert idx.store is memory_store

    def test_infer_doc_type_api(self):
        from dan.meta.self_knowledge import _infer_doc_type
        assert _infer_doc_type("llm-api-guide.md") == "api_reference"
        assert _infer_doc_type("API_REFERENCE.md") == "api_reference"

    def test_infer_doc_type_architecture(self):
        from dan.meta.self_knowledge import _infer_doc_type
        assert _infer_doc_type("architecture.md") == "architecture"
        assert _infer_doc_type("arch-overview.md") == "architecture"

    def test_infer_doc_type_fallback(self):
        from dan.meta.self_knowledge import _infer_doc_type
        assert _infer_doc_type("readme.md") == "api_reference"

    @pytest.mark.asyncio
    async def test_store_property_delegates_to_indexer(
        self, embedding_provider: FakeEmbeddingProvider, memory_store: MemoryVectorStore,
    ):
        idx = SelfKnowledgeIndex(
            embedding_provider=embedding_provider,
            store=memory_store,
        )
        assert idx.store is memory_store
