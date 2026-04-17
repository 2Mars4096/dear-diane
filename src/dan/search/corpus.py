"""Persistent Beacon Search corpus and lexical retrieval index."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

from dan.search.connectors import classify_source_family, source_family_ttl_seconds
from dan.server.search_models import canonicalize_search_url, domain_from_url
from dan.tools.text_chunk import _chunk_by_chars

logger = logging.getLogger(__name__)

_DEFAULT_BEACON_STORAGE_ROOT = Path.home() / ".dan" / "beacon"
_DEFAULT_BEACON_DB_NAME = "search.db"
_DEFAULT_CHUNK_SIZE = 1000
_DEFAULT_CHUNK_OVERLAP = 120
_DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
_QUERY_STOP_WORDS = frozenset({
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _default_beacon_db_path() -> Path:
    configured = str(os.environ.get("DAN_BEACON_SEARCH_DB", "") or "").strip()
    if configured:
        return Path(configured).expanduser()
    storage_root = str(os.environ.get("DAN_BEACON_STORAGE_ROOT", "") or "").strip()
    if storage_root:
        return Path(storage_root).expanduser() / _DEFAULT_BEACON_DB_NAME
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return Path("/tmp") / "dan-beacon" / _DEFAULT_BEACON_DB_NAME
    return _DEFAULT_BEACON_STORAGE_ROOT / _DEFAULT_BEACON_DB_NAME


def _stable_document_id(canonical_url: str) -> str:
    digest = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()
    return f"doc_{digest[:16]}"


def _stable_chunk_id(document_id: str, chunk_index: int) -> str:
    return f"{document_id}__chunk_{chunk_index}"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _metadata_json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True)


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", str(query or "").lower()):
        if token in _QUERY_STOP_WORDS:
            continue
        terms.append(token)
    return list(dict.fromkeys(terms))


def _fts_query(query: str) -> str:
    tokens = _query_terms(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens)


def _excerpt(text: str, limit: int = 240) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3].rstrip() + "..."


def _freshness_state(fetched_at: datetime | None, expires_at: datetime | None) -> str:
    now = _utcnow()
    if expires_at is not None and now > expires_at:
        return "stale"
    if fetched_at is None:
        return "unknown"
    age = now - fetched_at
    if age <= timedelta(days=2):
        return "fresh"
    if age <= timedelta(days=14):
        return "warm"
    return "stale"


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


async def _resolve_beacon_embedding_provider() -> tuple[Any | None, str]:
    try:
        from dan.rag import build_embedding_registry
        from dan.server.runtime_config import build_engine_config_from_env
    except Exception:
        return None, ""
    try:
        config = build_engine_config_from_env()
        registry = build_embedding_registry(config)
        model = getattr(config, "default_embedding_model", "") or "text-embedding-3-small"
        return registry.resolve(model), model
    except Exception:
        logger.debug("Beacon embedding provider unavailable", exc_info=True)
        return None, ""


@dataclass(frozen=True)
class BeaconStoredDocument:
    document_id: str
    chunk_count: int
    primary_chunk_id: str | None
    canonical_url: str
    url: str
    title: str
    source_type: str
    content_hash: str
    fetched_at: str
    published_at: str | None = None
    freshness_state: str = "fresh"
    refresh_state: str = "fresh"
    ttl_seconds: int = _DEFAULT_TTL_SECONDS
    expires_at: str | None = None
    duplicate_of_document_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "chunk_count": self.chunk_count,
            "primary_chunk_id": self.primary_chunk_id,
            "canonical_url": self.canonical_url,
            "url": self.url,
            "title": self.title,
            "source_type": self.source_type,
            "content_hash": self.content_hash,
            "fetched_at": self.fetched_at,
            "published_at": self.published_at,
            "freshness_state": self.freshness_state,
            "refresh_state": self.refresh_state,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at,
            "duplicate_of_document_id": self.duplicate_of_document_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class BeaconCorpusHit:
    document_id: str
    chunk_id: str
    canonical_url: str
    url: str
    title: str
    snippet: str
    source_type: str
    fetched_at: str
    published_at: str | None = None
    freshness_state: str = "fresh"
    duplicate_of_document_id: str | None = None
    ranking_features: dict[str, float] = field(default_factory=dict)

    def to_search_result_item(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider": "beacon",
            "result_kind": "corpus",
            "canonical_url": self.canonical_url,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "evidence_source": "corpus",
            "freshness_state": self.freshness_state,
            "ranking_features": dict(self.ranking_features),
        }


class BeaconCorpusStore:
    """SQLite-backed persistent corpus with FTS retrieval for Beacon Search."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._path = Path(db_path) if db_path is not None else _default_beacon_db_path()
        self._chunk_size = chunk_size or _env_int("DAN_BEACON_CHUNK_SIZE", _DEFAULT_CHUNK_SIZE)
        self._chunk_overlap = chunk_overlap or _env_int(
            "DAN_BEACON_CHUNK_OVERLAP", _DEFAULT_CHUNK_OVERLAP
        )
        self._ttl_seconds = ttl_seconds or _env_int("DAN_BEACON_TTL_SECONDS", _DEFAULT_TTL_SECONDS)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def db_path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(self._path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._ensure_schema()
            return self._conn

    def _ensure_schema(self) -> None:
        conn = self._conn
        if conn is None:
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                canonical_url TEXT NOT NULL UNIQUE,
                domain TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'web',
                content_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                published_at TEXT,
                last_seen_at TEXT NOT NULL,
                freshness_state TEXT NOT NULL DEFAULT 'fresh',
                refresh_state TEXT NOT NULL DEFAULT 'fresh',
                ttl_seconds INTEGER NOT NULL DEFAULT 604800,
                expires_at TEXT,
                duplicate_of_document_id TEXT,
                lineage_note TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_beacon_documents_domain ON documents(domain);
            CREATE INDEX IF NOT EXISTS idx_beacon_documents_hash ON documents(content_hash);
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                start_char INTEGER NOT NULL,
                end_char INTEGER NOT NULL,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_beacon_chunks_document ON chunks(document_id, chunk_index);
            CREATE TABLE IF NOT EXISTS chunk_embeddings (
                chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
                model TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                chunk_id UNINDEXED,
                document_id UNINDEXED,
                canonical_url UNINDEXED,
                title,
                text
            );
            """
        )
        conn.commit()

    def _chunk_document(self, content: str) -> list[dict[str, Any]]:
        if not content.strip():
            return []
        return _chunk_by_chars(content, self._chunk_size, self._chunk_overlap)["chunks"]

    def _best_chunk_id(self, chunks: list[dict[str, Any]], query: str, document_id: str) -> str | None:
        if not chunks:
            return None
        terms = _query_terms(query)
        best_chunk = chunks[0]
        best_score = -1
        for chunk in chunks:
            text = str(chunk.get("chunk", "") or "").lower()
            score = sum(text.count(term) for term in terms) if terms else 0
            if score > best_score:
                best_chunk = chunk
                best_score = score
        return _stable_chunk_id(document_id, int(best_chunk.get("index", 0)))

    def upsert_document(
        self,
        *,
        url: str,
        title: str = "",
        content: str,
        query: str = "",
        source_type: str = "web",
        published_at: str | None = None,
        fetched_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BeaconStoredDocument | None:
        raw_url = str(url or "").strip()
        text = str(content or "").strip()
        if not raw_url or not text:
            return None

        canonical_url = canonicalize_search_url(raw_url) or raw_url
        document_id = _stable_document_id(canonical_url)
        content_hash = _content_hash(text)
        title_text = str(title or "").strip()
        source_family = classify_source_family(canonical_url)
        ttl_seconds = min(self._ttl_seconds, source_family_ttl_seconds(source_family))
        fetched_dt = _parse_datetime(fetched_at) or _utcnow()
        published_dt = _parse_datetime(published_at)
        expires_dt = fetched_dt + timedelta(seconds=ttl_seconds)
        freshness_state = _freshness_state(fetched_dt, expires_dt)
        refresh_state = "fresh" if freshness_state == "fresh" else "stale"
        chunks = self._chunk_document(text)
        if not chunks:
            return None
        primary_chunk_id = self._best_chunk_id(chunks, query, document_id)
        metadata_payload = dict(metadata or {})
        metadata_payload.setdefault("query", query)
        metadata_payload.setdefault("chunk_size", self._chunk_size)
        metadata_payload.setdefault("chunk_overlap", self._chunk_overlap)
        metadata_payload.setdefault("source_family", source_family)

        with self._lock:
            conn = self._connect()
            existing = conn.execute(
                "SELECT content_hash FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            duplicate_row = conn.execute(
                """
                SELECT document_id
                FROM documents
                WHERE content_hash = ? AND document_id != ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (content_hash, document_id),
            ).fetchone()
            duplicate_of_document_id = (
                str(duplicate_row["document_id"]) if duplicate_row is not None else None
            )
            now_text = _isoformat()
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, url, canonical_url, domain, title, source_type,
                    content_hash, fetched_at, published_at, last_seen_at,
                    freshness_state, refresh_state, ttl_seconds, expires_at,
                    duplicate_of_document_id, lineage_note, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    url = excluded.url,
                    canonical_url = excluded.canonical_url,
                    domain = excluded.domain,
                    title = excluded.title,
                    source_type = excluded.source_type,
                    content_hash = excluded.content_hash,
                    fetched_at = excluded.fetched_at,
                    published_at = excluded.published_at,
                    last_seen_at = excluded.last_seen_at,
                    freshness_state = excluded.freshness_state,
                    refresh_state = excluded.refresh_state,
                    ttl_seconds = excluded.ttl_seconds,
                    expires_at = excluded.expires_at,
                    duplicate_of_document_id = excluded.duplicate_of_document_id,
                    lineage_note = excluded.lineage_note,
                    metadata_json = excluded.metadata_json
                """,
                (
                    document_id,
                    raw_url,
                    canonical_url,
                    domain_from_url(canonical_url),
                    title_text,
                    source_type,
                    content_hash,
                    fetched_dt.astimezone(timezone.utc).isoformat(),
                    published_dt.astimezone(timezone.utc).isoformat() if published_dt else None,
                    now_text,
                    freshness_state,
                    refresh_state,
                    ttl_seconds,
                    expires_dt.astimezone(timezone.utc).isoformat(),
                    duplicate_of_document_id,
                    "content_hash_match" if duplicate_of_document_id else "",
                    _metadata_json(metadata_payload),
                ),
            )
            if existing is None or str(existing["content_hash"]) != content_hash:
                conn.execute("DELETE FROM chunk_embeddings WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE document_id = ?)", (document_id,))
                conn.execute("DELETE FROM chunk_fts WHERE document_id = ?", (document_id,))
                conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
                for chunk in chunks:
                    chunk_id = _stable_chunk_id(document_id, int(chunk["index"]))
                    chunk_text = str(chunk["chunk"] or "")
                    conn.execute(
                        """
                        INSERT INTO chunks (
                            chunk_id, document_id, chunk_index, start_char, end_char, text, text_hash, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            document_id,
                            int(chunk["index"]),
                            int(chunk["start_char"]),
                            int(chunk["end_char"]),
                            chunk_text,
                            _content_hash(chunk_text),
                            _metadata_json({"query": query}),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO chunk_fts (chunk_id, document_id, canonical_url, title, text)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (chunk_id, document_id, canonical_url, title_text, chunk_text),
                    )
            conn.commit()

        return BeaconStoredDocument(
            document_id=document_id,
            chunk_count=len(chunks),
            primary_chunk_id=primary_chunk_id,
            canonical_url=canonical_url,
            url=raw_url,
            title=title_text,
            source_type=source_type,
            content_hash=content_hash,
            fetched_at=fetched_dt.astimezone(timezone.utc).isoformat(),
            published_at=published_dt.astimezone(timezone.utc).isoformat() if published_dt else None,
            freshness_state=freshness_state,
            refresh_state=refresh_state,
            ttl_seconds=ttl_seconds,
            expires_at=expires_dt.astimezone(timezone.utc).isoformat(),
            duplicate_of_document_id=duplicate_of_document_id,
            metadata=metadata_payload,
        )

    def _search_sql(
        self,
        query: str,
        *,
        limit: int,
        allowed_domains: tuple[str, ...],
        blocked_domains: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        params: list[Any] = [fts_query]
        filters: list[str] = []
        if allowed_domains:
            filters.append(
                "d.domain IN (" + ", ".join("?" for _ in allowed_domains) + ")"
            )
            params.extend(allowed_domains)
        if blocked_domains:
            filters.append(
                "d.domain NOT IN (" + ", ".join("?" for _ in blocked_domains) + ")"
            )
            params.extend(blocked_domains)
        params.append(max(limit * 4, limit))
        where = ""
        if filters:
            where = " AND " + " AND ".join(filters)
        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT
                c.chunk_id,
                c.document_id,
                c.chunk_index,
                c.text,
                d.url,
                d.canonical_url,
                d.title,
                d.source_type,
                d.fetched_at,
                d.published_at,
                d.freshness_state,
                d.duplicate_of_document_id,
                bm25(chunk_fts) AS bm25
            FROM chunk_fts
            JOIN chunks c ON chunk_fts.chunk_id = c.chunk_id
            JOIN documents d ON c.document_id = d.document_id
            WHERE chunk_fts MATCH ?{where}
            ORDER BY bm25 ASC, d.fetched_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: tuple[str, ...] = (),
        blocked_domains: tuple[str, ...] = (),
    ) -> list[BeaconCorpusHit]:
        if not str(query or "").strip():
            return []
        try:
            rows = self._search_sql(
                query,
                limit=limit,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
            )
        except sqlite3.OperationalError:
            logger.debug("Beacon FTS query failed", exc_info=True)
            rows = []
        terms = _query_terms(query)
        best_by_document: dict[str, BeaconCorpusHit] = {}
        for row in rows:
            text = str(row.get("text") or "")
            lowered = text.lower()
            lexical_score = float(sum(lowered.count(term) for term in terms)) or 0.0
            fetched_dt = _parse_datetime(str(row.get("fetched_at") or ""))
            expires_dt = None
            freshness_score = 0.0
            if fetched_dt is not None:
                expires_dt = fetched_dt + timedelta(seconds=self._ttl_seconds)
                age_days = max((_utcnow() - fetched_dt).total_seconds() / 86400.0, 0.0)
                freshness_score = max(0.0, 1.0 - min(age_days / 30.0, 1.0))
            bm25 = abs(float(row.get("bm25") or 0.0))
            final_score = lexical_score + freshness_score - min(bm25, 10.0) * 0.01
            hit = BeaconCorpusHit(
                document_id=str(row["document_id"]),
                chunk_id=str(row["chunk_id"]),
                canonical_url=str(row["canonical_url"]),
                url=str(row["url"]),
                title=str(row["title"] or "").strip(),
                snippet=_excerpt(text),
                source_type=str(row["source_type"] or "web"),
                fetched_at=str(row["fetched_at"] or ""),
                published_at=str(row["published_at"] or "").strip() or None,
                freshness_state=_freshness_state(fetched_dt, expires_dt),
                duplicate_of_document_id=str(row["duplicate_of_document_id"] or "").strip() or None,
                ranking_features={
                    "lexical": lexical_score,
                    "freshness": freshness_score,
                    "bm25_abs": bm25,
                    "final": final_score,
                },
            )
            current = best_by_document.get(hit.document_id)
            if current is None or hit.ranking_features["final"] > current.ranking_features["final"]:
                best_by_document[hit.document_id] = hit
        hits = sorted(
            best_by_document.values(),
            key=lambda item: item.ranking_features.get("final", 0.0),
            reverse=True,
        )
        return hits[:limit]

    def _semantic_candidate_rows(
        self,
        *,
        limit: int,
        allowed_domains: tuple[str, ...],
        blocked_domains: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        filters: list[str] = []
        if allowed_domains:
            filters.append(
                "d.domain IN (" + ", ".join("?" for _ in allowed_domains) + ")"
            )
            params.extend(allowed_domains)
        if blocked_domains:
            filters.append(
                "d.domain NOT IN (" + ", ".join("?" for _ in blocked_domains) + ")"
            )
            params.extend(blocked_domains)
        params.append(max(limit, 1))
        where = ""
        if filters:
            where = "WHERE " + " AND ".join(filters)
        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT
                c.chunk_id,
                c.document_id,
                c.chunk_index,
                c.text,
                d.url,
                d.canonical_url,
                d.title,
                d.source_type,
                d.fetched_at,
                d.published_at,
                d.freshness_state,
                d.duplicate_of_document_id
            FROM chunks c
            JOIN documents d ON c.document_id = d.document_id
            {where}
            ORDER BY d.fetched_at DESC, c.chunk_index ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_dict(row) for row in rows]

    async def semantic_search(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed_domains: tuple[str, ...] = (),
        blocked_domains: tuple[str, ...] = (),
    ) -> list[BeaconCorpusHit]:
        if not _env_bool("DAN_BEACON_ENABLE_EMBEDDINGS", False):
            return []
        provider, model = await _resolve_beacon_embedding_provider()
        if provider is None or not str(query or "").strip():
            return []
        candidate_rows = self._semantic_candidate_rows(
            limit=max(limit * 24, 64),
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )
        if not candidate_rows:
            return []

        query_result = await provider.embed([query], model)
        if not query_result.vectors:
            return []
        query_vector = list(query_result.vectors[0])

        chunk_ids = [str(row["chunk_id"]) for row in candidate_rows]
        embeddings: dict[str, list[float]] = {}
        conn = self._connect()
        if chunk_ids:
            placeholders = ", ".join("?" for _ in chunk_ids)
            rows = conn.execute(
                f"SELECT chunk_id, vector_json FROM chunk_embeddings WHERE chunk_id IN ({placeholders}) AND model = ?",
                [*chunk_ids, model],
            ).fetchall()
            for row in rows:
                try:
                    embeddings[str(row["chunk_id"])] = list(json.loads(str(row["vector_json"])))
                except Exception:
                    continue

        missing_rows = [row for row in candidate_rows if str(row["chunk_id"]) not in embeddings]
        for start in range(0, len(missing_rows), 32):
            batch = missing_rows[start : start + 32]
            embed_result = await provider.embed([str(row["text"] or "") for row in batch], model)
            for row, vector in zip(batch, embed_result.vectors):
                vector_list = list(vector)
                chunk_id = str(row["chunk_id"])
                embeddings[chunk_id] = vector_list
                conn.execute(
                    """
                    INSERT INTO chunk_embeddings (chunk_id, model, vector_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        model = excluded.model,
                        vector_json = excluded.vector_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        chunk_id,
                        model,
                        json.dumps(vector_list),
                        _isoformat(),
                    ),
                )
        conn.commit()

        terms = _query_terms(query)
        best_by_document: dict[str, BeaconCorpusHit] = {}
        for row in candidate_rows:
            chunk_id = str(row["chunk_id"])
            vector = embeddings.get(chunk_id)
            if vector is None:
                continue
            semantic_score = _cosine_similarity(query_vector, vector)
            lexical_score = float(
                sum(str(row.get("text") or "").lower().count(term) for term in terms)
            )
            final_score = semantic_score + lexical_score * 0.1
            hit = BeaconCorpusHit(
                document_id=str(row["document_id"]),
                chunk_id=chunk_id,
                canonical_url=str(row["canonical_url"]),
                url=str(row["url"]),
                title=str(row["title"] or "").strip(),
                snippet=_excerpt(str(row.get("text") or "")),
                source_type=str(row["source_type"] or "web"),
                fetched_at=str(row["fetched_at"] or ""),
                published_at=str(row["published_at"] or "").strip() or None,
                freshness_state=str(row["freshness_state"] or "fresh"),
                duplicate_of_document_id=str(row["duplicate_of_document_id"] or "").strip() or None,
                ranking_features={
                    "semantic": semantic_score,
                    "lexical": lexical_score,
                    "final": final_score,
                },
            )
            current = best_by_document.get(hit.document_id)
            if current is None or hit.ranking_features["final"] > current.ranking_features["final"]:
                best_by_document[hit.document_id] = hit
        hits = sorted(
            best_by_document.values(),
            key=lambda item: item.ranking_features.get("final", 0.0),
            reverse=True,
        )
        return hits[:limit]

    def lookup_url(self, url: str, *, query: str = "") -> BeaconCorpusHit | None:
        canonical_url = canonicalize_search_url(url) or str(url or "").strip()
        if not canonical_url:
            return None
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT
                c.chunk_id,
                c.document_id,
                c.text,
                d.url,
                d.canonical_url,
                d.title,
                d.source_type,
                d.fetched_at,
                d.published_at,
                d.freshness_state,
                d.duplicate_of_document_id
            FROM documents d
            JOIN chunks c ON d.document_id = c.document_id
            WHERE d.canonical_url = ?
            ORDER BY c.chunk_index ASC
            """,
            (canonical_url,),
        ).fetchall()
        if not rows:
            return None
        terms = _query_terms(query)
        best_row = rows[0]
        best_score = -1
        for row in rows:
            text = str(row["text"] or "").lower()
            score = sum(text.count(term) for term in terms) if terms else 0
            if score > best_score:
                best_row = row
                best_score = score
        row = _row_dict(best_row)
        return BeaconCorpusHit(
            document_id=str(row["document_id"]),
            chunk_id=str(row["chunk_id"]),
            canonical_url=str(row["canonical_url"]),
            url=str(row["url"]),
            title=str(row["title"] or "").strip(),
            snippet=_excerpt(str(row["text"] or "")),
            source_type=str(row["source_type"] or "web"),
            fetched_at=str(row["fetched_at"] or ""),
            published_at=str(row["published_at"] or "").strip() or None,
            freshness_state=str(row["freshness_state"] or "fresh"),
            duplicate_of_document_id=str(row["duplicate_of_document_id"] or "").strip() or None,
            ranking_features={"lexical": float(best_score)},
        )

    def stats(self) -> dict[str, int]:
        conn = self._connect()
        documents = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return {"documents": documents, "chunks": chunks}

    def get_chunk_text(self, chunk_id: str) -> str | None:
        text = str(chunk_id or "").strip()
        if not text:
            return None
        conn = self._connect()
        row = conn.execute(
            "SELECT text FROM chunks WHERE chunk_id = ?",
            (text,),
        ).fetchone()
        if row is None:
            return None
        return str(row["text"] or "").strip() or None

    def get_document_text(self, document_id: str, *, chunk_id: str | None = None) -> str | None:
        if chunk_id:
            chunk_text = self.get_chunk_text(chunk_id)
            if chunk_text:
                return chunk_text
        text = str(document_id or "").strip()
        if not text:
            return None
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT text
            FROM chunks
            WHERE document_id = ?
            ORDER BY chunk_index ASC
            """,
            (text,),
        ).fetchall()
        if not rows:
            return None
        joined = "\n\n".join(str(row["text"] or "").strip() for row in rows if str(row["text"] or "").strip())
        return joined or None

    def list_refresh_candidates(self, *, limit: int = 20) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT document_id, url, canonical_url, title, source_type, fetched_at, expires_at, metadata_json
            FROM documents
            WHERE expires_at IS NOT NULL
              AND expires_at <= ?
            ORDER BY expires_at ASC
            LIMIT ?
            """,
            (_isoformat(), max(1, limit)),
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            payload = _row_dict(row)
            try:
                payload["metadata"] = json.loads(str(payload.pop("metadata_json", "{}") or "{}"))
            except json.JSONDecodeError:
                payload["metadata"] = {}
            candidates.append(payload)
        return candidates

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


_DEFAULT_BEACON_CORPUS_STORE: BeaconCorpusStore | None = None


def get_default_beacon_corpus_store() -> BeaconCorpusStore:
    global _DEFAULT_BEACON_CORPUS_STORE
    if _DEFAULT_BEACON_CORPUS_STORE is None:
        _DEFAULT_BEACON_CORPUS_STORE = BeaconCorpusStore()
    return _DEFAULT_BEACON_CORPUS_STORE


def reset_default_beacon_corpus_store() -> None:
    global _DEFAULT_BEACON_CORPUS_STORE
    if _DEFAULT_BEACON_CORPUS_STORE is not None:
        _DEFAULT_BEACON_CORPUS_STORE.close()
    _DEFAULT_BEACON_CORPUS_STORE = None
