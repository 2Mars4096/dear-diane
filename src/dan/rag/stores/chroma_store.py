"""ChromaDB-backed vector store — persistent client with native metadata filtering.

Optional dependency: ``chromadb``.
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import chromadb
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False

from dan.rag.stores import DocumentRecord, QueryResult

logger = logging.getLogger(__name__)


class ChromaVectorStore:
    """Vector store backed by ChromaDB ``PersistentClient``.

    Delegates collection management to the Chroma client and
    supports native metadata filtering via the ``where`` clause.
    """

    def __init__(self, persist_directory: str = "") -> None:
        if not HAS_CHROMA:
            raise ImportError(
                "chromadb package required for ChromaVectorStore. "
                "Install with: pip install 'deep-agent-network[chroma]'"
            )
        if persist_directory:
            self._client = chromadb.PersistentClient(path=persist_directory)
        else:
            self._client = chromadb.Client()

    async def create_collection(self, name: str, dimensions: int) -> None:
        self._client.get_or_create_collection(
            name=name,
            metadata={"dimensions": dimensions},
        )

    async def delete_collection(self, name: str) -> None:
        try:
            self._client.delete_collection(name=name)
        except Exception:
            logger.debug("Collection '%s' not found for deletion", name)

    async def list_collections(self) -> list[str]:
        return sorted(c.name for c in self._client.list_collections())

    async def add(self, collection: str, records: list[DocumentRecord]) -> None:
        col = self._client.get_or_create_collection(name=collection)

        ids: list[str] = []
        documents: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict[str, Any]] = []

        for rec in records:
            ids.append(rec.id)
            documents.append(rec.text)
            metadatas.append(rec.metadata or {})
            if rec.embedding is not None:
                embeddings.append(rec.embedding)

        kwargs: dict[str, Any] = {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
        }
        if embeddings and len(embeddings) == len(ids):
            kwargs["embeddings"] = embeddings

        col.upsert(**kwargs)

    async def query(
        self,
        collection: str,
        vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> QueryResult:
        try:
            col = self._client.get_collection(name=collection)
        except Exception:
            return QueryResult(chunks=[], total_found=0)

        query_kwargs: dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": top_k,
        }
        if filters:
            query_kwargs["where"] = filters

        results = col.query(**query_kwargs)

        chunks: list[dict[str, Any]] = []
        result_ids = results.get("ids", [[]])[0]
        result_docs = results.get("documents", [[]])[0]
        result_metas = results.get("metadatas", [[]])[0]
        result_dists = results.get("distances", [[]])[0]

        for i, doc_id in enumerate(result_ids):
            score = 1.0 - result_dists[i] if result_dists else 0.0
            chunks.append({
                "id": doc_id,
                "text": result_docs[i] if result_docs else "",
                "metadata": result_metas[i] if result_metas else {},
                "score": round(score, 6),
            })

        return QueryResult(chunks=chunks, total_found=len(chunks))

    async def delete_by_ids(self, collection: str, ids: list[str]) -> None:
        try:
            col = self._client.get_collection(name=collection)
            col.delete(ids=ids)
        except Exception:
            logger.debug("Failed to delete from collection '%s'", collection)

    async def count(self, collection: str) -> int:
        try:
            col = self._client.get_collection(name=collection)
            return col.count()
        except Exception:
            return 0
