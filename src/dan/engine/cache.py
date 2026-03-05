"""Node-level and semantic cache helpers for token optimization (Plan 18-2)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dan.engine.executor import NodeResult
from dan.engine.state import NodeStatus
from dan.models.nodes import NodeBase
from dan.rag import EmbeddingRegistry
from dan.rag.stores import DocumentRecord, VectorStore, VectorStoreConfig, VectorStoreFactory


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(_stable_json(value))


def _serialize_result(result: NodeResult) -> dict[str, Any]:
    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    return {
        "outputs": result.outputs,
        "status": status,
        "error": result.error,
        "metadata": result.metadata,
    }


def _deserialize_result(data: dict[str, Any]) -> NodeResult:
    raw_status = data.get("status", NodeStatus.COMPLETED.value)
    try:
        status = NodeStatus(raw_status)
    except ValueError:
        status = NodeStatus.COMPLETED
    return NodeResult(
        outputs=data.get("outputs", {}),
        status=status,
        error=data.get("error"),
        metadata=data.get("metadata", {}),
    )


def _extract_savings(result: NodeResult) -> tuple[int, float]:
    meta = result.metadata if isinstance(result.metadata, dict) else {}
    usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    tokens_saved = int(usage.get("total_tokens", 0) or 0)
    cost_saved = float(
        meta.get("estimated_cost_usd", meta.get("cost", meta.get("llm_cost", 0.0))) or 0.0
    )
    return tokens_saved, cost_saved


def _node_cache_config(node: NodeBase) -> dict[str, Any]:
    node_type = getattr(node, "node_type", "")
    if node_type == "llm_operator":
        return {
            "model": getattr(node, "model", ""),
            "prompt_template": getattr(node, "prompt_template", ""),
            "system_prompt": getattr(node, "system_prompt", ""),
            "temperature": getattr(node, "temperature", 0.7),
            "max_tokens": getattr(node, "max_tokens", None),
            "output_json_schema": getattr(node, "output_json_schema", None),
            "tools": getattr(node, "tools", []),
            "input_format": getattr(node, "input_format", "json"),
            "prune_fields": getattr(node, "prune_fields", []),
            "jit_tool_loading": getattr(node, "jit_tool_loading", False),
            "agent_context_tools": getattr(node, "agent_context_tools", False),
            "summarize_inputs": getattr(node, "summarize_inputs", None),
            "target_input_tokens": getattr(node, "target_input_tokens", None),
            "semantic_cache": getattr(node, "semantic_cache", False),
        }
    if node_type == "tool_operator":
        return {
            "tool_id": getattr(node, "tool_id", ""),
            "tool_config": getattr(node, "tool_config", {}),
        }
    if node_type == "code_operator":
        return {
            "code": getattr(node, "code", ""),
            "language": getattr(node, "language", "python"),
            "sandbox_config": getattr(node, "sandbox_config", {}),
        }

    raw = node.model_dump()
    for key in (
        "id",
        "name",
        "description",
        "position",
        "ui",
        "metadata",
        "input_ports",
        "output_ports",
        "read_set",
        "write_set",
    ):
        raw.pop(key, None)
    return raw


@dataclass
class _MemoryEntry:
    payload: dict[str, Any]
    size_bytes: int


class NodeResultCache:
    """Node result memoization with in-memory LRU and optional disk persistence."""

    _DEFAULT_CACHE_DIR = "~/.dan/cache/"

    def __init__(
        self,
        *,
        max_size_mb: int = 100,
        cache_dir: str | None = None,
        enabled: bool = True,
        persistent: bool = False,
        cache_ttl: int | None = None,
    ) -> None:
        self._enabled = enabled
        self._persistent = persistent
        self._default_ttl = cache_ttl
        self._max_bytes = max(1, max_size_mb) * 1024 * 1024
        self._entries: OrderedDict[str, _MemoryEntry] = OrderedDict()
        self._current_bytes = 0

        resolved_dir = cache_dir
        if resolved_dir is None and persistent:
            resolved_dir = self._DEFAULT_CACHE_DIR
        self._cache_dir = Path(resolved_dir).expanduser() if resolved_dir else None
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._hits = 0
        self._misses = 0
        self._invalidations = 0
        self._tokens_saved = 0
        self._cost_saved = 0.0

    @staticmethod
    def compute_cache_key(
        node: NodeBase,
        inputs: dict[str, Any],
        *,
        policy_signature: str = "",
    ) -> str:
        payload = {
            "node_type": getattr(node, "node_type", ""),
            "node_config_hash": _sha256_json(_node_cache_config(node)),
            "effective_inputs_hash": _sha256_json(inputs),
            "policy_signature": policy_signature,
        }
        return _sha256_json(payload)

    def lookup(
        self,
        key: str,
        *,
        memory_snapshot_hash: str | None = None,
    ) -> tuple[NodeResult | None, str]:
        """Return (result, reason) where reason is hit/miss/expired/corrupt/memory_changed."""
        if not self._enabled:
            self._misses += 1
            return None, "disabled"

        payload = self._memory_payload(key)
        reason = "hit"
        if payload is None:
            payload = self._disk_payload(key)
            reason = "disk"

        if payload is None:
            self._misses += 1
            return None, "miss"

        expires_at = payload.get("expires_at")
        if expires_at is not None and time.time() >= float(expires_at):
            self.invalidate(key, reason="ttl_expired")
            self._misses += 1
            return None, "expired"

        if memory_snapshot_hash is not None:
            stored_hash = payload.get("memory_snapshot_hash")
            dep_keys = payload.get("memory_dependency_keys", [])
            if dep_keys and stored_hash and stored_hash != memory_snapshot_hash:
                self.invalidate(key, reason="memory_changed")
                self._misses += 1
                return None, "memory_changed"

        try:
            result = _deserialize_result(payload["result"])
        except Exception:
            self.invalidate(key, reason="corrupt")
            self._misses += 1
            return None, "corrupt"

        self._hits += 1
        saved_tokens, saved_cost = _extract_savings(result)
        self._tokens_saved += saved_tokens
        self._cost_saved += saved_cost
        return result, reason

    def put(
        self,
        key: str,
        result: NodeResult,
        *,
        ttl: int | None = None,
        memory_dependency_keys: list[str] | None = None,
        memory_snapshot_hash: str | None = None,
    ) -> None:
        if not self._enabled:
            return

        effective_ttl = ttl if ttl is not None else self._default_ttl
        now = time.time()
        payload: dict[str, Any] = {
            "result": _serialize_result(result),
            "created_at": now,
            "expires_at": (now + effective_ttl) if effective_ttl is not None else None,
            "memory_dependency_keys": memory_dependency_keys or [],
        }
        if memory_snapshot_hash is not None:
            payload["memory_snapshot_hash"] = memory_snapshot_hash
        size = len(_stable_json(payload).encode("utf-8"))
        self._set_memory(key, payload, size)
        self._save_disk_payload(key, payload)

    def invalidate(self, key: str, *, reason: str = "manual") -> None:
        self._invalidations += 1
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._current_bytes = max(0, self._current_bytes - entry.size_bytes)
        path = self._entry_path(key)
        if path is not None and path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    def clear(self) -> None:
        self._entries.clear()
        self._current_bytes = 0
        if self._cache_dir is None:
            return
        for p in self._cache_dir.glob("*.json"):
            try:
                p.unlink()
            except OSError:
                pass

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (self._hits / total) if total else 0.0,
            "invalidations": self._invalidations,
            "tokens_saved": self._tokens_saved,
            "cost_saved": self._cost_saved,
            "entries": len(self._entries),
            "current_size_bytes": self._current_bytes,
        }

    def _set_memory(self, key: str, payload: dict[str, Any], size: int) -> None:
        old = self._entries.pop(key, None)
        if old is not None:
            self._current_bytes = max(0, self._current_bytes - old.size_bytes)
        self._entries[key] = _MemoryEntry(payload=payload, size_bytes=size)
        self._entries.move_to_end(key, last=True)
        self._current_bytes += size
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        while self._current_bytes > self._max_bytes and self._entries:
            _, entry = self._entries.popitem(last=False)
            self._current_bytes = max(0, self._current_bytes - entry.size_bytes)

    def _memory_payload(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key, last=True)
        return entry.payload

    def _entry_path(self, key: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{key}.json"

    def _disk_payload(self, key: str) -> dict[str, Any] | None:
        path = self._entry_path(key)
        if path is None or not path.exists():
            return None
        try:
            if self._default_ttl is not None:
                mtime = path.stat().st_mtime
                if time.time() - mtime > self._default_ttl:
                    path.unlink(missing_ok=True)
                    return None
            payload = json.loads(path.read_text("utf-8"))
            size = len(_stable_json(payload).encode("utf-8"))
            self._set_memory(key, payload, size)
            return payload
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            return None

    def _save_disk_payload(self, key: str, payload: dict[str, Any]) -> None:
        path = self._entry_path(key)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        closed = False
        try:
            os.write(fd, _stable_json(payload).encode("utf-8"))
            os.close(fd)
            closed = True
            os.replace(tmp, str(path))
        except BaseException:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


class SemanticCache:
    """Semantic response cache using EmbeddingRegistry + VectorStore."""

    def __init__(
        self,
        *,
        embedding_registry: EmbeddingRegistry | None,
        embedding_model: str = "",
        threshold: float = 0.95,
        ttl_hours: float = 24.0,
        enabled: bool = True,
        store: VectorStore | None = None,
        cache_dir: str | None = None,
        collection: str = "__semantic_cache__",
    ) -> None:
        self._enabled = enabled
        self._embedding_registry = embedding_registry
        self._embedding_model = embedding_model
        self._threshold = threshold
        self._ttl_hours = ttl_hours
        self._collection = collection
        self._store = store or VectorStoreFactory.create(VectorStoreConfig(backend="memory"))
        self._collection_ready = False
        self._payloads: dict[str, dict[str, Any]] = {}
        self._hits = 0
        self._misses = 0
        self._cache_dir = Path(cache_dir).expanduser() / "semantic" if cache_dir else None
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def normalize_query(text: str) -> str:
        normalized = re.sub(r"\s+", " ", text.strip().lower())
        normalized = re.sub(r"[.!?,;:]+$", "", normalized)
        return normalized

    async def get(self, prompt: str, *, threshold: float | None = None) -> NodeResult | None:
        if not self._enabled:
            self._misses += 1
            return None

        vector = await self._embed(prompt)
        if vector is None:
            self._misses += 1
            return None

        await self._ensure_collection(len(vector))
        result = await self._store.query(self._collection, vector, top_k=1)
        if not result.chunks:
            self._misses += 1
            return None

        best = result.chunks[0]
        score = float(best.get("score", 0.0) or 0.0)
        if score < (threshold if threshold is not None else self._threshold):
            self._misses += 1
            return None

        entry_id = str(best.get("id", ""))
        payload = self._payloads.get(entry_id) or self._load_payload(entry_id)
        if not payload:
            self._misses += 1
            return None

        expires_at = payload.get("expires_at")
        if expires_at is not None and time.time() >= float(expires_at):
            await self._store.delete_by_ids(self._collection, [entry_id])
            self._payloads.pop(entry_id, None)
            self._delete_payload(entry_id)
            self._misses += 1
            return None

        try:
            cached = _deserialize_result(payload["result"])
        except Exception:
            self._payloads.pop(entry_id, None)
            self._delete_payload(entry_id)
            self._misses += 1
            return None

        self._hits += 1
        return cached

    async def put(
        self,
        prompt: str,
        result: NodeResult,
        *,
        model: str,
        ttl_hours: float | None = None,
    ) -> None:
        if not self._enabled:
            return

        vector = await self._embed(prompt)
        if vector is None:
            return

        await self._ensure_collection(len(vector))
        normalized = self.normalize_query(prompt)
        entry_id = _sha256_text(f"{normalized}|{model}")
        expires = time.time() + 3600.0 * float(ttl_hours if ttl_hours is not None else self._ttl_hours)
        payload = {
            "result": _serialize_result(result),
            "prompt": normalized,
            "model": model,
            "created_at": time.time(),
            "expires_at": expires,
        }
        self._payloads[entry_id] = payload
        self._save_payload(entry_id, payload)

        await self._store.add(
            self._collection,
            [
                DocumentRecord(
                    id=entry_id,
                    text=normalized,
                    embedding=vector,
                    metadata={"model": model, "expires_at": expires},
                )
            ],
        )

    async def clear(self) -> None:
        self._payloads.clear()
        self._collection_ready = False
        try:
            await self._store.delete_collection(self._collection)
        except Exception:
            pass
        if self._cache_dir is not None:
            for p in self._cache_dir.glob("*.json"):
                try:
                    p.unlink()
                except OSError:
                    pass

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": (self._hits / total) if total else 0.0,
            "entries": len(self._payloads),
        }

    async def _embed(self, prompt: str) -> list[float] | None:
        if self._embedding_registry is None:
            return None
        normalized = self.normalize_query(prompt)
        try:
            provider = self._embedding_registry.resolve(self._embedding_model or "default")
            emb = await provider.embed([normalized], self._embedding_model or "")
        except Exception:
            return None
        if not emb.vectors:
            return None
        return emb.vectors[0]

    async def _ensure_collection(self, dimensions: int) -> None:
        if self._collection_ready:
            return
        await self._store.create_collection(self._collection, dimensions)
        self._collection_ready = True

    def _payload_path(self, entry_id: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{entry_id}.json"

    def _save_payload(self, entry_id: str, payload: dict[str, Any]) -> None:
        path = self._payload_path(entry_id)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        closed = False
        try:
            os.write(fd, _stable_json(payload).encode("utf-8"))
            os.close(fd)
            closed = True
            os.replace(tmp, str(path))
        except BaseException:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _load_payload(self, entry_id: str) -> dict[str, Any] | None:
        path = self._payload_path(entry_id)
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text("utf-8"))
            self._payloads[entry_id] = payload
            return payload
        except Exception:
            return None

    def _delete_payload(self, entry_id: str) -> None:
        path = self._payload_path(entry_id)
        if path is None or not path.exists():
            return
        try:
            path.unlink()
        except OSError:
            pass
