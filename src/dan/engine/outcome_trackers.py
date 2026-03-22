"""Outcome trackers for per-node execution telemetry (29-6 §7, §8, §10).

Five tracker/optimizer families:
  - PromptTracker: records prompt/outcome pairs for future optimization
  - PromptAnalyzer + PromptVariantGenerator + PromptABTest: analyzes
    prompt/outcome data, generates variants, A/B tests them, promotes winners
  - ModelOutcomeTracker + ModelRecommender: learns which models work best per node
  - TopologyOutcomeTracker + TopologyAdvisor: learns structural patterns that
    correlate with success

Node-level quality signals (31-15 §2):
  - NodeOutcome: per-node success/failure with retry count, schema validity,
    and computed quality score
  - record_node_outcome(): records a node outcome and returns the quality score
  - compute_workflow_quality(): partial-success gradient for multi-node workflows

All trackers are opt-in via environment variables (default off) and store
state in the unified memory kernel.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from collections import Counter, defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment gates
# ---------------------------------------------------------------------------

_ENV_PROMPT_OPT = "DAN_PROMPT_OPTIMIZATION"
_ENV_MODEL_LEARNING = "DAN_MODEL_LEARNING"
_ENV_TOPOLOGY_LEARNING = "DAN_TOPOLOGY_LEARNING"


def _is_enabled(env_var: str) -> bool:
    return os.environ.get(env_var, "0") == "1"


def _is_prompt_tracking_enabled(scope: Literal["workflow", "system"] = "workflow") -> bool:
    explicit = os.environ.get(_ENV_PROMPT_OPT)
    if explicit == "1":
        return True
    if explicit == "0":
        return False
    try:
        from dan.engine.learning_tiers import is_feature_enabled
    except Exception:
        return False
    if scope == "system":
        return (
            is_feature_enabled("prompt_effectiveness_logging")
            or is_feature_enabled("prompt_variant_proposal")
            or is_feature_enabled("prompt_rewrite")
        )
    return _is_enabled(_ENV_PROMPT_OPT)


# ---------------------------------------------------------------------------
# Tier 0 evidence features (31-22 §4-7)
# Documents which learning_tiers features are tier-0 (passive evidence
# collection).  These are already registered in _TIER_FEATURES[0] in
# learning_tiers.py — this constant exists for cross-module documentation.
# ---------------------------------------------------------------------------

TIER_0_EVIDENCE_FEATURES = frozenset({
    "parameter_outcome_tracking",
    "prompt_effectiveness_logging",
    "pattern_accumulation",
    "retrieval_correlation_tracking",
})


# ===================================================================
# 31-15 §2: Node-level outcome tracking and quality signals
# ===================================================================


class NodeOutcome(BaseModel):
    """Per-node execution outcome with quality signals."""

    node_id: str
    node_type: str
    success: bool
    retry_count: int = 0
    schema_valid_first_try: bool = True
    quality_score: float = 1.0
    recorded_at: float = Field(default_factory=time.time)


def compute_retry_quality(retry_count: int) -> float:
    """Quality score based on retry count: ``1.0 / (1.0 + retries * 0.3)``, clamped to [0.1, 1.0]."""
    return max(0.1, min(1.0, 1.0 / (1.0 + retry_count * 0.3)))


def compute_node_quality(
    success: bool,
    retry_count: int = 0,
    schema_valid_first_try: bool = True,
) -> float:
    """Compute composite quality score for a single node execution."""
    if not success:
        return 0.0
    score = compute_retry_quality(retry_count)
    if not schema_valid_first_try:
        score *= 0.8
    return max(0.1, min(1.0, score))


def compute_workflow_quality(node_outcomes: list[NodeOutcome]) -> float:
    """Partial-success gradient: fraction of successful nodes weighted by quality."""
    if not node_outcomes:
        return 0.0
    total = len(node_outcomes)
    quality_sum = sum(o.quality_score for o in node_outcomes if o.success)
    return quality_sum / total


def record_node_outcome(
    node_id: str,
    node_type: str,
    success: bool,
    retry_count: int = 0,
    schema_valid_first_try: bool = True,
) -> NodeOutcome:
    """Create a ``NodeOutcome`` with computed quality score."""
    quality = compute_node_quality(success, retry_count, schema_valid_first_try)
    return NodeOutcome(
        node_id=node_id,
        node_type=node_type,
        success=success,
        retry_count=retry_count,
        schema_valid_first_try=schema_valid_first_try,
        quality_score=quality,
    )


# ===================================================================
# 7-1 / 7-8: PromptTracker
# ===================================================================

class PromptTracker:
    """Record (node_id, prompt_hash, input_summary, output_summary, outcome,
    tokens, latency) per LLM execution.

    Supports two scopes:
      - ``"workflow"`` (default): tracks workflow node prompts, keyed by node_id.
      - ``"system"``: tracks DAN's own system prompts, keyed by BehaviorStore
        prompt key (e.g. ``prompts/classifier.classification_system``).

    Gated by ``DAN_PROMPT_OPTIMIZATION=1``.
    """

    TAG_PREFIX = "prompt_tracker"

    def __init__(self, memory_kernel: Any) -> None:
        self.memory_kernel = memory_kernel

    @staticmethod
    def prompt_hash(prompt_text: str) -> str:
        return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]

    def record(
        self,
        node_id: str,
        prompt_hash: str,
        input_summary: str,
        output_summary: str,
        outcome: bool,
        tokens_used: int,
        latency_ms: float,
        quality_score: float | None = None,
        retry_count: int = 0,
        schema_valid_first_try: bool = True,
        scope: Literal["workflow", "system"] = "workflow",
    ) -> MemoryItem | None:
        if not _is_prompt_tracking_enabled(scope):
            return None

        if quality_score is None:
            quality_score = compute_node_quality(outcome, retry_count, schema_valid_first_try)

        if scope == "system":
            content = (
                f"System prompt outcome for {node_id}: "
                f"outcome={'success' if outcome else 'failure'}, "
                f"quality={quality_score:.2f}"
            )
            tags = [f"{self.TAG_PREFIX}:system", f"prompt_key:{node_id}"]
        else:
            content = (
                f"Prompt execution for node {node_id}: "
                f"outcome={'success' if outcome else 'failure'}, "
                f"quality={quality_score:.2f}, "
                f"tokens={tokens_used}, latency={latency_ms:.0f}ms"
            )
            tags = [self.TAG_PREFIX, f"node:{node_id}"]

        return self.memory_kernel.store(MemoryItem(
            content=content,
            memory_type=MemoryType.EPISODE,
            scope=MemoryScope.WORKFLOW,
            lifecycle=MemoryLifecycle.ACTIVE,
            tags=tags,
            metadata={
                "tracker": self.TAG_PREFIX,
                "node_id": node_id,
                "prompt_hash": prompt_hash,
                "input_summary": input_summary[:500],
                "output_summary": output_summary[:500],
                "outcome": outcome,
                "quality_score": quality_score,
                "retry_count": retry_count,
                "schema_valid_first_try": schema_valid_first_try,
                "tokens_used": tokens_used,
                "latency_ms": latency_ms,
                "scope": scope,
                "recorded_at": time.time(),
            },
        ))

    def get_history(self, node_id: str, limit: int = 20) -> list[dict[str, Any]]:
        if not _is_prompt_tracking_enabled("workflow"):
            return []

        items = self.memory_kernel.list_by_type(
            MemoryType.EPISODE, scope=MemoryScope.WORKFLOW, limit=500,
        )
        results: list[dict[str, Any]] = []
        for item in items:
            if (
                item.metadata.get("tracker") == self.TAG_PREFIX
                and item.metadata.get("node_id") == node_id
            ):
                results.append(item.metadata)
                if len(results) >= limit:
                    break
        return results

    def record_system_prompt_outcome(
        self,
        prompt_key: str,
        outcome: bool,
        quality_score: float = 1.0,
    ) -> MemoryItem | None:
        """Record outcome for a system prompt (not workflow node)."""
        if not _is_prompt_tracking_enabled("system"):
            return None

        content = (
            f"System prompt outcome for {prompt_key}: "
            f"outcome={'success' if outcome else 'failure'}, "
            f"quality={quality_score:.2f}"
        )
        return self.memory_kernel.store(MemoryItem(
            content=content,
            memory_type=MemoryType.EPISODE,
            scope=MemoryScope.WORKFLOW,
            lifecycle=MemoryLifecycle.ACTIVE,
            tags=[f"{self.TAG_PREFIX}:system", f"prompt_key:{prompt_key}"],
            metadata={
                "tracker": self.TAG_PREFIX,
                "prompt_key": prompt_key,
                "outcome": outcome,
                "quality_score": quality_score,
                "scope": "system",
                "recorded_at": time.time(),
            },
        ))

    def get_system_prompt_history(
        self,
        prompt_key: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve system-scope prompt tracking records."""
        if not _is_prompt_tracking_enabled("system"):
            return []

        items = self.memory_kernel.list_by_type(
            MemoryType.EPISODE, scope=MemoryScope.WORKFLOW, limit=500,
        )
        results: list[dict[str, Any]] = []
        for item in items:
            if (
                item.metadata.get("tracker") == self.TAG_PREFIX
                and item.metadata.get("scope") == "system"
                and item.metadata.get("prompt_key") == prompt_key
            ):
                results.append(item.metadata)
                if len(results) >= limit:
                    break
        return results

    # -- 31-22 §6-4: tier-aware prompt variant proposals --------------------

    def propose_prompt_variant(
        self,
        prompt_key: str,
        adaptation_registry: Any = None,
        min_negative_signals: int = 20,
    ) -> dict | None:
        """Propose a prompt variant when enough negative signals have accumulated.

        Behaviour depends on the active learning tier:
          - Tier 0 (logging only): returns info dict without creating a candidate.
          - Tier 1 (``prompt_variant_proposal``): creates a pending
            ``AdaptationCandidate`` in the registry.
          - Tier 2 (``prompt_rewrite``): creates an auto-apply candidate.
        """
        from dan.engine.learning_tiers import is_feature_enabled

        history = self.get_system_prompt_history(prompt_key, limit=500)
        total_count = len(history)
        negatives = [r for r in history if r.get("outcome") is False]
        negative_count = len(negatives)

        if negative_count < min_negative_signals:
            return None

        failure_rate = negative_count / max(total_count, 1)
        sample_failures = negatives[:3]

        result: dict[str, Any] = {
            "prompt_key": prompt_key,
            "negative_count": negative_count,
            "total_count": total_count,
            "failure_rate": failure_rate,
            "proposal_id": None,
        }

        tier1 = is_feature_enabled("prompt_variant_proposal")
        tier2 = is_feature_enabled("prompt_rewrite")

        if tier1 or tier2:
            from dan.engine.adaptation_registry import AdaptationCandidate

            candidate = AdaptationCandidate(
                source="prompt_opt",
                parameter_key=prompt_key,
                description=(
                    f"{negative_count}/{total_count} negative signals "
                    f"(rate={failure_rate:.2f}). "
                    f"Sample failures: {[s.get('output_summary', s.get('content', ''))[:80] for s in sample_failures]}"
                ),
                status="pending",
                auto_apply=tier2,
            )
            if adaptation_registry is not None:
                adaptation_registry.add(candidate)
            result["proposal_id"] = candidate.id

        return result

    # -- 31-22 §6-5: regression detection for prompt variants ---------------

    def check_prompt_regression(
        self,
        prompt_key: str,
        baseline_failure_rate: float,
        window_size: int = 20,
        regression_threshold: float = 0.15,
    ) -> dict | None:
        """Detect regression by comparing recent failure rate against a baseline.

        Returns a regression report dict when the current failure rate exceeds
        ``baseline_failure_rate + regression_threshold``, otherwise ``None``.
        """
        history = self.get_system_prompt_history(prompt_key, limit=window_size)
        if not history:
            return None

        failures = sum(1 for r in history if r.get("outcome") is False)
        current_failure_rate = failures / len(history)

        if current_failure_rate > baseline_failure_rate + regression_threshold:
            return {
                "prompt_key": prompt_key,
                "baseline_failure_rate": baseline_failure_rate,
                "current_failure_rate": current_failure_rate,
                "delta": current_failure_rate - baseline_failure_rate,
                "regression_detected": True,
            }
        return None


