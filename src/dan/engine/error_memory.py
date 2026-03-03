"""Error memory — extraction, indexing, and causal principle storage.

Implements Phase 9D Tiers 1 and 2:
- Tier 1: ErrorRecord extraction, ErrorMemoryIndex (vector RAG over failures),
  and ErrorContextProvider (injects past-failure context into prompts).
- Tier 2: CausalPrinciple model and PrincipleStore for learned do/don't rules.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEFAULT_INPUT_SNAPSHOT_MAX_CHARS = 2000
_DEFAULT_SESSION_ID = "_default"


# ---------------------------------------------------------------------------
# ErrorCategory enum
# ---------------------------------------------------------------------------


class ErrorCategory(str, Enum):
    LLM_FAILURE = "llm_failure"
    TOOL_FAILURE = "tool_failure"
    VALIDATION_FAILURE = "validation_failure"
    TIMEOUT = "timeout"
    SCHEMA_MISMATCH = "schema_mismatch"
    CONDITION_FAILURE = "condition_failure"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# ErrorRecord model
# ---------------------------------------------------------------------------


class ErrorRecord(BaseModel):
    run_id: str
    workflow_id: str
    node_id: str
    node_type: str = ""
    error_message: str
    error_category: ErrorCategory = ErrorCategory.UNKNOWN
    input_snapshot: dict[str, Any] | None = None
    upstream_node_ids: list[str] = Field(default_factory=list)
    timestamp: float = Field(default_factory=time.time)
    severity: Literal["warning", "error", "fatal"] = "error"


# ---------------------------------------------------------------------------
# CausalPrinciple model (Tier 2)
# ---------------------------------------------------------------------------


class CausalPrinciple(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    condition: str
    action: str
    reason: str = ""
    source_run_ids: list[str] = Field(default_factory=list)
    source_node_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    tags: list[str] = Field(default_factory=list)
    workflow_id: str = ""
    repair_level: Literal["retry", "prompt_fix", "parameter_fix", "structural_fix", "redesign"] = "prompt_fix"
    suggested_parameter_changes: dict[str, Any] = Field(default_factory=dict)
    structural_description: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_error(node_type: str, error_message: str) -> ErrorCategory:
    nt = node_type.lower()
    if nt == "tool_operator":
        return ErrorCategory.TOOL_FAILURE
    if nt == "llm_operator":
        return ErrorCategory.LLM_FAILURE
    if nt in ("gate", "if_else"):
        return ErrorCategory.CONDITION_FAILURE
    if nt == "validator":
        return ErrorCategory.VALIDATION_FAILURE

    msg = error_message.lower()
    if "timeout" in msg:
        return ErrorCategory.TIMEOUT
    if "schema" in msg or "json" in msg:
        return ErrorCategory.SCHEMA_MISMATCH

    return ErrorCategory.UNKNOWN


def _truncate_snapshot(
    snapshot: dict[str, Any] | None, max_chars: int,
) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    serialized = json.dumps(snapshot, default=str)
    if len(serialized) <= max_chars:
        return snapshot
    return {"_truncated": True, "_preview": serialized[:max_chars]}


# ---------------------------------------------------------------------------
# extract_error_records()
# ---------------------------------------------------------------------------


def extract_error_records(
    run_record: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
    max_snapshot_chars: int = _DEFAULT_INPUT_SNAPSHOT_MAX_CHARS,
) -> list[ErrorRecord]:
    """Extract :class:`ErrorRecord` instances from a run result and event stream.

    Sources:
    1. ``run_record["result"]["errors"]`` dict mapping *node_id → message*.
    2. Events with ``event_type == "node_failed"`` carrying ``node_id``
       and ``data.error``.

    Deduplicates by *node_id* — when both sources report the same node the
    record carrying more information wins.
    """
    run_id: str = run_record.get("run_id", "")
    workflow_id: str = (
        run_record.get("graph_id", "") or run_record.get("workflow_id", "")
    )
    result = run_record.get("result") or {}
    errors_dict: dict[str, str] = (
        result.get("errors") or run_record.get("errors") or {}
    )
    node_statuses: dict[str, Any] = run_record.get("node_statuses") or {}

    records_by_node: dict[str, ErrorRecord] = {}

    for node_id, error_message in errors_dict.items():
        status = (
            node_statuses.get(node_id, {})
            if isinstance(node_statuses, dict)
            else {}
        )
        node_type = ""
        input_snapshot: dict[str, Any] | None = None
        upstream: list[str] = []
        if isinstance(status, dict):
            node_type = status.get("node_type", "")
            input_snapshot = status.get("inputs")
            upstream = status.get("upstream_node_ids", [])

        records_by_node[node_id] = ErrorRecord(
            run_id=run_id,
            workflow_id=workflow_id,
            node_id=node_id,
            node_type=node_type,
            error_message=str(error_message),
            error_category=_classify_error(node_type, str(error_message)),
            input_snapshot=_truncate_snapshot(input_snapshot, max_snapshot_chars),
            upstream_node_ids=upstream if isinstance(upstream, list) else [],
            severity="error",
        )

    for event in events or []:
        if event.get("event_type") != "node_failed":
            continue

        node_id = event.get("node_id", "")
        if not node_id:
            continue

        data: dict[str, Any] = event.get("data") or {}
        error_message = data.get("error", "") or event.get("error", "")
        node_type = data.get("node_type", "") or event.get("node_type", "")
        input_snapshot = data.get("inputs")
        upstream = data.get("upstream_node_ids", [])
        ts = event.get("timestamp", time.time())

        if node_id in records_by_node:
            existing = records_by_node[node_id]
            existing_info = len(existing.error_message) + len(existing.node_type)
            new_info = len(str(error_message)) + len(str(node_type))
            if new_info <= existing_info:
                continue

        records_by_node[node_id] = ErrorRecord(
            run_id=run_id,
            workflow_id=workflow_id,
            node_id=node_id,
            node_type=node_type,
            error_message=str(error_message),
            error_category=_classify_error(node_type, str(error_message)),
            input_snapshot=_truncate_snapshot(input_snapshot, max_snapshot_chars),
            upstream_node_ids=upstream if isinstance(upstream, list) else [],
            timestamp=float(ts) if ts else time.time(),
            severity="error",
        )

    return list(records_by_node.values())


# ---------------------------------------------------------------------------
# ErrorMemoryIndex
# ---------------------------------------------------------------------------


class ErrorMemoryIndex:
    """Vector index over historical error records for RAG-based retrieval.

    Bypasses :class:`Indexer.create_index` to use deterministic document IDs
    (``error:{run_id}:{node_id}``) which enable idempotent re-indexing.
    """

    def __init__(
        self,
        embedding_provider: Any,
        embedding_model: str = "",
        store: Any | None = None,
        store_config: Any | None = None,
    ) -> None:
        from dan.rag.indexer import Indexer

        self._provider = embedding_provider
        self._model = embedding_model
        self._indexer = Indexer(
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            store=store,
            store_config=store_config,
        )
        self._store = self._indexer.store

    @staticmethod
    def _collection_name(workflow_id: str) -> str:
        return f"dan_errors_{workflow_id}"

    @staticmethod
    def _error_to_text(error: ErrorRecord) -> str:
        parts = [
            f"Error in node '{error.node_id}' (type: {error.node_type})",
            f"Category: {error.error_category.value}",
            f"Message: {error.error_message}",
        ]
        if error.upstream_node_ids:
            parts.append(
                f"Upstream nodes: {', '.join(error.upstream_node_ids)}"
            )
        if error.input_snapshot:
            preview = json.dumps(error.input_snapshot, default=str)[:500]
            parts.append(f"Input context: {preview}")
        return "\n".join(parts)

    @staticmethod
    def _error_to_metadata(error: ErrorRecord) -> dict[str, Any]:
        return {
            "run_id": error.run_id,
            "workflow_id": error.workflow_id,
            "node_id": error.node_id,
            "node_type": error.node_type,
            "error_category": error.error_category.value,
            "error_message": error.error_message[:1000],
            "severity": error.severity,
            "timestamp": error.timestamp,
            "upstream_node_ids": ",".join(error.upstream_node_ids),
        }

    async def index_errors(
        self, workflow_id: str, errors: list[ErrorRecord],
    ) -> int:
        if not errors:
            return 0

        from dan.rag.stores import DocumentRecord

        collection = self._collection_name(workflow_id)
        texts = [self._error_to_text(e) for e in errors]
        result = await self._provider.embed(texts, self._model)
        dimensions = result.dimensions or (
            len(result.vectors[0]) if result.vectors else 0
        )

        existing_collections = await self._store.list_collections()
        if collection not in existing_collections:
            await self._store.create_collection(collection, dimensions)

        records: list[DocumentRecord] = []
        for error, vector in zip(errors, result.vectors):
            doc_id = f"error:{error.run_id}:{error.node_id}"
            records.append(
                DocumentRecord(
                    id=doc_id,
                    text=self._error_to_text(error),
                    embedding=vector,
                    metadata=self._error_to_metadata(error),
                )
            )

        await self._store.add(collection, records)
        return len(records)

    async def query_similar(
        self,
        workflow_id: str,
        context: str,
        top_k: int = 5,
        scope: Literal["workflow", "global"] = "workflow",
    ) -> list[dict[str, Any]]:
        """Return similar past errors.

        *scope="workflow"* searches only the given workflow's collection
        (existing default).  *scope="global"* searches across all indexed
        workflow collections and merges results by score.
        """
        result = await self._provider.embed([context], self._model)
        if not result.vectors:
            return []
        vector = result.vectors[0]

        existing_collections = await self._store.list_collections()

        if scope == "workflow":
            collection = self._collection_name(workflow_id)
            if collection not in existing_collections:
                return []
            query_result = await self._store.query(
                collection, vector, top_k=top_k,
            )
            return query_result.chunks

        all_chunks: list[dict[str, Any]] = []
        prefix = "dan_errors_"
        for coll in existing_collections:
            if not coll.startswith(prefix):
                continue
            try:
                qr = await self._store.query(coll, vector, top_k=top_k)
                all_chunks.extend(qr.chunks)
            except Exception:
                logger.debug("Global query failed for collection %s", coll, exc_info=True)

        all_chunks.sort(key=lambda c: c.get("score", 0), reverse=True)
        return all_chunks[:top_k]

    async def clear(self, workflow_id: str) -> None:
        collection = self._collection_name(workflow_id)
        existing_collections = await self._store.list_collections()
        if collection in existing_collections:
            await self._store.delete_collection(collection)

    async def stats(self, workflow_id: str) -> dict[str, Any]:
        collection = self._collection_name(workflow_id)
        existing_collections = await self._store.list_collections()
        exists = collection in existing_collections
        count = await self._store.count(collection) if exists else 0
        return {"collection": collection, "exists": exists, "count": count}


# ---------------------------------------------------------------------------
# PrincipleStore (Tier 2)
# ---------------------------------------------------------------------------


class PrincipleStore:
    """Workflow-scoped persistence for :class:`CausalPrinciple` objects.

    Delegates to a :class:`MemoryStore` backend with a fixed session
    (``"_default"``) and ``principle:{id}`` key pattern.
    """

    def __init__(self, memory_store: Any) -> None:
        self._store = memory_store

    async def store_principles(
        self, workflow_id: str, principles: list[CausalPrinciple],
    ) -> None:
        from dan.engine.memory import MemoryEntry, MemoryScope

        for p in principles:
            p_copy = p.model_copy(
                update={"workflow_id": workflow_id, "updated_at": time.time()},
            )
            entry = MemoryEntry(
                key=f"principle:{p_copy.id}",
                value=p_copy.model_dump(),
                scope=MemoryScope.WORKFLOW,
                source_run_id=(
                    p_copy.source_run_ids[0] if p_copy.source_run_ids else None
                ),
            )
            await self._store.write(workflow_id, _DEFAULT_SESSION_ID, entry)

    async def load_principles(
        self,
        workflow_id: str,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
        scope: Literal["workflow", "global"] = "workflow",
    ) -> list[CausalPrinciple]:
        """Load principles for a workflow.

        *scope="global"* additionally includes principles stored under
        every other workflow_id known to the backing memory store.
        De-duplicates by principle id and keeps the highest-confidence
        version.
        """
        wf_ids = [workflow_id]
        if scope == "global":
            try:
                from pathlib import Path

                base: Path | None = getattr(self._store, "_base", None)
                if base is not None and base.exists():
                    wf_ids = sorted(
                        {
                            d.name
                            for d in base.iterdir()
                            if d.is_dir() and d.name != "_global"
                        }
                        | {workflow_id},
                    )
            except Exception:
                logger.debug("Global principle scan fallback to local", exc_info=True)

        prefix = "principle:"
        seen: dict[str, CausalPrinciple] = {}

        for wf in wf_ids:
            keys = await self._store.list_keys(wf, _DEFAULT_SESSION_ID)
            for key in keys:
                if not key.startswith(prefix):
                    continue
                entry = await self._store.read(wf, _DEFAULT_SESSION_ID, key)
                if entry is None or entry.value is None:
                    continue
                try:
                    principle = CausalPrinciple.model_validate(entry.value)
                except Exception:
                    logger.debug("Skipping malformed principle %s", key)
                    continue

                if principle.confidence < min_confidence:
                    continue
                if tags and not set(tags) & set(principle.tags):
                    continue

                existing = seen.get(principle.id)
                if existing is None or principle.confidence > existing.confidence:
                    seen[principle.id] = principle

        return list(seen.values())

    async def delete_principle(
        self, workflow_id: str, principle_id: str,
    ) -> bool:
        return await self._store.delete(
            workflow_id, _DEFAULT_SESSION_ID, f"principle:{principle_id}",
        )

    async def expire_principles(
        self, workflow_id: str, max_age_days: int,
    ) -> int:
        cutoff = time.time() - (max_age_days * 86400)
        keys = await self._store.list_keys(workflow_id, _DEFAULT_SESSION_ID)
        prefix = "principle:"

        removed = 0
        for key in keys:
            if not key.startswith(prefix):
                continue
            entry = await self._store.read(
                workflow_id, _DEFAULT_SESSION_ID, key,
            )
            if entry is None or entry.value is None:
                continue
            try:
                principle = CausalPrinciple.model_validate(entry.value)
            except Exception:
                continue
            if principle.created_at < cutoff:
                await self._store.delete(workflow_id, _DEFAULT_SESSION_ID, key)
                removed += 1
        return removed


# ---------------------------------------------------------------------------
# ErrorContextProvider
# ---------------------------------------------------------------------------


class ErrorContextProvider:
    """Builds a prompt section from past failures and learned principles.

    Injected into LLM node prompts so the model can avoid repeating
    previously observed mistakes.
    """

    def __init__(
        self,
        error_memory_index: ErrorMemoryIndex,
        max_tokens: int = 500,
        top_k: int = 5,
        principle_store: PrincipleStore | None = None,
    ) -> None:
        self._index = error_memory_index
        self._max_tokens = max_tokens
        self._top_k = top_k
        self._principle_store = principle_store

    async def get_context(
        self,
        node: Any,
        rendered_prompt: str,
        inputs: dict[str, Any],
        workflow_id: str,
        *,
        cross_workflow: bool = False,
    ) -> str:
        scope: Literal["workflow", "global"] = "global" if cross_workflow else "workflow"
        summary_parts = [
            f"Node: {getattr(node, 'id', 'unknown')} "
            f"({getattr(node, 'node_type', 'unknown')})",
        ]
        if inputs:
            input_keys = list(inputs.keys())[:10]
            summary_parts.append(f"Input keys: {', '.join(input_keys)}")
        if rendered_prompt:
            summary_parts.append(f"Task: {rendered_prompt[:300]}")
        summary = "\n".join(summary_parts)

        results = await self._index.query_similar(
            workflow_id, summary, top_k=self._top_k, scope=scope,
        )

        principles: list[CausalPrinciple] = []
        if self._principle_store:
            try:
                principles = await self._principle_store.load_principles(
                    workflow_id, min_confidence=0.3, scope=scope,
                )
            except Exception:
                logger.debug("Failed to load principles for context", exc_info=True)

        if not results and not principles:
            return ""

        max_chars = self._max_tokens * 4
        sections: list[str] = []

        if results:
            sections.append("## Relevant Past Failures\n\n")
            for i, chunk in enumerate(results, 1):
                meta = chunk.get("metadata", {})
                score = chunk.get("score", 0)
                entry = (
                    f"{i}. **{meta.get('error_category', 'unknown')}** "
                    f"in node `{meta.get('node_id', '?')}` "
                    f"(score: {score:.2f})\n"
                    f"   Message: "
                    f"{meta.get('error_message', chunk.get('text', ''))[:200]}\n"
                )
                sections.append(entry)

        if principles:
            sections.append("\n## Learned Principles\n\n")
            for p in principles[:5]:
                sections.append(
                    f"- If {p.condition}, then {p.action} "
                    f"(confidence: {p.confidence:.0%})\n"
                )

        full_text = "".join(sections)
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars].rsplit("\n", 1)[0] + "\n..."
        return full_text
