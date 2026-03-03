"""Static cost table for LLM API pricing — best-effort estimates."""

from __future__ import annotations

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


def estimate_cost(
    model: str, prompt_tokens: int, completion_tokens: int
) -> float | None:
    """Estimate cost in USD for a given model and token counts.

    Returns None for unknown models.
    """
    rates = COST_PER_1K_TOKENS.get(model)
    if rates is None:
        for key, r in COST_PER_1K_TOKENS.items():
            if model.startswith(key):
                rates = r
                break
    if rates is None:
        return None
    return (prompt_tokens * rates["prompt"] + completion_tokens * rates["completion"]) / 1000
