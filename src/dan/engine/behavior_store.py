"""Self-adaptive behavior infrastructure — externalized behavioral parameters.

Manages ~/.dan/behavior/ for DAN's own behavioral parameters (prompts,
thresholds, taxonomy, domains, models) following the seed→override→learn→revert
pattern.  Part of plan 31-22 (Self-Adaptive Behavior).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_CATEGORIES = ("prompts", "heuristics", "taxonomy", "domains", "models", "retrieval_policy")
_MAX_PREVIOUS_VERSIONS = 10
_MAX_SUMMARY_CHARS = 200
_MAX_STEP_PCT = 0.20


# ---------------------------------------------------------------------------
# BehaviorArtifact
# ---------------------------------------------------------------------------


class BehaviorArtifact(BaseModel):
    key: str
    value: Any
    version: int = 1
    evidence: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)
    previous_versions: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# BehaviorStore
# ---------------------------------------------------------------------------


class BehaviorStore:
    """Manages ~/.dan/behavior/ with stat-based hot-reload and per-category locking."""

    def __init__(
        self,
        base_path: Path | None = None,
        seed_defaults: dict[str, Any] | None = None,
    ) -> None:
        self._base = base_path or (Path.home() / ".dan" / "behavior")
        self._seeds = seed_defaults or {}
        self._locks: dict[str, threading.RLock] = {cat: threading.RLock() for cat in _CATEGORIES}
        self._global_lock = threading.RLock()
        # {key: (mtime, BehaviorArtifact)}
        self._cache: dict[str, tuple[float, BehaviorArtifact]] = {}

    def _lock_for(self, category: str) -> threading.RLock:
        lock = self._locks.get(category)
        if lock is not None:
            return lock
        with self._global_lock:
            if category not in self._locks:
                self._locks[category] = threading.RLock()
            return self._locks[category]

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        parts = key.split("/", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Key must be 'category/name', got: {key!r}")
        category = parts[0]
        if category not in _CATEGORIES:
            logger.warning(
                "Key category %r not in known categories %s — proceeding anyway",
                category, _CATEGORIES,
            )
        return category, parts[1]

    def register_seed(self, key: str, value: Any) -> None:
        """Register a seed default for a key (public API)."""
        self._seeds[key] = value

    def get_seed(self, key: str) -> Any:
        """Return the seed default for a key, or None."""
        return self._seeds.get(key)

    def _file_path(self, category: str, name: str) -> Path:
        return self._base / category / f"{name}.json"

    def _read_artifact(self, path: Path) -> BehaviorArtifact | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BehaviorArtifact.model_validate(data)
        except Exception:
            logger.debug("Failed to read artifact at %s", path, exc_info=True)
            return None

    def _write_artifact(self, path: Path, artifact: BehaviorArtifact) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(path)

    def _cached_or_load(self, key: str, category: str, name: str) -> BehaviorArtifact | None:
        path = self._file_path(category, name)
        if not path.exists():
            self._cache.pop(key, None)
            return None

        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None

        cached = self._cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]

        artifact = self._read_artifact(path)
        if artifact is not None:
            self._cache[key] = (mtime, artifact)
        return artifact

    # -- public API ---------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        category, name = self._split_key(key)
        with self._lock_for(category):
            artifact = self._cached_or_load(key, category, name)
            if artifact is not None:
                return artifact.value
        return self._seeds.get(key, default)

    def get_artifact(self, key: str) -> BehaviorArtifact | None:
        category, name = self._split_key(key)
        with self._lock_for(category):
            return self._cached_or_load(key, category, name)

    def set(
        self,
        key: str,
        value: Any,
        reason: str = "",
        evidence: list[str] | None = None,
    ) -> BehaviorArtifact:
        category, name = self._split_key(key)
        with self._lock_for(category):
            path = self._file_path(category, name)
            existing = self._read_artifact(path)

            prev_versions: list[dict] = []
            new_version = 1
            if existing is not None:
                snapshot = {
                    "value": existing.value,
                    "version": existing.version,
                    "updated_at": existing.updated_at,
                }
                prev_versions = existing.previous_versions[-(_MAX_PREVIOUS_VERSIONS - 1) :] + [snapshot]
                new_version = existing.version + 1

            artifact = BehaviorArtifact(
                key=key,
                value=value,
                version=new_version,
                evidence=evidence or ([reason] if reason else []),
                updated_at=time.time(),
                previous_versions=prev_versions,
            )

            self._write_artifact(path, artifact)
            self._cache[key] = (path.stat().st_mtime, artifact)
            logger.debug("BehaviorStore set %s → v%d", key, new_version)
            return artifact

    def revert(self, key: str, version: int | None = None) -> BehaviorArtifact:
        category, name = self._split_key(key)
        with self._lock_for(category):
            path = self._file_path(category, name)
            existing = self._read_artifact(path)
            if existing is None or not existing.previous_versions:
                raise KeyError(f"No previous version for {key!r}")

            if version is not None:
                target = None
                target_idx = -1
                for i, pv in enumerate(existing.previous_versions):
                    if pv.get("version") == version:
                        target = pv
                        target_idx = i
                        break
                if target is None:
                    raise KeyError(f"Version {version} not found for {key!r}")
                remaining = existing.previous_versions[:target_idx] + existing.previous_versions[target_idx + 1 :]
            else:
                target = existing.previous_versions[-1]
                remaining = existing.previous_versions[:-1]

            snapshot = {
                "value": existing.value,
                "version": existing.version,
                "updated_at": existing.updated_at,
            }
            remaining = remaining[-(_MAX_PREVIOUS_VERSIONS - 1) :] + [snapshot]

            artifact = BehaviorArtifact(
                key=key,
                value=target["value"],
                version=existing.version + 1,
                evidence=[f"reverted to v{target.get('version', '?')}"],
                updated_at=time.time(),
                previous_versions=remaining,
            )

            self._write_artifact(path, artifact)
            self._cache[key] = (path.stat().st_mtime, artifact)
            logger.debug("BehaviorStore reverted %s → v%d", key, artifact.version)
            return artifact

    def list_keys(self, category: str | None = None) -> list[str]:
        keys: list[str] = []
        cats = [category] if category else list(_CATEGORIES)
        for cat in cats:
            cat_dir = self._base / cat
            if not cat_dir.is_dir():
                continue
            for p in sorted(cat_dir.rglob("*.json")):
                name = p.relative_to(cat_dir).with_suffix("").as_posix()
                keys.append(f"{cat}/{name}")
        return keys

    def list_changes(self, since_hours: float = 24.0) -> list[BehaviorArtifact]:
        cutoff = time.time() - since_hours * 3600
        result: list[BehaviorArtifact] = []
        for key in self.list_keys():
            category, name = self._split_key(key)
            with self._lock_for(category):
                artifact = self._cached_or_load(key, category, name)
                if artifact is not None and artifact.updated_at >= cutoff:
                    result.append(artifact)
        result.sort(key=lambda a: a.updated_at, reverse=True)
        return result

    def reload(self, key: str) -> None:
        category, name = self._split_key(key)
        with self._lock_for(category):
            self._cache.pop(key, None)
            self._cached_or_load(key, category, name)

    def reload_all(self) -> None:
        for key in list(self._cache.keys()):
            try:
                self.reload(key)
            except ValueError:
                self._cache.pop(key, None)


# ---------------------------------------------------------------------------
# AdaptableParameter
# ---------------------------------------------------------------------------


class AdaptableParameter(BaseModel):
    key: str
    category: Literal["thresholds", "prompts", "taxonomy", "domains", "models", "retrieval_policy"]
    evidence_type: Literal[
        "parameter_decision",
        "correction_attribution",
        "pattern_accumulation",
        "model_outcome",
        "retrieval_correlation",
    ]
    min_evidence_count: int = 10
    risk_level: Literal["low", "medium", "high"] = "low"
    bounds: dict | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# AdaptableParameterRegistry
# ---------------------------------------------------------------------------


class AdaptableParameterRegistry:
    """In-memory registry mapping parameter keys to their adaptation metadata."""

    def __init__(self) -> None:
        self._params: dict[str, AdaptableParameter] = {}

    def register(self, param: AdaptableParameter) -> None:
        self._params[param.key] = param

    def get_by_key(self, key: str) -> AdaptableParameter | None:
        return self._params.get(key)

    def list_by_category(self, category: str) -> list[AdaptableParameter]:
        return [p for p in self._params.values() if p.category == category]

    def list_by_risk(self, risk: str) -> list[AdaptableParameter]:
        return [p for p in self._params.values() if p.risk_level == risk]

    def has_sufficient_evidence(self, key: str, evidence_count: int) -> bool:
        param = self._params.get(key)
        if param is None:
            return False
        return evidence_count >= param.min_evidence_count


# ---------------------------------------------------------------------------
# BehaviorChangeLog
# ---------------------------------------------------------------------------


class BehaviorChangeEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: float = Field(default_factory=time.time)
    category: str
    key: str
    action: str
    before_summary: str = ""
    after_summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    source: str = ""


class BehaviorChangeLog:
    """Append-only JSONL log at ~/.dan/behavior/changelog.jsonl."""

    def __init__(self, base_path: Path | None = None) -> None:
        base = base_path or (Path.home() / ".dan" / "behavior")
        self._path = base / "changelog.jsonl"
        self._lock = threading.RLock()
        self._cache: list[BehaviorChangeEntry] | None = None
        self._cache_mtime: float = 0.0

    def append(self, entry: BehaviorChangeEntry) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry.model_dump(mode="json"), default=str) + "\n"
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)
            self._cache = None

    def _read_all(self) -> list[BehaviorChangeEntry]:
        if not self._path.exists():
            return []
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return []
        if self._cache is not None and self._cache_mtime == mtime:
            return list(self._cache)
        entries: list[BehaviorChangeEntry] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(BehaviorChangeEntry.model_validate(json.loads(line)))
                except Exception:
                    logger.debug("Skipping corrupt changelog line", exc_info=True)
        except OSError:
            logger.debug("Failed to read changelog", exc_info=True)
        self._cache = entries
        self._cache_mtime = mtime
        return list(entries)

    def list_recent(self, hours: float = 24.0) -> list[BehaviorChangeEntry]:
        cutoff = time.time() - hours * 3600
        return [e for e in self._read_all() if e.timestamp >= cutoff]

    def list_all(self, category: str | None = None) -> list[BehaviorChangeEntry]:
        entries = self._read_all()
        if category is not None:
            entries = [e for e in entries if e.category == category]
        return entries

    def get(self, entry_id: str) -> BehaviorChangeEntry | None:
        for entry in self._read_all():
            if entry.id == entry_id:
                return entry
        return None


# ---------------------------------------------------------------------------
# ParameterDecisionLogger
# ---------------------------------------------------------------------------


class ParameterDecisionLogger:
    """Wraps telemetry event emission with parameter context."""

    def __init__(self, telemetry_store: Any = None) -> None:
        self._store = telemetry_store

    def log_decision(
        self,
        parameter_key: str,
        parameter_value: Any,
        decision: str,
        outcome: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        event = {
            "type": "parameter_decision",
            "parameter_key": parameter_key,
            "parameter_value": parameter_value,
            "decision": decision,
            "outcome": outcome,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }
        if self._store is not None:
            try:
                if hasattr(self._store, "record"):
                    self._store.record(event)
                elif hasattr(self._store, "append"):
                    self._store.append(event)
            except Exception:
                logger.debug("Telemetry store write failed", exc_info=True)
        else:
            logger.debug("parameter_decision: key=%s decision=%s", parameter_key, decision)


# ---------------------------------------------------------------------------
# PatternAccumulator
# ---------------------------------------------------------------------------


class PatternAccumulator:
    """Maintains counters for unrecognized patterns, clustering by keyword overlap.

    Uses an in-memory buffer to avoid writing a new BehaviorStore version on
    every call.  Call ``flush()`` periodically (e.g. after every N messages)
    to persist accumulated patterns.
    """

    _TAXONOMY_KEY = "taxonomy/unrecognized_patterns"
    _DOMAIN_KEY = "domains/unrecognized_clusters"
    _MAX_EXAMPLES = 5
    _FLUSH_THRESHOLD = 10

    def __init__(self, store: BehaviorStore) -> None:
        self._store = store
        self._pending: dict[str, list[dict]] = {}
        self._pending_count = 0

    _VALID_CATEGORIES = frozenset({"taxonomy", "domain"})

    def record_unrecognized(self, text: str, keywords: list[str], category: str) -> None:
        if category not in self._VALID_CATEGORIES:
            logger.warning(
                "PatternAccumulator: unexpected category %r (expected one of %s), routing to domain bucket",
                category,
                self._VALID_CATEGORIES,
            )
        store_key = self._TAXONOMY_KEY if category == "taxonomy" else self._DOMAIN_KEY

        if store_key not in self._pending:
            self._pending[store_key] = self._store.get(store_key, []) or []

        clusters = self._pending[store_key]
        kw_set = set(keywords)
        matched = False

        for cluster in clusters:
            existing_kw = set(cluster.get("keywords", []))
            if self._keyword_overlap(kw_set, existing_kw) >= 0.5:
                cluster["count"] = cluster.get("count", 0) + 1
                cluster["keywords"] = list(existing_kw | kw_set)
                examples: list[str] = cluster.get("examples", [])
                if len(examples) < self._MAX_EXAMPLES:
                    examples.append(text[:200])
                cluster["examples"] = examples
                matched = True
                break

        if not matched:
            clusters.append({
                "keywords": keywords,
                "count": 1,
                "examples": [text[:200]],
                "category": category,
            })

        self._pending_count += 1
        if self._pending_count >= self._FLUSH_THRESHOLD:
            self.flush()

    def flush(self) -> None:
        """Persist buffered patterns to BehaviorStore."""
        for store_key, clusters in self._pending.items():
            self._store.set(store_key, clusters, reason="pattern batch flush")
        self._pending.clear()
        self._pending_count = 0

    def get_clusters(self, category: str, min_count: int = 3) -> list[dict]:
        store_key = self._TAXONOMY_KEY if category == "taxonomy" else self._DOMAIN_KEY
        if store_key in self._pending:
            clusters = self._pending[store_key]
        else:
            clusters = self._store.get(store_key, []) or []
        return [c for c in clusters if c.get("count", 0) >= min_count]

    @staticmethod
    def _keyword_overlap(set_a: set[str], set_b: set[str]) -> float:
        if not set_a and not set_b:
            return 1.0
        union = set_a | set_b
        if not union:
            return 0.0
        return len(set_a & set_b) / len(union)


# ---------------------------------------------------------------------------
# ThresholdCalibrator
# ---------------------------------------------------------------------------


class ThresholdCalibrator:
    """Consumes parameter_decision telemetry to propose threshold adjustments."""

    def __init__(
        self,
        store: BehaviorStore,
        param_registry: AdaptableParameterRegistry,
        changelog: BehaviorChangeLog,
    ) -> None:
        self._store = store
        self._registry = param_registry
        self._changelog = changelog

    def analyze(self, parameter_key: str, decisions: list[dict]) -> dict | None:
        param = self._registry.get_by_key(parameter_key)
        if param is None:
            return None
        if not self._registry.has_sufficient_evidence(parameter_key, len(decisions)):
            return None

        current = self._store.get(parameter_key)
        if current is None or not isinstance(current, (int, float)):
            return None

        positive = sum(1 for d in decisions if d.get("outcome") in ("success", "correct", "accepted"))
        total = len(decisions)
        if total == 0:
            return None

        success_rate = positive / total

        if success_rate >= 0.9:
            return None

        if success_rate < 0.5:
            direction = -1
            magnitude = min(0.1, (0.5 - success_rate) * 0.5)
        else:
            direction = 1
            magnitude = min(0.05, (0.9 - success_rate) * 0.2)

        proposed = current * (1 + direction * magnitude)
        proposed = self._apply_bounded_change(parameter_key, float(current), proposed)

        return {
            "current": current,
            "proposed": proposed,
            "evidence_count": total,
            "success_rate": round(success_rate, 3),
            "reason": f"success_rate={success_rate:.1%} over {total} decisions",
        }

    def propose(
        self,
        parameter_key: str,
        decisions: list[dict],
        tier: int = 0,
    ) -> dict | None:
        analysis = self.analyze(parameter_key, decisions)
        if analysis is None:
            return None

        if tier == 0:
            logger.debug(
                "ThresholdCalibrator (tier 0) would propose %s: %s → %s (%s)",
                parameter_key,
                analysis["current"],
                analysis["proposed"],
                analysis["reason"],
            )
            return None

        if tier >= 2:
            before = analysis["current"]
            after = analysis["proposed"]
            self._store.set(
                parameter_key,
                after,
                reason=analysis["reason"],
                evidence=[f"auto-calibrated from {before} to {after}"],
            )
            category = parameter_key.split("/", 1)[0] if "/" in parameter_key else "heuristics"
            self._changelog.append(BehaviorChangeEntry(
                category=category,
                key=parameter_key,
                action="calibrate",
                before_summary=str(before)[:_MAX_SUMMARY_CHARS],
                after_summary=str(after)[:_MAX_SUMMARY_CHARS],
                evidence=[analysis["reason"]],
                source="threshold_cal",
            ))

        return analysis

    def _apply_bounded_change(self, key: str, current: float, proposed: float) -> float:
        max_delta = abs(current) * _MAX_STEP_PCT
        if max_delta == 0:
            max_delta = _MAX_STEP_PCT

        delta = proposed - current
        if abs(delta) > max_delta:
            delta = max_delta if delta > 0 else -max_delta
        result = current + delta

        param = self._registry.get_by_key(key)
        if param is not None and param.bounds:
            lo = param.bounds.get("min")
            hi = param.bounds.get("max")
            if lo is not None:
                result = max(result, float(lo))
            if hi is not None:
                result = min(result, float(hi))

        return round(result, 6)
