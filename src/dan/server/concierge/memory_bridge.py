"""Semantic workflow memory retrieval for the solver runtime.

Wraps ExperienceStore/ExperienceIndex to provide planning-oriented retrieval:
candidate ranking, reuse recommendations, and duplicate detection.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ReuseRecommendation(str, Enum):
    REUSE = "reuse"
    ADAPT = "adapt"
    REFERENCE_ONLY = "reference_only"
    NO_MATCH = "no_match"


class WorkflowCandidate(BaseModel):
    workflow_id: str
    name: str
    summary: str
    similarity_score: float
    recommendation: ReuseRecommendation
    recommendation_reason: str
    interface_summary: str = ""
    outcome_summary: str = ""
    tags: list[str] = Field(default_factory=list)


class ExperienceContext(BaseModel):
    learned_principles: list[str] = Field(default_factory=list)
    related_run_summaries: list[str] = Field(default_factory=list)
    error_patterns: list[str] = Field(default_factory=list)


class WorkflowMemoryIndex:
    """Planning-oriented retrieval layer over the experience system."""

    def __init__(
        self,
        experience_store: Any = None,
        experience_index: Any = None,
        graph_store: Any = None,
    ) -> None:
        self._store = experience_store
        self._index = experience_index
        self._graph_store = graph_store

    async def retrieve_candidates(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_similarity: float = 0.3,
    ) -> list[WorkflowCandidate]:
        hits = await self._search(query, top_k=top_k)
        candidates: list[WorkflowCandidate] = []
        for wf_id, score in hits:
            if score < min_similarity:
                continue
            exp = await self._load(wf_id)
            if exp is None:
                continue
            rec = self._score_reuse_recommendation(exp, score)
            candidates.append(WorkflowCandidate(
                workflow_id=wf_id,
                name=exp.get("name", ""),
                summary=exp.get("description", ""),
                similarity_score=round(score, 4),
                recommendation=rec,
                recommendation_reason=self._recommendation_reason(rec, score),
                interface_summary=self._build_interface_summary(wf_id),
                outcome_summary=self._build_outcome_summary(exp),
                tags=exp.get("tags", []),
            ))
        candidates.sort(key=lambda c: c.similarity_score, reverse=True)
        return candidates

    async def retrieve_experience_context(
        self, query: str, *, top_k: int = 3,
    ) -> ExperienceContext:
        hits = await self._search(query, top_k=top_k)
        principles: list[str] = []
        run_summaries: list[str] = []
        error_patterns: list[str] = []
        for wf_id, _score in hits:
            exp = await self._load(wf_id)
            if exp is None:
                continue
            for p in exp.get("principles", []):
                text = p.get("action", "") or p.get("reason", "")
                if text and text not in principles:
                    principles.append(text)
            rc, sc = exp.get("run_count", 0), exp.get("success_count", 0)
            if rc:
                run_summaries.append(
                    f"{exp.get('name', wf_id)}: {rc} runs, {sc} succeeded",
                )
            for fp in exp.get("failure_patterns", []):
                if fp not in error_patterns:
                    error_patterns.append(fp)
        return ExperienceContext(
            learned_principles=principles[:10],
            related_run_summaries=run_summaries[:10],
            error_patterns=error_patterns[:10],
        )

    async def check_duplicate(
        self, name: str, summary: str, *, threshold: float = 0.85,
    ) -> str | None:
        combined = f"{name} {summary}"
        hits = await self._search(combined, top_k=3)
        for wf_id, score in hits:
            if score >= threshold:
                return wf_id
        if not hits and self._store is not None:
            lexical = await self._lexical_search(combined, top_k=1)
            if lexical and lexical[0][1] >= threshold:
                return lexical[0][0]
        return None

    async def suggest_save(self, workflow_id: str, task_summary: str) -> bool:
        if self._graph_store is not None:
            graph = self._graph_store.get_graph(workflow_id)
            if graph and len(graph.get("nodes", [])) <= 2:
                return False
        dup = await self.check_duplicate(task_summary, task_summary, threshold=0.85)
        return dup is None

    async def _search(
        self, query: str, *, top_k: int = 5,
    ) -> list[tuple[str, float]]:
        if self._index is not None:
            try:
                return await self._index.search_similar(query, top_k=top_k)
            except Exception:
                logger.debug("Semantic search failed, falling back", exc_info=True)
        return await self._lexical_search(query, top_k)

    async def _lexical_search(
        self, query: str, top_k: int,
    ) -> list[tuple[str, float]]:
        if self._store is None:
            return []
        try:
            experiences = await self._store.list_experiences()
        except Exception:
            logger.debug("Failed to list experiences", exc_info=True)
            return []
        q_lower = query.lower()
        scored = [
            (exp.workflow_id, SequenceMatcher(
                None, q_lower,
                f"{exp.name} {exp.description} {' '.join(exp.tags)}".lower(),
            ).ratio())
            for exp in experiences
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:top_k]

    async def _load(self, workflow_id: str) -> dict | None:
        if self._store is None:
            return None
        try:
            exp = await self._store.load_experience(workflow_id)
            return exp.model_dump() if exp is not None else None
        except Exception:
            logger.debug("Failed to load experience %s", workflow_id, exc_info=True)
            return None

    @staticmethod
    def _score_reuse_recommendation(
        exp: dict, similarity: float,
    ) -> ReuseRecommendation:
        success_rate = (
            exp.get("success_count", 0) / exp["run_count"]
            if exp.get("run_count") else 0.0
        )
        if similarity > 0.85 and success_rate >= 0.5:
            return ReuseRecommendation.REUSE
        if similarity > 0.6:
            return ReuseRecommendation.ADAPT
        if similarity > 0.3:
            return ReuseRecommendation.REFERENCE_ONLY
        return ReuseRecommendation.NO_MATCH

    @staticmethod
    def _recommendation_reason(rec: ReuseRecommendation, score: float) -> str:
        if rec == ReuseRecommendation.REUSE:
            return f"High similarity ({score:.0%}) with successful history"
        if rec == ReuseRecommendation.ADAPT:
            return f"Moderate similarity ({score:.0%}); may need adjustments"
        if rec == ReuseRecommendation.REFERENCE_ONLY:
            return f"Low similarity ({score:.0%}); reference only"
        return "No meaningful match"

    def _build_interface_summary(self, workflow_id: str) -> str:
        if self._graph_store is None:
            return ""
        graph = self._graph_store.get_graph(workflow_id)
        if not graph:
            return ""
        nodes = graph.get("nodes", [])
        entries = [n.get("name", n.get("id", "")) for n in nodes if n.get("is_entry")]
        exits = [n.get("name", n.get("id", "")) for n in nodes if n.get("is_exit")]
        parts: list[str] = []
        if entries:
            parts.append(f"inputs: {', '.join(entries)}")
        if exits:
            parts.append(f"outputs: {', '.join(exits)}")
        return "; ".join(parts) if parts else f"{len(nodes)} nodes"

    @staticmethod
    def _build_outcome_summary(exp: dict) -> str:
        rc = exp.get("run_count", 0)
        return f"{rc} runs, {exp.get('success_count', 0) / rc:.0%} success" if rc > 0 else "never executed"


def enrich_planning_context(
    candidates: list[WorkflowCandidate],
    experience: ExperienceContext,
) -> dict[str, Any]:
    """Format candidates and experience into a dict for solver LLM prompts."""
    return {
        "similar_workflows": [
            {
                "workflow_id": c.workflow_id,
                "name": c.name,
                "summary": c.summary,
                "similarity": c.similarity_score,
                "recommendation": c.recommendation.value,
                "reason": c.recommendation_reason,
                "interface": c.interface_summary,
                "outcome": c.outcome_summary,
            }
            for c in candidates
        ],
        "learned_principles": experience.learned_principles,
        "related_runs": experience.related_run_summaries,
        "error_patterns": experience.error_patterns,
    }
