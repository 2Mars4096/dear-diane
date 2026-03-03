"""Static model-capability registry and latency tiers."""

from __future__ import annotations

from dan.providers.costs import COST_PER_1K_TOKENS

# ---------------------------------------------------------------------------
# Static capability data
# ---------------------------------------------------------------------------

CAPABILITIES: dict[str, set[str]] = {
    # OpenAI
    "gpt-4o": {"code", "vision", "tool_use", "json_mode", "long_context"},
    "gpt-4o-mini": {"code", "tool_use", "json_mode"},
    "o1": {"code", "reasoning"},
    "o3-mini": {"code", "reasoning"},
    # Anthropic
    "claude-opus-4": {"code", "vision", "tool_use", "long_context"},
    "claude-sonnet-4-6": {"code", "vision", "tool_use", "json_mode", "long_context"},
    "claude-3-5-haiku-20241022": {"code", "tool_use"},
    # Google
    "gemini-2.0-flash": {"code", "vision", "tool_use", "json_mode"},
    "gemini-2.5-pro": {
        "code", "vision", "tool_use", "json_mode", "long_context", "reasoning",
    },
}

LATENCY_TIER: dict[str, str] = {
    # fast
    "gpt-4o-mini": "fast",
    "claude-3-5-haiku-20241022": "fast",
    "gemini-2.0-flash": "fast",
    # medium
    "gpt-4o": "medium",
    "claude-sonnet-4-6": "medium",
    "o3-mini": "medium",
    # slow
    "claude-opus-4": "slow",
    "o1": "slow",
    "gemini-2.5-pro": "slow",
}

_LATENCY_RANK = {"fast": 0, "medium": 1, "slow": 2}


def _avg_cost(model: str) -> float:
    """Average per-1K-token cost (prompt + completion) / 2.  Falls back to inf."""
    rates = COST_PER_1K_TOKENS.get(model)
    if rates is None:
        return float("inf")
    return (rates["prompt"] + rates["completion"]) / 2


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ModelCapabilityRegistry:
    """Query and filter models by capability, latency, and cost."""

    def __init__(self) -> None:
        self._capabilities: dict[str, set[str]] = dict(CAPABILITIES)
        self._latency: dict[str, str] = dict(LATENCY_TIER)

    def register(
        self, model: str, capabilities: set[str], latency: str = "medium",
    ) -> None:
        """Add or overwrite a model's capability set and latency tier."""
        self._capabilities[model] = set(capabilities)
        self._latency[model] = latency

    def get_capabilities(self, model: str) -> set[str]:
        """Return capability set for *model*, or empty set if unknown."""
        return set(self._capabilities.get(model, set()))

    def get_latency(self, model: str) -> str:
        return self._latency.get(model, "medium")

    def filter(
        self,
        required: list[str],
        prefer: str = "cheapest",
    ) -> list[str]:
        """Return models that have **all** *required* capabilities, sorted.

        Parameters
        ----------
        prefer:
            ``"cheapest"``  – ascending average token cost
            ``"fastest"``   – ascending latency tier then cost
            ``"strongest"`` – descending capability count, then slowest tier
                              (proxy for strongest), then cost
        """
        req = set(required)
        matches = [
            m for m, caps in self._capabilities.items() if req <= caps
        ]

        if prefer == "cheapest":
            matches.sort(key=lambda m: (_avg_cost(m), _LATENCY_RANK.get(self._latency.get(m, "medium"), 1)))
        elif prefer == "fastest":
            matches.sort(key=lambda m: (_LATENCY_RANK.get(self._latency.get(m, "medium"), 1), _avg_cost(m)))
        elif prefer == "strongest":
            matches.sort(
                key=lambda m: (
                    -len(self._capabilities.get(m, set())),
                    -_LATENCY_RANK.get(self._latency.get(m, "medium"), 1),
                    _avg_cost(m),
                ),
            )
        return matches
