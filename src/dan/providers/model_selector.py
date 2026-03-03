"""Model selector — resolves ModelPolicy to a concrete model string."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from dan.providers.model_policy import (
    BudgetPolicy,
    CascadePolicy,
    CapabilityPolicy,
    ModelConstraints,
    RouterPolicy,
    StaticPolicy,
)

_POLICY_ADAPTER = TypeAdapter(
    StaticPolicy | BudgetPolicy | CascadePolicy | CapabilityPolicy | RouterPolicy
)

if TYPE_CHECKING:
    from dan.providers.capabilities import ModelCapabilityRegistry
    from dan.providers.cost_tracker import CostTracker
    from dan.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

ModelPolicy = StaticPolicy | BudgetPolicy | CascadePolicy | CapabilityPolicy | RouterPolicy


class ModelSelector:
    """Resolves a :pydata:`ModelPolicy` to a concrete model string.

    Parameters
    ----------
    provider_registry:
        Used by ``RouterPolicy`` to call the routing LLM.
    cost_tracker:
        Used by ``BudgetPolicy`` to check remaining budget.
    capability_registry:
        Used by ``CapabilityPolicy`` to filter by required capabilities.
    """

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        cost_tracker: CostTracker | None = None,
        capability_registry: ModelCapabilityRegistry | None = None,
    ) -> None:
        self._provider_registry = provider_registry
        self._cost_tracker = cost_tracker
        self._capability_registry = capability_registry

    async def select(
        self,
        policy: ModelPolicy,
        node: Any = None,
        context: Any = None,
    ) -> str:
        """Resolve *policy* to a model name string."""
        strategy = policy.strategy

        if strategy == "static":
            model = policy.model

        elif strategy == "budget":
            model = self._select_budget(policy)

        elif strategy == "cascade":
            model = policy.models[0]

        elif strategy == "capability":
            model = self._select_capability(policy)

        elif strategy == "router":
            model = await self._select_router(policy, node, context)

        else:
            raise ValueError(f"Unknown policy strategy: {strategy!r}")

        if policy.constraints is not None:
            self._apply_constraints(model, policy.constraints)
        return model

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _select_budget(self, policy: BudgetPolicy) -> str:
        if self._cost_tracker is None:
            return policy.preferred_model

        remaining = self._cost_tracker.remaining_budget()
        if remaining is not None and remaining <= 0:
            logger.info(
                "Budget exhausted — falling back to %s", policy.fallback_model,
            )
            return policy.fallback_model

        if self._cost_tracker.is_over_budget():
            return policy.fallback_model

        return policy.preferred_model

    def _select_capability(self, policy: CapabilityPolicy) -> str:
        if self._capability_registry is None:
            raise RuntimeError(
                "CapabilityPolicy requires a ModelCapabilityRegistry, but none was provided"
            )
        candidates = self._capability_registry.filter(
            required=policy.required_capabilities,
            prefer=policy.prefer,
        )
        if not candidates:
            raise RuntimeError(
                f"No model satisfies capabilities: {policy.required_capabilities}"
            )
        return candidates[0]

    async def _select_router(
        self,
        policy: RouterPolicy,
        node: Any = None,
        context: Any = None,
    ) -> str:
        prompt = policy.routing_prompt or self._default_routing_prompt(
            policy.candidates, node,
        )
        provider = self._provider_registry.resolve(policy.router_model)
        result = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            model=policy.router_model,
            temperature=0.0,
        )
        chosen = result.text.strip().strip('"').strip("'")
        if chosen in policy.candidates:
            return chosen
        for c in policy.candidates:
            if c in chosen:
                return c
        logger.warning(
            "Router returned %r which is not in candidates %s — defaulting to first",
            chosen, policy.candidates,
        )
        return policy.candidates[0]

    # ------------------------------------------------------------------
    # Constraint enforcement (advisory — logs warnings)
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_constraints(model: str, constraints: ModelConstraints) -> None:
        if constraints.required_provider:
            logger.debug(
                "Constraint: model %s should use provider %s",
                model, constraints.required_provider,
            )

    # ------------------------------------------------------------------
    # Policy resolution
    # ------------------------------------------------------------------

    def resolve_effective_policy(
        self,
        node: Any,
        engine_config: Any,
    ) -> ModelPolicy:
        """Determine the effective policy for a node.

        Precedence (highest first):
        1. ``node.model_policy``
        2. ``StaticPolicy(model=node.model)`` when *model* is set
        3. ``engine_config.default_model_policy``
        4. ``StaticPolicy(model=engine_config.llm_default_model)``
        """
        node_policy = getattr(node, "model_policy", None)
        if node_policy is not None:
            if isinstance(node_policy, dict):
                node_policy = _POLICY_ADAPTER.validate_python(node_policy)
            return node_policy

        node_model = getattr(node, "model", None)
        if node_model:
            return StaticPolicy(model=node_model)

        engine_policy = getattr(engine_config, "default_model_policy", None)
        if engine_policy is not None:
            return engine_policy

        default_model: str = getattr(
            engine_config, "llm_default_model", "claude-sonnet-4-6",
        )
        return StaticPolicy(model=default_model)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _default_routing_prompt(candidates: list[str], node: Any = None) -> str:
        node_desc = ""
        if node is not None:
            name = getattr(node, "name", "")
            desc = getattr(node, "description", "")
            if name or desc:
                node_desc = f"\nTask: {name}. {desc}\n"

        return (
            f"You are a model router. Choose the best model for the task "
            f"from these candidates: {json.dumps(candidates)}.{node_desc}\n"
            f"Respond with ONLY the model name, nothing else."
        )


# ---------------------------------------------------------------------------
# Cascade handler
# ---------------------------------------------------------------------------


class CascadeHandler:
    """Walk through a :class:`CascadePolicy` model list on qualifying failures."""

    def __init__(self, policy: CascadePolicy) -> None:
        self._models = list(policy.models)
        self._cascade_on: set[str] = set(policy.cascade_on)
        self._max = policy.max_attempts or len(self._models)
        self._idx = 0
        self._attempts = 0

    def current_model(self) -> str:
        return self._models[self._idx]

    def should_cascade(self, error: Exception) -> bool:
        """Return ``True`` if *error* matches a cascade trigger."""
        if self._attempts >= self._max:
            return False
        if self._idx + 1 >= len(self._models):
            return False

        error_type = type(error).__name__.lower()
        error_str = str(error).lower()

        for trigger in self._cascade_on:
            trigger_l = trigger.lower()
            if trigger_l in error_type or trigger_l in error_str:
                return True
        return False

    def advance(self) -> str | None:
        """Move to the next model and return its name, or ``None`` if exhausted."""
        self._attempts += 1
        if self._idx + 1 >= len(self._models):
            return None
        self._idx += 1
        return self._models[self._idx]
