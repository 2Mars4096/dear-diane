"""Workflow Experience Memory — capture, consolidate, and retrieve execution patterns.

Implements Plan 19-1: extracts structural metadata from graphs, aggregates
run-level statistics, persists experiences to MemoryStore, and enables
semantic search via EmbeddingProvider + VectorStore.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WorkflowExperience model
# ---------------------------------------------------------------------------


class WorkflowExperience(BaseModel):
    """Aggregated knowledge about a workflow's structure and execution history."""

    workflow_id: str
    name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    node_types_used: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    skills_applied: list[str] = Field(default_factory=list)
    run_count: int = 0
    success_count: int = 0
    last_run_at: float | None = None
    avg_elapsed_seconds: float | None = None
    avg_total_cost: float | None = None
    success_patterns: list[str] = Field(default_factory=list)
    failure_patterns: list[str] = Field(default_factory=list)
    principles: list[dict] = Field(default_factory=list)
    processed_run_ids: list[str] = Field(
        default_factory=list,
        description="Run IDs already consolidated into this experience",
    )
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Graph → Experience extraction
# ---------------------------------------------------------------------------


def extract_experience_from_graph(graph: Any) -> WorkflowExperience:
    """Extract structural metadata from a Graph into an initial WorkflowExperience.

    The caller is responsible for setting ``workflow_id`` on the returned object.
    """
    from dan.models.graph import Graph

    if not isinstance(graph, Graph):
        raise TypeError(f"Expected Graph, got {type(graph).__name__}")

    node_names = [n.name for n in graph.nodes]
    node_types = sorted({n.node_type for n in graph.nodes})

    tools: list[str] = []
    for node in graph.nodes:
        tool_id = getattr(node, "tool_id", None)
        if tool_id:
            tools.append(tool_id)

    skills = [he.name for he in graph.hyperedges]

    entry_names: list[str] = []
    exit_names: list[str] = []
    for ep in graph.entry_points:
        n = graph.node_by_id(ep)
        entry_names.append(n.name if n else ep)
    for ep in graph.exit_points:
        n = graph.node_by_id(ep)
        exit_names.append(n.name if n else ep)

    parts = [f"Workflow with {len(graph.nodes)} nodes: [{', '.join(node_names)}]."]
    if node_types:
        parts.append(f"Uses {', '.join(node_types)}.")
    if entry_names:
        parts.append(f"Entry: [{', '.join(entry_names)}].")
    if exit_names:
        parts.append(f"Exit: [{', '.join(exit_names)}].")

    return WorkflowExperience(
        workflow_id="",
        name=graph.metadata.name,
        description=" ".join(parts),
        tags=list(graph.metadata.tags),
        node_types_used=node_types,
        tools_used=sorted(set(tools)),
        skills_applied=skills,
    )


# ---------------------------------------------------------------------------
# Run consolidation helpers
# ---------------------------------------------------------------------------


def _extract_failure_patterns(
    snapshots: list[dict[str, Any]], top_n: int = 5,
) -> list[str]:
    """Collect and group error messages from failed run snapshots."""
    messages: list[str] = []
    for snap in snapshots:
        if snap.get("success"):
            continue
        errors = snap.get("errors") or snap.get("result", {}).get("errors") or {}
        if isinstance(errors, dict):
            messages.extend(str(v) for v in errors.values())
        elif isinstance(errors, list):
            messages.extend(str(e) for e in errors)
        top_level = snap.get("error")
        if top_level:
            messages.append(str(top_level))

    if not messages:
        return []

    counter: Counter[str] = Counter()
    for msg in messages:
        normalized = msg.strip()[:200]
        if normalized:
            counter[normalized] += 1
    return [msg for msg, _ in counter.most_common(top_n)]


def _extract_success_patterns(
    snapshots: list[dict[str, Any]], top_n: int = 5,
) -> list[str]:
    """Extract execution-path patterns from successful runs."""
    patterns: list[str] = []
    for snap in snapshots:
        if not snap.get("success"):
            continue
        node_statuses = snap.get("node_statuses") or {}
        if not isinstance(node_statuses, dict):
            continue
        completed = sorted(
            nid
            for nid, status in node_statuses.items()
            if (
                (isinstance(status, dict) and status.get("status") == "completed")
                or status == "node_completed"
                or status == "completed"
            )
        )
        if completed:
            patterns.append(" \u2192 ".join(completed))

    if not patterns:
        return []
    counter: Counter[str] = Counter(patterns)
    return [p for p, _ in counter.most_common(top_n)]