# ===================================================================
# 7-2..7-7: Prompt optimization pipeline (PromptAnalyzer,
#   PromptVariantGenerator, PromptABTest)
# ===================================================================


def _avg(records: list[dict[str, Any]], key: str) -> float:
    """Average of a numeric field across records, ignoring missing values."""
    values = [r[key] for r in records if key in r and isinstance(r[key], (int, float))]
    return sum(values) / max(len(values), 1)


def _extract_failure_patterns(failures: list[dict[str, Any]]) -> list[str]:
    """Extract keywords that appear in >=20% of failure output summaries."""
    keywords = ("format", "schema", "timeout", "length", "parse", "invalid", "missing")
    counts: Counter[str] = Counter()
    for f in failures:
        summary = f.get("output_summary", "").lower()
        for kw in keywords:
            if kw in summary:
                counts[kw] += 1
    total = max(len(failures), 1)
    return [f"{kw} ({c}/{len(failures)})" for kw, c in counts.most_common(5) if c / total >= 0.2]


def _extract_success_patterns(successes: list[dict[str, Any]]) -> list[str]:
    """Summarize token/latency averages from successful runs."""
    if not successes:
        return []
    return [
        f"avg_tokens={_avg(successes, 'tokens_used'):.0f}",
        f"avg_latency={_avg(successes, 'latency_ms'):.0f}ms",
    ]


