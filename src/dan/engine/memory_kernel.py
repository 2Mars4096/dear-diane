"""Unified typed memory kernel for the concierge layer.

Replaces the 6 siloed memory systems (ConversationMemory, UserProfile,
ExperienceStore, ErrorMemoryIndex, PrincipleStore, raw MemoryStore) with a
single store that organizes items by type, scope, and lifecycle, and retrieves
them through task-specific policies with per-type ranking.

Part of Phase 19 (plan 29-1).
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from pydantic import BaseModel, Field

from dan.keyword_overlap import query_keyword_overlap

logger = logging.getLogger(__name__)

evolvement_logger = logging.getLogger("dan.evolvement")
_evolvement_level = os.environ.get("DAN_EVOLVEMENT_LOG_LEVEL", "INFO").upper()
evolvement_logger.setLevel(getattr(logging, _evolvement_level, logging.INFO))


def _run_async_compat(awaitable: Any) -> Any:
    """Run async vector-store/embed calls from synchronous kernel code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - passthrough path
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MemoryType(str, enum.Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    WORKFLOW_PATTERN = "workflow_pattern"
    WORKFLOW_ASSET = "workflow_asset"
    FAILURE_PATTERN = "failure_pattern"
    PRINCIPLE = "principle"
    EPISODE = "episode"
    WORKING_STATE = "working_state"


class MemoryScope(str, enum.Enum):
    SESSION = "session"
    PROJECT = "project"
    WORKFLOW = "workflow"
    USER = "user"
    GLOBAL = "global"


class MemoryLifecycle(str, enum.Enum):
    ACTIVE = "active"
    DURABLE = "durable"
    ARCHIVE = "archive"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class Provenance(BaseModel):
    source_interaction_id: str | None = None
    source_run_id: str | None = None
    confirmed_by_user: bool = False


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    content: str
    memory_type: MemoryType
    scope: MemoryScope = MemoryScope.USER
    lifecycle: MemoryLifecycle = MemoryLifecycle.ACTIVE
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_accessed: float = Field(default_factory=time.time)
    access_count: int = 0
    provenance: Provenance = Field(default_factory=Provenance)
    tags: list[str] = Field(default_factory=list)
    related_ids: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ScoredMemoryItem:
    item: MemoryItem
    score: float
    match_reason: str = ""


class RetrievalPolicy(BaseModel):
    """Defines which memory types to query and how to allocate prompt budget."""

    task_type: str
    sections: list[MemoryType]
    budget_allocation: dict[str, float] = Field(default_factory=dict)
    max_items_per_section: int = 10
    max_total_items: int = 30


# ---------------------------------------------------------------------------
# Predefined policies
# ---------------------------------------------------------------------------

WORKFLOW_BUILD_POLICY = RetrievalPolicy(
    task_type="workflow_build",
    sections=[
        MemoryType.WORKING_STATE,
        MemoryType.PREFERENCE,
        MemoryType.FACT,
        MemoryType.WORKFLOW_PATTERN,
        MemoryType.WORKFLOW_ASSET,
        MemoryType.FAILURE_PATTERN,
        MemoryType.PRINCIPLE,
    ],
    budget_allocation={
        "working_state": 0.25,
        "preference": 0.10,
        "fact": 0.10,
        "workflow_pattern": 0.20,
        "workflow_asset": 0.15,
        "failure_pattern": 0.10,
        "principle": 0.10,
    },
)

FACTUAL_ANSWER_POLICY = RetrievalPolicy(
    task_type="factual_answer",
    sections=[
        MemoryType.WORKING_STATE,
        MemoryType.FACT,
        MemoryType.EPISODE,
        MemoryType.PREFERENCE,
    ],
    budget_allocation={
        "working_state": 0.30,
        "fact": 0.30,
        "episode": 0.20,
        "preference": 0.20,
    },
)

WORKFLOW_REPAIR_POLICY = RetrievalPolicy(
    task_type="workflow_repair",
    sections=[
        MemoryType.WORKING_STATE,
        MemoryType.FAILURE_PATTERN,
        MemoryType.PRINCIPLE,
        MemoryType.WORKFLOW_ASSET,
    ],
    budget_allocation={
        "working_state": 0.25,
        "failure_pattern": 0.30,
        "principle": 0.25,
        "workflow_asset": 0.20,
    },
)

GENERAL_CONVERSATION_POLICY = RetrievalPolicy(
    task_type="general_conversation",
    sections=[
        MemoryType.WORKING_STATE,
        MemoryType.PREFERENCE,
        MemoryType.FACT,
        MemoryType.EPISODE,
    ],
    budget_allocation={
        "working_state": 0.30,
        "preference": 0.25,
        "fact": 0.25,
        "episode": 0.20,
    },
)

POLICY_REGISTRY: dict[str, RetrievalPolicy] = {
    "workflow_build": WORKFLOW_BUILD_POLICY,
    "factual_answer": FACTUAL_ANSWER_POLICY,
    "workflow_repair": WORKFLOW_REPAIR_POLICY,
    "general_conversation": GENERAL_CONVERSATION_POLICY,
}


# ---------------------------------------------------------------------------
# Per-type ranking functions
# ---------------------------------------------------------------------------

def _rank_working_state(item: MemoryItem, query: str) -> float:
    return 1.0  # always highest within its scope


def _rank_preference(item: MemoryItem, query: str) -> float:
    score = 0.3
    if item.provenance.confirmed_by_user:
        score += 0.4
    score += min(item.access_count / 20, 0.2)
    score += _recency_bonus(item.last_accessed, half_life_days=30) * 0.1
    return min(score, 1.0)


def _rank_fact(item: MemoryItem, query: str) -> float:
    score = item.importance * 0.5
    scope_bonus = {
        MemoryScope.SESSION: 0.1,
        MemoryScope.PROJECT: 0.2,
        MemoryScope.WORKFLOW: 0.15,
        MemoryScope.USER: 0.25,
        MemoryScope.GLOBAL: 0.05,
    }
    score += scope_bonus.get(item.scope, 0.1)
    score += _recency_bonus(item.last_accessed, half_life_days=14) * 0.2
    score += _keyword_overlap(query, item.content) * 0.1
    return min(score, 1.0)


def _rank_workflow_pattern(item: MemoryItem, query: str) -> float:
    score = _keyword_overlap(query, item.content) * 0.4
    success_rate = item.metadata.get("success_rate", 0.5)
    score += success_rate * 0.3
    score += _recency_bonus(item.last_accessed, half_life_days=60) * 0.15
    score += item.importance * 0.15
    return min(score, 1.0)


def _rank_workflow_asset(item: MemoryItem, query: str) -> float:
    score = _keyword_overlap(query, item.content) * 0.35
    success_rate = item.metadata.get("success_rate", 0.5)
    score += success_rate * 0.3
    score += _recency_bonus(item.last_accessed, half_life_days=90) * 0.15
    score += item.importance * 0.2
    return min(score, 1.0)


def _rank_failure_pattern(item: MemoryItem, query: str) -> float:
    score = _keyword_overlap(query, item.content) * 0.35
    recurrence = min(item.access_count / 10, 1.0)
    score += recurrence * 0.3
    score += _recency_bonus(item.last_accessed, half_life_days=30) * 0.2
    score += item.importance * 0.15
    return min(score, 1.0)


def _rank_principle(item: MemoryItem, query: str) -> float:
    confidence = item.metadata.get("confidence", 0.5)
    score = confidence * 0.4
    score += _keyword_overlap(query, item.content) * 0.3
    recurrence = min(item.access_count / 10, 1.0)
    score += recurrence * 0.2
    score += _recency_bonus(item.last_accessed, half_life_days=60) * 0.1
    return min(score, 1.0)


def _rank_episode(item: MemoryItem, query: str) -> float:
    score = _recency_bonus(item.last_accessed, half_life_days=7) * 0.5
    score += _keyword_overlap(query, item.content) * 0.5
    return min(score, 1.0)


_TYPE_RANKERS = {
    MemoryType.WORKING_STATE: _rank_working_state,
    MemoryType.PREFERENCE: _rank_preference,
    MemoryType.FACT: _rank_fact,
    MemoryType.WORKFLOW_PATTERN: _rank_workflow_pattern,
    MemoryType.WORKFLOW_ASSET: _rank_workflow_asset,
    MemoryType.FAILURE_PATTERN: _rank_failure_pattern,
    MemoryType.PRINCIPLE: _rank_principle,
    MemoryType.EPISODE: _rank_episode,
}

_TYPE_SEMANTIC_WEIGHTS: dict[MemoryType, float] = {
    MemoryType.WORKING_STATE: 0.0,
    MemoryType.PREFERENCE: 0.15,
    MemoryType.FACT: 0.30,
    MemoryType.WORKFLOW_PATTERN: 0.65,
    MemoryType.WORKFLOW_ASSET: 0.65,
    MemoryType.FAILURE_PATTERN: 0.55,
    MemoryType.PRINCIPLE: 0.55,
    MemoryType.EPISODE: 0.35,
}


def _recency_bonus(last_accessed: float, half_life_days: float) -> float:
    age_days = (time.time() - last_accessed) / 86400
    return math.exp(-0.693 * age_days / max(half_life_days, 1))


def _preference_key(content: str) -> str:
    """Extract a canonical key from preference content for conflict detection.

    Preferences follow patterns like ``"models: drafting -> claude"`` or
    ``"output_format: latex"``.  The key is the category (plus sub-key when
    using ``->``), stripping the final value.

    Examples::

        "models: drafting -> claude"  →  "models: drafting"
        "output_format: latex"        →  "output_format"
        "domains: supply chain"       →  "domains"
    """
    if not content:
        return ""
    if "->" in content:
        return content.split("->", 1)[0].strip().lower()
    if ":" in content:
        return content.split(":", 1)[0].strip().lower()
    return content.strip().lower()


def _keyword_overlap(query: str, content: str) -> float:
    return query_keyword_overlap(query, content)


# ---------------------------------------------------------------------------
# Task-type classifier
# ---------------------------------------------------------------------------

_BUILD_KEYWORDS = {"build", "create", "make", "generate", "workflow", "pipeline", "automate"}
_REPAIR_KEYWORDS = {"fix", "repair", "debug", "error", "fail", "broken", "wrong"}
_FACTUAL_KEYWORDS = {"what", "when", "where", "who", "how much", "how many", "tell me", "explain"}


def classify_task_type(message: str, has_active_build: bool = False) -> str:
    lower = message.lower()
    words = set(lower.split())

    if has_active_build or words & _BUILD_KEYWORDS:
        return "workflow_build"
    if words & _REPAIR_KEYWORDS:
        return "workflow_repair"
    if words & _FACTUAL_KEYWORDS:
        return "factual_answer"
    return "general_conversation"


# ---------------------------------------------------------------------------
# Memory Kernel
# ---------------------------------------------------------------------------

class DualWriteAdapter:
    """Thin adapter that writes new memory items to legacy stores (29-1 §5-7).

    Enabled via ``DAN_MEMORY_DUAL_WRITE=1`` (default off). Maps kernel
    MemoryType to the appropriate legacy store write call.
    """

    def __init__(
        self,
        conversation_memory: Any = None,
        user_profile: Any = None,
        experience_store: Any = None,
    ) -> None:
        self._conversation_memory = conversation_memory
        self._user_profile = user_profile
        self._experience_store = experience_store

    def write(self, item: MemoryItem) -> None:
        """Best-effort write to legacy stores. Never raises."""
        try:
            self._do_write(item)
        except Exception:
            logger.debug("Dual-write to legacy store failed for %s", item.id, exc_info=True)

    def _do_write(self, item: MemoryItem) -> None:
        if item.memory_type == MemoryType.EPISODE and self._conversation_memory:
            if hasattr(self._conversation_memory, "add_summary"):
                self._conversation_memory.add_summary(
                    summary=item.content,
                    workflow_id=str(item.metadata.get("workflow_id", "") or ""),
                    topic_tags=list(item.tags or []),
                )
            elif hasattr(self._conversation_memory, "add"):
                self._conversation_memory.add(item.content)
        elif item.memory_type == MemoryType.PREFERENCE and self._user_profile:
            self._write_preference_to_profile(item)
        elif item.memory_type == MemoryType.FACT and self._user_profile:
            self._write_fact_to_profile(item)
        elif item.memory_type == MemoryType.WORKFLOW_ASSET and self._experience_store:
            self._write_workflow_asset(item)

    def _save_profile_if_needed(self, changed: bool) -> None:
        if not changed:
            return
        from dan.engine.user_profile import save_user_profile

        save_user_profile(self._user_profile)

    def _write_preference_to_profile(self, item: MemoryItem) -> None:
        text = item.content.strip()
        changed = False
        if hasattr(self._user_profile, "merge_preferences"):
            if text.startswith("models:") and "->" in text:
                model_payload = text.split(":", 1)[1].strip()
                task_type, model_name = [part.strip() for part in model_payload.split("->", 1)]
                before = dict(getattr(self._user_profile, "preferred_models", {}) or {})
                self._user_profile.merge_preferences(models={task_type: model_name})
                changed = before != dict(getattr(self._user_profile, "preferred_models", {}) or {})
            elif text.startswith("domains:"):
                domain = text.split(":", 1)[1].strip()
                before = list(getattr(self._user_profile, "common_domains", []) or [])
                self._user_profile.merge_preferences(domains=[domain])
                changed = before != list(getattr(self._user_profile, "common_domains", []) or [])
            elif text.startswith("output_format:"):
                output_format = text.split(":", 1)[1].strip()
                before = str(getattr(self._user_profile, "preferred_output_format", "") or "")
                self._user_profile.merge_preferences(output_format=output_format)
                changed = before != str(getattr(self._user_profile, "preferred_output_format", "") or "")
        self._save_profile_if_needed(changed)
        if hasattr(self._user_profile, "set"):
            self._user_profile.set(f"pref_{item.id}", item.content)

    def _write_fact_to_profile(self, item: MemoryItem) -> None:
        changed = False
        if hasattr(self._user_profile, "merge_search_dirs"):
            path_value = str(item.metadata.get("path", "") or "").strip()
            if not path_value and ":" in item.content:
                path_value = item.content.split(":", 1)[1].strip()
            if path_value and (
                item.metadata.get("is_directory") or "search_dir" in (item.tags or [])
            ):
                changed = bool(self._user_profile.merge_search_dirs([path_value]))
        self._save_profile_if_needed(changed)
        if hasattr(self._user_profile, "set"):
            self._user_profile.set(f"fact_{item.id}", item.content)

    def _write_workflow_asset(self, item: MemoryItem) -> None:
        wf_id = str(item.metadata.get("workflow_id") or item.id)
        if hasattr(self._experience_store, "record"):
            self._experience_store.record(
                workflow_id=wf_id,
                name=item.content[:80],
                summary=item.content[:200],
            )
            return
        if hasattr(self._experience_store, "save_experience"):
            from dan.engine.experience import WorkflowExperience

            experience = WorkflowExperience(
                workflow_id=wf_id,
                name=item.content[:80],
                description=item.content[:200],
                tags=list(item.tags or []),
            )
            result = self._experience_store.save_experience(experience)
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    loop.create_task(result)


class MemoryKernel:
    """Unified typed memory store with per-type ranking and policy-based retrieval.

    Internally delegates persistence to a ``MemoryBackend`` (31-15 §6-5).
    A lightweight in-memory type index (``_type_index``) eliminates linear
    scans in ``list_by_type`` and ``retrieve`` (§6-6).
    """

    def __init__(
        self,
        base_dir: str | None = None,
        dual_write_adapter: DualWriteAdapter | None = None,
        backend: Any | None = None,
        embedding_provider: Any | None = None,
        embedding_model: str | None = None,
        vector_store: Any | None = None,
        *,
        domain_consolidation_hook: Callable[["MemoryKernel"], int] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir or os.path.expanduser("~/.dan/memory_kernel"))
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, MemoryItem] = {}
        self._type_index: dict[str, list[str]] = {}
        self._write_lock = threading.RLock()
        self._dirty = False
        self._dual_write = dual_write_adapter if os.environ.get("DAN_MEMORY_DUAL_WRITE", "0") == "1" else None
        self._journal_ops_since_snapshot = 0
        self._journal_compact_every = max(
            1,
            int(os.environ.get("DAN_MEMORY_JOURNAL_COMPACT_EVERY", "100")),
        )
        self._embedding_model = (
            str(embedding_model or "").strip()
            or os.environ.get("DAN_MEMORY_EMBEDDING_MODEL", "").strip()
            or os.environ.get("DAN_DEFAULT_EMBEDDING_MODEL", "").strip()
            or "text-embedding-3-small"
        )
        self._embedding_provider = embedding_provider or self._resolve_embedding_provider()
        self._vector_collection = "memory_items"
        self._vector_dimensions: int | None = None
        self._vector_store = vector_store or self._create_vector_store()

        if backend is not None:
            self._backend = backend
        else:
            from dan.engine.learning_tiers import resolve_memory_backend
            self._backend = resolve_memory_backend(self._base_dir)

        self._load_index()
        self._rebuild_vector_index()
        # Injected by composition roots (server / chat_factory) — see
        # ``domain_learning.consolidate_memory_kernel_domain_templates``.
        self._domain_consolidation_hook = domain_consolidation_hook

    # -- Type index helpers -------------------------------------------------

    def _type_index_add(self, item: MemoryItem) -> None:
        key = item.memory_type.value
        bucket = self._type_index.get(key)
        if bucket is None:
            self._type_index[key] = [item.id]
        elif item.id not in bucket:
            bucket.append(item.id)

    def _type_index_remove(self, item: MemoryItem) -> None:
        bucket = self._type_index.get(item.memory_type.value)
        if bucket is not None:
            try:
                bucket.remove(item.id)
            except ValueError:
                pass

    def _rebuild_type_index(self) -> None:
        self._type_index.clear()
        for item in self._index.values():
            self._type_index_add(item)

    # -- Embedding / vector helpers ----------------------------------------

    def _resolve_embedding_provider(self) -> Any | None:
        try:
            from types import SimpleNamespace
            from dan.rag import build_embedding_registry

            api_key = (
                os.environ.get("DAN_LLM_API_KEY", "").strip()
                or os.environ.get("OPENAI_API_KEY", "").strip()
            )
            base_url = (
                os.environ.get("DAN_LLM_BASE_URL", "").strip()
                or os.environ.get("OPENAI_BASE_URL", "").strip()
            )
            provider = os.environ.get("DAN_MEMORY_EMBEDDING_PROVIDER", "").strip().lower()
            config = SimpleNamespace(
                llm_api_key=api_key,
                llm_base_url=base_url,
                default_embedding_model=self._embedding_model,
                embedding_providers={},
                embedding_model_provider_map={},
            )
            if provider == "local":
                config.embedding_providers = {
                    "local": SimpleNamespace(default_model=self._embedding_model),
                }
                config.embedding_model_provider_map = {self._embedding_model: "local"}

            registry = build_embedding_registry(config)
            if provider == "local" and registry.has_provider("local"):
                return registry.resolve(self._embedding_model)
            if registry.has_provider("default"):
                return registry.resolve(self._embedding_model)
        except Exception:
            logger.debug("Memory kernel embedding provider unavailable", exc_info=True)
        return None

    def _create_vector_store(self) -> Any | None:
        try:
            from dan.rag.stores import VectorStoreConfig, VectorStoreFactory

            backend = os.environ.get("DAN_MEMORY_VECTOR_BACKEND", "memory").strip() or "memory"
            persist_dir = self._base_dir / "vector_index"
            return VectorStoreFactory.create(
                VectorStoreConfig(
                    backend=backend,
                    persist_directory=str(persist_dir),
                    collection_name=self._vector_collection,
                )
            )
        except Exception:
            logger.debug("Memory kernel vector store unavailable", exc_info=True)
            return None

    @staticmethod
    def _semantic_score(raw_score: float) -> float:
        bounded = max(min(raw_score, 1.0), -1.0)
        return (bounded + 1.0) / 2.0

    def _document_record_for_item(self, item: MemoryItem) -> Any | None:
        if item.embedding is None or item.lifecycle == MemoryLifecycle.ARCHIVE:
            return None
        try:
            from dan.rag.stores import DocumentRecord

            return DocumentRecord(
                id=item.id,
                text=item.content,
                embedding=list(item.embedding),
                metadata={
                    "memory_type": item.memory_type.value,
                    "scope": item.scope.value,
                    "lifecycle": item.lifecycle.value,
                    "project_id": item.metadata.get("project_id"),
                },
            )
        except Exception:
            logger.debug("Failed to create vector document for %s", item.id, exc_info=True)
            return None

    def _ensure_vector_collection(self, dimensions: int) -> None:
        if self._vector_store is None or dimensions <= 0:
            return
        if self._vector_dimensions == dimensions:
            return
        _run_async_compat(
            self._vector_store.create_collection(self._vector_collection, dimensions),
        )
        self._vector_dimensions = dimensions

    def _upsert_vector_item(self, item: MemoryItem) -> None:
        if self._vector_store is None:
            return
        record = self._document_record_for_item(item)
        try:
            if record is None:
                _run_async_compat(
                    self._vector_store.delete_by_ids(self._vector_collection, [item.id]),
                )
                return
            self._ensure_vector_collection(len(record.embedding or []))
            _run_async_compat(self._vector_store.add(self._vector_collection, [record]))
        except Exception:
            logger.debug("Vector upsert failed for %s", item.id, exc_info=True)

    def _delete_vector_item(self, item_id: str) -> None:
        if self._vector_store is None:
            return
        try:
            _run_async_compat(
                self._vector_store.delete_by_ids(self._vector_collection, [item_id]),
            )
        except Exception:
            logger.debug("Vector delete failed for %s", item_id, exc_info=True)

    def _rebuild_vector_index(self) -> None:
        if self._vector_store is None:
            return
        records: list[Any] = []
        dimensions = 0
        try:
            for item in self._index.values():
                record = self._document_record_for_item(item)
                if record is None:
                    continue
                records.append(record)
                if not dimensions and record.embedding:
                    dimensions = len(record.embedding)
            if dimensions:
                _run_async_compat(
                    self._vector_store.delete_collection(self._vector_collection),
                )
                _run_async_compat(
                    self._vector_store.create_collection(self._vector_collection, dimensions),
                )
                _run_async_compat(self._vector_store.add(self._vector_collection, records))
                self._vector_dimensions = dimensions
        except Exception:
            logger.debug("Failed to rebuild memory vector index", exc_info=True)

    def _embed_query(self, query: str) -> list[float] | None:
        if self._embedding_provider is None or not query.strip():
            return None
        try:
            result = _run_async_compat(
                self._embedding_provider.embed([query], self._embedding_model),
            )
            vectors = getattr(result, "vectors", None) or []
            if vectors:
                return list(vectors[0])
        except Exception:
            logger.debug("Memory query embedding failed", exc_info=True)
        return None

    def _semantic_hits(
        self,
        memory_type: MemoryType,
        query: str,
        *,
        top_k: int,
        query_vector: list[float] | None = None,
    ) -> dict[str, float]:
        if self._vector_store is None:
            return {}
        vector = query_vector or self._embed_query(query)
        if not vector:
            return {}
        try:
            result = _run_async_compat(
                self._vector_store.query(
                    self._vector_collection,
                    vector,
                    top_k=max(top_k, 1),
                    filters={"memory_type": memory_type.value},
                )
            )
        except Exception:
            logger.debug("Memory vector query failed", exc_info=True)
            return {}

        hits: dict[str, float] = {}
        for chunk in getattr(result, "chunks", []) or []:
            item_id = str(chunk.get("id") or "").strip()
            if item_id:
                hits[item_id] = float(chunk.get("score", 0.0) or 0.0)
        return hits

    # -- Persistence --------------------------------------------------------

    def _index_path(self) -> Path:
        return self._base_dir / "_index.json"

    def _journal_path(self) -> Path:
        return self._base_dir / "_index.journal.jsonl"

    def _apply_loaded_item(self, item: MemoryItem) -> None:
        existing = self._index.get(item.id)
        if existing is not None and existing.memory_type != item.memory_type:
            self._type_index_remove(existing)
        self._index[item.id] = item
        self._type_index_add(item)

    def _append_journal_entry(self, entry: dict[str, Any]) -> None:
        path = self._journal_path()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
        self._journal_ops_since_snapshot += 1
        self._dirty = True

    def _persist_item_change(self, item: MemoryItem) -> None:
        self._append_journal_entry({
            "op": "upsert",
            "item": item.model_dump(mode="json"),
        })
        self._maybe_compact_journal()

    def _persist_delete(self, item_id: str) -> None:
        self._append_journal_entry({
            "op": "delete",
            "item_id": item_id,
        })
        self._maybe_compact_journal()

    def _apply_journal_entry(self, entry: dict[str, Any]) -> None:
        op = str(entry.get("op") or "").strip().lower()
        if op == "delete":
            removed = self._index.pop(str(entry.get("item_id") or ""), None)
            if removed is not None:
                self._type_index_remove(removed)
            return
        if op != "upsert":
            return
        item_data = entry.get("item")
        if not isinstance(item_data, dict):
            return
        item = MemoryItem.model_validate(item_data)
        self._apply_loaded_item(item)

    def _maybe_compact_journal(self) -> None:
        if self._journal_ops_since_snapshot >= self._journal_compact_every:
            self._save_index()

    def _load_index(self) -> None:
        with self._write_lock:
            self._index = {}
            self._type_index = {}
            snapshot_path = self._index_path()
            if snapshot_path.exists():
                try:
                    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
                    for item_dict in data:
                        self._apply_loaded_item(MemoryItem.model_validate(item_dict))
                except Exception:
                    logger.warning("Failed to load memory snapshot, starting fresh", exc_info=True)
                    self._index = {}
                    self._type_index = {}
            journal_path = self._journal_path()
            if journal_path.exists():
                try:
                    for line in journal_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        self._apply_journal_entry(json.loads(line))
                except Exception:
                    logger.warning("Failed to replay memory journal", exc_info=True)
            logger.debug("Loaded %d memory items from index/journal", len(self._index))

    def _save_index(self) -> None:
        with self._write_lock:
            path = self._index_path()
            data = [item.model_dump(mode="json") for item in self._index.values()]
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
            try:
                tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
                os.replace(tmp, path)
                self._journal_path().unlink(missing_ok=True)
                self._journal_ops_since_snapshot = 0
                self._dirty = False
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass

    # -- CRUD ---------------------------------------------------------------

    def store(self, item: MemoryItem) -> MemoryItem:
        with self._write_lock:
            item.updated_at = time.time()
            self._apply_loaded_item(item)
            self._persist_item_change(item)
            self._upsert_vector_item(item)
        evolvement_logger.debug(
            "Memory stored: type=%s scope=%s id=%s",
            item.memory_type.value, item.scope.value, item.id,
        )
        if self._dual_write:
            self._dual_write.write(item)
        return item

    def store_many(self, items: Sequence[MemoryItem]) -> list[MemoryItem]:
        with self._write_lock:
            now = time.time()
            for item in items:
                item.updated_at = now
                self._apply_loaded_item(item)
                self._append_journal_entry({
                    "op": "upsert",
                    "item": item.model_dump(mode="json"),
                })
                self._upsert_vector_item(item)
            self._maybe_compact_journal()
        if self._dual_write:
            for item in items:
                self._dual_write.write(item)
        return list(items)

    def get(self, item_id: str) -> MemoryItem | None:
        with self._write_lock:
            return self._index.get(item_id)

    def update(self, item_id: str, **changes: Any) -> MemoryItem | None:
        with self._write_lock:
            item = self._index.get(item_id)
            if item is None:
                return None
            original_type = item.memory_type
            for k, v in changes.items():
                if hasattr(item, k):
                    setattr(item, k, v)
            item.updated_at = time.time()
            if item.memory_type != original_type:
                original_bucket = self._type_index.get(original_type.value)
                if original_bucket is not None:
                    try:
                        original_bucket.remove(item.id)
                    except ValueError:
                        pass
                self._type_index_add(item)
            self._persist_item_change(item)
            self._upsert_vector_item(item)
            return item

    def delete(self, item_id: str, hard: bool = False) -> bool:
        with self._write_lock:
            if hard:
                removed = self._index.pop(item_id, None)
                if removed is not None:
                    self._type_index_remove(removed)
                    self._persist_delete(item_id)
                    self._delete_vector_item(item_id)
            else:
                item = self._index.get(item_id)
                if item is None:
                    return False
                item.lifecycle = MemoryLifecycle.ARCHIVE
                item.updated_at = time.time()
                removed = item
                self._persist_item_change(item)
                self._delete_vector_item(item_id)
            return removed is not None

    def list_by_type(
        self,
        memory_type: MemoryType,
        scope: MemoryScope | None = None,
        lifecycle: MemoryLifecycle | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        with self._write_lock:
            ids = self._type_index.get(memory_type.value, [])
            results = []
            for item_id in ids:
                item = self._index.get(item_id)
                if item is None:
                    continue
                if scope and item.scope != scope:
                    continue
                if lifecycle is not None:
                    if item.lifecycle != lifecycle:
                        continue
                elif item.lifecycle == MemoryLifecycle.ARCHIVE:
                    continue
                results.append(item)
            results.sort(key=lambda x: x.updated_at, reverse=True)
            return results[:limit]

    # -- Retrieval ----------------------------------------------------------

    def retrieve(
        self,
        query: str,
        policy: RetrievalPolicy | str | None = None,
        limit: int | None = None,
        project_id: str | None = None,
    ) -> list[ScoredMemoryItem]:
        if isinstance(policy, str):
            policy = POLICY_REGISTRY.get(policy, GENERAL_CONVERSATION_POLICY)
        if policy is None:
            policy = GENERAL_CONVERSATION_POLICY

        max_total = limit or policy.max_total_items
        all_scored: list[ScoredMemoryItem] = []
        n_sections = len(policy.sections) or 1
        query_vector = self._embed_query(query)

        semantic_hits_by_type = {
            mem_type: self._semantic_hits(
                mem_type,
                query,
                top_k=max_total * 4,
                query_vector=query_vector,
            )
            for mem_type in policy.sections
        }

        with self._write_lock:
            for mem_type in policy.sections:
                ranker = _TYPE_RANKERS.get(mem_type, _rank_episode)
                weight = policy.budget_allocation.get(mem_type.value, 1.0 / n_sections)
                section_limit = max(1, int(max_total * weight))

                type_ids = self._type_index.get(mem_type.value, [])
                candidates = []
                for iid in type_ids:
                    if iid not in self._index:
                        continue
                    item = self._index[iid]
                    if item.lifecycle == MemoryLifecycle.ARCHIVE:
                        continue
                    if item.scope == MemoryScope.PROJECT:
                        item_proj = item.metadata.get("project_id")
                        if project_id and item_proj and item_proj != project_id:
                            continue
                    candidates.append(item)

                scored = []
                _PROJECT_MATCH_BONUS = 0.3
                semantic_hits = semantic_hits_by_type.get(mem_type, {})
                semantic_weight = _TYPE_SEMANTIC_WEIGHTS.get(mem_type, 0.0)
                for item in candidates:
                    score = ranker(item, query)
                    semantic_raw = semantic_hits.get(item.id)
                    if semantic_raw is not None and semantic_weight > 0:
                        semantic_score = self._semantic_score(semantic_raw)
                        score = (
                            score * (1.0 - semantic_weight)
                            + semantic_score * semantic_weight
                        )
                    if (
                        project_id
                        and item.scope == MemoryScope.PROJECT
                        and item.metadata.get("project_id") == project_id
                    ):
                        score = min(score + _PROJECT_MATCH_BONUS, 1.0)
                    scored.append(ScoredMemoryItem(
                        item=item,
                        score=score,
                        match_reason=(
                            f"{mem_type.value}+semantic"
                            if semantic_raw is not None and semantic_weight > 0
                            else mem_type.value
                        ),
                    ))

                scored.sort(key=lambda x: x.score, reverse=True)
                all_scored.extend(scored[:section_limit])

            all_scored.sort(key=lambda x: x.score, reverse=True)

            now = time.time()
            for si in all_scored[:max_total]:
                si.item.last_accessed = now
                si.item.access_count += 1
                self._persist_item_change(si.item)
            return all_scored[:max_total]

    def retrieve_by_task(
        self,
        query: str,
        task_type: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[ScoredMemoryItem]:
        policy_key = task_type or classify_task_type(query)
        policy = POLICY_REGISTRY.get(policy_key, GENERAL_CONVERSATION_POLICY)
        return self.retrieve(query, policy=policy, limit=limit, project_id=project_id)

    # -- Convenience --------------------------------------------------------

    def store_fact(
        self,
        content: str,
        scope: MemoryScope = MemoryScope.USER,
        project_id: str | None = None,
        **kw: Any,
    ) -> MemoryItem:
        if project_id:
            scope = MemoryScope.PROJECT
            meta = dict(kw.pop("metadata", None) or {})
            meta["project_id"] = project_id
            kw["metadata"] = meta
        return self.store(MemoryItem(content=content, memory_type=MemoryType.FACT, scope=scope, **kw))

    def store_preference(
        self,
        content: str,
        confirmed: bool = False,
        project_id: str | None = None,
        **kw: Any,
    ) -> MemoryItem | None:
        skip = self._resolve_preference_conflicts(content, confirmed)
        if skip:
            return None
        scope = MemoryScope.USER
        if project_id:
            scope = MemoryScope.PROJECT
            meta = dict(kw.pop("metadata", None) or {})
            meta["project_id"] = project_id
            kw["metadata"] = meta
        return self.store(MemoryItem(
            content=content,
            memory_type=MemoryType.PREFERENCE,
            scope=scope,
            provenance=Provenance(confirmed_by_user=confirmed),
            **kw,
        ))

    def store_episode(self, content: str, scope: MemoryScope = MemoryScope.SESSION, **kw: Any) -> MemoryItem:
        return self.store(MemoryItem(content=content, memory_type=MemoryType.EPISODE, scope=scope, **kw))

    def store_principle(
        self,
        content: str,
        confidence: float = 0.5,
        project_id: str | None = None,
        **kw: Any,
    ) -> MemoryItem:
        scope = MemoryScope.GLOBAL
        meta: dict[str, Any] = {"confidence": confidence}
        if project_id:
            scope = MemoryScope.PROJECT
            meta["project_id"] = project_id
        if "metadata" in kw:
            meta.update(kw.pop("metadata"))
        return self.store(MemoryItem(
            content=content,
            memory_type=MemoryType.PRINCIPLE,
            scope=scope,
            metadata=meta,
            **kw,
        ))

    def store_workflow_asset(self, content: str, workflow_id: str, success_rate: float = 0.0, **kw: Any) -> MemoryItem:
        return self.store(MemoryItem(
            content=content,
            memory_type=MemoryType.WORKFLOW_ASSET,
            scope=MemoryScope.USER,
            metadata={"workflow_id": workflow_id, "success_rate": success_rate},
            **kw,
        ))

    def store_failure_pattern(self, content: str, **kw: Any) -> MemoryItem:
        return self.store(MemoryItem(
            content=content,
            memory_type=MemoryType.FAILURE_PATTERN,
            scope=MemoryScope.GLOBAL,
            **kw,
        ))

    def increment_workflow_asset_usage(self, workflow_id: str, success: bool) -> bool:
        """Record a real reuse/adapt attempt on a workflow asset.

        Retrieval already bumps ``access_count`` when an item is surfaced. Keep a
        separate reuse counter here so ``success_rate`` reflects actual reuse
        outcomes instead of search exposure volume.
        """
        with self._write_lock:
            for item in self._index.values():
                if (
                    item.memory_type == MemoryType.WORKFLOW_ASSET
                    and item.lifecycle != MemoryLifecycle.ARCHIVE
                    and item.metadata.get("workflow_id") == workflow_id
                ):
                    item.access_count += 1
                    reuse_count = int(item.metadata.get("reuse_count", 0) or 0)
                    success_count = int(item.metadata.get("reuse_success_count", 0) or 0)
                    reuse_count += 1
                    if success:
                        success_count += 1
                    item.metadata["reuse_count"] = reuse_count
                    item.metadata["reuse_success_count"] = success_count
                    item.metadata["success_count"] = success_count
                    item.metadata["success_rate"] = min(success_count / max(reuse_count, 1), 1.0)
                    item.last_accessed = time.time()
                    item.updated_at = time.time()
                    self._persist_item_change(item)
                    self._upsert_vector_item(item)
                    return True
            return False

    # -- Preference evolution (29-6 §6) ------------------------------------

    def _resolve_preference_conflicts(self, new_content: str, new_confirmed: bool) -> bool:
        """Demote/archive existing preferences that conflict with *new_content*.

        Priority: explicit user statement (confirmed) > inferred from behavior > default.
        A "conflict" is detected when both preferences share the same tag-set prefix
        (e.g. both are "models: drafting -> ...") but differ in value.

        Returns True if the new preference should be **skipped** (not stored).
        """
        with self._write_lock:
            prefix = _preference_key(new_content)
            if not prefix:
                return False
            for item in list(self._index.values()):
                if item.memory_type != MemoryType.PREFERENCE:
                    continue
                if item.lifecycle == MemoryLifecycle.ARCHIVE:
                    continue
                existing_prefix = _preference_key(item.content)
                if existing_prefix != prefix:
                    continue
                if item.content == new_content:
                    continue
                if new_confirmed:
                    item.lifecycle = MemoryLifecycle.ARCHIVE
                    item.metadata["superseded_by"] = new_content
                    item.updated_at = time.time()
                    self._persist_item_change(item)
                    self._delete_vector_item(item.id)
                elif item.provenance.confirmed_by_user:
                    return True
                else:
                    item.importance = max(0.0, item.importance * 0.5)
                    item.metadata["conflict_demoted"] = True
                    item.updated_at = time.time()
                    self._persist_item_change(item)
                    self._upsert_vector_item(item)
            return False

    def maybe_surface_preferences(self, session_count: int | None = None) -> list[MemoryItem]:
        """Return accumulated preferences that should be surfaced for user confirmation.

        Surfaces after every ``DAN_PREFERENCE_SURFACE_INTERVAL`` interactions
        (default 10). Only returns non-confirmed, non-archived preferences with
        importance >= 0.3.
        """
        with self._write_lock:
            interval = int(os.environ.get("DAN_PREFERENCE_SURFACE_INTERVAL", "10"))
            if session_count is not None and (session_count % interval != 0 or session_count == 0):
                return []
            candidates = [
                item for item in self._index.values()
                if item.memory_type == MemoryType.PREFERENCE
                and item.lifecycle != MemoryLifecycle.ARCHIVE
                and not item.provenance.confirmed_by_user
                and item.importance >= 0.3
            ]
            candidates.sort(key=lambda x: x.access_count, reverse=True)
            return candidates[:5]

    def confirm_preference(self, item_id: str) -> MemoryItem | None:
        """Boost a preference to maximum importance upon user confirmation."""
        with self._write_lock:
            item = self._index.get(item_id)
            if item is None or item.memory_type != MemoryType.PREFERENCE:
                return None
            item.provenance.confirmed_by_user = True
            item.importance = 1.0
            item.lifecycle = MemoryLifecycle.DURABLE
            item.updated_at = time.time()
            self._persist_item_change(item)
            self._upsert_vector_item(item)
            return item

    def reject_preference(self, item_id: str) -> MemoryItem | None:
        """Archive a preference that the user explicitly rejects."""
        with self._write_lock:
            item = self._index.get(item_id)
            if item is None or item.memory_type != MemoryType.PREFERENCE:
                return None
            item.lifecycle = MemoryLifecycle.ARCHIVE
            item.metadata["rejected_by_user"] = True
            item.updated_at = time.time()
            self._persist_item_change(item)
            self._delete_vector_item(item.id)
            return item

    # -- Utility ------------------------------------------------------------

    def flush(self) -> None:
        """Force-save pending access-time updates to disk."""
        with self._write_lock:
            if self._dirty:
                self._save_index()
                self._dirty = False

    @property
    def count(self) -> int:
        with self._write_lock:
            return len(self._index)

    def stats(self) -> dict[str, Any]:
        with self._write_lock:
            by_type: dict[str, int] = {}
            by_lifecycle: dict[str, int] = {}
            for item in self._index.values():
                by_type[item.memory_type.value] = by_type.get(item.memory_type.value, 0) + 1
                by_lifecycle[item.lifecycle.value] = by_lifecycle.get(item.lifecycle.value, 0) + 1
            return {
                "total": len(self._index),
                "by_type": by_type,
                "by_lifecycle": by_lifecycle,
                "storage_path": str(self._base_dir),
            }

    # -- Consolidation (basic) ----------------------------------------------
    # Internal no-save variants — mutate _index in place without disk I/O.
    # Used by run_consolidation_async to fan out and save once.

    def _apply_decay_nosave(self, decay_days: int = 60, decay_factor: float = 0.9) -> int:
        with self._write_lock:
            threshold = time.time() - decay_days * 86400
            decayed = 0
            for item in self._index.values():
                if item.lifecycle == MemoryLifecycle.ARCHIVE:
                    continue
                if item.last_accessed < threshold and item.importance > 0.05:
                    item.importance *= decay_factor
                    decayed += 1
            return decayed

    def _promote_to_durable_nosave(self, age_hours: float = 24.0) -> int:
        with self._write_lock:
            threshold = time.time() - age_hours * 3600
            promoted = 0
            for item in self._index.values():
                if item.lifecycle == MemoryLifecycle.ACTIVE and item.created_at < threshold:
                    item.lifecycle = MemoryLifecycle.DURABLE
                    promoted += 1
            return promoted

    def _archive_stale_nosave(self, stale_days: int = 30) -> int:
        with self._write_lock:
            threshold = time.time() - stale_days * 86400
            archived = 0
            for item in self._index.values():
                if item.lifecycle == MemoryLifecycle.DURABLE and item.last_accessed < threshold:
                    item.lifecycle = MemoryLifecycle.ARCHIVE
                    archived += 1
            return archived

    def apply_decay(self, decay_days: int = 60, decay_factor: float = 0.9) -> int:
        decayed = self._apply_decay_nosave(decay_days, decay_factor)
        if decayed:
            self._save_index()
        return decayed

    def promote_to_durable(self, age_hours: float = 24.0) -> int:
        promoted = self._promote_to_durable_nosave(age_hours)
        if promoted:
            self._save_index()
        return promoted

    def archive_stale(self, stale_days: int = 30) -> int:
        archived = self._archive_stale_nosave(stale_days)
        if archived:
            self._save_index()
            self._rebuild_vector_index()
        return archived

    def _consolidate_domain_templates(self) -> int:
        """Consolidate domain knowledge items and update templates (31-21 task 7).

        Implementation lives in ``concierge.domain_learning`` and is injected via
        ``domain_consolidation_hook`` so this module stays free of ``server/``
        imports.
        """
        hook = self._domain_consolidation_hook
        if hook is None:
            return 0
        try:
            return int(hook(self))
        except Exception:
            logger.debug("Domain template consolidation failed", exc_info=True)
            return 0

    def run_consolidation(self, graph_store: Any = None) -> dict[str, int]:
        """Run all consolidation steps sequentially. Returns counts of items affected.

        When *graph_store* is provided, also runs ``PatternExtractor`` to
        discover recurring workflow sub-structures (29-6 §4).
        Prefer :meth:`run_consolidation_async` when an event loop is available.
        """
        promoted = self.promote_to_durable()
        archived = self.archive_stale()
        decayed = self.apply_decay()
        evolvement_logger.info(
            "Consolidation: promoted=%d archived=%d decayed=%d",
            promoted, archived, decayed,
        )
        patterns_extracted = self._run_pattern_extraction(graph_store)
        domains_consolidated = self._consolidate_domain_templates()
        return {
            "promoted": promoted,
            "archived": archived,
            "decayed": decayed,
            "patterns_extracted": patterns_extracted,
            "domains_consolidated": domains_consolidated,
        }

    async def run_consolidation_async(self, graph_store: Any = None) -> dict[str, int]:
        """Fan out independent consolidation sections (29-5 §5-3).

        Promote and decay are independent and run concurrently.
        Archive depends on promote (ACTIVE->DURABLE->ARCHIVE ordering) so runs after.
        Single disk save at the end to avoid redundant writes.
        """
        import asyncio

        promoted_fut = asyncio.to_thread(self._promote_to_durable_nosave)
        decayed_fut = asyncio.to_thread(self._apply_decay_nosave)
        promoted, decayed = await asyncio.gather(promoted_fut, decayed_fut)

        archived = self._archive_stale_nosave()

        if promoted or archived or decayed:
            self._save_index()
            if archived:
                self._rebuild_vector_index()

        evolvement_logger.info(
            "Consolidation (async): promoted=%d archived=%d decayed=%d",
            promoted, archived, decayed,
        )
        patterns_extracted = self._run_pattern_extraction(graph_store)
        domains_consolidated = await asyncio.to_thread(self._consolidate_domain_templates)
        return {
            "promoted": promoted,
            "archived": archived,
            "decayed": decayed,
            "patterns_extracted": patterns_extracted,
            "domains_consolidated": domains_consolidated,
        }

    def _run_pattern_extraction(self, graph_store: Any) -> int:
        if graph_store is None:
            return 0
        try:
            from dan.engine.pattern_extractor import PatternExtractor

            extractor = PatternExtractor(self, graph_store)
            if extractor.should_run():
                return len(extractor.extract_patterns())
        except Exception:
            logger.debug("Pattern extraction failed during consolidation", exc_info=True)
        return 0
