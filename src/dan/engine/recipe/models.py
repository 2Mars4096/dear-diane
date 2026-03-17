"""Data models for the recipe distillation system.

Part of Plan 36 (recipe distillation spec). Defines enums, core models for
ingredient provenance, furnace sessions, paper-corpus memory metadata, and
recipe versioning.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FurnacePhase(str, Enum):
    """Pipeline phase for recipe training."""

    NORMALIZE = "normalize"
    EXTRACT = "extract"
    AGGREGATE = "aggregate"
    INFER = "infer"
    PROJECT = "project"


class PaperStatus(str, Enum):
    """Status of a paper in the furnace queue."""

    PENDING = "pending"
    INGESTED = "ingested"
    EXTRACTED = "extracted"
    SKIPPED = "skipped"
    DEFERRED = "deferred"


class IngredientStatus(str, Enum):
    """Status of an ingredient in the recipe."""

    ACTIVE = "active"
    EXCLUDED = "excluded"
    REMOVED = "removed"
    DEFERRED = "deferred"


class KnowledgeKind(str, Enum):
    """Taxonomy of knowledge extracted from papers and distilled into recipes."""

    # Source-level
    SOURCE_METADATA = "source_metadata"
    SOURCE_SUMMARY = "source_summary"
    CLAIM = "claim"
    METHOD = "method"
    DATASET = "dataset"
    MEASURE = "measure"
    # Domain-level
    TERMINOLOGY = "terminology"
    CITATION_NORM = "citation_norm"
    RHETORICAL_MOVE = "rhetorical_move"
    QUESTION_PATTERN = "question_pattern"
    ASSOCIATION_EDGE = "association_edge"
    # Recipe-level
    TASTE_SIGNAL = "taste_signal"
    WRITING_RULE = "writing_rule"
    ANTI_PATTERN = "anti_pattern"
    EVALUATION_CASE = "evaluation_case"
    RECIPE_SNAPSHOT = "recipe_snapshot"


class Generality(str, Enum):
    """Scope of knowledge: paper, domain, or recipe."""

    PAPER = "paper"
    DOMAIN = "domain"
    RECIPE = "recipe"


class AcquisitionSource(str, Enum):
    """How a paper was acquired."""

    UNIVERSITY_PROXY = "university_proxy"
    DIRECT_URL = "direct_url"
    MANUAL_IMPORT = "manual_import"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    ARXIV = "arxiv"


class SourceType(str, Enum):
    """Type of knowledge source for a corpus."""

    PAPER = "paper"
    BLOG = "blog"
    DOCUMENTATION = "documentation"
    CODE = "code"
    CONVERSATION = "conversation"
    BOOK = "book"
    VIDEO = "video"
    NOTE = "note"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class IngredientRecord(BaseModel):
    """One paper's contribution to a recipe."""

    paper_id: str  # bibtex_id
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    pdf_path: str | None = None
    note_path: str | None = None
    pdf_root: str | None = None  # which configured root
    note_root: str | None = None
    source: AcquisitionSource = AcquisitionSource.MANUAL_IMPORT
    status: IngredientStatus = IngredientStatus.ACTIVE
    inclusion_reason: str = ""
    exclusion_reason: str = ""
    included_in_versions: list[str] = Field(default_factory=list)
    added_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BatchCheckpoint(BaseModel):
    """Snapshot after processing one batch of papers."""

    checkpoint_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    batch_index: int
    recipe_version: str
    paper_ids: list[str] = Field(default_factory=list)
    phase_completed: FurnacePhase
    token_usage: int = 0
    cost_usd: float = 0.0
    benchmark_refs: list[str] = Field(default_factory=list)
    changes_from_previous: str = ""
    created_at: float = Field(default_factory=time.time)


