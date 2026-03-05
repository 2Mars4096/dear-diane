"""Discovery service — enumerates available tools, skills, patterns, and past workflows.

Provides the planner (19-2) and controller (19-4) with a structured view
of the DAN ecosystem: what building blocks exist and which prior workflows
are relevant to a given goal.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "DiscoveryResult",
    "DiscoveryService",
    "PatternInfo",
    "SkillInfo",
    "ToolInfo",
    "WorkflowMatch",
]


# ---------------------------------------------------------------------------
# Descriptor models
# ---------------------------------------------------------------------------


class ToolInfo(BaseModel):
    """Metadata about a registered tool."""

    tool_id: str
    description: str = ""


class SkillInfo(BaseModel):
    """Metadata about a registered skill/hyperedge template."""

    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    inject_as: str = ""


class PatternInfo(BaseModel):
    """Metadata about a composable graph pattern."""

    name: str
    description: str = ""


class WorkflowMatch(BaseModel):
    """A past workflow that may be relevant to the current goal."""

    workflow_id: str
    name: str = ""
    description: str = ""
    score: float = 0.0
    reuse_fit_score: float = 0.0
    tags: list[str] = Field(default_factory=list)
    success_rate: float | None = None


class DiscoveryResult(BaseModel):
    """Aggregated output from all discovery channels."""

    tools: list[ToolInfo] = Field(default_factory=list)
    skills: list[SkillInfo] = Field(default_factory=list)
    patterns: list[PatternInfo] = Field(default_factory=list)
    workflows: list[WorkflowMatch] = Field(default_factory=list)
    self_knowledge_chunks: list[dict[str, Any]] = Field(default_factory=list)
    self_knowledge_formatted: str = ""  # Token-budgeted prompt section from format_for_prompt


# ---------------------------------------------------------------------------
# Pattern metadata (extracted from PATTERN_LIBRARY function docstrings)
# ---------------------------------------------------------------------------

_PATTERN_DESCRIPTIONS: dict[str, str] = {
    "chain": "Chain of N LLM nodes connected sequentially.",
    "review_loop": "Writer → Reviewer → Gate (while) with back-edge to Writer.",
    "fan_out": "Source → ForEach with body LLM → downstream collector.",
    "rag_qa": "Ingest → Index → RAG retrieval → LLM answer pipeline.",
    "data_ingest": "File reader → code transform → output pipeline.",
    "data_analysis": "Data loader → analysis → visualisation pipeline.",
}


# ---------------------------------------------------------------------------
# DiscoveryService
# ---------------------------------------------------------------------------


class DiscoveryService:
    """Discovers available building blocks and relevant past workflows."""

    def __init__(
        self,
        experience_index: Any | None = None,
        experience_store: Any | None = None,
        graph_store: Any | None = None,
        tool_registry: Any | None = None,
        self_knowledge: Any | None = None,
    ) -> None:
        self._experience_index = experience_index
        self._experience_store = experience_store
        self._graph_store = graph_store
        self._tool_registry = tool_registry
        self._self_knowledge = self_knowledge

    def discover_tools(self) -> list[ToolInfo]:
        """Query the tool registry for available tools."""
        if self._tool_registry is None:
            return []
        ids = self._tool_registry.registered_ids()
        return [ToolInfo(tool_id=tid) for tid in ids]

    def discover_skills(self) -> list[SkillInfo]:
        """Query SKILL_LIBRARY for available skill descriptors."""
        from dan.server.skill_library import SKILL_LIBRARY

        results: list[SkillInfo] = []
        for _key, entry in SKILL_LIBRARY.items():
            if not isinstance(entry, dict):
                continue
            results.append(SkillInfo(
                name=entry.get("name", _key),
                description=entry.get("description", ""),
                tags=entry.get("tags", []),
                inject_as=entry.get("inject_as", ""),
            ))
        return results

    def discover_patterns(self) -> list[PatternInfo]:
        """Query PATTERN_LIBRARY for composable graph patterns."""
        from dan.server.graph_mutator import PATTERN_LIBRARY

        results: list[PatternInfo] = []
        for name in sorted(PATTERN_LIBRARY):
            desc = _PATTERN_DESCRIPTIONS.get(name, "")
            results.append(PatternInfo(name=name, description=desc))
        return results

    async def discover_workflows(
        self, query: str, top_k: int = 5,
    ) -> list[WorkflowMatch]:
        """Semantic search over past workflow experiences."""
        if not self._experience_index:
            return []

        hits = await self._experience_index.search_similar(query, top_k=top_k)
        matches: list[WorkflowMatch] = []

        for wf_id, score in hits:
            exp = None
            if self._experience_store:
                exp = await self._experience_store.load_experience(wf_id)

            success_rate: float | None = None
            if exp and exp.run_count > 0:
                success_rate = exp.success_count / exp.run_count

            reuse_bonus = 0.1 if (success_rate is not None and success_rate > 0.7) else 0.0
            reuse_fit = min(1.0, score + reuse_bonus)

            matches.append(WorkflowMatch(
                workflow_id=wf_id,
                name=exp.name if exp else "",
                description=exp.description if exp else "",
                score=score,
                reuse_fit_score=reuse_fit,
                tags=exp.tags if exp else [],
                success_rate=success_rate,
            ))

        return sorted(matches, key=lambda m: m.reuse_fit_score, reverse=True)

    async def discover_all(self, query: str, top_k: int = 5) -> DiscoveryResult:
        """Run all discovery channels and return a unified result."""
        sk_chunks: list[dict[str, Any]] = []
        sk_formatted: str = ""
        if self._self_knowledge is not None:
            try:
                chunks = await self._self_knowledge.retrieve(query)
                sk_chunks = [
                    {
                        "text": c.text,
                        "source_file": c.source_file,
                        "section_title": c.section_title,
                        "doc_type": c.doc_type,
                        "score": c.score,
                    }
                    for c in chunks
                ]
                sk_formatted = self._self_knowledge.format_for_prompt(chunks)
            except Exception:
                logger.debug("Self-knowledge retrieval failed", exc_info=True)

        return DiscoveryResult(
            tools=self.discover_tools(),
            skills=self.discover_skills(),
            patterns=self.discover_patterns(),
            workflows=await self.discover_workflows(query, top_k),
            self_knowledge_chunks=sk_chunks,
            self_knowledge_formatted=sk_formatted,
        )