def _running_average(
    old_avg: float | None, old_count: int, new_values: list[float],
) -> float | None:
    """Compute a weighted running average, or return None when no data exists."""
    if not new_values:
        return old_avg
    new_avg = sum(new_values) / len(new_values)
    if old_avg is not None and old_count > 0:
        total = old_count + len(new_values)
        return (old_avg * old_count + new_avg * len(new_values)) / total
    return new_avg


# ---------------------------------------------------------------------------
# consolidate_experience
# ---------------------------------------------------------------------------


def consolidate_experience(
    experience: WorkflowExperience,
    run_snapshots: list[dict[str, Any]],
    principles: list[dict[str, Any]],
) -> WorkflowExperience:
    """Merge run statistics and causal principles into an existing experience.

    *run_snapshots* are ``RunRecord.snapshot()`` dicts; *principles* are
    serialised ``CausalPrinciple`` dicts (condition/action/reason/confidence).
    """
    seen_run_ids = set(experience.processed_run_ids)
    new_snapshots: list[dict[str, Any]] = []
    for snap in run_snapshots:
        run_id = str(snap.get("run_id", "")).strip()
        if run_id and run_id in seen_run_ids:
            continue
        new_snapshots.append(snap)
        if run_id:
            seen_run_ids.add(run_id)

    new_runs = len(new_snapshots)
    new_successes = sum(1 for s in new_snapshots if s.get("success"))

    total_run_count = experience.run_count + new_runs
    total_success_count = experience.success_count + new_successes

    last_run_at = experience.last_run_at
    for snap in new_snapshots:
        ts = snap.get("finished_at") or snap.get("started_at")
        if ts is not None:
            ts_f = float(ts)
            if last_run_at is None or ts_f > last_run_at:
                last_run_at = ts_f

    elapsed_values = [
        float(s["elapsed_seconds"])
        for s in new_snapshots
        if s.get("elapsed_seconds") is not None
    ]
    avg_elapsed = _running_average(
        experience.avg_elapsed_seconds, experience.run_count, elapsed_values,
    )

    cost_values = [
        float(s["total_cost"])
        for s in new_snapshots
        if s.get("total_cost") is not None
    ]
    avg_cost = _running_average(
        experience.avg_total_cost, experience.run_count, cost_values,
    )

    failure_patterns = _extract_failure_patterns(new_snapshots)
    success_patterns = _extract_success_patterns(new_snapshots)

    all_failure = list(dict.fromkeys(failure_patterns + experience.failure_patterns))[:5]
    all_success = list(dict.fromkeys(success_patterns + experience.success_patterns))[:5]

    all_principles = list(principles) + list(experience.principles)
    all_principles.sort(key=lambda p: p.get("confidence", 0), reverse=True)
    top_principles = all_principles[:10]

    return experience.model_copy(update={
        "run_count": total_run_count,
        "success_count": total_success_count,
        "last_run_at": last_run_at,
        "avg_elapsed_seconds": avg_elapsed,
        "avg_total_cost": avg_cost,
        "failure_patterns": all_failure,
        "success_patterns": all_success,
        "principles": top_principles,
        "processed_run_ids": sorted(seen_run_ids),
        "updated_at": time.time(),
    })


# ---------------------------------------------------------------------------
# ExperienceStore
# ---------------------------------------------------------------------------


