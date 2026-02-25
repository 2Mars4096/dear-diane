"""Pure-Python in-memory vector store — zero external dependencies.

Uses stdlib ``math`` for cosine similarity via dot-product on
L2-normalized vectors.  O(n) linear scan per query — suitable
for collections under ~10 000 documents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from dan.rag.stores import DocumentRecord, QueryResult


@dataclass
class _StoredDoc:
    id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any]


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = _norm(a), _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class MemoryVectorStore:
    """In-memory vector store backed by Python dicts and lists.

    Implements the full ``VectorStore`` protocol using only stdlib.
    """

    def __init__(self) -> None:
        self._collections: dict[str, list[_StoredDoc]] = {}
        self._dimensions: dict[str, int] = {}

    async def create_collection(self, name: str, dimensions: int) -> None:
        if name not in self._collections:
            self._collections[name] = []
            self._dimensions[name] = dimensions

    async def delete_collection(self, name: str) -> None:
        self._collections.pop(name, None)
        self._dimensions.pop(name, None)

    async def list_collections(self) -> list[str]:
        return sorted(self._collections)

    async def add(self, collection: str, records: list[DocumentRecord]) -> None:
        if collection not in self._collections:
            dims = 0
            for r in records:
                if r.embedding:
                    dims = len(r.embedding)
                    break
            self._collections[collection] = []
            self._dimensions[collection] = dims

        docs = self._collections[collection]
        existing_ids = {d.id for d in docs}

        for rec in records:
            if rec.embedding is None:
                continue
            if rec.id in existing_ids:
                docs[:] = [d for d in docs if d.id != rec.id]
            docs.append(_StoredDoc(
                id=rec.id,
                text=rec.text,
                embedding=rec.embedding,
                metadata=dict(rec.metadata),
            ))

    async def query(
        self,
        collection: str,
        vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> QueryResult:
        docs = self._collections.get(collection, [])

        scored: list[tuple[float, _StoredDoc]] = []
        for doc in docs:
            if filters:
                if not _matches_filters(doc.metadata, filters):
                    continue
            sim = _cosine_similarity(vector, doc.embedding)
            scored.append((sim, doc))

        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[:top_k]

        chunks = [
            {
                "id": doc.id,
                "text": doc.text,
                "metadata": doc.metadata,
                "score": round(score, 6),
            }
            for score, doc in top
        ]
        return QueryResult(chunks=chunks, total_found=len(scored))

    async def delete_by_ids(self, collection: str, ids: list[str]) -> None:
        if collection not in self._collections:
            return
        id_set = set(ids)
        self._collections[collection] = [
            d for d in self._collections[collection] if d.id not in id_set
        ]

    async def count(self, collection: str) -> int:
        return len(self._collections.get(collection, []))


def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Simple equality-based metadata filter matching."""
    for key, value in filters.items():
        if key not in metadata or metadata[key] != value:
            return False
    return True
