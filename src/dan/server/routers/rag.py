"""RAG collection CRUD endpoints."""

from __future__ import annotations

import asyncio
import os
import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# ------------------------------------------------------------------
# Lazily-initialised indexer (module-level state)
# ------------------------------------------------------------------

_rag_indexer: "Any | None" = None
_rag_lock = asyncio.Lock()


async def _get_indexer():
    global _rag_indexer
    if _rag_indexer is not None:
        return _rag_indexer

    async with _rag_lock:
        if _rag_indexer is not None:
            return _rag_indexer

        from dan.rag import build_embedding_registry
        from dan.rag.indexer import Indexer
        from dan.rag.stores import VectorStoreConfig, VectorStoreFactory
        from dan.server.routers.dependencies import get_engine_config

        config = get_engine_config()
        model = config.default_embedding_model

        registry = build_embedding_registry(config)
        provider = registry.resolve(model)

        backend = os.environ.get("DAN_RAG_STORE_BACKEND", "memory")
        persist_dir = os.environ.get("DAN_RAG_PERSIST_DIR", "./rag_data")
        store = VectorStoreFactory.create(
            VectorStoreConfig(backend=backend, persist_directory=persist_dir),
        )
        _rag_indexer = Indexer(
            embedding_provider=provider,
            embedding_model=model,
            store=store,
        )
        return _rag_indexer


# ------------------------------------------------------------------
# Request models
# ------------------------------------------------------------------


class RAGCreateRequest(BaseModel):
    name: str
    documents: list[dict[str, Any]]
    chunking_config: dict[str, Any] | None = None
    embedding_model: str = ""


class RAGAddDocsRequest(BaseModel):
    documents: list[dict[str, Any]]
    chunking_config: dict[str, Any] | None = None
    embedding_model: str = ""


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/api/rag/collections")
async def list_rag_collections():
    indexer = await _get_indexer()
    names = await indexer.list_indices()
    return {"collections": names}


@router.post("/api/rag/collections")
async def create_rag_collection(req: RAGCreateRequest):
    indexer = await _get_indexer()
    stats = await indexer.create_index(
        name=req.name,
        documents=req.documents,
        chunking_config=req.chunking_config,
        embedding_model=req.embedding_model,
    )
    return stats


@router.get("/api/rag/collections/{name}/stats")
async def rag_collection_stats(name: str):
    indexer = await _get_indexer()
    return await indexer.get_index_stats(name)


@router.post("/api/rag/collections/{name}/documents")
async def add_rag_documents(name: str, req: RAGAddDocsRequest):
    indexer = await _get_indexer()
    chunks_added = await indexer.add_documents(
        name=name,
        documents=req.documents,
        chunking_config=req.chunking_config,
        embedding_model=req.embedding_model,
    )
    return {"name": name, "chunks_added": chunks_added}


@router.delete("/api/rag/collections/{name}")
async def delete_rag_collection(name: str):
    indexer = await _get_indexer()
    await indexer.delete_index(name)
    return {"name": name, "status": "deleted"}
