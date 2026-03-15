"""Corpus memory layer on top of MemoryKernel (Plan 36-3).

Adds corpus metadata to MemoryItem.metadata, provides
knowledge-kind-aware storage and retrieval, and implements
promotion rules from paper → domain → recipe generality.
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryType,
    MemoryScope,
    RetrievalPolicy,
)
from dan.engine.recipe.models import (
    Generality,
    KnowledgeKind,
    CorpusMetadata,
    KNOWLEDGE_KIND_TO_MEMORY_TYPE,
)

if TYPE_CHECKING:
    from dan.engine.memory_kernel import MemoryKernel

logger = logging.getLogger(__name__)


PAPER_CORPUS_RETRIEVAL_POLICY = RetrievalPolicy(
    task_type="corpus_retrieval",
    sections=[
        MemoryType.FACT,
        MemoryType.PREFERENCE,
        MemoryType.PRINCIPLE,
        MemoryType.EPISODE,
        MemoryType.WORKFLOW_ASSET,
    ],
    max_items_per_section=15,
    max_total_items=50,
)

WRITING_RETRIEVAL_POLICY = RetrievalPolicy(
    task_type="corpus_writing",
    sections=[MemoryType.PRINCIPLE, MemoryType.PREFERENCE, MemoryType.FACT],
    max_items_per_section=10,
    max_total_items=25,
)

EVALUATION_RETRIEVAL_POLICY = RetrievalPolicy(
    task_type="corpus_evaluation",
    sections=[MemoryType.EPISODE, MemoryType.PRINCIPLE, MemoryType.FACT],
    max_items_per_section=8,
    max_total_items=20,
)


class CorpusWriter:
    """Writes corpus knowledge into the MemoryKernel."""

    def __init__(self, kernel: MemoryKernel):
        self._kernel = kernel

    def store_knowledge(
        self,
        content: str,
        knowledge_kind: KnowledgeKind,
        corpus_id: str,
        source_id: str = "",
        generality: Generality = Generality.PAPER,
        confidence: float = 0.5,
        evidence: list[dict[str, Any]] | None = None,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> MemoryItem:
        """Store a knowledge item with corpus metadata."""
        mt_name = KNOWLEDGE_KIND_TO_MEMORY_TYPE.get(knowledge_kind, "FACT")
        memory_type = MemoryType(mt_name.lower())

        corpus_meta = CorpusMetadata(
            corpus_id=corpus_id,
            source_id=source_id,
            knowledge_kind=knowledge_kind,
            generality=generality,
            confidence=confidence,
            evidence=evidence or [],
        )

        item = MemoryItem(
            content=content,
            memory_type=memory_type,
            scope=MemoryScope.PROJECT,
            importance=importance,
            tags=list(tags or []) + [f"corpus:{corpus_id}", f"kind:{knowledge_kind.value}"],
            metadata={"corpus": corpus_meta.model_dump()},
        )
        if source_id:
            item.tags.append(f"source:{source_id}")

        self._kernel.store(item)
        return item

    def store_many(
        self,
        items: list[dict[str, Any]],
        corpus_id: str,
        source_id: str = "",
    ) -> list[MemoryItem]:
        """Batch store multiple knowledge items."""
        results = []
        for item_data in items:
            mi = self.store_knowledge(
                content=item_data["content"],
                knowledge_kind=KnowledgeKind(item_data["knowledge_kind"]),
                corpus_id=corpus_id,
                source_id=source_id or item_data.get("source_id", "") or item_data.get("paper_id", ""),
                generality=Generality(item_data.get("generality", "paper")),
                confidence=item_data.get("confidence", 0.5),
                evidence=item_data.get("evidence"),
                tags=item_data.get("tags"),
                importance=item_data.get("importance", 0.5),
            )
            results.append(mi)
        return results


class CorpusReader:
    """Reads corpus knowledge from the MemoryKernel."""

    def __init__(self, kernel: MemoryKernel):
        self._kernel = kernel

    def has_corpus_meta(self, item: MemoryItem) -> bool:
        return "corpus" in item.metadata

    def get_corpus_meta(self, item: MemoryItem) -> CorpusMetadata | None:
        raw = item.metadata.get("corpus")
        if raw is None:
            return None
        return CorpusMetadata.model_validate(raw)

    def query_by_corpus(
        self,
        corpus_id: str,
        knowledge_kind: KnowledgeKind | None = None,
        generality: Generality | None = None,
    ) -> list[MemoryItem]:
        """Query memory items by corpus, optionally filtered by kind/generality."""
        results = []
        for mt in MemoryType:
            for item in self._kernel.list_by_type(mt):
                meta = self.get_corpus_meta(item)
                if meta is None or meta.corpus_id != corpus_id:
                    continue
                if knowledge_kind and meta.knowledge_kind != knowledge_kind:
                    continue
                if generality and meta.generality != generality:
                    continue
                results.append(item)
        return results

    def query_by_source(
        self, source_id: str, corpus_id: str | None = None
    ) -> list[MemoryItem]:
        """Get all knowledge items from a specific source."""
        results = []
        for mt in MemoryType:
            for item in self._kernel.list_by_type(mt):
                meta = self.get_corpus_meta(item)
                if meta is None or meta.source_id != source_id:
                    continue
                if corpus_id and meta.corpus_id != corpus_id:
                    continue
                results.append(item)
        return results

    def query_by_paper(
        self, paper_id: str, corpus_id: str | None = None
    ) -> list[MemoryItem]:
        """Backward-compatible alias for query_by_source."""
        return self.query_by_source(paper_id, corpus_id)

    def get_domain_patterns(self, corpus_id: str) -> list[MemoryItem]:
        """Get all domain-level patterns (promoted from paper-level)."""
        return self.query_by_corpus(corpus_id, generality=Generality.DOMAIN)

    def get_recipe_knowledge(self, corpus_id: str) -> list[MemoryItem]:
        """Get all recipe-level promoted knowledge."""
        return self.query_by_corpus(corpus_id, generality=Generality.RECIPE)

    def retrieve_for_writing(
        self, corpus_id: str, query: str = ""
    ) -> list[MemoryItem]:
        """Retrieve corpus knowledge optimized for draft writing."""
        items = self.query_by_corpus(corpus_id)

        def sort_key(item: MemoryItem) -> tuple:
            meta = self.get_corpus_meta(item)
            gen_rank = {"recipe": 0, "domain": 1, "paper": 2}
            g = gen_rank.get(meta.generality.value, 3) if meta else 3
            return (g, -item.importance)

        items.sort(key=sort_key)
        return items[:WRITING_RETRIEVAL_POLICY.max_total_items]


class PromotionEngine:
    """Promotes paper-level knowledge to domain and recipe levels."""

    MIN_SUPPORT_FOR_DOMAIN = 3
    MIN_SUPPORT_FOR_RECIPE = 5
    MIN_CONFIDENCE_FOR_RECIPE = 0.7

    def __init__(self, reader: CorpusReader, writer: CorpusWriter):
        self._reader = reader
        self._writer = writer

    def promote_to_domain(self, corpus_id: str) -> list[MemoryItem]:
        """Promote paper-level signals to domain patterns when recurrence is high enough.

        Idempotent: skips content that already has a domain-level entry.
        """
        paper_items = self._reader.query_by_corpus(
            corpus_id, generality=Generality.PAPER
        )
        existing_domain = {
            item.content.strip().lower()[:100]
            for item in self._reader.query_by_corpus(
                corpus_id, generality=Generality.DOMAIN
            )
        }

        content_groups: dict[str, list[MemoryItem]] = {}
        for item in paper_items:
            key = item.content.strip().lower()[:100]
            content_groups.setdefault(key, []).append(item)

        promoted = []
        for key, group in content_groups.items():
            if key in existing_domain:
                continue
            if len(group) >= self.MIN_SUPPORT_FOR_DOMAIN:
                best = max(group, key=lambda i: i.importance)
                meta = self._reader.get_corpus_meta(best)
                if meta and meta.generality == Generality.PAPER:
                    new_item = self._writer.store_knowledge(
                        content=best.content,
                        knowledge_kind=meta.knowledge_kind,
                        corpus_id=corpus_id,
                        generality=Generality.DOMAIN,
                        confidence=min(1.0, meta.confidence + 0.1 * len(group)),
                        evidence=[
                            {
                                "source_ids": [i.id for i in group],
                                "support_count": len(group),
                            }
                        ],
                        tags=best.tags,
                        importance=min(1.0, best.importance + 0.1),
                    )
                    promoted.append(new_item)
        return promoted

    def promote_to_recipe(self, corpus_id: str) -> list[MemoryItem]:
        """Promote strong domain patterns to recipe-level output.

        Idempotent: skips content that already has a recipe-level entry.
        """
        domain_items = self._reader.query_by_corpus(
            corpus_id, generality=Generality.DOMAIN
        )
        existing_recipe = {
            item.content.strip().lower()[:100]
            for item in self._reader.query_by_corpus(
                corpus_id, generality=Generality.RECIPE
            )
        }

        promoted = []
        for item in domain_items:
            key = item.content.strip().lower()[:100]
            if key in existing_recipe:
                continue
            meta = self._reader.get_corpus_meta(item)
            if meta is None:
                continue
            if meta.confidence >= self.MIN_CONFIDENCE_FOR_RECIPE:
                new_item = self._writer.store_knowledge(
                    content=item.content,
                    knowledge_kind=meta.knowledge_kind,
                    corpus_id=corpus_id,
                    generality=Generality.RECIPE,
                    confidence=meta.confidence,
                    evidence=meta.evidence,
                    tags=item.tags,
                    importance=min(1.0, item.importance + 0.1),
                )
                promoted.append(new_item)
        return promoted


# Backward compatibility aliases
PaperCorpusWriter = CorpusWriter
PaperCorpusReader = CorpusReader
