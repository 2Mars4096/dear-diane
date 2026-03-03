"""Accumulates per-node and per-run LLM costs with optional budget enforcement."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from typing import Any

from dan.providers.costs import estimate_cost

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-node token composition models (Plan 18-4)
# ---------------------------------------------------------------------------


@dataclass
class TokenBreakdown:
    """Decomposed token accounting for a single node execution."""

    system_tokens: int = 0
    user_tokens: int = 0
    assistant_tokens: int = 0
    output_tokens: int = 0
    context_edge_tokens: int = 0
    hyperedge_tokens: int = 0
    memory_tokens: int = 0
    rag_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "system_tokens": self.system_tokens,
            "user_tokens": self.user_tokens,
            "assistant_tokens": self.assistant_tokens,
            "output_tokens": self.output_tokens,
            "context_edge_tokens": self.context_edge_tokens,
            "hyperedge_tokens": self.hyperedge_tokens,
            "memory_tokens": self.memory_tokens,
            "rag_tokens": self.rag_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }


@dataclass
class TokenSaving:
    """A single recorded optimization saving."""

    action: str
    tokens_saved: int = 0
    cost_saved: float = 0.0
    node_id: str = ""
    details: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "tokens_saved": self.tokens_saved,
            "cost_saved": self.cost_saved,
            "node_id": self.node_id,
            "details": self.details,
        }


class CostTracker:
    """Track token costs across nodes and enforce optional run budgets.

    Parameters
    ----------
    run_budget:
        Maximum total cost (USD) for the entire run.  ``None`` = unlimited.
    on_budget_exceeded:
        Action when budget is exceeded:
        * ``"warn"``   – log a warning and continue (default)
        * ``"switch"`` – signal the caller to switch to a cheaper model
        * ``"halt"``   – raise :class:`BudgetExceededError`
    """

    def __init__(
        self,
        run_budget: float | None = None,
        on_budget_exceeded: str = "warn",
    ) -> None:
        if on_budget_exceeded not in ("warn", "switch", "halt"):
            raise ValueError(
                f"on_budget_exceeded must be 'warn', 'switch', or 'halt', "
                f"got {on_budget_exceeded!r}"
            )
        self._node_costs: dict[str, float] = defaultdict(float)
        self._total_cost: float = 0.0
        self._run_budget = run_budget
        self._on_budget_exceeded = on_budget_exceeded
        self._total_prompt_tokens: int = 0
        self._total_cached_tokens: int = 0
        self._total_cache_write_tokens: int = 0
        self._node_cached_tokens: dict[str, int] = defaultdict(int)
        self._node_cache_write_tokens: dict[str, int] = defaultdict(int)
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._semantic_cache_hits: int = 0
        self._cache_tokens_saved: int = 0
        self._cache_cost_saved: float = 0.0
        self._node_breakdowns: dict[str, TokenBreakdown] = {}
        self._savings: list[TokenSaving] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        node_id: str,
        model: str,
        usage: dict[str, int] | None,
        *,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Record a single LLM call and return its estimated cost.

        Returns 0.0 when *usage* is ``None`` or the model is unknown.
        """
        if usage is None:
            return 0.0

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        self._total_prompt_tokens += prompt_tokens
        self._total_cached_tokens += cached_input_tokens
        self._total_cache_write_tokens += cache_write_tokens
        self._node_cached_tokens[node_id] += cached_input_tokens
        self._node_cache_write_tokens[node_id] += cache_write_tokens

        cost = estimate_cost(model, prompt_tokens, completion_tokens)
        if cost is None:
            logger.debug("Unknown cost for model %s — recording 0.0", model)
            return 0.0

        self._node_costs[node_id] += cost
        self._total_cost += cost

        if self.is_over_budget():
            self._handle_budget_exceeded()

        return cost

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def total_cost(self) -> float:
        return self._total_cost

    def node_cost(self, node_id: str) -> float:
        return self._node_costs.get(node_id, 0.0)

    def remaining_budget(self) -> float | None:
        """``None`` when no budget is set; non-negative otherwise."""
        if self._run_budget is None:
            return None
        return max(0.0, self._run_budget - self._total_cost)

    def is_over_budget(self) -> bool:
        if self._run_budget is None:
            return False
        return self._total_cost > self._run_budget

    @property
    def on_budget_exceeded(self) -> str:
        return self._on_budget_exceeded

    def cache_summary(self) -> dict[str, int | float]:
        """Return aggregate cache metrics for the run."""
        total = self._total_prompt_tokens
        total_lookups = self._cache_hits + self._cache_misses
        hit_rate = self._total_cached_tokens / total if total > 0 else 0.0
        return {
            "cached_tokens": self._total_cached_tokens,
            "cache_write_tokens": self._total_cache_write_tokens,
            "cache_hit_rate": hit_rate,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "semantic_cache_hits": self._semantic_cache_hits,
            "lookup_hit_rate": (self._cache_hits / total_lookups) if total_lookups > 0 else 0.0,
            "tokens_saved": self._cache_tokens_saved,
            "cost_saved": self._cache_cost_saved,
        }

    def record_breakdown(self, node_id: str, breakdown: TokenBreakdown) -> None:
        """Store a per-node token composition breakdown."""
        existing = self._node_breakdowns.get(node_id)
        if existing is None:
            self._node_breakdowns[node_id] = breakdown
            return
        for field_name in TokenBreakdown.__dataclass_fields__:
            setattr(
                existing,
                field_name,
                int(getattr(existing, field_name, 0)) + int(getattr(breakdown, field_name, 0)),
            )

    def get_breakdown(self, node_id: str) -> TokenBreakdown | None:
        return self._node_breakdowns.get(node_id)

    def all_breakdowns(self) -> dict[str, dict[str, int]]:
        return {nid: bd.to_dict() for nid, bd in self._node_breakdowns.items()}

    def record_saving(self, saving: TokenSaving) -> None:
        """Append a token-saving event."""
        self._savings.append(saving)

    def all_savings(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._savings]

    def savings_summary(self) -> dict[str, Any]:
        total_tokens = sum(s.tokens_saved for s in self._savings)
        total_cost = sum(s.cost_saved for s in self._savings)
        by_action: dict[str, int] = defaultdict(int)
        for s in self._savings:
            by_action[s.action] += s.tokens_saved
        return {
            "total_tokens_saved": total_tokens,
            "total_cost_saved": total_cost,
            "by_action": dict(by_action),
            "count": len(self._savings),
        }

    def record_cache_result(
        self,
        *,
        hit: bool,
        semantic: bool = False,
        tokens_saved: int = 0,
        cost_saved: float = 0.0,
    ) -> None:
        """Track memoization/semantic cache hit/miss metrics."""
        if hit:
            self._cache_hits += 1
            if semantic:
                self._semantic_cache_hits += 1
            self._cache_tokens_saved += max(0, int(tokens_saved))
            self._cache_cost_saved += max(0.0, float(cost_saved))
        else:
            self._cache_misses += 1

    # ------------------------------------------------------------------
    # Serialization (checkpoint support)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "node_costs": dict(self._node_costs),
            "total_cost": self._total_cost,
            "run_budget": self._run_budget,
            "on_budget_exceeded": self._on_budget_exceeded,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_cached_tokens": self._total_cached_tokens,
            "total_cache_write_tokens": self._total_cache_write_tokens,
            "node_cached_tokens": dict(self._node_cached_tokens),
            "node_cache_write_tokens": dict(self._node_cache_write_tokens),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "semantic_cache_hits": self._semantic_cache_hits,
            "cache_tokens_saved": self._cache_tokens_saved,
            "cache_cost_saved": self._cache_cost_saved,
            "node_breakdowns": {nid: bd.to_dict() for nid, bd in self._node_breakdowns.items()},
            "savings": [s.to_dict() for s in self._savings],
        }

    def restore(self, data: dict) -> None:
        self._node_costs = defaultdict(float, data.get("node_costs", {}))
        self._total_cost = data.get("total_cost", 0.0)
        self._run_budget = data.get("run_budget")
        self._on_budget_exceeded = data.get("on_budget_exceeded", "warn")
        self._total_prompt_tokens = data.get("total_prompt_tokens", 0)
        self._total_cached_tokens = data.get("total_cached_tokens", 0)
        self._total_cache_write_tokens = data.get("total_cache_write_tokens", 0)
        self._node_cached_tokens = defaultdict(int, data.get("node_cached_tokens", {}))
        self._node_cache_write_tokens = defaultdict(int, data.get("node_cache_write_tokens", {}))
        self._cache_hits = data.get("cache_hits", 0)
        self._cache_misses = data.get("cache_misses", 0)
        self._semantic_cache_hits = data.get("semantic_cache_hits", 0)
        self._cache_tokens_saved = data.get("cache_tokens_saved", 0)
        self._cache_cost_saved = data.get("cache_cost_saved", 0.0)
        self._node_breakdowns = {}
        for nid, bd_data in data.get("node_breakdowns", {}).items():
            self._node_breakdowns[nid] = TokenBreakdown(**{k: bd_data.get(k, 0) for k in TokenBreakdown.__dataclass_fields__})
        self._savings = []
        for s_data in data.get("savings", []):
            self._savings.append(TokenSaving(**{k: s_data.get(k, v) for k, v in [("action", ""), ("tokens_saved", 0), ("cost_saved", 0.0), ("node_id", ""), ("details", {})]}))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle_budget_exceeded(self) -> None:
        msg = (
            f"Run budget exceeded: ${self._total_cost:.4f} / "
            f"${self._run_budget:.4f}"
        )
        if self._on_budget_exceeded == "halt":
            raise BudgetExceededError(msg)
        elif self._on_budget_exceeded == "warn":
            logger.warning(msg)
        # "switch" — caller checks is_over_budget() to decide


class BudgetExceededError(RuntimeError):
    """Raised when *on_budget_exceeded='halt'* and the budget is blown."""
