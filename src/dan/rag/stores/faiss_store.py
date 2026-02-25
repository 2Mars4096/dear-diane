"""FAISS-backed vector store — high-performance approximate nearest neighbor search.

Wraps ``faiss.IndexFlatIP`` (inner product on L2-normalized vectors,
equivalent to cosine similarity).  Optional dependency: ``faiss-cpu``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

try:
    import faiss
    import numpy as np
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False

from dan.rag.stores import DocumentRecord, QueryResult

logger = logging.getLogger(__name__)


def _l2_normalize(vectors: Any) -> Any:
    """L2-normalize rows so inner product equals cosine similarity."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return vectors / norms


class FAISSVectorStore:
    """Vector store backed by FAISS ``IndexFlatIP``.

    Metadata is stored in a Python dict sidecar since FAISS indexes
    only store vectors.  Post-retrieval filtering is applied after
    the top-k nearest neighbor search.
    """

    def __init__(self, persist_directory: str = "") -> None:
        if not HAS_FAISS:
            raise ImportError(
                "faiss-cpu package required for FAISSVectorStore. "
                "Install with: pip install 'deep-agent-network[faiss]'"
            )
        self._persist_dir = persist_directory
        self._indexes: dict[str, Any] = {}
        self._metadata: dict[str, list[dict[str, Any]]] = {}
        self._id_maps: dict[str, list[str]] = {}
        self._texts: dict[str, list[str]] = {}
        self._dimensions: dict[str, int] = {}

    async def create_collection(self, name: str, dimensions: int) -> None:
        if name in self._indexes:
            return
        self._indexes[name] = faiss.IndexFlatIP(dimensions)
        self._metadata[name] = []
        self._id_maps[name] = []
        self._texts[name] = []
        self._dimensions[name] = dimensions

    async def delete_collection(self, name: str) -> None:
        self._indexes.pop(name, None)
        self._metadata.pop(name, None)
        self._id_maps.pop(name, None)
        self._texts.pop(name, None)
        self._dimensions.pop(name, None)
        if self._persist_dir:
            for suffix in (".faiss", ".meta.json"):
                path = os.path.join(self._persist_dir, f"{name}{suffix}")
                if os.path.exists(path):
                    os.remove(path)

    async def list_collections(self) -> list[str]:
        return sorted(self._indexes)

    async def add(self, collection: str, records: list[DocumentRecord]) -> None:
        if collection not in self._indexes:
            dims = 0
            for r in records:
                if r.embedding:
                    dims = len(r.embedding)
                    break
            if dims == 0:
                return
            await self.create_collection(collection, dims)

        existing_ids = set(self._id_maps[collection])
        to_remove = [r.id for r in records if r.id in existing_ids and r.embedding is not None]
        if to_remove:
            await self._rebuild_without(collection, set(to_remove))

        vectors = []
        for rec in records:
            if rec.embedding is None:
                continue
            vectors.append(rec.embedding)
            self._id_maps[collection].append(rec.id)
            self._texts[collection].append(rec.text)
            self._metadata[collection].append(dict(rec.metadata))

        if vectors:
            arr = np.array(vectors, dtype=np.float32)
            arr = _l2_normalize(arr)
            self._indexes[collection].add(arr)

    async def query(
        self,
        collection: str,
        vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> QueryResult:
        if collection not in self._indexes:
            return QueryResult(chunks=[], total_found=0)

        index = self._indexes[collection]
        if index.ntotal == 0:
            return QueryResult(chunks=[], total_found=0)

        search_k = top_k * 3 if filters else top_k
        search_k = min(search_k, index.ntotal)

        q = np.array([vector], dtype=np.float32)
        q = _l2_normalize(q)
        scores, indices = index.search(q, search_k)

        chunks: list[dict[str, Any]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            meta = self._metadata[collection][idx]
            if filters and not _matches_filters(meta, filters):
                continue
            chunks.append({
                "id": self._id_maps[collection][idx],
                "text": self._texts[collection][idx],
                "metadata": meta,
                "score": round(float(score), 6),
            })
            if len(chunks) >= top_k:
                break

        return QueryResult(chunks=chunks, total_found=len(chunks))

    async def delete_by_ids(self, collection: str, ids: list[str]) -> None:
        if collection not in self._indexes:
            return
        await self._rebuild_without(collection, set(ids))

    async def count(self, collection: str) -> int:
        if collection not in self._indexes:
            return 0
        return self._indexes[collection].ntotal

    # -- Persistence ---------------------------------------------------------

    def save(self, path: str | None = None) -> None:
        """Persist all collections to disk."""
        directory = path or self._persist_dir
        if not directory:
            raise ValueError("No persist directory configured")
        os.makedirs(directory, exist_ok=True)
        for name in list(self._indexes):
            faiss.write_index(
                self._indexes[name],
                os.path.join(directory, f"{name}.faiss"),
            )
            sidecar = {
                "ids": self._id_maps[name],
                "texts": self._texts[name],
                "metadata": self._metadata[name],
                "dimensions": self._dimensions.get(name, 0),
            }
            with open(os.path.join(directory, f"{name}.meta.json"), "w") as f:
                json.dump(sidecar, f)

    def load(self, path: str | None = None) -> None:
        """Load collections from disk."""
        directory = path or self._persist_dir
        if not directory or not os.path.isdir(directory):
            return
        for filename in os.listdir(directory):
            if not filename.endswith(".faiss"):
                continue
            name = filename[:-6]
            meta_path = os.path.join(directory, f"{name}.meta.json")
            if not os.path.exists(meta_path):
                continue
            self._indexes[name] = faiss.read_index(
                os.path.join(directory, filename)
            )
            with open(meta_path) as f:
                sidecar = json.load(f)
            self._id_maps[name] = sidecar.get("ids", [])
            self._texts[name] = sidecar.get("texts", [])
            self._metadata[name] = sidecar.get("metadata", [])
            self._dimensions[name] = sidecar.get("dimensions", 0)

    # -- Internal helpers ----------------------------------------------------

    async def _rebuild_without(self, collection: str, remove_ids: set[str]) -> None:
        """Rebuild the FAISS index excluding documents with given IDs."""
        old_ids = self._id_maps[collection]
        old_texts = self._texts[collection]
        old_meta = self._metadata[collection]
        old_index = self._indexes[collection]
        dims = self._dimensions.get(collection, old_index.d)

        new_ids: list[str] = []
        new_texts: list[str] = []
        new_meta: list[dict[str, Any]] = []
        vectors: list[Any] = []

        for i, doc_id in enumerate(old_ids):
            if doc_id in remove_ids:
                continue
            new_ids.append(doc_id)
            new_texts.append(old_texts[i])
            new_meta.append(old_meta[i])
            vec = old_index.reconstruct(i)
            vectors.append(vec)

        new_index = faiss.IndexFlatIP(dims)
        if vectors:
            arr = np.stack(vectors)
            new_index.add(arr)

        self._indexes[collection] = new_index
        self._id_maps[collection] = new_ids
        self._texts[collection] = new_texts
        self._metadata[collection] = new_meta


def _matches_filters(metadata: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, value in filters.items():
        if key not in metadata or metadata[key] != value:
            return False
    return True
