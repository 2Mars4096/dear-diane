"""Smart context assembly utilities — selection, analysis, pruning, summarization.

Part of Plan 18-1.  Provides heuristic-first utilities that reduce input
token spend without brute-force truncation.  All classes work standalone
(no LLM calls required) except summarization which is deferred to the
caller.

Key design choices:
  - ``ContextSelector`` scores and partitions inputs into inline vs. deferred
    buckets.  Embedding-based scoring is opt-in (not implemented here).
  - ``PromptAnalyzer`` statically analyses prompt templates for unused
    variables, repeated boilerplate, and token estimates.
  - ``PayloadPruner`` mechanically strips fields and re-serializes data
    in compact formats (JSON / YAML / compact).
  - ``SummarizationConfig`` is a Pydantic model describing summarization
    parameters — actual LLM summarization is wired by the executor layer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from pydantic import BaseModel, Field

from dan.models.context import CompactionRule, CompactionStrategy
from dan.models.nodes import HistoryPolicy
from dan.utils.tokens import estimate_tokens

logger = logging.getLogger(__name__)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# Summarization config (Pydantic model, no runtime behaviour)
# ---------------------------------------------------------------------------


class SummarizationConfig(BaseModel):
    """Describes how to summarize long free-text inputs before injection."""

    model: str = Field(
        default="",
        description="Model for summarization; empty string means cheapest available",
    )
    max_summary_tokens: int = Field(
        default=500,
        description="Token budget for the generated summary",
    )
    preserve_structured: bool = Field(
        default=True,
        description="Keep JSON/dict fields verbatim; only summarize free text",
    )
    persist_to_memory: bool = Field(
        default=False,
        description="When True, store summary in ShortTermMemory",
    )


# ---------------------------------------------------------------------------
# Prompt analysis
# ---------------------------------------------------------------------------

_TEMPLATE_VAR_RE = re.compile(r"\{(\w+)\}")


@dataclass
class PromptAnalysis:
    """Result of static prompt template analysis."""

    unused_variables: list[str] = field(default_factory=list)
    referenced_variables: list[str] = field(default_factory=list)
    estimated_template_tokens: int = 0
    verbose_patterns: list[str] = field(default_factory=list)


class PromptAnalyzer:
    """Statically analyses prompt templates for optimisation opportunities."""

    def analyze(self, prompt_template: str, input_ports: list[str]) -> PromptAnalysis:
        referenced = list(dict.fromkeys(_TEMPLATE_VAR_RE.findall(prompt_template)))
        unused = [p for p in input_ports if p not in referenced]

        verbose = self._detect_verbose_patterns(prompt_template)

        return PromptAnalysis(
            unused_variables=unused,
            referenced_variables=referenced,
            estimated_template_tokens=estimate_tokens(prompt_template),
            verbose_patterns=verbose,
        )

    @staticmethod
    def _detect_verbose_patterns(text: str, min_len: int = 50) -> list[str]:
        """Flag instruction phrases (>min_len chars) that appear 2+ times."""
        sentences = re.split(r"[.!?\n]", text)
        seen: dict[str, int] = {}
        for s in sentences:
            s = s.strip()
            if len(s) >= min_len:
                seen[s] = seen.get(s, 0) + 1

        return [s for s, count in seen.items() if count >= 2]


# ---------------------------------------------------------------------------
# Context selection (relevance scoring)
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: dict[str, float] = {
    "keyword_overlap": 0.25,
    "recency": 0.15,
    "declared_dependency": 0.45,
    "length_penalty": 0.15,
}


class ContextSelector:
    """Scores input ports by relevance and partitions into inline / deferred."""

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = dict(_DEFAULT_WEIGHTS)
        if weights:
            self.weights.update(weights)

    # -- public API ---------------------------------------------------------

    def score_inputs(
        self,
        inputs: dict[str, Any],
        prompt_template: str,
    ) -> dict[str, float]:
        """Return a relevance score (0.0–1.0) for each input port."""
        scores: dict[str, float] = {}
        for port_name, value in inputs.items():
            content = self._to_str(value)
            metadata = value if isinstance(value, dict) else {}

            raw = {
                "keyword_overlap": self._keyword_overlap(content, prompt_template),
                "recency": self._recency_score(metadata),
                "declared_dependency": self._declared_dependency(port_name, prompt_template),
                "length_penalty": self._length_penalty(content),
            }

            score = sum(self.weights.get(k, 0.0) * v for k, v in raw.items())
            scores[port_name] = round(min(max(score, 0.0), 1.0), 4)

        return scores

    def select(
        self,
        inputs: dict[str, Any],
        prompt_template: str,
        target_tokens: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Partition *inputs* into (inline, deferred) based on relevance.

        When *target_tokens* is None every input is inlined.
        """
        if target_tokens is None:
            return dict(inputs), {}

        scores = self.score_inputs(inputs, prompt_template)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

        inline: dict[str, Any] = {}
        deferred: dict[str, Any] = {}
        running_tokens = estimate_tokens(prompt_template)

        for port_name, _score in ranked:
            value = inputs[port_name]
            tokens = estimate_tokens(self._to_str(value))
            if running_tokens + tokens <= target_tokens:
                inline[port_name] = value
                running_tokens += tokens
            else:
                deferred[port_name] = value

        return inline, deferred

    # -- scoring heuristics (each returns 0.0–1.0) -------------------------

    @staticmethod
    def _keyword_overlap(content: str, template: str) -> float:
        """Fraction of template variables whose names appear in *content*."""
        variables = _TEMPLATE_VAR_RE.findall(template)
        if not variables:
            return 0.0
        content_lower = content.lower()
        hits = sum(1 for v in variables if v.lower() in content_lower)
        return hits / len(variables)

    @staticmethod
    def _recency_score(metadata: dict[str, Any]) -> float:
        """Newer inputs score higher.  Returns 1.0 when no timestamp."""
        import time

        ts = metadata.get("timestamp") or metadata.get("created_at")
        if ts is None:
            return 1.0
        try:
            age_hours = (time.time() - float(ts)) / 3600
            return max(0.0, 1.0 - age_hours / 168)  # decay over 1 week
        except (TypeError, ValueError):
            return 1.0

    @staticmethod
    def _declared_dependency(port_name: str, template: str) -> float:
        """1.0 if ``{port_name}`` appears as a variable in *template*."""
        return 1.0 if f"{{{port_name}}}" in template else 0.0

    @staticmethod
    def _length_penalty(content: str, max_efficient: int = 5000) -> float:
        """Penalize very long inputs — shorter is higher score."""
        length = len(content)
        if length <= max_efficient:
            return 1.0
        return max(0.0, 1.0 - (length - max_efficient) / (max_efficient * 4))

    @staticmethod
    def _to_str(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return str(value)


# ---------------------------------------------------------------------------
# Payload pruning & format compaction
# ---------------------------------------------------------------------------


class PayloadPruner:
    """Strips specified fields from structured data and re-serializes."""

    def __init__(self, prune_patterns: list[str]) -> None:
        self._patterns = prune_patterns

    def prune(self, data: Any) -> tuple[Any, int]:
        """Return (pruned_data, fields_removed_count)."""
        counter = {"removed": 0}
        pruned = self._walk(data, path="", counter=counter)
        return pruned, counter["removed"]

    def format_compact(self, data: Any, fmt: str = "json") -> str:
        """Serialize *data* as JSON, YAML, or compact format."""
        if fmt == "yaml":
            return self._format_yaml(data)
        if fmt == "compact":
            return self._format_compact(data)
        return json.dumps(data, indent=2, default=str)

    # -- internals ----------------------------------------------------------

    def _matches(self, path: str, key: str) -> bool:
        """Check whether *key* at *path* matches any prune pattern.

        ``"*.key"`` matches *key* at any depth.
        ``"root.key"`` matches only at that exact path.
        """
        full_path = f"{path}.{key}" if path else key
        for pattern in self._patterns:
            if pattern.startswith("*."):
                # wildcard depth — match the key portion
                suffix = pattern[2:]
                if fnmatch(key, suffix):
                    return True
            else:
                if fnmatch(full_path, pattern):
                    return True
        return False

    def _walk(self, obj: Any, path: str, counter: dict[str, int]) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for k, v in obj.items():
                if self._matches(path, k):
                    counter["removed"] += 1
                    continue
                child_path = f"{path}.{k}" if path else k
                out[k] = self._walk(v, child_path, counter)
            return out
        if isinstance(obj, list):
            return [self._walk(item, path, counter) for item in obj]
        return obj

    @staticmethod
    def _format_yaml(data: Any) -> str:
        try:
            import yaml

            return yaml.dump(data, default_flow_style=False, sort_keys=False)
        except ImportError:
            logger.debug("pyyaml not installed — falling back to JSON for yaml format")
            return json.dumps(data, indent=2, default=str)

    @staticmethod
    def _format_compact(data: Any, indent: int = 0) -> str:
        """Indentation-based key:value format — no braces or brackets."""
        prefix = "  " * indent
        lines: list[str] = []

        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{prefix}{k}:")
                    lines.append(PayloadPruner._format_compact(v, indent + 1))
                else:
                    lines.append(f"{prefix}{k}: {v}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}-")
                    lines.append(PayloadPruner._format_compact(item, indent + 1))
                else:
                    lines.append(f"{prefix}- {item}")
        else:
            lines.append(f"{prefix}{data}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool-schema JIT loading
# ---------------------------------------------------------------------------


class ToolSchemaResolver:
    """Serve lightweight tool catalog + on-demand full schemas."""

    def __init__(self, full_schemas: list[dict[str, Any]]) -> None:
        self._schemas: dict[str, dict[str, Any]] = {}
        for schema in full_schemas:
            fn = schema.get("function", {}) if isinstance(schema, dict) else {}
            name = fn.get("name")
            if isinstance(name, str) and name:
                self._schemas[name] = schema

    def get_catalog(self) -> list[dict[str, Any]]:
        catalog: list[dict[str, Any]] = []
        for name, schema in sorted(self._schemas.items()):
            fn = schema.get("function", {})
            catalog.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": fn.get("description", ""),
                    },
                }
            )
        return catalog

    def get_full_schema(self, tool_name: str) -> dict[str, Any] | None:
        return self._schemas.get(tool_name)

    @staticmethod
    def get_resolver_tool() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "get_tool_schema",
                "description": (
                    "Get full parameter schema for a tool by name. "
                    "Call before invoking unknown tools."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool_name": {
                            "type": "string",
                            "description": "Tool name to inspect",
                        }
                    },
                    "required": ["tool_name"],
                },
            },
        }

    def tokens_saved(self) -> int:
        full = estimate_tokens(json.dumps(list(self._schemas.values()), default=str))
        catalog = estimate_tokens(json.dumps(self.get_catalog(), default=str))
        return max(0, full - catalog)


