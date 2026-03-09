"""Generation feedback loop — tracks codegen/intent-compiler outcomes.

Records per-pattern and per-method success rates, common failure modes, and
feeds stats back into reuse scoring and prompt context.  Persisted as a single
``WORKING_STATE`` item in the memory kernel to keep the hot path lightweight.

Part of 29-6 §5 (Generation feedback loop).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

GENERATION_STATS_TAG = "generation_stats"
_STATS_CONTENT_KEY = "generation_stats_v1"


class GenerationOutcome(BaseModel):
    """Single recorded generation attempt."""

    method: str  # "intent_compiler" | "codegen" | "diagnosis"
    pattern: str = ""  # workflow pattern name if known
    success: bool = True
    error_type: str = ""
    fix_needed: bool = False
    timestamp: float = Field(default_factory=time.time)


class GenerationStats(BaseModel):
    """Aggregate stats over all recorded generation outcomes.

    Stored as JSON inside a single ``WORKING_STATE`` memory item so that the
    memory kernel can load it cheaply without a separate persistence layer.
    """

    outcomes: list[GenerationOutcome] = Field(default_factory=list)
    max_outcomes: int = 500

    # --- Recording ---------------------------------------------------------

    def record(
        self,
        method: str,
        pattern: str = "",
        success: bool = True,
        error_type: str = "",
        fix_needed: bool = False,
    ) -> None:
        self.outcomes.append(GenerationOutcome(
            method=method,
            pattern=pattern,
            success=success,
            error_type=error_type,
            fix_needed=fix_needed,
        ))
        if len(self.outcomes) > self.max_outcomes:
            self.outcomes = self.outcomes[-self.max_outcomes:]

    # --- Queries -----------------------------------------------------------

    def get_pattern_success_rate(self, pattern: str) -> float:
        """Return success rate for a specific pattern (0.0–1.0)."""
        relevant = [o for o in self.outcomes if o.pattern == pattern]
        if not relevant:
            return 0.5  # neutral prior when no data
        return sum(1 for o in relevant if o.success) / len(relevant)

    def get_method_success_rate(self, method: str) -> float:
        relevant = [o for o in self.outcomes if o.method == method]
        if not relevant:
            return 0.5
        return sum(1 for o in relevant if o.success) / len(relevant)

    def get_common_failures(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return the most frequent (error_type, method) failure pairs."""
        counts: dict[tuple[str, str], int] = {}
        for o in self.outcomes:
            if not o.success and o.error_type:
                key = (o.error_type, o.method)
                counts[key] = counts.get(key, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        return [
            {"error_type": et, "method": m, "count": c}
            for (et, m), c in ranked[:limit]
        ]

    # --- Prompt injection --------------------------------------------------

    def format_for_prompt(self) -> str:
        """Build a short string suitable for injection into codegen prompts."""
        failures = self.get_common_failures(limit=5)
        if not failures:
            return ""
        lines = ["Common generation mistakes to avoid:"]
        for f in failures:
            lines.append(f"- {f['error_type']} (via {f['method']}, seen {f['count']}x)")
        return "\n".join(lines)

    # --- Reuse score adjustment --------------------------------------------

    def adjust_reuse_score(self, pattern: str, base_score: float) -> float:
        """Patterns that fail during generation are more valuable to reuse.

        If a pattern generates successfully most of the time, generation is
        cheap and reuse adds less value → slight decrease.  If generation
        often fails, reuse avoids that cost → boost the score.
        """
        rate = self.get_pattern_success_rate(pattern)
        if rate >= 0.8:
            return max(0.0, base_score - 0.05)
        if rate <= 0.3:
            return min(1.0, base_score + 0.15)
        return base_score


# ---------------------------------------------------------------------------
# Persistence helpers — load/save from memory kernel
# ---------------------------------------------------------------------------

def load_generation_stats(memory_kernel: Any) -> GenerationStats:
    """Load GenerationStats from the memory kernel's WORKING_STATE store."""
    from dan.engine.memory_kernel import MemoryLifecycle, MemoryType

    for item in memory_kernel.list_by_type(MemoryType.WORKING_STATE, limit=100):
        if item.lifecycle == MemoryLifecycle.ARCHIVE:
            continue
        if GENERATION_STATS_TAG in item.tags:
            try:
                data = json.loads(item.content)
                return GenerationStats.model_validate(data)
            except Exception:
                logger.debug("Corrupt generation stats item %s, starting fresh", item.id)
    return GenerationStats()


def save_generation_stats(memory_kernel: Any, stats: GenerationStats) -> None:
    """Persist GenerationStats back into the memory kernel."""
    from dan.engine.memory_kernel import MemoryItem, MemoryLifecycle, MemoryScope, MemoryType

    content = stats.model_dump_json()
    for item in memory_kernel.list_by_type(MemoryType.WORKING_STATE, limit=100):
        if item.lifecycle == MemoryLifecycle.ARCHIVE:
            continue
        if GENERATION_STATS_TAG in item.tags:
            item.content = content
            memory_kernel.store(item)
            return

    memory_kernel.store(MemoryItem(
        content=content,
        memory_type=MemoryType.WORKING_STATE,
        scope=MemoryScope.USER,
        tags=[GENERATION_STATS_TAG],
        metadata={"kind": _STATS_CONTENT_KEY},
    ))


def record_generation_outcome(
    memory_kernel: Any,
    method: str,
    pattern: str = "",
    success: bool = True,
    error_type: str = "",
    fix_needed: bool = False,
) -> None:
    """One-call convenience: load stats, record, save.  Fire-and-forget safe."""
    try:
        stats = load_generation_stats(memory_kernel)
        stats.record(
            method=method,
            pattern=pattern,
            success=success,
            error_type=error_type,
            fix_needed=fix_needed,
        )
        save_generation_stats(memory_kernel, stats)
    except Exception:
        logger.debug("Failed to record generation outcome", exc_info=True)
