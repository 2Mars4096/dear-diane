"""Accumulates per-node and per-run LLM costs with optional budget enforcement."""

from __future__ import annotations

import logging
from collections import defaultdict

from dan.providers.costs import estimate_cost

logger = logging.getLogger(__name__)


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

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self, node_id: str, model: str, usage: dict[str, int] | None
    ) -> float:
        """Record a single LLM call and return its estimated cost.

        Returns 0.0 when *usage* is ``None`` or the model is unknown.
        """
        if usage is None:
            return 0.0

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

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

    # ------------------------------------------------------------------
    # Serialization (checkpoint support)
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "node_costs": dict(self._node_costs),
            "total_cost": self._total_cost,
            "run_budget": self._run_budget,
            "on_budget_exceeded": self._on_budget_exceeded,
        }

    def restore(self, data: dict) -> None:
        self._node_costs = defaultdict(float, data.get("node_costs", {}))
        self._total_cost = data.get("total_cost", 0.0)
        self._run_budget = data.get("run_budget")
        self._on_budget_exceeded = data.get("on_budget_exceeded", "warn")

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
