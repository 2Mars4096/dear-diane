"""Self-knowledge RAG — indexes DAN's own documentation for planner grounding.

Provides ``SelfKnowledgeIndex`` which indexes DAN's docs (``llm-api-guide.md``,
``architecture.md``), tool schemas, and workflow examples into a dedicated RAG
collection.  The ``WorkflowPlanner`` retrieves relevant API sections before
generating workflows, keeping it grounded in the actual current API.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dan.rag import EmbeddingProvider
from dan.rag.indexer import Indexer
from dan.rag.stores import VectorStore, VectorStoreConfig
from dan.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)

COLLECTION_NAME = "_dan_self_knowledge"

__all__ = [
    "COLLECTION_NAME",
    "RetrievedChunk",
    "SelfKnowledgeIndex",
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RetrievedChunk:
    """A chunk retrieved from the self-knowledge index."""

    text: str
    source_file: str
    section_title: str
    doc_type: str  # api_reference | architecture | example | tool_schema
    score: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_markdown_sections(
    text: str, source_file: str, doc_type: str,
) -> list[dict[str, Any]]:
    """Split markdown on ``## `` headers into document dicts for the Indexer.

    Each section keeps its header line so the embedding captures the topic.
    The preamble (content before the first ``##``) is kept as a separate chunk.
    """
    sections: list[dict[str, Any]] = []
    parts = re.split(r"(?=^## )", text, flags=re.MULTILINE)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        header_match = re.match(r"^## (.+)", part)
        section_title = header_match.group(1).strip() if header_match else "(preamble)"
        content_hash = hashlib.sha256(part.encode()).hexdigest()

        sections.append({
            "text": part,
            "id": f"{source_file}::{section_title}",
            "metadata": {
                "source_file": source_file,
                "section_title": section_title,
                "doc_type": doc_type,
                "content_hash": content_hash,
            },
        })

    return sections


def _infer_doc_type(filename: str) -> str:
    """Infer ``doc_type`` metadata from a filename."""
    name = filename.lower()
    if "api" in name or "guide" in name:
        return "api_reference"
    if "architecture" in name or "arch" in name:
        return "architecture"
    return "api_reference"


def _format_tool_params(params: Any) -> str:
    """Format a tool's parameter schema for human-readable indexing."""
    if not isinstance(params, dict):
        return str(params)
    lines: list[str] = []
    for name, schema in params.items():
        if isinstance(schema, dict):
            desc = schema.get("description", "")
            ptype = schema.get("type", "any")
            req = " (required)" if schema.get("required") else ""
            lines.append(f"  - {name}: {ptype}{req} — {desc}")
        else:
            lines.append(f"  - {name}: {schema}")
    return "\n".join(lines) if lines else str(params)


def _format_tool_examples(examples: Any) -> str:
    """Format a tool's examples list for human-readable indexing."""
    if not isinstance(examples, list):
        return str(examples)
    return "\n".join(f"  Example {i}: {ex}" for i, ex in enumerate(examples, 1))


# ---------------------------------------------------------------------------
# Chunking config shared by all indexing methods
# ---------------------------------------------------------------------------

_CHUNK_CFG: dict[str, Any] = {"chunk_size": 2000, "overlap": 200}


# ---------------------------------------------------------------------------
# SelfKnowledgeIndex
# ---------------------------------------------------------------------------


class SelfKnowledgeIndex:
    """Indexes DAN's own docs for planner grounding.

    Wraps the existing ``dan.rag`` infrastructure (``Indexer``,
    ``VectorStore``, ``EmbeddingProvider``) to embed and store
    documentation, tool schemas, and workflow examples in a dedicated
    collection (``_dan_self_knowledge``).
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        embedding_model: str = "",
        store: VectorStore | None = None,
        store_config: VectorStoreConfig | None = None,
        token_budget: int = 4000,
    ) -> None:
        self._provider = embedding_provider
        self._model = embedding_model
        self._token_budget = token_budget
        self._indexer = Indexer(
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            store=store,
            store_config=store_config,
        )
        self._file_hashes: dict[str, str] = {}

    @property
    def store(self) -> VectorStore:
        """Underlying vector store (delegated from the internal ``Indexer``)."""
        return self._indexer.store

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def index_docs(self, doc_paths: list[Path]) -> dict[str, Any]:
        """Index markdown documentation files, chunking by ``##`` headers.

        Each ``## `` section becomes a separate document in the vector store.
        Metadata includes *source_file*, *section_title*, *doc_type*, and
        *content_hash*.
        """
        all_docs: list[dict[str, Any]] = []
        files_indexed = 0

        for path in doc_paths:
            if not path.exists():
                logger.warning("Doc path does not exist, skipping: %s", path)
                continue

            content = path.read_text(encoding="utf-8")
            if not content.strip():
                continue

            file_hash = hashlib.sha256(content.encode()).hexdigest()
            self._file_hashes[str(path)] = file_hash

            doc_type = _infer_doc_type(path.name)
            sections = _split_markdown_sections(content, path.name, doc_type)
            all_docs.extend(sections)
            files_indexed += 1

        if not all_docs:
            return {"files_indexed": 0, "chunks": 0}

        stats = await self._indexer.create_index(
            COLLECTION_NAME, all_docs, chunking_config=_CHUNK_CFG,
        )

        logger.info(
            "Self-knowledge: indexed %d doc files → %d chunks",
            files_indexed,
            stats.get("chunks", 0),
        )
        return {"files_indexed": files_indexed, "chunks": stats.get("chunks", 0)}

    async def index_tool_schemas(
        self, tool_registry: Any | None = None,
    ) -> dict[str, Any]:
        """Index tool schemas from *tool_registry* or ``dan.tools.get_all_tools()``.

        Each tool's metadata (description, parameters, examples) is formatted
        as a readable text chunk and added to the self-knowledge collection.
        """
        tools: dict[str, tuple[Any, dict]] = {}

        if tool_registry is not None:
            for tid in tool_registry.registered_ids():
                meta = tool_registry.get_metadata(tid)
                if meta:
                    tools[tid] = (None, meta)
        else:
            from dan.tools import get_all_tools
            tools = get_all_tools()

        if not tools:
            return {"tools_indexed": 0, "chunks": 0}

        docs: list[dict[str, Any]] = []
        for tool_id, (_fn, meta) in tools.items():
            parts = [f"# Tool: {tool_id}"]
            if meta.get("description"):
                parts.append(f"Description: {meta['description']}")
            if meta.get("category"):
                parts.append(f"Category: {meta['category']}")
            if meta.get("parameters"):
                parts.append(f"Parameters:\n{_format_tool_params(meta['parameters'])}")
            if meta.get("returns"):
                parts.append(f"Returns: {meta['returns']}")
            if meta.get("examples"):
                parts.append(f"Examples:\n{_format_tool_examples(meta['examples'])}")

            text = "\n".join(parts)
            content_hash = hashlib.sha256(text.encode()).hexdigest()

            docs.append({
                "text": text,
                "id": f"tool::{tool_id}",
                "metadata": {
                    "source_file": f"dan.tools.{tool_id}",
                    "section_title": tool_id,
                    "doc_type": "tool_schema",
                    "content_hash": content_hash,
                },
            })

        added = await self._indexer.add_documents(
            COLLECTION_NAME, docs, chunking_config=_CHUNK_CFG,
        )

        logger.info(
            "Self-knowledge: indexed %d tool schemas → %d chunks",
            len(tools),
            added,
        )
        return {"tools_indexed": len(tools), "chunks": added}

    async def index_examples(self, examples_dir: Path) -> dict[str, Any]:
        """Index workflow example files (``.py``, ``.md``) from a directory."""
        if not examples_dir.is_dir():
            logger.warning("Examples directory does not exist: %s", examples_dir)
            return {"files_indexed": 0, "chunks": 0}

        docs: list[dict[str, Any]] = []
        files_indexed = 0

        for path in sorted(examples_dir.iterdir()):
            if path.suffix not in (".py", ".md"):
                continue

            content = path.read_text(encoding="utf-8")
            if not content.strip():
                continue

            file_hash = hashlib.sha256(content.encode()).hexdigest()
            self._file_hashes[str(path)] = file_hash

            docs.append({
                "text": content,
                "id": f"example::{path.name}",
                "metadata": {
                    "source_file": path.name,
                    "section_title": path.stem,
                    "doc_type": "example",
                    "content_hash": file_hash,
                },
            })
            files_indexed += 1

        if not docs:
            return {"files_indexed": 0, "chunks": 0}

        added = await self._indexer.add_documents(
            COLLECTION_NAME, docs, chunking_config=_CHUNK_CFG,
        )

        logger.info(
            "Self-knowledge: indexed %d example files → %d chunks",
            files_indexed,
            added,
        )
        return {"files_indexed": files_indexed, "chunks": added}

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def retrieve(
        self, query: str, top_k: int = 8,
    ) -> list[RetrievedChunk]:
        """Retrieve relevant chunks via vector similarity search."""
        result = await self._provider.embed([query], self._model)
        if not result.vectors:
            return []

        qr = await self._indexer.store.query(
            COLLECTION_NAME, vector=result.vectors[0], top_k=top_k,
        )

        chunks: list[RetrievedChunk] = []
        for hit in qr.chunks:
            meta = hit.get("metadata", {})
            chunks.append(RetrievedChunk(
                text=hit.get("text", ""),
                source_file=meta.get("source_file", ""),
                section_title=meta.get("section_title", ""),
                doc_type=meta.get("doc_type", ""),
                score=hit.get("score", 0.0),
            ))
        return chunks

    # ------------------------------------------------------------------
    # Incremental refresh
    # ------------------------------------------------------------------

    async def refresh(
        self,
        doc_paths: list[Path] | None = None,
        examples_dir: Path | None = None,
        tool_registry: Any | None = None,
    ) -> dict[str, Any]:
        """Re-index only files whose content hash has changed (incremental).

        Compares current file SHA-256 against stored hashes.  Unchanged
        files are skipped.  Tool schemas are always re-indexed since they
        have no on-disk hash to compare.
        """
        stats: dict[str, Any] = {
            "docs_refreshed": 0,
            "examples_refreshed": 0,
            "tools_refreshed": False,
        }

        if doc_paths:
            changed = [p for p in doc_paths if self._file_changed(p)]
            if changed:
                result = await self._index_docs_incremental(changed)
                stats["docs_refreshed"] = result.get("files_indexed", 0)

        if examples_dir and examples_dir.is_dir():
            changed_examples = [
                p
                for p in sorted(examples_dir.iterdir())
                if p.suffix in (".py", ".md") and self._file_changed(p)
            ]
            if changed_examples:
                result = await self._index_examples_incremental(changed_examples)
                stats["examples_refreshed"] = result.get("files_indexed", 0)

        tool_result = await self.index_tool_schemas(tool_registry)
        stats["tools_refreshed"] = tool_result.get("tools_indexed", 0) > 0

        return stats

    # ------------------------------------------------------------------
    # Prompt formatting
    # ------------------------------------------------------------------

    def format_for_prompt(self, chunks: list[RetrievedChunk]) -> str:
        """Format retrieved chunks as a prompt section with source attribution.

        Adds chunks in order until the running token count exceeds
        ``token_budget``.  Each chunk is prefixed with a source reference.
        """
        if not chunks:
            return ""

        header = "## DAN API Reference (retrieved)\n"
        parts: list[str] = [header]
        running_tokens = estimate_tokens(header)

        for chunk in chunks:
            attribution = f"[Source: {chunk.source_file} > {chunk.section_title}]"
            entry = f"{attribution}\n{chunk.text}\n"
            entry_tokens = estimate_tokens(entry)

            if running_tokens + entry_tokens > self._token_budget:
                break

            parts.append(entry)
            running_tokens += entry_tokens

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _file_changed(self, path: Path) -> bool:
        """Return True if *path* content differs from the stored hash."""
        if not path.exists():
            return False
        content = path.read_text(encoding="utf-8")
        current_hash = hashlib.sha256(content.encode()).hexdigest()
        return current_hash != self._file_hashes.get(str(path))

    async def _index_docs_incremental(
        self, paths: list[Path],
    ) -> dict[str, Any]:
        """Add/upsert only the given doc files (used by ``refresh``)."""
        all_docs: list[dict[str, Any]] = []
        files_indexed = 0

        for path in paths:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                continue

            file_hash = hashlib.sha256(content.encode()).hexdigest()
            self._file_hashes[str(path)] = file_hash

            doc_type = _infer_doc_type(path.name)
            sections = _split_markdown_sections(content, path.name, doc_type)
            all_docs.extend(sections)
            files_indexed += 1

        if not all_docs:
            return {"files_indexed": 0, "chunks": 0}

        added = await self._indexer.add_documents(
            COLLECTION_NAME, all_docs, chunking_config=_CHUNK_CFG,
        )

        logger.info(
            "Self-knowledge refresh: re-indexed %d doc files → %d chunks",
            files_indexed,
            added,
        )
        return {"files_indexed": files_indexed, "chunks": added}

    async def _index_examples_incremental(
        self, paths: list[Path],
    ) -> dict[str, Any]:
        """Add/upsert only the given example files (used by ``refresh``)."""
        docs: list[dict[str, Any]] = []
        files_indexed = 0

        for path in paths:
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                continue

            file_hash = hashlib.sha256(content.encode()).hexdigest()
            self._file_hashes[str(path)] = file_hash

            docs.append({
                "text": content,
                "id": f"example::{path.name}",
                "metadata": {
                    "source_file": path.name,
                    "section_title": path.stem,
                    "doc_type": "example",
                    "content_hash": file_hash,
                },
            })
            files_indexed += 1

        if not docs:
            return {"files_indexed": 0, "chunks": 0}

        added = await self._indexer.add_documents(
            COLLECTION_NAME, docs, chunking_config=_CHUNK_CFG,
        )

        logger.info(
            "Self-knowledge refresh: re-indexed %d example files → %d chunks",
            files_indexed,
            added,
        )
        return {"files_indexed": files_indexed, "chunks": added}