def _simplify_prompt(prompt: str) -> str:
    """Remove duplicate and blank lines from a prompt."""
    lines = prompt.strip().split("\n")
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        key = line.strip().lower()
        if not key:
            continue
        if key not in seen:
            seen.add(key)
            result.append(line)
    return "\n".join(result)


def _is_safe_variant(original: str, variant: str) -> bool:
    """Reject variants that drop user-specified constraints or output schemas.

    Checks that constraint markers (MUST, REQUIRED, schema, DO NOT, NEVER)
    and code-fence blocks present in the original are preserved.
    """
    constraint_markers = ("must", "required", "schema", "do not", "never")
    orig_lower = original.lower()
    var_lower = variant.lower()
    for marker in constraint_markers:
        if marker in orig_lower and marker not in var_lower:
            return False
    orig_blocks = original.count("```")
    var_blocks = variant.count("```")
    if orig_blocks > 0 and var_blocks < orig_blocks:
        return False
    return True


class PromptAnalyzer:
    """Analyze prompt/outcome pairs to identify success/failure correlations.

    Only applies to workflow LLM nodes, not chat system prompts.
    Gated by ``DAN_PROMPT_OPTIMIZATION=1``.
    """

    def __init__(self, prompt_tracker: PromptTracker) -> None:
        self.tracker = prompt_tracker

    def should_analyze(self, node_id: str, threshold: int = 20) -> bool:
        """True if enough data has accumulated for meaningful analysis."""
        if not _is_enabled(_ENV_PROMPT_OPT):
            return False
        history = self.tracker.get_history(node_id, limit=threshold + 1)
        return len(history) >= threshold

    def analyze(self, node_id: str) -> dict[str, Any]:
        """Analyze prompt/outcome pairs and return aggregated statistics."""
        if not _is_enabled(_ENV_PROMPT_OPT):
            return {"node_id": node_id, "total_runs": 0, "success_rate": 0.0}

        history = self.tracker.get_history(node_id, limit=100)
        successes = [h for h in history if h.get("outcome") is True]
        failures = [h for h in history if h.get("outcome") is not True]

        return {
            "node_id": node_id,
            "total_runs": len(history),
            "success_rate": len(successes) / max(len(history), 1),
            "avg_tokens_success": _avg(successes, "tokens_used"),
            "avg_tokens_failure": _avg(failures, "tokens_used"),
            "avg_latency_success": _avg(successes, "latency_ms"),
            "avg_latency_failure": _avg(failures, "latency_ms"),
            "failure_patterns": _extract_failure_patterns(failures),
            "success_patterns": _extract_success_patterns(successes),
        }


