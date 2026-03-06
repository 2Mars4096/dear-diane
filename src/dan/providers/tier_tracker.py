"""Tier success telemetry — persist per-node tier stats and suggest de-escalation.

Part of Plan 18-5 task 4-2. Tracks consecutive successes/failures per node
and tier; after N successes at tier X, suggests cheaper tier X-1.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from dan.providers.model_policy import TaskTier

logger = logging.getLogger(__name__)

_TIER_ORDER: list[TaskTier] = [
    TaskTier.micro,
    TaskTier.routine,
    TaskTier.reasoning,
    TaskTier.critical,
]

_DEESCALATION_MAP: dict[str, str] = {
    TaskTier.critical.value: TaskTier.reasoning.value,
    TaskTier.reasoning.value: TaskTier.routine.value,
    TaskTier.routine.value: TaskTier.micro.value,
    TaskTier.micro.value: "",  # Never suggest below micro
}


@dataclass
class NodeTierStats:
    """Per-node tier success statistics."""

    node_id: str
    node_type: str
    current_tier: str
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    total_runs: int = 0
    total_successes: int = 0
    last_suggested_tier: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeTierStats:
        return cls(
            node_id=data.get("node_id", ""),
            node_type=data.get("node_type", ""),
            current_tier=data.get("current_tier", ""),
            consecutive_successes=int(data.get("consecutive_successes", 0)),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            total_runs=int(data.get("total_runs", 0)),
            total_successes=int(data.get("total_successes", 0)),
            last_suggested_tier=data.get("last_suggested_tier"),
        )


class TierSuccessTracker:
    """Persists per-node tier success stats and suggests cheaper tiers.

    Storage: {store_path}/tier_stats.json (atomic JSON write).
    """

    def __init__(self, store_path: str | Path) -> None:
        """Initialize tracker. store_path is the directory; file is {store_path}/tier_stats.json."""
        path = Path(store_path)
        self._file = path / "tier_stats.json"
        self._stats: dict[str, NodeTierStats] = {}
        self.load()

    def _get_or_create(self, node_id: str, node_type: str, tier: str) -> NodeTierStats:
        key = node_id
        if key not in self._stats:
            self._stats[key] = NodeTierStats(
                node_id=node_id,
                node_type=node_type,
                current_tier=tier,
            )
        return self._stats[key]

    def record_success(self, node_id: str, node_type: str, tier: str) -> None:
        """Increment consecutive_successes, reset consecutive_failures."""
        stats = self._get_or_create(node_id, node_type, tier)
        stats.consecutive_successes += 1
        stats.consecutive_failures = 0
        stats.total_runs += 1
        stats.total_successes += 1
        stats.current_tier = tier

    def record_failure(self, node_id: str, node_type: str, tier: str) -> None:
        """Increment consecutive_failures, reset consecutive_successes."""
        stats = self._get_or_create(node_id, node_type, tier)
        stats.consecutive_failures += 1
        stats.consecutive_successes = 0
        stats.total_runs += 1
        stats.current_tier = tier

    def suggest_deescalation(
        self,
        node_id: str,
        tier: str,
        threshold: int = 5,
    ) -> str | None:
        """If consecutive_successes >= threshold at current tier, suggest one tier lower.

        critical -> reasoning, reasoning -> routine, routine -> micro.
        Never suggest below micro. Returns suggested tier or None.
        """
        stats = self._get_or_create(node_id, "", tier)
        if stats.consecutive_successes < threshold:
            return None
        next_tier = _DEESCALATION_MAP.get(tier, "")
        if not next_tier:
            return None
        stats.last_suggested_tier = next_tier
        return next_tier

    def get_stats(self, node_id: str) -> NodeTierStats | None:
        """Return stats for a node."""
        return self._stats.get(node_id)

    def save(self) -> None:
        """Atomic JSON write."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._stats.items()}
        raw = json.dumps(data, indent=2, default=str)
        fd, tmp = tempfile.mkstemp(dir=str(self._file.parent), suffix=".tmp")
        closed = False
        try:
            os.write(fd, raw.encode("utf-8"))
            os.close(fd)
            closed = True
            os.replace(tmp, str(self._file))
        except BaseException:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def load(self) -> None:
        """Load from disk."""
        if not self._file.exists():
            self._stats = {}
            return
        try:
            raw = self._file.read_text("utf-8")
            data = json.loads(raw)
            self._stats = {}
            for k, v in data.items():
                if isinstance(v, dict):
                    self._stats[k] = NodeTierStats.from_dict(v)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load tier stats from %s: %s", self._file, exc)
            self._stats = {}
