"""RAG executor — embedding + vector search + chunk retrieval."""

from __future__ import annotations

import logging
import string
import time
from typing import Any

from dan.engine.executor import ExecutionContext, NodeResult
from dan.engine.state import NodeStatus
from dan.models.nodes import NodeBase, RAGOperator
from dan.rag import EmbeddingProvider, EmbeddingRegistry
from dan.rag.stores import QueryResult, VectorStore, VectorStoreConfig, VectorStoreFactory

logger = logging.getLogger(__name__)

_store_cache: dict[str, VectorStore] = {}


def _render_template(template: str, variables: dict[str, Any]) -> str:
    try:
        return template.format_map(variables)
    except (KeyError, IndexError, ValueError):
        return string.Template(template).safe_substitute(variables)


def _get_or_create_store(config: dict[str, Any], collection: str) -> VectorStore:
    """Cache store instances per collection across executor calls."""
    cache_key = f"{config.get('backend', 'memory')}:{config.get('persist_directory', '')}:{collection}"
    if cache_key not in _store_cache:
        vs_config = VectorStoreConfig(
            backend=config.get("backend", "memory"),
            persist_directory=config.get("persist_directory", ""),
            collection_name=collection,
            extra=config.get("extra", {}),
        )
        _store_cache[cache_key] = VectorStoreFactory.create(vs_config)
    return _store_cache[cache_key]


def _resolve_embedding_provider(
    node: RAGOperator, context: ExecutionContext,
) -> tuple[EmbeddingProvider | None, str]:
    """Resolve embedding provider and model from context or node config.

    Returns (provider, model_name) or (None, "") if unavailable.
    """
    embedding_registry: EmbeddingRegistry | None = getattr(
        context, "embedding_registry", None
    )
    default_model = getattr(
        context.config, "default_embedding_model", "text-embedding-3-small",
    )
    model = node.embedding_model or default_model

    if embedding_registry is not None:
        try:
            return embedding_registry.resolve(model), model
        except KeyError:
            pass

    vs_config = node.vector_store_config
    if "embedding_provider" in vs_config:
        return vs_config["embedding_provider"], model

    return None, model


class RAGExecutor:
    """Executes ``RAGOperator`` nodes: embed query → search → return chunks."""

    async def execute(
        self,
        node: NodeBase,
        inputs: dict[str, Any],
        context: ExecutionContext,
    ) -> NodeResult:
        assert isinstance(node, RAGOperator)
        t0 = time.time()

        template_vars = dict(inputs)
        if "query" not in template_vars:
            if "input" in template_vars:
                template_vars["query"] = template_vars["input"]
            elif "data" in template_vars:
                template_vars["query"] = template_vars["data"]
        query = _render_template(node.query_template, template_vars)

        await context.emit_event(
            event_type="retrieval_started",
            node_id=node.id,
            node_type="rag_operator",
            data={
                "collection": node.collection,
                "query_preview": query[:100],
                "top_k": node.top_k,
            },
        )

        provider, model = _resolve_embedding_provider(node, context)
        if provider is None:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=(
                    "No embedding provider available. Configure "
                    "EngineConfig.embedding_providers (Option B) or pass "
                    "'embedding_provider' in vector_store_config."
                ),
            )

        try:
            embed_result = await provider.embed([query], model)
        except Exception as exc:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Embedding failed: {exc}",
            )

        if not embed_result.vectors:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error="Embedding returned no vectors",
            )

        query_vector = embed_result.vectors[0]

        store = _get_or_create_store(node.vector_store_config, node.collection)

        try:
            result: QueryResult = await store.query(
                collection=node.collection,
                vector=query_vector,
                top_k=node.top_k,
            )
        except Exception as exc:
            return NodeResult(
                outputs={},
                status=NodeStatus.FAILED,
                error=f"Vector store query failed: {exc}",
            )

        chunks = result.chunks
        if node.similarity_threshold is not None:
            chunks = [
                c for c in chunks if c.get("score", 0) >= node.similarity_threshold
            ]

        if not node.include_metadata:
            for c in chunks:
                c.pop("metadata", None)

        scores = [c.get("score", 0.0) for c in chunks]
        top_score = max(scores) if scores else 0.0
        latency_ms = round((time.time() - t0) * 1000, 1)

        await context.emit_event(
            event_type="retrieval_completed",
            node_id=node.id,
            node_type="rag_operator",
            data={
                "chunk_count": len(chunks),
                "latency_ms": latency_ms,
                "top_score": top_score,
            },
        )

        return NodeResult(
            outputs={"chunks": chunks, "scores": scores},
            status=NodeStatus.COMPLETED,
            metadata={
                "collection": node.collection,
                "chunk_count": len(chunks),
                "latency_ms": latency_ms,
                "embedding_model": model,
            },
        )
