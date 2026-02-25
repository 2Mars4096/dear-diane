"""Vector store abstraction — protocol, data classes, and factory."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DocumentRecord:
    """A single document to add to a vector store."""

    id: str
    text: str
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResult:
    """Result of a vector store query."""

    chunks: list[dict[str, Any]] = field(default_factory=list)
    total_found: int = 0


@dataclass
class VectorStoreConfig:
    """Configuration for creating a vector store instance."""

    backend: str = "memory"
    persist_directory: str = ""
    collection_name: str = "default"
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# VectorStore protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorStore(Protocol):
    """Protocol that all vector store backends must satisfy."""

    async def create_collection(self, name: str, dimensions: int) -> None: ...
    async def delete_collection(self, name: str) -> None: ...
    async def list_collections(self) -> list[str]: ...
    async def add(self, collection: str, records: list[DocumentRecord]) -> None: ...
    async def query(
        self,
        collection: str,
        vector: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> QueryResult: ...
    async def delete_by_ids(self, collection: str, ids: list[str]) -> None: ...
    async def count(self, collection: str) -> int: ...


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class VectorStoreFactory:
    """Creates vector store instances based on backend configuration."""

    @staticmethod
    def create(config: VectorStoreConfig) -> VectorStore:
        backend = config.backend.lower()

        if backend == "memory":
            from dan.rag.stores.memory import MemoryVectorStore
            return MemoryVectorStore()

        if backend == "faiss":
            try:
                from dan.rag.stores.faiss_store import FAISSVectorStore
                return FAISSVectorStore(persist_directory=config.persist_directory)
            except ImportError:
                logger.warning(
                    "faiss-cpu not installed; falling back to MemoryVectorStore"
                )
                from dan.rag.stores.memory import MemoryVectorStore
                return MemoryVectorStore()

        if backend == "chroma":
            try:
                from dan.rag.stores.chroma_store import ChromaVectorStore
                return ChromaVectorStore(persist_directory=config.persist_directory)
            except ImportError:
                logger.warning(
                    "chromadb not installed; falling back to MemoryVectorStore"
                )
                from dan.rag.stores.memory import MemoryVectorStore
                return MemoryVectorStore()

        raise ValueError(
            f"Unknown vector store backend '{backend}'. "
            f"Supported: memory, faiss, chroma"
        )