# ---------------------------------------------------------------------------
# Agent context tools (MemGPT-style)
# ---------------------------------------------------------------------------


class ContextToolProvider:
    """Built-in tools for on-demand context paging."""

    TOOL_NAMES = {
        "search_context",
        "read_context",
        "read_state",
        "list_available_context",
    }

    def __init__(
        self,
        *,
        artifacts: Any = None,
        state_store: Any = None,
        local_state: Any = None,
        memory: Any = None,
        embedding_registry: Any = None,
        deferred_inputs: dict[str, Any] | None = None,
        run_scope: str = "",
    ) -> None:
        self._artifacts = artifacts
        self._state_store = state_store
        self._local_state = local_state
        self._memory = memory
        self._embedding_registry = embedding_registry
        self._deferred = deferred_inputs or {}
        self._run_scope = run_scope

    @classmethod
    def has_tool(cls, name: str) -> bool:
        return name in cls.TOOL_NAMES

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            self._search_schema(),
            self._read_context_schema(),
            self._read_state_schema(),
            self._list_schema(),
        ]

    async def execute_tool(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "search_context":
            result = await self.search_context(
                query=str(args.get("query", "")),
                scope=str(args.get("scope", "all")),
            )
            return json.dumps(result, default=str)
        if tool_name == "read_context":
            result = await self.read_context(str(args.get("ref", "")))
            return json.dumps(result, default=str)
        if tool_name == "read_state":
            result = await self.read_state(str(args.get("key", "")))
            return json.dumps(result, default=str)
        if tool_name == "list_available_context":
            result = await self.list_available_context()
            return json.dumps(result, default=str)
        return json.dumps({"error": f"Unknown context tool '{tool_name}'"})

    async def search_context(self, query: str, scope: str = "all") -> list[dict[str, Any]]:
        q = query.strip().lower()
        if not q:
            return []

        hits: list[dict[str, Any]] = []
        if scope in ("all", "deferred"):
            for key, value in self._deferred.items():
                text = self._to_preview(value, max_chars=2000).lower()
                if q in text:
                    hits.append({
                        "ref": key,
                        "type": "deferred_input",
                        "summary": self._to_preview(value),
                    })

        if scope in ("all", "memory") and self._memory is not None:
            for item in getattr(self._memory, "items", []):
                content = getattr(item, "content", "")
                if q in content.lower():
                    hits.append({
                        "ref": f"memory:{getattr(item, 'source_node_id', '')}:{int(getattr(item, 'timestamp', 0))}",
                        "type": "memory_item",
                        "summary": self._to_preview(content),
                    })

        if scope in ("all", "artifacts") and self._artifacts is not None:
            for uri in getattr(self._artifacts, "_store", {}).keys():
                if q in uri.lower():
                    hits.append({
                        "ref": uri,
                        "type": "artifact",
                        "summary": uri,
                    })

        return hits[:20]

    async def read_context(self, ref: str) -> dict[str, Any]:
        if ref in self._deferred:
            return {
                "ref": ref,
                "type": "deferred_input",
                "value": self._deferred[ref],
            }
        if self._artifacts is not None and self._artifacts.has(ref):
            try:
                return {
                    "ref": ref,
                    "type": "artifact",
                    "value": self._artifacts.fetch(ref),
                }
            except Exception as exc:
                return {"error": f"Artifact read failed: {exc}"}
        if self._memory is not None and ref.startswith("memory:"):
            return {"ref": ref, "type": "memory", "value": self._memory.to_text(20)}
        return {"error": f"Context ref '{ref}' not found"}

    async def read_state(self, key: str) -> dict[str, Any]:
        if self._state_store is not None:
            scope = self._run_scope or "run"
            try:
                value = await self._state_store.read(scope, key)
                return {"scope": scope, "key": key, "value": value}
            except Exception as exc:
                return {"error": f"StateStore read failed: {exc}"}

        if self._local_state is not None:
            scopes = getattr(self._local_state, "_scopes", {})
            for scope_id, data in scopes.items():
                if key in data:
                    return {"scope": scope_id, "key": key, "value": data[key]}
        return {"error": "State store not available"}

    async def list_available_context(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for name, value in self._deferred.items():
            items.append({
                "ref": name,
                "type": "deferred_input",
                "summary": self._to_preview(value),
                "estimated_tokens": estimate_tokens(self._to_preview(value, max_chars=4000)),
            })

        if self._artifacts is not None:
            for uri in getattr(self._artifacts, "_store", {}).keys():
                items.append({
                    "ref": uri,
                    "type": "artifact",
                    "summary": uri,
                    "estimated_tokens": 0,
                })

        if self._memory is not None:
            for idx, item in enumerate(getattr(self._memory, "items", [])[-10:]):
                items.append({
                    "ref": f"memory:{idx}",
                    "type": "memory_item",
                    "summary": self._to_preview(getattr(item, "content", "")),
                    "estimated_tokens": getattr(item, "token_count", 0),
                })
        return items

    @staticmethod
    def _to_preview(value: Any, *, max_chars: int = 200) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, default=str)
            except Exception:
                text = str(value)
        return text[:max_chars]

    @staticmethod
    def _search_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "search_context",
                "description": "Search available context (deferred inputs, memory, artifacts).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "scope": {
                            "type": "string",
                            "enum": ["all", "deferred", "memory", "artifacts"],
                            "default": "all",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    @staticmethod
    def _read_context_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_context",
                "description": "Read a deferred context item or artifact by reference.",
                "parameters": {
                    "type": "object",
                    "properties": {"ref": {"type": "string"}},
                    "required": ["ref"],
                },
            },
        }

    @staticmethod
    def _read_state_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_state",
                "description": "Read structured execution state by key.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
        }

    @staticmethod
    def _list_schema() -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "list_available_context",
                "description": "List available deferred context with summaries and token estimates.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }


# ---------------------------------------------------------------------------
# System prompt dedup tracker
# ---------------------------------------------------------------------------


class SystemPromptTracker:
    """Track duplicated system prompts across sequential calls."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._unique_tokens = 0
        self._duplicate_tokens = 0
        self._total_calls = 0

    def track(self, system_prompt: str) -> dict[str, Any]:
        key = _sha256_text(system_prompt)
        count = self._counts.get(key, 0)
        tokens = estimate_tokens(system_prompt)
        self._total_calls += 1
        if count == 0:
            self._unique_tokens += tokens
        else:
            self._duplicate_tokens += tokens
        self._counts[key] = count + 1
        return {
            "is_duplicate": count > 0,
            "shared_prefix_length": 0,
            "unique_tokens": tokens if count == 0 else 0,
        }

    @staticmethod
    def extract_shared_prefix(prompt_a: str, prompt_b: str) -> str:
        i = 0
        limit = min(len(prompt_a), len(prompt_b))
        while i < limit and prompt_a[i] == prompt_b[i]:
            i += 1
        return prompt_a[:i]

    def metrics(self) -> dict[str, Any]:
        return {
            "unique_prompts": len(self._counts),
            "total_calls": self._total_calls,
            "duplicate_tokens": self._duplicate_tokens,
            "unique_tokens": self._unique_tokens,
        }


# ---------------------------------------------------------------------------
# History management and loop compaction helpers
# ---------------------------------------------------------------------------


class HistoryManager:
    """Assemble conversation history per HistoryPolicy without silent loss."""

    def assemble_history(
        self,
        messages: list[dict[str, Any]],
        policy: HistoryPolicy,
        *,
        context_tools_available: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        system_msgs = [m for m in messages if m.get("role") == "system"] if policy.keep_system else []
        non_system = [m for m in messages if m.get("role") != "system"]
        n_recent = policy.inline_recent if policy.inline_recent is not None else len(non_system)
        n_recent = max(0, n_recent)
        older = non_system[:-n_recent] if n_recent < len(non_system) else []
        recent = non_system[-n_recent:] if n_recent > 0 else []

        assembled = list(system_msgs)
        if older and policy.summarize_older:
            preview = " | ".join(str(m.get("content", ""))[:80] for m in older[-3:])
            assembled.append(
                {
                    "role": "system",
                    "content": (
                        f"[History summary] {len(older)} earlier messages condensed. "
                        f"Recent snippets: {preview}"
                    ),
                }
            )
        assembled.extend(recent)

        if older and context_tools_available:
            assembled.append(
                {
                    "role": "system",
                    "content": (
                        f"{len(older)} older messages are available via context tools "
                        "(search_context/read_context)."
                    ),
                }
            )
        return assembled, older

    async def persist_older_messages(
        self,
        messages: list[dict[str, Any]],
        memory: Any,
        *,
        node_id: str,
        run_id: str,
    ) -> int:
        if memory is None:
            return 0
        from dan.engine.memory_pipeline import MemoryItem

        count = 0
        for m in messages:
            memory.append(
                MemoryItem(
                    content=json.dumps(m, default=str),
                    source_node_id=node_id,
                    source_run_id=run_id,
                    metadata={"entry_type": "raw_event"},
                )
            )
            count += 1
        return count


class LoopCompactor:
    """Applies CompactionRule strategies to loop iteration payloads.

    When ``require_persistent_recall`` is True and no memory/state store is
    available, lossy strategies (sliding_window, keep_last) fall back to
    ``none`` with a logged warning rather than raising.
    """

    def __init__(
        self,
        rule: CompactionRule,
        *,
        state_store: Any = None,
        memory: Any = None,
        cost_tracker: Any = None,
    ) -> None:
        self._rule = rule
        self._state_store = state_store
        self._memory = memory
        self._cost_tracker = cost_tracker

    def validate_safety(self) -> tuple[bool, str]:
        strategy = self._rule.strategy
        if strategy in (CompactionStrategy.SLIDING_WINDOW, CompactionStrategy.KEEP_LAST):
            if self._rule.require_persistent_recall and self._memory is None and self._state_store is None:
                return (
                    False,
                    (
                        f"Strategy '{strategy.value}' requires persistent recall "
                        "(memory/state store) but neither is available."
                    ),
                )
        return True, ""

    async def compact(
        self,
        accumulated_payloads: list[dict[str, Any]],
        *,
        loop_node_id: str,
        iteration: int,
        context: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Apply compaction and return (compacted_list, emit_data).

        On safety failure (persistent recall required but unavailable),
        falls back to ``none`` strategy with a warning.
        """
        safe, reason = self.validate_safety()
        strategy = self._rule.strategy

        if not safe:
            logger.warning(
                "LoopCompactor: %s — falling back to 'none' for node '%s'",
                reason, loop_node_id,
            )
            strategy = CompactionStrategy.NONE

        before = self._token_count(accumulated_payloads)
        compacted = list(accumulated_payloads)

        if strategy == CompactionStrategy.KEEP_LAST:
            compacted = self._apply_keep_last(accumulated_payloads)
        elif strategy == CompactionStrategy.SLIDING_WINDOW:
            compacted = self._apply_sliding_window(accumulated_payloads, self._rule.window_size or 3)
        elif strategy == CompactionStrategy.DIFF_BASED:
            compacted = self._apply_diff_based(accumulated_payloads)
        elif strategy == CompactionStrategy.SUMMARIZE:
            compacted = await self._apply_summarize(accumulated_payloads, iteration)

        after = self._token_count(compacted)
        evicted = accumulated_payloads[: max(0, len(accumulated_payloads) - len(compacted))]
        items_persisted = self.persist_evicted(evicted, loop_node_id)

        if self._state_store is not None:
            try:
                await self._state_store.write(
                    loop_node_id,
                    f"iter_{iteration}_compaction",
                    {
                        "strategy": strategy.value,
                        "tokens_before": before,
                        "tokens_after": after,
                        "removed_items": len(evicted),
                    },
                )
            except Exception:
                logger.debug("Failed writing loop compaction state", exc_info=True)

        return compacted, {
            "strategy": strategy.value,
            "tokens_before": before,
            "tokens_after": after,
            "items_persisted": items_persisted,
        }

    def persist_evicted(
        self,
        evicted_payloads: list[dict[str, Any]],
        node_id: str,
        run_id: str = "",
    ) -> int:
        """Write evicted payloads to ShortTermMemory. Returns count persisted."""
        if not evicted_payloads or self._memory is None:
            return 0
        from dan.engine.memory_pipeline import MemoryItem

        for payload in evicted_payloads:
            self._memory.append(
                MemoryItem(
                    content=json.dumps(payload, default=str),
                    source_node_id=node_id,
                    source_run_id=run_id,
                    metadata={"entry_type": "loop_compaction"},
                )
            )
        return len(evicted_payloads)

    @staticmethod
    def _token_count(payloads: list[dict[str, Any]]) -> int:
        return sum(estimate_tokens(json.dumps(p, default=str)) for p in payloads)

    @staticmethod
    def _apply_sliding_window(payloads: list[dict[str, Any]], window_size: int) -> list[dict[str, Any]]:
        return list(payloads[-max(1, window_size):])

    @staticmethod
    def _apply_keep_last(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(payloads[-1:]) if payloads else []

    async def _apply_summarize(
        self, payloads: list[dict[str, Any]], iteration: int,
    ) -> list[dict[str, Any]]:
        every = self._rule.summarize_every_n or 5
        if (iteration + 1) % every != 0 or len(payloads) <= 1:
            return list(payloads)
        all_keys: set[str] = set()
        for p in payloads[:-1]:
            all_keys.update(p.keys())
        n = len(payloads) - 1
        summary: dict[str, Any] = {
            "__summary__": True,
            "description": f"Iterations 1-{n}: keys={sorted(all_keys)}, total_items={n}",
            "covered_iterations": n,
            "keys": sorted(all_keys),
        }
        return [summary, payloads[-1]]

    @staticmethod
    def _apply_diff_based(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(payloads) <= 1:
            return list(payloads)
        out: list[dict[str, Any]] = [payloads[0]]
        prev = payloads[0]
        for payload in payloads[1:]:
            delta: dict[str, Any] = {}
            for k, v in payload.items():
                if prev.get(k) != v:
                    delta[k] = v
            out.append(delta if delta else {"__unchanged__": True})
            prev = payload
        return out


class TokenBudgetAdvisor:
    """Compute and track advisory per-node token budgets."""

    def __init__(self, total_budget: int | None = None, strategy: str = "proportional") -> None:
        self._total_budget = total_budget
        self._strategy = strategy
        self._usage_history: dict[str, int] = {}
        self._allocations: dict[str, int] = {}

    def compute_advisory(
        self,
        *,
        node_id: str,
        node_type: str,
        historical_usage: int | None = None,
        priority: int = 5,
        remaining_nodes: int = 1,
    ) -> int | None:
        if self._total_budget is None:
            return None

        base = max(1, int(self._total_budget / max(1, remaining_nodes)))
        if historical_usage is not None and historical_usage > 0:
            base = int((base + historical_usage) / 2)

        if self._strategy == "priority":
            weight = max(1, priority)
            base = int(base * (weight / 5.0))
        elif self._strategy == "adaptive":
            prior = self._allocations.get(node_id)
            if prior is not None:
                base = int((base + prior) / 2)

        allocation = max(1, base)
        self._allocations[node_id] = allocation
        return allocation

    def record_actual(self, node_id: str, actual_tokens: int) -> None:
        self._usage_history[node_id] = max(0, int(actual_tokens))
        if self._strategy == "adaptive":
            self._reallocate()

    def _reallocate(self) -> None:
        if self._total_budget is None:
            return
        spent = sum(self._usage_history.values())
        remaining = max(0, self._total_budget - spent)
        pending = [nid for nid in self._allocations if nid not in self._usage_history]
        if not pending:
            return
        per_node = max(1, int(remaining / len(pending)))
        for nid in pending:
            self._allocations[nid] = per_node

    # -- unified cross-source budget (18-3 task 4-4) -------------------------

    SOURCE_CATEGORIES = (
        "edge_data",
        "system_prompt",
        "context_injection",
        "hyperedge_injection",
        "memory_retrieval",
        "rag_chunks",
    )

    def estimate_source_tokens(
        self,
        node: Any,
        context: Any,
    ) -> dict[str, int]:
        """Estimate token contribution from each source category.

        Returns a dict mapping source category -> estimated tokens.
        Advisory only — never blocks execution.
        """
        estimates: dict[str, int] = {cat: 0 for cat in self.SOURCE_CATEGORIES}

        system_prompt = getattr(node, "system_prompt", "") or ""
        if system_prompt:
            estimates["system_prompt"] = estimate_tokens(system_prompt)

        prompt_template = getattr(node, "prompt_template", "") or ""
        if prompt_template:
            estimates["edge_data"] = estimate_tokens(prompt_template)

        if context is not None:
            if getattr(context, "hyperedge_resolver", None) is not None:
                estimates["hyperedge_injection"] = 200

            stm = getattr(context, "short_term_memory", None)
            if stm is not None:
                estimates["memory_retrieval"] = getattr(stm, "total_tokens", 0)

        return estimates

    def compute_variable_budget(
        self,
        node: Any,
        context: Any,
        *,
        node_id: str = "",
    ) -> dict[str, Any]:
        """Subtract fixed-source estimates and allocate remaining to variable sources."""
        source_tokens = self.estimate_source_tokens(node, context)
        fixed = source_tokens.get("system_prompt", 0) + source_tokens.get("hyperedge_injection", 0)
        total = self._total_budget or 0
        remaining_for_variable = max(0, total - fixed)
        variable_sources = ["edge_data", "context_injection", "memory_retrieval", "rag_chunks"]
        variable_total = sum(source_tokens.get(s, 0) for s in variable_sources)
        allocation: dict[str, int] = {}
        for src in variable_sources:
            est = source_tokens.get(src, 0)
            if variable_total > 0 and remaining_for_variable > 0:
                allocation[src] = int(remaining_for_variable * est / variable_total)
            else:
                allocation[src] = est
        return {
            "source_estimates": source_tokens,
            "fixed_tokens": fixed,
            "variable_budget": remaining_for_variable,
            "variable_allocation": allocation,
        }

    def summary(self) -> dict[str, Any]:
        remaining = None
        if self._total_budget is not None:
            remaining = self._total_budget - sum(self._usage_history.values())
        return {
            "total_budget": self._total_budget,
            "strategy": self._strategy,
            "allocated": dict(self._allocations),
            "actual": dict(self._usage_history),
            "remaining": remaining,
        }


# ---------------------------------------------------------------------------
# Waste detection & optimization report (Plan 18-4)
# ---------------------------------------------------------------------------


@dataclass
class WasteFinding:
    """A single waste / optimization opportunity detected post-run."""

    category: str  # unused_context | duplicate | loop_growth | oversized_system | unused_memory_rag | redundant_retrieval
    node_id: str
    description: str
    estimated_saveable_tokens: int = 0
    suggestion: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "node_id": self.node_id,
            "description": self.description,
            "estimated_saveable_tokens": self.estimated_saveable_tokens,
            "suggestion": self.suggestion,
            "details": self.details,
        }


@dataclass
class TokenOptimizationReport:
    """Aggregated waste report for a completed run."""

    findings: list[WasteFinding] = field(default_factory=list)
    total_saveable_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in sorted(self.findings, key=lambda f: f.estimated_saveable_tokens, reverse=True)],
            "total_saveable_tokens": self.total_saveable_tokens,
            "count": len(self.findings),
        }