class ExperienceStore:
    """Persists WorkflowExperience objects to a MemoryStore backend."""

    GLOBAL_WORKFLOW = "_global"
    SESSION_ID = "_experience"

    def __init__(self, memory_store: Any, experience_index: Any | None = None) -> None:
        self._store = memory_store
        self._index = experience_index

    def has_index(self) -> bool:
        """Return whether semantic indexing is configured for saved experiences."""
        return self._index is not None

    async def write_experience(self, experience: WorkflowExperience) -> None:
        """Persist a workflow experience without waiting on semantic indexing."""
        from dan.engine.memory import MemoryEntry, MemoryScope

        entry = MemoryEntry(
            key=f"experience:{experience.workflow_id}",
            value=experience.model_dump(),
            scope=MemoryScope.GLOBAL,
        )
        await self._store.write(self.GLOBAL_WORKFLOW, self.SESSION_ID, entry)

    async def index_saved_experience(self, experience: WorkflowExperience) -> None:
        """Best-effort semantic indexing for an already-persisted experience."""
        if self._index is None:
            return
        try:
            await self._index.index_experience(experience)
        except Exception:
            logger.debug(
                "Failed to index experience for workflow %s",
                experience.workflow_id,
                exc_info=True,
            )

    async def save_experience(self, experience: WorkflowExperience) -> None:
        """Persist a workflow experience at GLOBAL scope."""
        await self.write_experience(experience)
        await self.index_saved_experience(experience)

    async def load_experience(self, workflow_id: str) -> WorkflowExperience | None:
        """Load a single workflow experience by ID."""
        entry = await self._store.read(
            self.GLOBAL_WORKFLOW, self.SESSION_ID, f"experience:{workflow_id}",
        )
        if entry is None:
            return None
        return WorkflowExperience.model_validate(entry.value)

    async def list_experiences(self) -> list[WorkflowExperience]:
        """Return all stored workflow experiences."""
        keys = await self._store.list_keys(self.GLOBAL_WORKFLOW, self.SESSION_ID)
        results: list[WorkflowExperience] = []
        for key in keys:
            if not key.startswith("experience:"):
                continue
            entry = await self._store.read(
                self.GLOBAL_WORKFLOW, self.SESSION_ID, key,
            )
            if entry is None:
                continue
            try:
                results.append(WorkflowExperience.model_validate(entry.value))
            except Exception:
                logger.warning("Skipping corrupt experience: %s", key)
        return results

    async def delete_experience(self, workflow_id: str) -> bool:
        """Remove a workflow experience."""
        return await self._store.delete(
            self.GLOBAL_WORKFLOW, self.SESSION_ID, f"experience:{workflow_id}",
        )


# ---------------------------------------------------------------------------
# ExperienceIndex
# ---------------------------------------------------------------------------


class ExperienceIndex:
    """Semantic search over workflow experiences via embedding + vector store."""

    COLLECTION = "workflow_experiences"

    def __init__(
        self,
        embedding_provider: Any,
        vector_store: Any,
        embedding_model: str = "",
    ) -> None:
        self._embedder = embedding_provider
        self._store = vector_store
        self._model = embedding_model
        self._initialized = False

    async def _ensure_collection(self) -> None:
        if not self._initialized:
            collections = await self._store.list_collections()
            if self.COLLECTION not in collections:
                await self._store.create_collection(self.COLLECTION, 1536)
            self._initialized = True

    @staticmethod
    def _composite_text(exp: WorkflowExperience) -> str:
        """Build a searchable text representation of an experience."""
        parts = [exp.name, exp.description]
        if exp.tags:
            parts.append("Tags: " + ", ".join(exp.tags))
        if exp.success_patterns:
            parts.append("Success patterns: " + "; ".join(exp.success_patterns[:5]))
        if exp.failure_patterns:
            parts.append("Failure patterns: " + "; ".join(exp.failure_patterns[:5]))
        if exp.principles:
            for p in exp.principles[:5]:
                parts.append(
                    f"Principle: {p.get('condition', '')} \u2192 {p.get('action', '')}",
                )
        return "\n".join(p for p in parts if p)

    async def index_experience(self, experience: WorkflowExperience) -> None:
        """Embed and store an experience for semantic retrieval."""
        await self._ensure_collection()
        text = self._composite_text(experience)
        result = await self._embedder.embed([text], self._model)
        vector = result.vectors[0]

        from dan.rag.stores import DocumentRecord

        record = DocumentRecord(
            id=f"exp:{experience.workflow_id}",
            text=text,
            embedding=vector,
            metadata={
                "workflow_id": experience.workflow_id,
                "name": experience.name,
            },
        )
        await self._store.add(self.COLLECTION, [record])

    async def search_similar(
        self, query: str, top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """Find experiences similar to a natural-language query.

        Returns ``(workflow_id, score)`` tuples ordered by relevance.
        """
        await self._ensure_collection()
        result = await self._embedder.embed([query], self._model)
        vector = result.vectors[0]
        query_result = await self._store.query(
            self.COLLECTION, vector, top_k=top_k,
        )
        results: list[tuple[str, float]] = []
        for hit in query_result.chunks:
            wf_id = hit.get("metadata", {}).get("workflow_id", "")
            score = hit.get("score", 0.0)
            results.append((wf_id, score))
        return results


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "WorkflowExperience",
    "ExperienceStore",
    "ExperienceIndex",
    "extract_experience_from_graph",
    "consolidate_experience",
]