class FurnaceSession(BaseModel):
    """Resumable recipe training session state."""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    corpus_id: str
    recipe_id: str
    name: str = ""
    topic: str = ""
    description: str = ""
    variant_label: str = ""
    tags: list[str] = Field(default_factory=list)
    status: Literal["active", "paused", "completed", "failed"] = "active"
    current_phase: FurnacePhase = FurnacePhase.NORMALIZE
    current_batch_index: int = 0
    paper_queue: dict[str, PaperStatus] = Field(default_factory=dict)
    checkpoints: list[BatchCheckpoint] = Field(default_factory=list)
    total_token_usage: int = 0
    total_cost_usd: float = 0.0
    budget_limit_usd: float = 100.0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def source_queue(self) -> dict[str, PaperStatus]:
        """Alias for paper_queue — forward-compatible with multi-source ingestion."""
        return self.paper_queue

    def papers_by_status(self, status: PaperStatus) -> list[str]:
        """Return paper IDs with the given status."""
        return [pid for pid, s in self.paper_queue.items() if s == status]

    def sources_by_status(self, status: PaperStatus) -> list[str]:
        """Alias for papers_by_status — forward-compatible with multi-source."""
        return self.papers_by_status(status)

    def advance_phase(self) -> FurnacePhase | None:
        """Move to next phase; returns new phase or None if already at last."""
        phases = list(FurnacePhase)
        idx = phases.index(self.current_phase)
        if idx + 1 < len(phases):
            self.current_phase = phases[idx + 1]
            self.updated_at = time.time()
            return self.current_phase
        return None


class CorpusMetadata(BaseModel):
    """Corpus metadata attached to MemoryItem.metadata['corpus']."""

    family: str = "corpus"
    corpus_id: str = ""
    source_id: str = ""
    source_type: SourceType = SourceType.PAPER
    knowledge_kind: KnowledgeKind = KnowledgeKind.SOURCE_METADATA
    generality: Generality = Generality.PAPER
    support_count: int = 1
    author_diversity: float = 0.0
    venue_weight: float = 0.5
    confidence: float = 0.5
    evidence: list[dict[str, Any]] = Field(
        default_factory=list
    )  # [{source_id, section, page, quote}]


class VersionDiff(BaseModel):
    """Diff between two recipe versions."""

    from_version: str
    to_version: str
    added_ingredients: list[str] = Field(default_factory=list)
    removed_ingredients: list[str] = Field(default_factory=list)
    changed_roles: list[dict[str, str]] = Field(default_factory=list)
    summary: str = ""
    created_at: float = Field(default_factory=time.time)


class RecipeVersion(BaseModel):
    """One snapshot of the recipe artifact."""

    version: str  # semver
    recipe_id: str
    corpus_id: str
    checkpoint_id: str | None = None
    ingredient_count: int = 0
    benchmark_results: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchLibraryConfig(BaseModel):
    """Configurable paper PDF and note roots."""

    pdf_roots: list[str] = Field(default_factory=list)
    note_roots: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Knowledge kind to MemoryType mapping
# ---------------------------------------------------------------------------

KNOWLEDGE_KIND_TO_MEMORY_TYPE: dict[KnowledgeKind, str] = {
    KnowledgeKind.SOURCE_METADATA: "FACT",
    KnowledgeKind.SOURCE_SUMMARY: "FACT",
    KnowledgeKind.CLAIM: "FACT",
    KnowledgeKind.METHOD: "FACT",
    KnowledgeKind.DATASET: "FACT",
    KnowledgeKind.MEASURE: "FACT",
    KnowledgeKind.ASSOCIATION_EDGE: "FACT",
    KnowledgeKind.TERMINOLOGY: "PREFERENCE",
    KnowledgeKind.TASTE_SIGNAL: "PREFERENCE",
    KnowledgeKind.CITATION_NORM: "PRINCIPLE",
    KnowledgeKind.RHETORICAL_MOVE: "PRINCIPLE",
    KnowledgeKind.QUESTION_PATTERN: "PRINCIPLE",
    KnowledgeKind.WRITING_RULE: "PRINCIPLE",
    KnowledgeKind.ANTI_PATTERN: "PRINCIPLE",
    KnowledgeKind.EVALUATION_CASE: "EPISODE",
    KnowledgeKind.RECIPE_SNAPSHOT: "WORKFLOW_ASSET",
}

# Backward compatibility aliases
PaperCorpusMetadata = CorpusMetadata
SourceStatus = PaperStatus
