"""Default tier-to-model mappings and per-tier parameter overrides.

Each provider ecosystem ships with a sensible mapping from the four
``TaskTier`` labels to concrete model strings.  When tiers share a model
name (e.g. Anthropic reasoning/critical both use Opus), per-tier
``tier_params`` can differentiate them at call time.
"""

from __future__ import annotations

from typing import Any, Protocol

DEFAULT_TIER_MAPS: dict[str, dict[str, str]] = {
    "anthropic": {
        "micro": "claude-3-5-haiku-20241022",
        "routine": "claude-sonnet-4-6",
        "reasoning": "claude-opus-4",
        "critical": "claude-opus-4",
    },
    "openai": {
        "micro": "gpt-4o-mini",
        "routine": "gpt-4o",
        "reasoning": "o3-mini",
        "critical": "o3",
    },
    "google": {
        "micro": "gemini-2.0-flash",
        "routine": "gemini-2.0-flash",
        "reasoning": "gemini-2.5-pro",
        "critical": "gemini-2.5-pro",
    },
}

DEFAULT_TIER_PARAMS: dict[str, dict[str, dict[str, Any]]] = {
    "anthropic": {
        "critical": {"extended_thinking": True, "max_tokens": 8192},
    },
}

_TIER_KEYS = {"micro", "routine", "reasoning", "critical"}

_NUMERIC_TO_CANONICAL: dict[str, list[str]] = {
    "1": ["micro"],
    "2": ["routine"],
    "3": ["reasoning", "critical"],
}

_PROVIDER_PREFERENCE = ["anthropic", "openai", "google"]


class SeedStore(Protocol):
    """Minimal behavior-store shape needed for tier-map seeding."""

    def register_seed(self, key: str, value: Any) -> None: ...


def normalize_tier_map(raw: dict[str, str] | None) -> dict[str, str] | None:
    """Normalize a user-supplied tier map so all keys are canonical tier names.

    Numeric shorthand is expanded:
      ``"1"`` → ``micro``, ``"2"`` → ``routine``,
      ``"3"`` → ``reasoning`` **and** ``critical`` (unless the caller already
      provided an explicit ``critical`` entry).

    Named canonical keys pass through unchanged.  Returns *None* if *raw*
    is ``None`` or empty.
    """
    if not raw:
        return None

    result: dict[str, str] = {}
    has_explicit_critical = "critical" in raw

    for key, model in raw.items():
        if key in _TIER_KEYS:
            result[key] = model
        elif key in _NUMERIC_TO_CANONICAL:
            for canonical in _NUMERIC_TO_CANONICAL[key]:
                if canonical == "critical" and has_explicit_critical:
                    continue
                result.setdefault(canonical, model)

    return result or None


def register_seed_tier_maps(store: SeedStore) -> None:
    """Register default tier maps as seed defaults."""
    store.register_seed("models/tier_maps", dict(DEFAULT_TIER_MAPS))


def resolve_tier_map(
    configured_providers: list[str],
    user_override: dict[str, str] | None = None,
    behavior_store: Any = None,
) -> dict[str, str]:
    """Resolve a complete tier->model map from provider config and user overrides.

    * If *user_override* covers all four tier keys, return it as-is.
    * Otherwise pick the best-match provider ecosystem (prefer
      anthropic > openai > google when multiple are configured) and
      merge any partial *user_override* on top of the provider defaults.
    * If *behavior_store* is provided, its ``models/tier_maps`` entry
      is used in place of the module-level ``DEFAULT_TIER_MAPS``.
    """
    user_override = normalize_tier_map(user_override) or user_override
    if user_override and _TIER_KEYS <= user_override.keys():
        return user_override

    tier_maps = DEFAULT_TIER_MAPS
    if behavior_store is not None:
        stored = behavior_store.get("models/tier_maps")
        if isinstance(stored, dict):
            tier_maps = stored

    base: dict[str, str] = {}
    for provider in _PROVIDER_PREFERENCE:
        if provider in configured_providers and provider in tier_maps:
            base = dict(tier_maps[provider])
            break

    if not base:
        for provider in configured_providers:
            if provider in tier_maps:
                base = dict(tier_maps[provider])
                break

    if not base:
        fallback = tier_maps.get("anthropic") or DEFAULT_TIER_MAPS["anthropic"]
        base = dict(fallback)

    if user_override:
        base.update(user_override)
    return base


def resolve_tier_params(
    provider: str,
    user_override: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Resolve per-tier parameter overrides for a given provider.

    Merges *user_override* on top of the provider's defaults (if any).
    """
    base: dict[str, dict[str, Any]] = {}
    if provider in DEFAULT_TIER_PARAMS:
        base = {k: dict(v) for k, v in DEFAULT_TIER_PARAMS[provider].items()}

    if user_override:
        for tier, params in user_override.items():
            if tier in base:
                base[tier].update(params)
            else:
                base[tier] = dict(params)
    return base
