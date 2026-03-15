"""Paper acquisition workflow bridge (Plan 36-5).

Links the paper download/summary workflow to the recipe system:
- Captures acquisition source in ingredient provenance
- Feeds summary + metadata into paper-corpus memory
- Creates/updates paper notes at note_root/<bibtex_id>/index.md
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from dan.engine.recipe.models import (
    AcquisitionSource,
    IngredientRecord,
    KnowledgeKind,
    Generality,
    ResearchLibraryConfig,
)

if TYPE_CHECKING:
    from dan.engine.recipe.ingredient_ledger import IngredientLedger
    from dan.engine.recipe.corpus import CorpusWriter

logger = logging.getLogger(__name__)


def load_library_config() -> ResearchLibraryConfig:
    """Load research library config from environment or defaults."""
    pdf_roots_env = os.environ.get("DAN_PAPER_PDF_ROOTS", "")
    note_roots_env = os.environ.get("DAN_PAPER_NOTE_ROOTS", "")
    pdf_roots = [r.strip() for r in pdf_roots_env.split(",") if r.strip()] if pdf_roots_env else []
    note_roots = [r.strip() for r in note_roots_env.split(",") if r.strip()] if note_roots_env else []
    return ResearchLibraryConfig(pdf_roots=pdf_roots, note_roots=note_roots)


def resolve_paper_paths(
    paper_id: str,
    config: ResearchLibraryConfig | None = None,
) -> dict[str, str | None]:
    """Resolve PDF and note paths for a paper using configured roots."""
    if config is None:
        config = load_library_config()

    pdf_path = None
    note_path = None

    for root in config.pdf_roots:
        expanded = os.path.expanduser(root)
        candidate = os.path.join(expanded, f"{paper_id}.pdf")
        if os.path.exists(candidate):
            pdf_path = candidate
            break

    for root in config.note_roots:
        expanded = os.path.expanduser(root)
        candidate = os.path.join(expanded, paper_id, "index.md")
        if os.path.exists(candidate):
            note_path = candidate
            break

    return {
        "pdf_path": pdf_path,
        "note_path": note_path,
        "pdf_root": config.pdf_roots[0] if config.pdf_roots else None,
        "note_root": config.note_roots[0] if config.note_roots else None,
    }


def register_acquired_paper(
    ledger: IngredientLedger,
    paper_id: str,
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    source: AcquisitionSource = AcquisitionSource.MANUAL_IMPORT,
    pdf_path: str | None = None,
    note_path: str | None = None,
    inclusion_reason: str = "",
    config: ResearchLibraryConfig | None = None,
) -> IngredientRecord:
    """Register a newly acquired paper in the ingredient ledger.

    Resolves paths from configured roots if not explicitly provided.
    """
    if pdf_path is None or note_path is None:
        paths = resolve_paper_paths(paper_id, config)
        pdf_path = pdf_path or paths["pdf_path"]
        note_path = note_path or paths["note_path"]
        pdf_root = paths["pdf_root"]
        note_root = paths["note_root"]
    else:
        pdf_root = None
        note_root = None

    record = IngredientRecord(
        paper_id=paper_id,
        title=title,
        authors=authors or [],
        year=year,
        pdf_path=pdf_path,
        note_path=note_path,
        pdf_root=pdf_root,
        note_root=note_root,
        source=source,
        inclusion_reason=inclusion_reason,
    )
    ledger.add_ingredient(record)
    return record


def ingest_paper_metadata(
    writer: CorpusWriter,
    corpus_id: str,
    paper_id: str,
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    venue: str | None = None,
    abstract: str | None = None,
    doi: str | None = None,
) -> list:
    """Store paper metadata and summary in corpus memory."""
    items = []

    # Paper metadata
    meta_content = f"Paper: {title}"
    if authors:
        meta_content += f"\nAuthors: {', '.join(authors[:5])}"
    if year:
        meta_content += f"\nYear: {year}"
    if venue:
        meta_content += f"\nVenue: {venue}"
    if doi:
        meta_content += f"\nDOI: {doi}"

    items.append({
        "content": meta_content,
        "knowledge_kind": KnowledgeKind.SOURCE_METADATA.value,
        "source_id": paper_id,
        "generality": Generality.PAPER.value,
        "confidence": 1.0,
        "importance": 0.6,
        "tags": ["metadata"],
    })

    # Paper summary from abstract
    if abstract:
        items.append({
            "content": abstract,
            "knowledge_kind": KnowledgeKind.SOURCE_SUMMARY.value,
            "source_id": paper_id,
            "generality": Generality.PAPER.value,
            "confidence": 0.9,
            "importance": 0.7,
            "tags": ["summary", "abstract"],
        })

    return writer.store_many(items, corpus_id=corpus_id, source_id=paper_id)


def ingest_paper_extractions(
    writer: CorpusWriter,
    corpus_id: str,
    paper_id: str,
    extractions: list[dict[str, Any]],
) -> list:
    """Store LLM-extracted knowledge from a paper into corpus memory.

    Each extraction dict should have:
    - content: str
    - knowledge_kind: str (KnowledgeKind value)
    - confidence: float (optional, default 0.5)
    - evidence: list[dict] (optional, page/section refs)
    - tags: list[str] (optional)
    """
    items = []
    for ext in extractions:
        items.append({
            "content": ext["content"],
            "knowledge_kind": ext["knowledge_kind"],
            "source_id": paper_id,
            "generality": ext.get("generality", Generality.PAPER.value),
            "confidence": ext.get("confidence", 0.5),
            "evidence": ext.get("evidence"),
            "importance": ext.get("importance", 0.5),
            "tags": ext.get("tags", []),
        })
    return writer.store_many(items, corpus_id=corpus_id, source_id=paper_id)


def generate_paper_note(
    paper_id: str,
    title: str,
    authors: list[str] | None = None,
    year: int | None = None,
    abstract: str | None = None,
    summary: str | None = None,
    source: str = "manual_import",
    config: ResearchLibraryConfig | None = None,
) -> str | None:
    """Generate an index.md note for a paper at note_root/<bibtex_id>/index.md.

    Returns the path where the note was written, or None if no note roots configured.
    """
    if config is None:
        config = load_library_config()

    if not config.note_roots:
        logger.warning("No note_roots configured; skipping note generation for %s", paper_id)
        return None

    note_root = os.path.expanduser(config.note_roots[0])
    note_dir = os.path.join(note_root, paper_id)
    note_path = os.path.join(note_dir, "index.md")

    os.makedirs(note_dir, exist_ok=True)

    authors_str = ", ".join(authors or [])
    lines = [
        "---",
        f"title: \"{title}\"",
        f"authors: [{authors_str}]",
        f"year: {year or 'unknown'}",
        f"paper_id: {paper_id}",
        f"source: {source}",
        f"created: {time.strftime('%Y-%m-%d')}",
        "---",
        "",
        f"# {title}",
        "",
    ]

    if abstract:
        lines.extend(["## Abstract", "", abstract, ""])

    if summary:
        lines.extend(["## Summary", "", summary, ""])

    lines.extend([
        "## Notes",
        "",
        "_Add your notes here._",
        "",
    ])

    Path(note_path).write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote paper note: %s", note_path)
    return note_path
