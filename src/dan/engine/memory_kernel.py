"""Unified typed memory kernel for the concierge layer.

Replaces the 6 siloed memory systems (ConversationMemory, UserProfile,
ExperienceStore, ErrorMemoryIndex, PrincipleStore, raw MemoryStore) with a
single store that organizes items by type, scope, and lifecycle, and retrieves
them through task-specific policies with per-type ranking.

Part of Phase 19 (plan 29-1).
"""

from __future__ import annotations

import enum
import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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


def _recency_bonus(last_accessed: float, half_life_days: float) -> float:
    age_days = (time.time() - last_accessed) / 86400
    return math.exp(-0.693 * age_days / max(half_life_days, 1))


def _keyword_overlap(query: str, content: str) -> float:
    if not query or not content:
        return 0.0
    q_words = set(query.lower().split())
    c_words = set(content.lower().split())
    if not q_words:
        return 0.0
    return len(q_words & c_words) / len(q_words)


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

class MemoryKernel:
    """Unified typed memory store with per-type ranking and policy-based retrieval."""

    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = Path(base_dir or os.path.expanduser("~/.dan/memory_kernel"))
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._index: dict[str, MemoryItem] = {}
        self._dirty = False
        self._save_counter = 0
        self._load_index()

    # -- Persistence --------------------------------------------------------

    def _index_path(self) -> Path:
        return self._base_dir / "_index.json"

    def _load_index(self) -> None:
        path = self._index_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for item_dict in data:
                    item = MemoryItem.model_validate(item_dict)
                    self._index[item.id] = item
                logger.debug("Loaded %d memory items from index", len(self._index))
            except Exception:
                logger.warning("Failed to load memory index, starting fresh", exc_info=True)
                self._index = {}

    def _save_index(self) -> None:
        path = self._index_path()
        tmp = path.with_suffix(".tmp")
        data = [item.model_dump(mode="json") for item in self._index.values()]
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(path)

    # -- CRUD ---------------------------------------------------------------

    def store(self, item: MemoryItem) -> MemoryItem:
        item.updated_at = time.time()
        self._index[item.id] = item
        self._save_index()
        return item

    def store_many(self, items: Sequence[MemoryItem]) -> list[MemoryItem]:
        now = time.time()
        for item in items:
            item.updated_at = now
            self._index[item.id] = item
        self._save_index()
        return list(items)

    def get(self, item_id: str) -> MemoryItem | None:
        return self._index.get(item_id)

    def update(self, item_id: str, **changes: Any) -> MemoryItem | None:
        item = self._index.get(item_id)
        if item is None:
            return None
        for k, v in changes.items():
            if hasattr(item, k):
                setattr(item, k, v)
        item.updated_at = time.time()
        self._save_index()
        return item

    def delete(self, item_id: str, hard: bool = False) -> bool:
        if hard:
            removed = self._index.pop(item_id, None)
        else:
            item = self._index.get(item_id)
            if item is None:
                return False
            item.lifecycle = MemoryLifecycle.ARCHIVE
            item.updated_at = time.time()
            removed = item
        if removed:
            self._save_index()
        return removed is not None

    def list_by_type(
        self,
        memory_type: MemoryType,
        scope: MemoryScope | None = None,
        lifecycle: MemoryLifecycle | None = None,
        limit: int = 50,
    ) -> list[MemoryItem]:
        results = []
        for item in self._index.values():
            if item.memory_type != memory_type:
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
    ) -> list[ScoredMemoryItem]:
        if isinstance(policy, str):
            policy = POLICY_REGISTRY.get(policy, GENERAL_CONVERSATION_POLICY)
        if policy is None:
            policy = GENERAL_CONVERSATION_POLICY

        max_total = limit or policy.max_total_items
        all_scored: list[ScoredMemoryItem] = []
        n_sections = len(policy.sections) or 1

        for mem_type in policy.sections:
            ranker = _TYPE_RANKERS.get(mem_type, _rank_episode)
            weight = policy.budget_allocation.get(mem_type.value, 1.0 / n_sections)
            section_limit = max(1, int(max_total * weight))

            candidates = [
                item for item in self._index.values()
                if item.memory_type == mem_type
                and item.lifecycle != MemoryLifecycle.ARCHIVE
            ]

            scored = []
            for item in candidates:
                score = ranker(item, query)
                scored.append(ScoredMemoryItem(
                    item=item,
                    score=score,
                    match_reason=mem_type.value,
                ))

            scored.sort(key=lambda x: x.score, reverse=True)
            all_scored.extend(scored[:section_limit])

        all_scored.sort(key=lambda x: x.score, reverse=True)

        now = time.time()
        for si in all_scored[:max_total]:
            si.item.last_accessed = now
            si.item.access_count += 1

        self._dirty = True
        self._save_counter += 1
        if self._save_counter % 5 == 0:
            self._save_index()
            self._dirty = False
        return all_scored[:max_total]

    def retrieve_by_task(self, query: str, task_type: str | None = None, limit: int = 20) -> list[ScoredMemoryItem]:
        policy_key = task_type or classify_task_type(query)
        policy = POLICY_REGISTRY.get(policy_key, GENERAL_CONVERSATION_POLICY)
        return self.retrieve(query, policy=policy, limit=limit)

    # -- Convenience --------------------------------------------------------

    def store_fact(self, content: str, scope: MemoryScope = MemoryScope.USER, **kw: Any) -> MemoryItem:
        return self.store(MemoryItem(content=content, memory_type=MemoryType.FACT, scope=scope, **kw))

    def store_preference(self, content: str, confirmed: bool = False, **kw: Any) -> MemoryItem:
        return self.store(MemoryItem(
            content=content,
            memory_type=MemoryType.PREFERENCE,
            scope=MemoryScope.USER,
            provenance=Provenance(confirmed_by_user=confirmed),
            **kw,
        ))

    def store_episode(self, content: str, scope: MemoryScope = MemoryScope.SESSION, **kw: Any) -> MemoryItem:
        return self.store(MemoryItem(content=content, memory_type=MemoryType.EPISODE, scope=scope, **kw))

    def store_principle(self, content: str, confidence: float = 0.5, **kw: Any) -> MemoryItem:
        return self.store(MemoryItem(
            content=content,
            memory_type=MemoryType.PRINCIPLE,
            scope=MemoryScope.GLOBAL,
            metadata={"confidence": confidence},
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

    def flush(self) -> None:
        """Force-save pending access-time updates to disk."""
        if self._dirty:
            self._save_index()
            self._dirty = False

    @property
    def count(self) -> int:
        return len(self._index)

    def stats(self) -> dict[str, Any]:
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

    def apply_decay(self, decay_days: int = 60, decay_factor: float = 0.9) -> int:
        threshold = time.time() - decay_days * 86400
        decayed = 0
        for item in self._index.values():
            if item.lifecycle == MemoryLifecycle.ARCHIVE:
                continue
            if item.last_accessed < threshold and item.importance > 0.05:
                item.importance *= decay_factor
                decayed += 1
        if decayed:
            self._save_index()
        return decayed

    def promote_to_durable(self, age_hours: float = 24.0) -> int:
        threshold = time.time() - age_hours * 3600
        promoted = 0
        for item in self._index.values():
            if item.lifecycle == MemoryLifecycle.ACTIVE and item.created_at < threshold:
                item.lifecycle = MemoryLifecycle.DURABLE
                promoted += 1
        if promoted:
            self._save_index()
        return promoted

    def archive_stale(self, stale_days: int = 30) -> int:
        threshold = time.time() - stale_days * 86400
        archived = 0
        for item in self._index.values():
            if item.lifecycle == MemoryLifecycle.DURABLE and item.last_accessed < threshold:
                item.lifecycle = MemoryLifecycle.ARCHIVE
                archived += 1
        if archived:
            self._save_index()
        return archived