class TokenWasteAnalyzer:
    """Post-execution analyzer that identifies token waste in a completed run.

    Operates on a snapshot of run data — no live access required. Each
    ``analyze_*`` method populates findings which are collected into a
    :class:`TokenOptimizationReport`.
    """

    def __init__(self) -> None:
        self._findings: list[WasteFinding] = []

    def analyze(
        self,
        *,
        node_breakdowns: dict[str, dict[str, int]],
        node_configs: dict[str, dict[str, Any]],
        events: list[dict[str, Any]] | None = None,
        graph_edges: list[dict[str, Any]] | None = None,
    ) -> TokenOptimizationReport:
        """Run all waste detectors and return a consolidated report."""
        self._findings.clear()
        self._detect_unused_context(node_breakdowns, node_configs)
        self._detect_duplicate_context(node_breakdowns, graph_edges or [])
        self._detect_loop_growth(events or [])
        self._detect_oversized_system(node_breakdowns)
        self._detect_unused_memory_rag(node_breakdowns)
        self._detect_jit_opportunity(node_configs, events or [])
        self._detect_memoization_opportunity(events or [])
        self._detect_reference_opportunity(node_breakdowns, graph_edges or [])

        total = sum(f.estimated_saveable_tokens for f in self._findings)
        return TokenOptimizationReport(findings=list(self._findings), total_saveable_tokens=total)

    # -- individual detectors -----------------------------------------------

    def _detect_unused_context(
        self,
        breakdowns: dict[str, dict[str, int]],
        configs: dict[str, dict[str, Any]],
    ) -> None:
        """Flag nodes whose context_edge_tokens >> 0 but prompt has few template vars."""
        for nid, bd in breakdowns.items():
            ctx_tokens = bd.get("context_edge_tokens", 0)
            total_in = bd.get("total_input_tokens", 1) or 1
            if ctx_tokens > 0 and ctx_tokens / total_in > 0.5:
                cfg = configs.get(nid, {})
                prompt = cfg.get("prompt_template", cfg.get("system_prompt", ""))
                if prompt:
                    vars_in_template = set(_TEMPLATE_VAR_RE.findall(prompt))
                    port_count = len(cfg.get("input_ports", []))
                    if port_count > 0 and len(vars_in_template) < port_count * 0.5:
                        self._findings.append(WasteFinding(
                            category="unused_context",
                            node_id=nid,
                            description=f"Context edges contribute {ctx_tokens} tokens ({ctx_tokens * 100 // total_in}% of input), but only {len(vars_in_template)} of {port_count} ports referenced in prompt",
                            estimated_saveable_tokens=ctx_tokens // 2,
                            suggestion=f"Enable `agent_context_tools` on node {nid} — let the agent pull what it needs on demand instead of injecting all context inline.",
                        ))

    def _detect_duplicate_context(
        self,
        breakdowns: dict[str, dict[str, int]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Flag when the same source feeds multiple edges to the same target."""
        target_sources: dict[str, list[str]] = {}
        for e in edges:
            target = e.get("target", "")
            source = e.get("source", "")
            target_sources.setdefault(target, []).append(source)
        for target, sources in target_sources.items():
            if len(sources) != len(set(sources)):
                dupes = [s for s in sources if sources.count(s) > 1]
                bd = breakdowns.get(target, {})
                ctx_tokens = bd.get("context_edge_tokens", 0)
                if ctx_tokens > 0:
                    est = ctx_tokens // len(sources) * (len(dupes) // 2)
                    self._findings.append(WasteFinding(
                        category="duplicate",
                        node_id=target,
                        description=f"Duplicate edges from {set(dupes)} to node {target}",
                        estimated_saveable_tokens=est,
                        suggestion=f"Remove duplicate context edges feeding into node {target}.",
                        details={"duplicate_sources": list(set(dupes))},
                    ))

    def _detect_loop_growth(self, events: list[dict[str, Any]]) -> None:
        """Flag loops where context grew > 2x without compaction."""
        loop_iterations: dict[str, list[int]] = {}
        for ev in events:
            etype = str(ev.get("event_type", ""))
            data = ev.get("data", {})
            if etype in ("iteration_started", "ITERATION_STARTED", "loop_iteration_start", "LOOP_ITERATION_START"):
                loop_id = str(data.get("loop_id") or ev.get("node_id", ""))
                tokens = data.get("context_tokens", data.get("input_tokens", 0))
                if tokens:
                    loop_iterations.setdefault(loop_id, []).append(int(tokens))

        for loop_id, token_list in loop_iterations.items():
            if len(token_list) >= 3 and token_list[-1] > token_list[0] * 2:
                growth = token_list[-1] / max(1, token_list[0])
                self._findings.append(WasteFinding(
                    category="loop_growth",
                    node_id=loop_id,
                    description=f"Loop context grew {growth:.1f}x across {len(token_list)} iterations ({token_list[0]} → {token_list[-1]} tokens)",
                    estimated_saveable_tokens=token_list[-1] - token_list[0],
                    suggestion=f"Set `compaction_rule.strategy=sliding_window` with persistent recall on loop {loop_id} — context grew {growth:.1f}x across iterations.",
                    details={"growth_factor": growth, "iteration_tokens": token_list},
                ))

    def _detect_oversized_system(self, breakdowns: dict[str, dict[str, int]]) -> None:
        """Flag nodes where system prompt consumes >30% of input budget."""
        for nid, bd in breakdowns.items():
            sys_tok = bd.get("system_tokens", 0)
            total_in = bd.get("total_input_tokens", 1) or 1
            if sys_tok > 0 and sys_tok / total_in > 0.3 and total_in > 500:
                pct = sys_tok * 100 // total_in
                self._findings.append(WasteFinding(
                    category="oversized_system",
                    node_id=nid,
                    description=f"System prompt consumes {sys_tok} tokens ({pct}% of {total_in} total input tokens)",
                    estimated_saveable_tokens=max(0, sys_tok - total_in // 3),
                    suggestion=f"Consider extracting parts of the system prompt on node {nid} into `agent_context_tools` or `prune_fields` — system prompt consumes {pct}% of input.",
                ))

    def _detect_unused_memory_rag(self, breakdowns: dict[str, dict[str, int]]) -> None:
        """Flag nodes where memory/RAG tokens were injected but form a large share."""
        for nid, bd in breakdowns.items():
            mem_tok = bd.get("memory_tokens", 0)
            rag_tok = bd.get("rag_tokens", 0)
            total_in = bd.get("total_input_tokens", 1) or 1
            injected = mem_tok + rag_tok
            if injected > 0 and injected / total_in > 0.3 and total_in > 500:
                pct = injected * 100 // total_in
                self._findings.append(WasteFinding(
                    category="unused_memory_rag",
                    node_id=nid,
                    description=f"Memory/RAG injection contributes {injected} tokens ({pct}% of input) — may be excessive",
                    estimated_saveable_tokens=injected // 3,
                    suggestion=f"Review memory/RAG retrieval on node {nid}: {pct}% of input comes from retrieval. Consider reducing top-k, enabling relevance filtering, or using pass_by_reference.",
                    details={"memory_tokens": mem_tok, "rag_tokens": rag_tok},
                ))

    def _detect_jit_opportunity(
        self,
        configs: dict[str, dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> None:
        """Flag nodes with many tool schemas but few actually called."""
        tools_called: dict[str, set[str]] = {}
        for ev in events:
            etype = str(ev.get("event_type", ""))
            if etype in ("tool_call_started", "TOOL_CALL_STARTED", "tool_call_start", "TOOL_CALL_START"):
                nid = ev.get("node_id", "")
                tool_name = ev.get("data", {}).get("tool_name", "")
                if nid and tool_name:
                    tools_called.setdefault(nid, set()).add(tool_name)

        for nid, cfg in configs.items():
            tools = cfg.get("tools", [])
            jit = cfg.get("jit_tool_loading", False)
            if len(tools) >= 5 and not jit:
                called = tools_called.get(nid, set())
                if len(called) < len(tools) * 0.5:
                    unused_count = len(tools) - len(called)
                    est = unused_count * 200  # ~200 tokens/schema estimate
                    self._findings.append(WasteFinding(
                        category="jit_opportunity",
                        node_id=nid,
                        description=f"Node has {len(tools)} tool schemas but only {len(called)} were called — {unused_count} schemas sent unnecessarily",
                        estimated_saveable_tokens=est,
                        suggestion=f"Enable `jit_tool_loading=True` on node {nid} — {unused_count} of {len(tools)} tool schemas were never used.",
                        details={"total_tools": len(tools), "tools_called": list(called)},
                    ))

    def _detect_memoization_opportunity(self, events: list[dict[str, Any]]) -> None:
        """Flag nodes that ran multiple times with identical inputs."""
        node_runs: dict[str, list[str]] = {}
        for ev in events:
            etype = str(ev.get("event_type", ""))
            if etype in ("node_started", "NODE_STARTED", "node_execution_start", "NODE_EXECUTION_START"):
                nid = ev.get("node_id", "")
                input_hash = ev.get("data", {}).get("input_hash", "")
                if nid and input_hash:
                    node_runs.setdefault(nid, []).append(input_hash)

        for nid, hashes in node_runs.items():
            if len(hashes) >= 2:
                unique = set(hashes)
                if len(unique) < len(hashes):
                    dups = len(hashes) - len(unique)
                    self._findings.append(WasteFinding(
                        category="memoization_opportunity",
                        node_id=nid,
                        description=f"Node {nid} ran {len(hashes)} times with {dups} duplicate input(s)",
                        estimated_saveable_tokens=0,
                        suggestion=f"Enable `memoize=True` on node {nid} — it ran {dups} time(s) with identical inputs.",
                        details={"runs": len(hashes), "unique_inputs": len(unique)},
                    ))

    def _detect_reference_opportunity(
        self,
        breakdowns: dict[str, dict[str, int]],
        edges: list[dict[str, Any]],
    ) -> None:
        """Flag large context edges that aren't using pass_by_reference."""
        for e in edges:
            if str(e.get("edge_type", "")) not in ("context", "ContextEdge"):
                continue
            target = e.get("target", "")
            by_ref = e.get("pass_by_reference", False)
            if by_ref:
                continue
            bd = breakdowns.get(target, {})
            ctx_tokens = bd.get("context_edge_tokens", 0)
            if ctx_tokens > 10000:
                self._findings.append(WasteFinding(
                    category="reference_opportunity",
                    node_id=target,
                    description=f"Edge to node {target} passes {ctx_tokens} tokens inline",
                    estimated_saveable_tokens=ctx_tokens - 500,  # reference payload is ~500 tok
                    suggestion=f"Switch edge to `pass_by_reference=True` — {ctx_tokens} tokens of context are passed inline. Let the agent pull what it needs.",
                    details={
                        "edge_id": e.get("id", ""),
                        "source": e.get("source", ""),
                        "target": target,
                        "edge_tokens": ctx_tokens,
                    },
                ))


# ---------------------------------------------------------------------------
# Evolving context playbooks (Plan 18-4 Task 5 + Plan 17 integration)
# ---------------------------------------------------------------------------

_FINDING_TO_PARAM_ACTION: dict[str, dict[str, Any]] = {
    "jit_opportunity": {"jit_tool_loading": True},
    "oversized_system": {"agent_context_tools": True},
    "reference_opportunity": {},  # edge-level, handled separately
    "memoization_opportunity": {"memoize": True},
}

_SAVINGS_THRESHOLD = 0.10  # 10% improvement required for promotion


@dataclass
class PlaybookEntry:
    """A tracked optimization recommendation + its measured effectiveness."""

    finding: WasteFinding
    run_id: str
    applied: bool = False
    pre_tokens: int = 0
    post_tokens: int = 0
    effectiveness: float = 0.0
    promoted: bool = False
    principle_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.finding.category,
            "node_id": self.finding.node_id,
            "suggestion": self.finding.suggestion,
            "applied": self.applied,
            "pre_tokens": self.pre_tokens,
            "post_tokens": self.post_tokens,
            "effectiveness": self.effectiveness,
            "promoted": self.promoted,
            "principle_id": self.principle_id,
        }


class OptimizationPlaybook:
    """Bridges waste findings with Plan 17's rule generation lifecycle.

    1. After a run, waste findings are recorded as playbook entries.
    2. When a finding is applied (via the apply endpoint) and re-run,
       before/after token counts are compared.
    3. Findings that exceed the savings threshold are promoted to
       ``CausalPrinciple`` objects for Plan 17's ``RuleGenerator``.
    4. Approval mode controls whether generated rules activate immediately.
    """

    def __init__(
        self,
        *,
        approval_mode: str = "always_approve",
        savings_threshold: float = _SAVINGS_THRESHOLD,
    ) -> None:
        if approval_mode not in ("always_approve", "auto_accept"):
            raise ValueError(
                "approval_mode must be 'always_approve' or 'auto_accept'",
            )
        self._approval_mode = approval_mode
        self._threshold = savings_threshold
        self._entries: dict[str, list[PlaybookEntry]] = {}  # keyed by run_id

    @property
    def approval_mode(self) -> str:
        return self._approval_mode

    def record_findings(
        self,
        run_id: str,
        report: TokenOptimizationReport,
    ) -> list[PlaybookEntry]:
        """Record waste findings from a completed run."""
        entries = []
        for finding in report.findings:
            entry = PlaybookEntry(finding=finding, run_id=run_id)
            entries.append(entry)
        self._entries[run_id] = entries
        return entries

    def mark_applied(
        self,
        run_id: str,
        node_id: str,
        pre_tokens: int,
    ) -> None:
        """Mark that a recommendation was applied and record pre-run token count."""
        for entry in self._entries.get(run_id, []):
            if entry.finding.node_id == node_id and not entry.applied:
                entry.applied = True
                entry.pre_tokens = pre_tokens
                break

    def evaluate_effectiveness(
        self,
        run_id: str,
        node_id: str,
        post_tokens: int,
    ) -> PlaybookEntry | None:
        """Compare post-run tokens against pre-run to compute savings."""
        for entry in self._entries.get(run_id, []):
            if entry.finding.node_id == node_id and entry.applied:
                entry.post_tokens = post_tokens
                if entry.pre_tokens > 0:
                    entry.effectiveness = 1.0 - (post_tokens / entry.pre_tokens)
                return entry
        return None

    def promote_effective(
        self,
        run_id: str,
        *,
        workflow_id: str = "",
    ) -> list[dict[str, Any]]:
        """Convert high-effectiveness entries into CausalPrinciple dicts.

        Returns principle dicts (not actual CausalPrinciple objects to avoid
        tight coupling). The caller can pass these to ``RuleLifecycleManager``.
        """
        principles = []
        for entry in self._entries.get(run_id, []):
            if not entry.applied or entry.promoted:
                continue
            if entry.effectiveness < self._threshold:
                continue

            param_changes = _FINDING_TO_PARAM_ACTION.get(entry.finding.category, {})
            principle_dict = {
                "condition": f"Node '{entry.finding.node_id}' with category '{entry.finding.category}'",
                "action": entry.finding.suggestion,
                "reason": f"Applied optimization saved {entry.effectiveness:.0%} tokens ({entry.pre_tokens} → {entry.post_tokens})",
                "source_run_ids": [run_id],
                "source_node_ids": [entry.finding.node_id],
                "confidence": min(1.0, 0.5 + entry.effectiveness),
                "tags": ["token_optimization", entry.finding.category],
                "workflow_id": workflow_id,
                "repair_level": "parameter_fix",
                "suggested_parameter_changes": param_changes,
                "auto_activate": self._approval_mode == "auto_accept",
                "approval_mode": self._approval_mode,
            }
            entry.promoted = True
            principles.append(principle_dict)
        return principles

    def get_entries(self, run_id: str) -> list[PlaybookEntry]:
        return list(self._entries.get(run_id, []))

    def summary(self) -> dict[str, Any]:
        total = sum(len(v) for v in self._entries.values())
        applied = sum(1 for entries in self._entries.values() for e in entries if e.applied)
        promoted = sum(1 for entries in self._entries.values() for e in entries if e.promoted)
        return {
            "total_entries": total,
            "applied": applied,
            "promoted": promoted,
            "approval_mode": self._approval_mode,
            "runs_tracked": len(self._entries),
        }

    def generate_mutation(self, finding: WasteFinding) -> dict[str, Any] | None:
        """Generate a graph mutation operation for a waste finding.

        Returns a mutation dict compatible with ``graph_mutator.apply_mutation``.
        """
        param_changes = _FINDING_TO_PARAM_ACTION.get(finding.category)
        if param_changes:
            return {
                "op": "edit_node",
                "node_id": finding.node_id,
                "updates": param_changes,
            }
        if finding.category == "reference_opportunity":
            edge_id = str(finding.details.get("edge_id", ""))
            if edge_id:
                return {
                    "op": "edit_edge",
                    "edge_id": edge_id,
                    "updates": {"pass_by_reference": True},
                }
        if finding.category == "loop_growth":
            return {
                "op": "edit_node",
                "node_id": finding.node_id,
                "updates": {
                    "compaction_rule": {
                        "strategy": "sliding_window",
                        "window_size": 3,
                        "require_persistent_recall": True,
                    },
                },
            }
        return None
