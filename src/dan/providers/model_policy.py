"""Model policy definitions — strategy-driven model selection.

Each LLMOperator / OrchestratorNode / RouterNode can declare a
``model_policy`` that the ``ModelSelector`` resolves to a concrete model
string at runtime.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


class ModelConstraints(BaseModel):
    """Hard filters applied regardless of strategy."""

    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost: float | None = Field(
        default=None, description="Max cost (USD) for a single call",
    )
    required_provider: str | None = Field(
        default=None, description="Force a specific provider name",
    )


class StaticPolicy(BaseModel):
    """Use a fixed model string — current default behavior."""

    strategy: Literal["static"] = "static"
    model: str
    constraints: ModelConstraints | None = None


class BudgetPolicy(BaseModel):
    """Switch to a cheaper model when budget nears the limit."""

    strategy: Literal["budget"] = "budget"
    preferred_model: str
    fallback_model: str
    max_cost_per_call: float | None = None
    max_cost_per_run: float | None = None
    cost_threshold_model: str | None = Field(
        default=None,
        description="Model to switch to when remaining budget is below threshold",
    )
    constraints: ModelConstraints | None = None


class CascadePolicy(BaseModel):
    """Try models in order; advance to next on qualifying failure."""

    strategy: Literal["cascade"] = "cascade"
    models: list[str] = Field(min_length=1)
    cascade_on: list[str] = Field(
        default_factory=lambda: ["error", "timeout"],
        description="Conditions to try next model: error, timeout, quality_low, cost_high",
    )
    max_attempts: int | None = Field(
        default=None, description="Defaults to len(models)",
    )
    constraints: ModelConstraints | None = None


class CapabilityPolicy(BaseModel):
    """Select a model based on required capabilities."""

    strategy: Literal["capability"] = "capability"
    required_capabilities: list[str] = Field(
        min_length=1,
        description="e.g. code, vision, long_context, json_mode, tool_use",
    )
    prefer: Literal["cheapest", "fastest", "strongest"] = "cheapest"
    constraints: ModelConstraints | None = None


class RouterPolicy(BaseModel):
    """LLM-powered model selection — a routing model chooses per call."""

    strategy: Literal["router"] = "router"
    router_model: str = Field(description="The model that makes the routing decision")
    candidates: list[str] = Field(min_length=2)
    routing_prompt: str | None = Field(
        default=None,
        description="Custom prompt for the routing decision; uses default if None",
    )
    constraints: ModelConstraints | None = None


class TaskTier(str, Enum):
    """Provider-agnostic model tier — maps to a concrete model via tier map."""

    micro = "micro"
    routine = "routine"
    reasoning = "reasoning"
    critical = "critical"

    @classmethod
    def from_score(cls, score: float) -> TaskTier:
        if score < 0.25:
            return cls.micro
        if score < 0.50:
            return cls.routine
        if score < 0.75:
            return cls.reasoning
        return cls.critical


class TierWeights(BaseModel):
    """Weights for the three tier-scoring dimensions.  Must sum to 1.0."""

    difficulty: float = 0.45
    impact: float = 0.35
    recoverability: float = 0.20

    @model_validator(mode="after")
    def _check_sum(self) -> TierWeights:
        if abs(self.difficulty + self.impact + self.recoverability - 1.0) >= 0.01:
            msg = (
                f"TierWeights must sum to 1.0 "
                f"(got {self.difficulty + self.impact + self.recoverability:.4f})"
            )
            raise ValueError(msg)
        return self


class TierPolicy(BaseModel):
    """Automatic tier-based model selection — assigns the right-weight model per call."""

    strategy: Literal["tier"] = "tier"
    tier_map: dict[str, str] | None = None
    tier_params: dict[str, dict[str, Any]] | None = None
    weights: TierWeights | None = None
    constraints: ModelConstraints | None = None


ModelPolicy = Annotated[
    Union[
        StaticPolicy,
        BudgetPolicy,
        CascadePolicy,
        CapabilityPolicy,
        RouterPolicy,
        TierPolicy,
    ],
    Field(discriminator="strategy"),
]
