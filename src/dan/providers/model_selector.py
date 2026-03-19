"""Model selector — resolves ModelPolicy to a concrete model string."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from dan.providers.model_policy import (
    BudgetPolicy,
    CascadePolicy,
    CapabilityPolicy,
    ModelConstraints,
    RouterPolicy,
    StaticPolicy,
    TierPolicy,
)

_POLICY_ADAPTER = TypeAdapter(
    StaticPolicy | BudgetPolicy | CascadePolicy | CapabilityPolicy | RouterPolicy | TierPolicy
)

if TYPE_CHECKING:
    from dan.providers.capabilities import ModelCapabilityRegistry
    from dan.providers.cost_tracker import CostTracker
    from dan.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

ModelPolicy = StaticPolicy | BudgetPolicy | CascadePolicy | CapabilityPolicy | RouterPolicy | TierPolicy


def _graph_fingerprint(graph) -> str | None:
    if graph is None:
        return None
    cached = getattr(graph, "_tier_fingerprint", None)
    if cached is not None:
        return cached
    node_ids = sorted(n.id for n in graph.nodes)
    edge_ids = sorted(e.id for e in graph.edges)
    fp = f"{len(node_ids)}:{','.join(node_ids)}|{len(edge_ids)}:{','.join(edge_ids)}"
    try:
        graph._tier_fingerprint = fp
    except (AttributeError, TypeError):
        pass
    return fp


@dataclass
class SelectionResult:
    model: str
    tier_result: Any = None
    tier_params: dict[str, Any] = field(default_factory=dict)


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
        self._tier_map_validated: bool = False
        self._cached_scorer: Any = None
        self._cached_scorer_graph_id: str | None = None
        self._cached_scorer_weights_key: tuple[float, float, float] | None = None

    async def select(
        self,
        policy: ModelPolicy,
        node: Any = None,
        context: Any = None,
    ) -> SelectionResult:
        """Resolve *policy* to a :class:`SelectionResult`."""
        strategy = policy.strategy
        tier_result = None
        tier_params: dict[str, Any] = {}

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

        elif strategy == "tier":
            model, tier_result, tier_params = self._select_tier(policy, node, context)

        else:
            raise ValueError(f"Unknown policy strategy: {strategy!r}")

        if policy.constraints is not None:
            self._apply_constraints(model, policy.constraints)
        return SelectionResult(model=model, tier_result=tier_result, tier_params=tier_params)

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

    def _select_tier(
        self,
        policy: TierPolicy,
        node: Any,
        context: Any,
    ) -> tuple[str, Any, dict[str, Any]]:
        """Score the node and resolve a concrete model from the tier map."""
        from dan.providers.tier_defaults import resolve_tier_map, resolve_tier_params
        from dan.providers.tier_scorer import TierScorer

        graph = getattr(context, "graph", None) if context else None
        weights = policy.weights
        graph_fp = _graph_fingerprint(graph)
        weights_key = (weights.difficulty, weights.impact, weights.recoverability) if weights else None
        if (
            self._cached_scorer is not None
            and self._cached_scorer_graph_id == graph_fp
            and self._cached_scorer_weights_key == weights_key
            and graph_fp is not None
        ):
            scorer = self._cached_scorer
        else:
            scorer = TierScorer(graph=graph, weights=weights)
            if graph_fp is not None:
                self._cached_scorer = scorer
                self._cached_scorer_graph_id = graph_fp
                self._cached_scorer_weights_key = weights_key

        tool_count = 0
        if context and hasattr(context, "tool_registry") and context.tool_registry:
            tool_count = len(getattr(context.tool_registry, "_tools", {}))

        result = scorer.score_node(node, tool_count=tool_count)

        config = getattr(context, "config", None) if context else None
        configured_providers = self._detect_providers(config)
        user_map = policy.tier_map or (
            getattr(config, "tier_map", None) if config else None
        )
        tier_map = resolve_tier_map(configured_providers, user_map)

        user_params = policy.tier_params or (
            getattr(config, "tier_params", None) if config else None
        )
        params: dict[str, Any] = {}

        if not self._tier_map_validated:
            self._tier_map_validated = True
            for tier_name, tier_model in tier_map.items():
                try:
                    self._provider_registry.resolve(tier_model)
                except Exception:
                    logger.warning(
                        "Tier '%s' maps to model '%s' which no provider can handle",
                        tier_name,
                        tier_model,
                    )

        model = tier_map.get(result.tier.value)
        if not model:
            default_model = (
                getattr(config, "llm_default_model", "claude-sonnet-4-6")
                if config
                else "claude-sonnet-4-6"
            )
            model = default_model
            logger.warning(
                "Tier %s not in tier map, falling back to %s",
                result.tier.value,
                model,
            )

        try:
            provider_name = self._provider_registry.resolve_name(model)
        except Exception:
            provider_name = configured_providers[0] if configured_providers else "anthropic"
        if provider_name == "default" and configured_providers:
            provider_name = configured_providers[0]

        tier_params_map = resolve_tier_params(provider_name, user_params)
        params = tier_params_map.get(result.tier.value, {})

        return model, result, params

    @staticmethod
    def _detect_providers(config: Any) -> list[str]:
        """Detect which provider ecosystems are configured."""
        if config is None:
            return []
        providers: list[str] = []
        provider_cfg = getattr(config, "providers", {}) or {}
        model_map = getattr(config, "model_provider_map", {}) or {}
        if provider_cfg.get("anthropic") or any(
            k.startswith("claude") for k in model_map
        ):
            providers.append("anthropic")
        if provider_cfg.get("openai") or any(
            k.startswith(("gpt", "o1", "o3", "o4")) for k in model_map
        ):
            providers.append("openai")
        if provider_cfg.get("google") or any(
            k.startswith("gemini") for k in model_map
        ):
            providers.append("google")
        return providers

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
