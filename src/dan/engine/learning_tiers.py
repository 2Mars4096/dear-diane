"""Learning tiers — graduated activation and health monitoring.

Three tiers control which learning features are active:
- Tier 0 (baseline): memory, post-run learning, reuse scoring, preference evolution
- Tier 1 (advisory): + topology suggestions, model recommendations, prompt variant proposals
- Tier 2 (active): + A/B prompt promotion, skill refinement, auto-adaptation
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

LearningTier = Literal[0, 1, 2]
_SAFE_JSON_KEY_RE = re.compile(r"^[a-zA-Z0-9_]+$")

_TIER_FEATURES: dict[int, set[str]] = {
    0: {
        "memory", "post_run_learning", "reuse_scoring",
        "preference_evolution", "memory_extraction",
    },
    1: {
        "topology_suggestions", "model_recommendations",
        "prompt_variant_proposals",
    },
    2: {
        "ab_prompt_promotion", "skill_refinement",
        "auto_adaptation",
    },
}

_FEATURE_ENV_OVERRIDES: dict[str, str] = {
    "prompt_variant_proposals": "DAN_PROMPT_OPTIMIZATION",
    "ab_prompt_promotion": "DAN_PROMPT_OPTIMIZATION",
    "model_recommendations": "DAN_MODEL_LEARNING",
    "topology_suggestions": "DAN_TOPOLOGY_LEARNING",
    "skill_refinement": "DAN_SKILL_LEARNING",
    "memory_extraction": "DAN_MEMORY_EXTRACTION",
}


def resolve_learning_tier() -> int:
    """Resolve the active learning tier from environment.

    Reads ``DAN_LEARNING_TIER`` (default ``0``).
    Backward compat: ``DAN_LEARNING_MODE=1`` maps to tier ``1``.
    """
    explicit = os.environ.get("DAN_LEARNING_TIER")
    if explicit is not None:
        try:
            tier = int(explicit)
            return max(0, min(tier, 2))
        except ValueError:
            pass

    legacy = os.environ.get("DAN_LEARNING_MODE")
    if legacy == "1":
        return 1

    return 0


def is_feature_enabled(feature: str, tier: int | None = None) -> bool:
    """Check whether *feature* is enabled at the given tier.

    After tier-level resolution, individual env var overrides are checked:
    if the corresponding env var is explicitly ``"1"``, the feature is
    force-enabled regardless of tier; if ``"0"``, force-disabled.
    """
    env_key = _FEATURE_ENV_OVERRIDES.get(feature)
    if env_key:
        explicit = os.environ.get(env_key)
        if explicit == "1":
            return True
        if explicit == "0":
            return False

    if tier is None:
        tier = resolve_learning_tier()

    enabled: set[str] = set()
    for t in range(tier + 1):
        enabled |= _TIER_FEATURES.get(t, set())
    return feature in enabled


def features_enabled_at_tier(tier: int | None = None) -> set[str]:
    """Return the set of all features enabled at *tier*, including overrides."""
    if tier is None:
        tier = resolve_learning_tier()
    base: set[str] = set()
    for t in range(tier + 1):
        base |= _TIER_FEATURES.get(t, set())
    for feature, env_key in _FEATURE_ENV_OVERRIDES.items():
        explicit = os.environ.get(env_key)
        if explicit == "1":
            base.add(feature)
        elif explicit == "0":
            base.discard(feature)
    return base


def check_tier_promotion_gates(
    health_counters: "LearningHealthCounters",
    model_recommender_precision: float | None = None,
    model_recommender_samples: int = 0,
    false_positive_adaptations: int = 0,
) -> dict[str, bool | str]:
    """Check whether gates for promoting tier 0 → 1 are met.

    Returns a dict with each gate's pass/fail status plus a human-readable
    summary.  This is a manual check function — it does NOT perform
    automated promotion.

    Gates:
      (a) health counters >= 95% success over 100+ events
      (b) model recommender precision > 0.7 on 15+ outcomes
      (c) no false-positive adaptations in integration tests
    """
    summary = health_counters.get_summary()
    total_attempted = sum(c["attempted"] for c in summary.values())
    total_succeeded = sum(c["succeeded"] for c in summary.values())
    success_rate = total_succeeded / max(total_attempted, 1)

    gate_a = total_attempted >= 100 and success_rate >= 0.95
    gate_b = (
        model_recommender_precision is not None
        and model_recommender_samples >= 15
        and model_recommender_precision > 0.7
    )
    gate_c = false_positive_adaptations == 0

    all_pass = gate_a and gate_b and gate_c

    lines = [
        f"Gate A (health >= 95% on 100+ events): "
        f"{'PASS' if gate_a else 'FAIL'} "
        f"({success_rate:.1%} on {total_attempted} events)",
        f"Gate B (model precision > 0.7 on 15+ samples): "
        f"{'PASS' if gate_b else 'FAIL'} "
        f"(precision={model_recommender_precision}, samples={model_recommender_samples})",
        f"Gate C (no false-positive adaptations): "
        f"{'PASS' if gate_c else 'FAIL'} "
        f"({false_positive_adaptations} false positives)",
        f"Overall: {'READY for tier 1 promotion' if all_pass else 'NOT ready — fix failing gates'}",
    ]

    return {
        "gate_a_health": gate_a,
        "gate_b_precision": gate_b,
        "gate_c_no_false_positives": gate_c,
        "all_pass": all_pass,
        "summary": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# Health Counters
# ---------------------------------------------------------------------------

_LEARNING_PATHS = [
    "memory_extraction",
    "run_learning",
    "prompt_tracking",
    "model_tracking",
    "topology_tracking",
    "skill_tracking",
]

OutcomeKind = Literal["success", "skip", "fail"]


class LearningHealthCounters:
    """Track attempted/succeeded/skipped/failed for each learning path."""

    def __init__(self) -> None:
        self._counters: dict[str, dict[str, int]] = {
            path: {"attempted": 0, "succeeded": 0, "skipped": 0, "failed": 0}
            for path in _LEARNING_PATHS
        }
        self._first_failure_warned: set[str] = set()
        self._last_event_ts: float = 0.0

    def record(self, path: str, outcome: OutcomeKind) -> None:
        if path not in self._counters:
            self._counters[path] = {
                "attempted": 0, "succeeded": 0, "skipped": 0, "failed": 0,
            }
        self._counters[path]["attempted"] += 1
        self._last_event_ts = __import__("time").time()
        if outcome == "success":
            self._counters[path]["succeeded"] += 1
        elif outcome == "skip":
            self._counters[path]["skipped"] += 1
        elif outcome == "fail":
            self._counters[path]["failed"] += 1

    def record_failure(self, path: str, exc: Exception | None = None) -> None:
        """Record a failure with rate-limited warning on first occurrence.

        Replaces bare ``logger.debug(...)`` exception swallowing: emits
        ``logger.warning`` on first failure per path, ``logger.debug``
        on subsequent failures.
        """
        self.record(path, "fail")
        if path not in self._first_failure_warned:
            self._first_failure_warned.add(path)
            logger.warning(
                "Learning path '%s' first failure: %s",
                path,
                exc or "unknown error",
                exc_info=exc is not None,
            )
        else:
            logger.debug(
                "Learning path '%s' failure (repeated): %s",
                path,
                exc or "unknown error",
            )

    def get_summary(self) -> dict[str, dict[str, int]]:
        return {k: dict(v) for k, v in self._counters.items()}

    @property
    def last_event_timestamp(self) -> float:
        return self._last_event_ts

    def format_status(self) -> str:
        """Human-readable status string for ``/status``."""
        lines: list[str] = []
        tier = resolve_learning_tier()
        lines.append(f"Learning tier: {tier}")
        for path, counts in self._counters.items():
            att = counts["attempted"]
            if att == 0:
                lines.append(f"  {path}: no events")
                continue
            rate = counts["succeeded"] / att if att > 0 else 0.0
            lines.append(
                f"  {path}: {att} attempted, "
                f"{counts['succeeded']} ok, "
                f"{counts['skipped']} skipped, "
                f"{counts['failed']} failed "
                f"({rate:.0%} success)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Memory Backend Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryBackend(Protocol):
    """Pluggable storage backend for memory items."""

    def load(self) -> dict[str, Any]: ...
    def save(self, index: dict[str, Any]) -> None: ...
    def upsert(self, item_id: str, data: dict[str, Any]) -> None: ...
    def delete(self, item_id: str) -> None: ...
    def query(self, **filters: Any) -> list[dict[str, Any]]: ...


class JsonFileBackend:
    """JSON file backend — current default behavior."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, index: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(index, default=str, indent=2),
            encoding="utf-8",
        )

    def upsert(self, item_id: str, data: dict[str, Any]) -> None:
        index = self.load()
        index[item_id] = data
        self.save(index)

    def delete(self, item_id: str) -> None:
        index = self.load()
        index.pop(item_id, None)
        self.save(index)

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        index = self.load()
        results = list(index.values())
        for key, value in filters.items():
            results = [r for r in results if r.get(key) == value]
        return results


