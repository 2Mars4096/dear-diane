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
            fetch_k = node.top_k * 3 if node.rerank else node.top_k
            result: QueryResult = await store.query(
                collection=node.collection,
                vector=query_vector,
                top_k=fetch_k,
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

        if node.rerank and len(chunks) > 0:
            chunks = await self._rerank_chunks(query, chunks, node.top_k, context)

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
                "reranked": node.rerank,
            },
        )

    async def _rerank_chunks(self, query: str, chunks: list[dict], top_k: int, context: ExecutionContext) -> list[dict]:
        if not context.provider_registry:
            return chunks[:top_k]
            
        try:
            provider = context.provider_registry.resolve(context.config.llm_default_model)
        except KeyError:
            return chunks[:top_k]

        import json
        prompt = (
            f"Given the query: '{query}', score the following chunks based on their relevance to the query.\n"
            "Return a JSON array of objects with 'index' (0-based) and 'score' (0.0 to 1.0).\n"
            "Example: [{\"index\": 0, \"score\": 0.9}, {\"index\": 1, \"score\": 0.2}]\n\n"
        )
        for i, c in enumerate(chunks):
            prompt += f"Chunk {i}:\n{c.get('text', '')}\n\n"

        try:
            result = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                model=context.config.llm_default_model,
                temperature=0.0,
            )
            
            text = result.text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                if len(lines) >= 2:
                    text = "\n".join(lines[1:-1])
            
            scores_data = json.loads(text)
            if isinstance(scores_data, dict) and "scores" in scores_data:
                scores_data = scores_data["scores"]
            elif isinstance(scores_data, dict) and "results" in scores_data:
                scores_data = scores_data["results"]
                
            if isinstance(scores_data, list):
                for item in scores_data:
                    idx = item.get("index")
                    if isinstance(idx, int) and 0 <= idx < len(chunks):
                        chunks[idx]["score"] = float(item.get("score", chunks[idx].get("score", 0.0)))
                        
            chunks.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        except Exception as exc:
            logger.warning("Reranking failed: %s", exc)
            
        return chunks[:top_k]