class PromptVariantGenerator:
    """Generate prompt variants informed by analysis (heuristic, no LLM for v1).

    Only applies to workflow LLM nodes, not chat system prompts.
    Gated by ``DAN_PROMPT_OPTIMIZATION=1``.
    """

    def generate_variants(
        self,
        current_prompt: str,
        analysis: dict[str, Any],
        count: int = 3,
    ) -> list[dict[str, Any]]:
        """Return up to *count* variant candidates based on *analysis* insights."""
        if not _is_enabled(_ENV_PROMPT_OPT):
            return []

        variants: list[dict[str, Any]] = []

        if (
            "step by step" not in current_prompt.lower()
            and analysis.get("success_rate", 1.0) < 0.8
        ):
            candidate = f"Think step by step.\n\n{current_prompt}"
            if _is_safe_variant(current_prompt, candidate):
                variants.append({
                    "variant_prompt": candidate,
                    "rationale": (
                        f"Success rate is {analysis.get('success_rate', 0):.0%}; "
                        "step-by-step may improve reasoning"
                    ),
                    "strategy": "add_step_by_step",
                })

        failure_patterns = analysis.get("failure_patterns", [])
        if failure_patterns and "format" in str(failure_patterns).lower():
            candidate = (
                f"{current_prompt}\n\n"
                "IMPORTANT: Follow the exact output format specified above."
            )
            if _is_safe_variant(current_prompt, candidate):
                variants.append({
                    "variant_prompt": candidate,
                    "rationale": "Format-related failures detected",
                    "strategy": "reinforce_format",
                })

        avg_tok_s = analysis.get("avg_tokens_success", 0)
        avg_tok_f = analysis.get("avg_tokens_failure", 0)
        if avg_tok_s and avg_tok_f and avg_tok_s < avg_tok_f * 0.8:
            candidate = _simplify_prompt(current_prompt)
            if candidate != current_prompt and _is_safe_variant(current_prompt, candidate):
                variants.append({
                    "variant_prompt": candidate,
                    "rationale": "Successful runs use fewer tokens; simpler prompt may help",
                    "strategy": "simplify",
                })

        return variants[:count]


