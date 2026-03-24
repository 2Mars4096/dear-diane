"""Static cost table for LLM API pricing — best-effort estimates."""

from __future__ import annotations

from typing import Any, Protocol

COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    # OpenAI
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4.1": {"prompt": 0.002, "completion": 0.008},
    "gpt-4.1-mini": {"prompt": 0.0004, "completion": 0.0016},
    "gpt-4.1-nano": {"prompt": 0.0001, "completion": 0.0004},
    "o1": {"prompt": 0.015, "completion": 0.06},
    "o3": {"prompt": 0.01, "completion": 0.04},
    "o3-mini": {"prompt": 0.0011, "completion": 0.0044},
    "o4-mini": {"prompt": 0.0011, "completion": 0.0044},
    # Anthropic
    "claude-opus-4": {"prompt": 0.015, "completion": 0.075},
    "claude-sonnet-4": {"prompt": 0.003, "completion": 0.015},
    "claude-sonnet-4-6": {"prompt": 0.003, "completion": 0.015},
    "claude-haiku-3.5": {"prompt": 0.0008, "completion": 0.004},
    "claude-3-5-haiku-20241022": {"prompt": 0.0008, "completion": 0.004},
    # Google
    "gemini-2.0-flash": {"prompt": 0.0001, "completion": 0.0004},
    "gemini-2.0-pro": {"prompt": 0.00125, "completion": 0.005},
    "gemini-2.5-pro": {"prompt": 0.00125, "completion": 0.01},
    "gemini-2.5-flash": {"prompt": 0.00015, "completion": 0.0006},
}


class SeedStore(Protocol):
    """Minimal behavior-store shape needed for seed registration."""

    def register_seed(self, key: str, value: Any) -> None: ...


def register_seed_cost_table(store: SeedStore) -> None:
    """Register cost table as seed default."""
    store.register_seed("models/cost_table", dict(COST_PER_1K_TOKENS))


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    behavior_store: Any = None,
) -> float | None:
    """Estimate cost in USD for a given model and token counts.

    Returns None for unknown models.  If *behavior_store* is provided,
    its ``models/cost_table`` entry is consulted before the module constant.
    """
    cost_table = COST_PER_1K_TOKENS
    if behavior_store is not None:
        stored = behavior_store.get("models/cost_table")
        if isinstance(stored, dict):
            cost_table = stored

    rates = cost_table.get(model)
    if rates is None:
        for key, r in cost_table.items():
            if model.startswith(key):
                rates = r
                break
    if rates is None:
        return None
    return (prompt_tokens * rates["prompt"] + completion_tokens * rates["completion"]) / 1000