class SqliteBackend:
    """SQLite backend — optional, for indexed queries."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn: sqlite3.Connection | None = None

    def _ensure_table(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._path)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_items ("
                "  id TEXT PRIMARY KEY,"
                "  data TEXT NOT NULL,"
                "  memory_type TEXT,"
                "  importance REAL DEFAULT 0.5,"
                "  created_at TEXT"
                ")"
            )
            self._conn.commit()
        return self._conn

    def load(self) -> dict[str, Any]:
        conn = self._ensure_table()
        rows = conn.execute("SELECT id, data FROM memory_items").fetchall()
        result: dict[str, Any] = {}
        for row_id, data_str in rows:
            try:
                result[row_id] = json.loads(data_str)
            except json.JSONDecodeError:
                pass
        return result

    def save(self, index: dict[str, Any]) -> None:
        conn = self._ensure_table()
        conn.execute("DELETE FROM memory_items")
        for item_id, data in index.items():
            memory_type = data.get("memory_type", "")
            importance = data.get("importance", 0.5)
            created_at = data.get("created_at", "")
            conn.execute(
                "INSERT OR REPLACE INTO memory_items (id, data, memory_type, importance, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (item_id, json.dumps(data, default=str), memory_type, importance, created_at),
            )
        conn.commit()

    def upsert(self, item_id: str, data: dict[str, Any]) -> None:
        conn = self._ensure_table()
        memory_type = data.get("memory_type", "")
        importance = data.get("importance", 0.5)
        created_at = data.get("created_at", "")
        conn.execute(
            "INSERT OR REPLACE INTO memory_items (id, data, memory_type, importance, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (item_id, json.dumps(data, default=str), memory_type, importance, created_at),
        )
        conn.commit()

    def delete(self, item_id: str) -> None:
        conn = self._ensure_table()
        conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))
        conn.commit()

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        conn = self._ensure_table()
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in filters.items():
            if key == "memory_type":
                clauses.append("memory_type = ?")
                params.append(value)
            elif key == "importance":
                clauses.append("importance >= ?")
                params.append(value)
            else:
                if not _SAFE_JSON_KEY_RE.fullmatch(key):
                    raise ValueError(f"Invalid filter key: {key}")
                clauses.append(f"json_extract(data, '$.{key}') = ?")
                params.append(json.dumps(value) if not isinstance(value, str) else value)

        where = " AND ".join(clauses) if clauses else "1=1"
        rows = conn.execute(
            f"SELECT data FROM memory_items WHERE {where}", params,
        ).fetchall()
        results: list[dict[str, Any]] = []
        for (data_str,) in rows:
            try:
                results.append(json.loads(data_str))
            except json.JSONDecodeError:
                pass
        return results

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


def resolve_memory_backend(base_path: Path | None = None) -> MemoryBackend:
    """Create a memory backend based on ``DAN_MEMORY_BACKEND`` env var."""
    backend_type = os.environ.get("DAN_MEMORY_BACKEND", "json").lower()
    if base_path is None:
        base_path = Path.home() / ".dan"

    if backend_type == "sqlite":
        return SqliteBackend(base_path / "memory.db")
    return JsonFileBackend(base_path / "memory_index.json")