class PromptABTest:
    """Manage A/B testing of prompt variants for a node.

    Test records are stored as ``WORKING_STATE`` items in the memory kernel
    with deterministic IDs (``ab_test:<node_id>``).
    Only applies to workflow LLM nodes, not chat system prompts.
    Gated by ``DAN_PROMPT_OPTIMIZATION=1``.
    """

    TRACKER_TAG = "ab_test"

    def __init__(self, memory_kernel: Any) -> None:
        self.memory_kernel = memory_kernel

    def create_test(
        self,
        node_id: str,
        original: str,
        variants: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Create an A/B test record. Returns test metadata or None if disabled."""
        if not _is_enabled(_ENV_PROMPT_OPT):
            return None

        test: dict[str, Any] = {
            "tracker": self.TRACKER_TAG,
            "node_id": node_id,
            "status": "active",
            "original_prompt": original,
            "original_runs": 0,
            "original_successes": 0,
            "variants": [
                {
                    "prompt": v["variant_prompt"],
                    "strategy": v.get("strategy", ""),
                    "runs": 0,
                    "successes": 0,
                }
                for v in variants
            ],
            "created_at": time.time(),
        }
        self.memory_kernel.store(MemoryItem(
            id=f"ab_test:{node_id}",
            content=f"A/B test for node {node_id}",
            memory_type=MemoryType.WORKING_STATE,
            scope=MemoryScope.WORKFLOW,
            metadata=test,
        ))
        return test

    def select_variant(self, node_id: str) -> str | None:
        """Randomly select a variant for the next run, or ``None`` to use original."""
        if not _is_enabled(_ENV_PROMPT_OPT):
            return None

        test_item = self.memory_kernel.get(f"ab_test:{node_id}")
        if not test_item:
            return None
        meta = test_item.metadata
        if meta.get("status") != "active":
            return None
        variants = meta.get("variants", [])
        if not variants:
            return None
        # Pool includes original (None) and all variants for fair comparison
        choices: list[str | None] = [None] + [v["prompt"] for v in variants]
        return random.choice(choices)

    def record_outcome(
        self, node_id: str, prompt_used: str | None, success: bool,
    ) -> None:
        """Record the outcome of a variant (or original when *prompt_used* is None)."""
        if not _is_enabled(_ENV_PROMPT_OPT):
            return

        test_item = self.memory_kernel.get(f"ab_test:{node_id}")
        if not test_item:
            return
        meta = test_item.metadata
        if meta.get("status") != "active":
            return

        matched = False
        for variant in meta.get("variants", []):
            if variant["prompt"] == prompt_used:
                variant["runs"] += 1
                if success:
                    variant["successes"] += 1
                matched = True
                break

        if not matched:
            meta["original_runs"] = meta.get("original_runs", 0) + 1
            if success:
                meta["original_successes"] = meta.get("original_successes", 0) + 1

        self.memory_kernel.update(test_item.id, metadata=meta)

    def check_promotion(
        self, node_id: str, min_runs_per_variant: int = 10,
    ) -> dict[str, Any] | None:
        """Return winning variant dict if one beats the original, else ``None``."""
        if not _is_enabled(_ENV_PROMPT_OPT):
            return None

        test_item = self.memory_kernel.get(f"ab_test:{node_id}")
        if not test_item:
            return None
        meta = test_item.metadata
        if meta.get("status") != "active":
            return None

        variants = meta.get("variants", [])
        if not variants:
            return None

        for v in variants:
            if v["runs"] < min_runs_per_variant:
                return None

        orig_runs = meta.get("original_runs", 0)
        if orig_runs < min_runs_per_variant:
            return None

        original_rate = meta.get("original_successes", 0) / max(orig_runs, 1)
        best = max(variants, key=lambda v: v["successes"] / max(v["runs"], 1))
        best_rate = best["successes"] / max(best["runs"], 1)

        if best_rate > original_rate:
            return {"variant": best, "improvement": best_rate - original_rate}
        return None

    def promote(self, node_id: str, variant: dict[str, Any]) -> None:
        """Promote winning variant and store old prompt in provenance for rollback."""
        if not _is_enabled(_ENV_PROMPT_OPT):
            return

        test_item = self.memory_kernel.get(f"ab_test:{node_id}")
        if not test_item:
            return
        meta = test_item.metadata

        self.memory_kernel.store(MemoryItem(
            content=(
                f"Prompt provenance for node {node_id}: "
                f"replaced by variant (strategy={variant.get('strategy', '')})"
            ),
            memory_type=MemoryType.EPISODE,
            scope=MemoryScope.WORKFLOW,
            tags=["prompt_provenance", f"node:{node_id}"],
            metadata={
                "tracker": "prompt_provenance",
                "node_id": node_id,
                "old_prompt": meta.get("original_prompt", ""),
                "new_prompt": variant.get("prompt", ""),
                "strategy": variant.get("strategy", ""),
                "success_rate": variant.get("successes", 0) / max(variant.get("runs", 1), 1),
                "promoted_at": time.time(),
            },
        ))

        meta["status"] = "completed"
        meta["winner"] = variant
        meta["completed_at"] = time.time()
        self.memory_kernel.update(test_item.id, metadata=meta)


# ===================================================================
# 8-1..8-8: ModelOutcomeTracker + ModelRecommender
# ===================================================================

_QUALITY_WEIGHT = 0.6
_COST_WEIGHT = 0.2
_LATENCY_WEIGHT = 0.2
_MIN_EXECUTIONS = 15


class ModelOutcomeTracker:
    """Record per-execution model outcome telemetry.

    Gated by ``DAN_MODEL_LEARNING=1``.
    """

    TAG_PREFIX = "model_outcome"

    def __init__(self, memory_kernel: Any) -> None:
        self.memory_kernel = memory_kernel

    def record(
        self,
        node_id: str,
        node_type: str,
        task_description: str,
        model: str,
        quality_score: float | None = None,
        cost: float = 0.0,
        latency_ms: float = 0.0,
        success: bool = True,
        retry_count: int = 0,
        schema_valid_first_try: bool = True,
    ) -> MemoryItem | None:
        if not _is_enabled(_ENV_MODEL_LEARNING):
            return None

        if quality_score is None:
            quality_score = compute_node_quality(success, retry_count, schema_valid_first_try)

        content = (
            f"Model outcome for node {node_id} ({node_type}): "
            f"model={model}, quality={quality_score:.2f}, "
            f"cost={cost:.6f}, latency={latency_ms:.0f}ms"
        )
        return self.memory_kernel.store(MemoryItem(
            content=content,
            memory_type=MemoryType.EPISODE,
            scope=MemoryScope.WORKFLOW,
            lifecycle=MemoryLifecycle.ACTIVE,
            tags=[self.TAG_PREFIX, f"node:{node_id}", f"model:{model}"],
            metadata={
                "tracker": self.TAG_PREFIX,
                "node_id": node_id,
                "node_type": node_type,
                "task_description": task_description[:300],
                "model": model,
                "quality_score": quality_score,
                "retry_count": retry_count,
                "schema_valid_first_try": schema_valid_first_try,
                "cost": cost,
                "latency_ms": latency_ms,
                "recorded_at": time.time(),
            },
        ))

    def get_node_records(self, node_id: str, limit: int = 200) -> list[dict[str, Any]]:
        items = self.memory_kernel.list_by_type(
            MemoryType.EPISODE, scope=MemoryScope.WORKFLOW, limit=500,
        )
        results: list[dict[str, Any]] = []
        for item in items:
            if (
                item.metadata.get("tracker") == self.TAG_PREFIX
                and item.metadata.get("node_id") == node_id
            ):
                results.append(item.metadata)
                if len(results) >= limit:
                    break
        return results


class ModelRecommender:
    """Suggest best empirical model for a node based on accumulated evidence.

    Override hierarchy: explicit per-node model > empirical recommendation
    > TierPolicy > default.

    Persists recommendations as PREFERENCE items (scope=WORKFLOW).
    """

    PREF_TAG = "model_recommendation"

    def __init__(self, tracker: ModelOutcomeTracker) -> None:
        self.tracker = tracker

    def suggest(self, node_id: str) -> str | None:
        """Best model for this node, or None if insufficient data (< 15 executions)."""
        if not _is_enabled(_ENV_MODEL_LEARNING):
            return None

        records = self.tracker.get_node_records(node_id)
        if len(records) < _MIN_EXECUTIONS:
            return None

        by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            by_model[r["model"]].append(r)

        best_model: str | None = None
        best_score = -1.0

        all_costs = [r["cost"] for r in records if r["cost"] > 0]
        all_latencies = [r["latency_ms"] for r in records if r["latency_ms"] > 0]
        max_cost = max(all_costs) if all_costs else 1.0
        max_latency = max(all_latencies) if all_latencies else 1.0

        for model, model_records in by_model.items():
            avg_quality = sum(r["quality_score"] for r in model_records) / len(model_records)
            avg_cost = sum(r["cost"] for r in model_records) / len(model_records)
            avg_latency = sum(r["latency_ms"] for r in model_records) / len(model_records)

            norm_cost = avg_cost / max_cost if max_cost > 0 else 0.0
            norm_latency = avg_latency / max_latency if max_latency > 0 else 0.0

            score = (
                avg_quality * _QUALITY_WEIGHT
                + (1 - norm_cost) * _COST_WEIGHT
                + (1 - norm_latency) * _LATENCY_WEIGHT
            )

            if score > best_score:
                best_score = score
                best_model = model

        if best_model:
            self._persist_recommendation(node_id, best_model, best_score)

        return best_model

    def _persist_recommendation(
        self, node_id: str, model: str, score: float,
    ) -> None:
        """Store recommendation as PREFERENCE in memory kernel."""
        mk = self.tracker.memory_kernel
        existing = mk.list_by_type(
            MemoryType.PREFERENCE, scope=MemoryScope.WORKFLOW, limit=200,
        )
        for item in existing:
            if (
                item.metadata.get("tracker") == self.PREF_TAG
                and item.metadata.get("node_id") == node_id
            ):
                mk.update(
                    item.id,
                    content=f"Empirical model recommendation for node {node_id}: {model} (score={score:.3f})",
                    metadata={
                        **item.metadata,
                        "model": model,
                        "score": score,
                        "updated_at": time.time(),
                    },
                )
                return

        mk.store(MemoryItem(
            content=f"Empirical model recommendation for node {node_id}: {model} (score={score:.3f})",
            memory_type=MemoryType.PREFERENCE,
            scope=MemoryScope.WORKFLOW,
            lifecycle=MemoryLifecycle.DURABLE,
            tags=[self.PREF_TAG, f"node:{node_id}"],
            metadata={
                "tracker": self.PREF_TAG,
                "node_id": node_id,
                "model": model,
                "score": score,
                "updated_at": time.time(),
            },
        ))

    def get_recommendation(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve persisted recommendation without recalculating."""
        mk = self.tracker.memory_kernel
        items = mk.list_by_type(
            MemoryType.PREFERENCE, scope=MemoryScope.WORKFLOW, limit=200,
        )
        for item in items:
            if (
                item.metadata.get("tracker") == self.PREF_TAG
                and item.metadata.get("node_id") == node_id
            ):
                return item.metadata
        return None


# ===================================================================
# 10-1..10-6: TopologyOutcomeTracker + TopologyAdvisor
# ===================================================================

class TopologyOutcomeTracker:
    """Record (topology_signature, outcome, failure_node, failure_type) per run.

    Reuses ``_compute_signature()`` from ``PatternExtractor``.
    Gated by ``DAN_TOPOLOGY_LEARNING=1``.
    """

    TAG_PREFIX = "topology_outcome"

    def __init__(self, memory_kernel: Any) -> None:
        self.memory_kernel = memory_kernel

    @staticmethod
    def compute_signature(graph_dict: dict[str, Any]) -> str:
        """Produce a topology signature reusing pattern_extractor logic."""
        from dan.engine.pattern_extractor import PatternExtractor

        extractor = PatternExtractor(memory_kernel=None)
        return extractor._compute_signature(graph_dict)

    def record(
        self,
        topology_signature: str,
        outcome: bool,
        failure_node: str | None = None,
        failure_type: str | None = None,
        node_outcomes: list[NodeOutcome] | None = None,
    ) -> MemoryItem | None:
        if not _is_enabled(_ENV_TOPOLOGY_LEARNING):
            return None

        workflow_quality = (
            compute_workflow_quality(node_outcomes) if node_outcomes else (1.0 if outcome else 0.0)
        )

        content = (
            f"Topology run: sig={topology_signature[:80]}, "
            f"outcome={'success' if outcome else 'failure'}, "
            f"quality={workflow_quality:.2f}"
        )
        if failure_node:
            content += f", failure_node={failure_node}"

        return self.memory_kernel.store(MemoryItem(
            content=content,
            memory_type=MemoryType.EPISODE,
            scope=MemoryScope.WORKFLOW,
            lifecycle=MemoryLifecycle.ACTIVE,
            tags=[self.TAG_PREFIX, f"sig:{topology_signature[:40]}"],
            metadata={
                "tracker": self.TAG_PREFIX,
                "topology_signature": topology_signature,
                "outcome": outcome,
                "workflow_quality": workflow_quality,
                "completed_nodes": sum(1 for o in (node_outcomes or []) if o.success),
                "total_nodes": len(node_outcomes or []),
                "failure_node": failure_node,
                "failure_type": failure_type,
                "recorded_at": time.time(),
            },
        ))

    def get_records(self, topology_signature: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        items = self.memory_kernel.list_by_type(
            MemoryType.EPISODE, scope=MemoryScope.WORKFLOW, limit=500,
        )
        results: list[dict[str, Any]] = []
        for item in items:
            if item.metadata.get("tracker") != self.TAG_PREFIX:
                continue
            if topology_signature and item.metadata.get("topology_signature") != topology_signature:
                continue
            results.append(item.metadata)
            if len(results) >= limit:
                break
        return results


class TopologyAdvisor:
    """Suggest structural improvements based on accumulated topology evidence.

    Suggestions are advisory, presented during build sessions.
    """

    _MIN_RECORDS = 5

    def __init__(self, tracker: TopologyOutcomeTracker) -> None:
        self.tracker = tracker

    def suggest(self, graph_dict: dict[str, Any]) -> list[str]:
        """Advisory suggestions based on accumulated evidence."""
        if not _is_enabled(_ENV_TOPOLOGY_LEARNING):
            return []

        records = self.tracker.get_records()
        if len(records) < self._MIN_RECORDS:
            return []

        suggestions: list[str] = []

        failure_nodes = self._identify_failure_hotspots(records)
        node_type_map = self._build_node_type_map(graph_dict)
        has_validator = self._has_validator_after(graph_dict)

        for node_id, stats in failure_nodes.items():
            ntype = node_type_map.get(node_id, "unknown")

            if ntype in ("llm", "llm_generate") and not has_validator.get(node_id, False):
                rate = stats["failure_rate"]
                if rate > 0.3:
                    suggestions.append(
                        f"Consider adding a validator after node '{node_id}' "
                        f"({ntype}) — {rate:.0%} failure rate across "
                        f"{stats['total']} runs"
                    )

            if stats.get("timeout_rate", 0) > 0.2:
                suggestions.append(
                    f"Node '{node_id}' has {stats['timeout_rate']:.0%} timeout "
                    f"rate — consider adding a retry wrapper or increasing timeout"
                )

        sig_stats = self._signature_success_rates(records)
        current_sig = TopologyOutcomeTracker.compute_signature(graph_dict)
        if current_sig in sig_stats:
            rate = sig_stats[current_sig]["success_rate"]
            total = sig_stats[current_sig]["total"]
            if rate < 0.5 and total >= 3:
                suggestions.append(
                    f"This topology pattern has a {rate:.0%} success rate "
                    f"across {total} runs — consider restructuring"
                )

        return suggestions

    def record_suggestion_outcome(
        self,
        suggestion: str,
        accepted: bool,
        outcome_improved: bool | None = None,
    ) -> MemoryItem | None:
        """Track whether a suggestion was accepted and whether it improved outcomes."""
        if not _is_enabled(_ENV_TOPOLOGY_LEARNING):
            return None

        mk = self.tracker.memory_kernel
        return mk.store(MemoryItem(
            content=f"Topology suggestion {'accepted' if accepted else 'rejected'}: {suggestion[:200]}",
            memory_type=MemoryType.EPISODE,
            scope=MemoryScope.WORKFLOW,
            lifecycle=MemoryLifecycle.ACTIVE,
            tags=["topology_suggestion_outcome"],
            metadata={
                "tracker": "topology_suggestion",
                "suggestion": suggestion[:500],
                "accepted": accepted,
                "outcome_improved": outcome_improved,
                "recorded_at": time.time(),
            },
        ))

    # -- Internal analysis helpers ------------------------------------------

    def _identify_failure_hotspots(
        self, records: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Identify nodes that fail frequently across topology records."""
        node_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "failures": 0, "timeouts": 0},
        )
        for r in records:
            fn = r.get("failure_node")
            if not fn:
                continue
            node_stats[fn]["total"] += 1
            if not r.get("outcome"):
                node_stats[fn]["failures"] += 1
            if r.get("failure_type") == "timeout":
                node_stats[fn]["timeouts"] += 1

        result: dict[str, dict[str, Any]] = {}
        for node_id, stats in node_stats.items():
            total = stats["total"]
            if total < 2:
                continue
            result[node_id] = {
                "total": total,
                "failure_rate": stats["failures"] / total,
                "timeout_rate": stats["timeouts"] / total,
            }
        return result

    def _build_node_type_map(self, graph_dict: dict[str, Any]) -> dict[str, str]:
        nodes = graph_dict.get("nodes") or []
        result: dict[str, str] = {}
        for n in nodes:
            nid = n.get("id") if isinstance(n, dict) else getattr(n, "id", "")
            ntype = n.get("node_type", "unknown") if isinstance(n, dict) else getattr(n, "node_type", "unknown")
            result[nid] = ntype
        return result

    def _has_validator_after(self, graph_dict: dict[str, Any]) -> dict[str, bool]:
        """Check if each node has a validator as a direct successor."""
        edges = graph_dict.get("edges") or []
        nodes = graph_dict.get("nodes") or []

        node_type_map = self._build_node_type_map(graph_dict)
        successors: dict[str, list[str]] = defaultdict(list)
        for e in edges:
            src = e.get("source_node_id") if isinstance(e, dict) else getattr(e, "source_node_id", "")
            tgt = e.get("target_node_id") if isinstance(e, dict) else getattr(e, "target_node_id", "")
            if src and tgt:
                successors[src].append(tgt)

        result: dict[str, bool] = {}
        for nid in node_type_map:
            result[nid] = any(
                "validat" in node_type_map.get(s, "").lower()
                for s in successors.get(nid, [])
            )
        return result

    def _signature_success_rates(
        self, records: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        by_sig: dict[str, dict[str, int]] = defaultdict(
            lambda: {"total": 0, "successes": 0},
        )
        for r in records:
            sig = r.get("topology_signature", "")
            if not sig:
                continue
            by_sig[sig]["total"] += 1
            if r.get("outcome"):
                by_sig[sig]["successes"] += 1

        result: dict[str, dict[str, Any]] = {}
        for sig, stats in by_sig.items():
            result[sig] = {
                "total": stats["total"],
                "success_rate": stats["successes"] / stats["total"] if stats["total"] else 0.0,
            }
        return result
