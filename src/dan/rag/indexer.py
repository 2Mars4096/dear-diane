"""Index lifecycle management — create, populate, and manage vector store indexes."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from dan.rag import EmbeddingProvider, EmbeddingResult
from dan.rag.stores import DocumentRecord, VectorStore, VectorStoreConfig, VectorStoreFactory
from dan.tools.text_chunk import _chunk_by_chars, _chunk_by_words

logger = logging.getLogger(__name__)


def _chunk_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 100,
    method: str = "characters",
) -> list[dict[str, Any]]:
    """Split text into chunks using the same logic as the text_chunk tool."""
    if not text:
        return []
    if method == "words":
        result = _chunk_by_words(text, chunk_size, overlap)
    else:
        result = _chunk_by_chars(text, chunk_size, overlap)
    return result["chunks"]


class Indexer:
    """Manages vector store indexes: create, populate, delete, stats.

    Reuses ``text_chunk`` tool logic for document splitting and
    supports batch embedding with configurable batch sizes.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        embedding_model: str = "",
        store: VectorStore | None = None,
        store_config: VectorStoreConfig | None = None,
        batch_size: int = 100,
    ) -> None:
        self._provider = embedding_provider
        self._model = embedding_model
        self._batch_size = batch_size

        if store is not None:
            self._store = store
        elif store_config is not None:
            self._store = VectorStoreFactory.create(store_config)
        else:
            from dan.rag.stores.memory import MemoryVectorStore
            self._store = MemoryVectorStore()

    @property
    def store(self) -> VectorStore:
        return self._store

    async def create_index(
        self,
        name: str,
        documents: list[dict[str, Any]],
        chunking_config: dict[str, Any] | None = None,
        embedding_model: str = "",
    ) -> dict[str, Any]:
        """Create an index, chunk documents, embed, and store.

        Each document in *documents* should have at least a ``text`` key.
        Optional keys: ``id``, ``metadata``.

        Returns stats dict with count, dimensions, etc.
        """
        model = embedding_model or self._model
        chunk_cfg = chunking_config or {}
        chunk_size = chunk_cfg.get("chunk_size", 1000)
        overlap = chunk_cfg.get("overlap", 100)
        method = chunk_cfg.get("method", "characters")

        records: list[DocumentRecord] = []
        for doc in documents:
            text = doc.get("text", "")
            doc_id = doc.get("id", str(uuid.uuid4()))
            doc_meta = doc.get("metadata", {})

            chunks = _chunk_text(text, chunk_size, overlap, method)
            for chunk_info in chunks:
                chunk_id = f"{doc_id}__chunk_{chunk_info['index']}"
                records.append(DocumentRecord(
                    id=chunk_id,
                    text=chunk_info["chunk"],
                    metadata={
                        **doc_meta,
                        "source_doc_id": doc_id,
                        "chunk_index": chunk_info["index"],
                        "start_char": chunk_info["start_char"],
                        "end_char": chunk_info["end_char"],
                    },
                ))

        if not records:
            await self._store.create_collection(name, 0)
            return {"name": name, "count": 0, "dimensions": 0, "chunks": 0}

        dimensions = 0
        for batch_start in range(0, len(records), self._batch_size):
            batch = records[batch_start : batch_start + self._batch_size]
            texts = [r.text for r in batch]

            result: EmbeddingResult = await self._provider.embed(texts, model)
            dimensions = result.dimensions or (
                len(result.vectors[0]) if result.vectors else 0
            )

            for rec, vec in zip(batch, result.vectors):
                rec.embedding = vec

            logger.debug(
                "Indexed batch %d-%d of %d for '%s'",
                batch_start, batch_start + len(batch), len(records), name,
            )

        await self._store.create_collection(name, dimensions)
        await self._store.add(name, records)

        return {
            "name": name,
            "count": len(documents),
            "chunks": len(records),
            "dimensions": dimensions,
        }

    async def delete_index(self, name: str) -> None:
        await self._store.delete_collection(name)

    async def list_indices(self) -> list[str]:
        return await self._store.list_collections()

    async def add_documents(
        self,
        name: str,
        documents: list[dict[str, Any]],
        chunking_config: dict[str, Any] | None = None,
        embedding_model: str = "",
    ) -> int:
        """Add documents to an existing index. Returns number of chunks added."""
        model = embedding_model or self._model
        chunk_cfg = chunking_config or {}
        chunk_size = chunk_cfg.get("chunk_size", 1000)
        overlap = chunk_cfg.get("overlap", 100)
        method = chunk_cfg.get("method", "characters")

        records: list[DocumentRecord] = []
        for doc in documents:
            text = doc.get("text", "")
            doc_id = doc.get("id", str(uuid.uuid4()))
            doc_meta = doc.get("metadata", {})

            chunks = _chunk_text(text, chunk_size, overlap, method)
            for chunk_info in chunks:
                chunk_id = f"{doc_id}__chunk_{chunk_info['index']}"
                records.append(DocumentRecord(
                    id=chunk_id,
                    text=chunk_info["chunk"],
                    metadata={
                        **doc_meta,
                        "source_doc_id": doc_id,
                        "chunk_index": chunk_info["index"],
                        "start_char": chunk_info["start_char"],
                        "end_char": chunk_info["end_char"],
                    },
                ))

        if not records:
            return 0

        for batch_start in range(0, len(records), self._batch_size):
            batch = records[batch_start : batch_start + self._batch_size]
            texts = [r.text for r in batch]
            result = await self._provider.embed(texts, model)
            for rec, vec in zip(batch, result.vectors):
                rec.embedding = vec

        await self._store.add(name, records)
        return len(records)

    async def get_index_stats(self, name: str) -> dict[str, Any]:
        """Return stats for a named index."""
        count = await self._store.count(name)
        collections = await self._store.list_collections()
        return {
            "name": name,
            "exists": name in collections,
            "count": count,
        }
