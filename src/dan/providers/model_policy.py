"""Model policy definitions — strategy-driven model selection.

Each LLMOperator / OrchestratorNode / RouterNode can declare a
``model_policy`` that the ``ModelSelector`` resolves to a concrete model
string at runtime.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


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


ModelPolicy = Annotated[
    Union[StaticPolicy, BudgetPolicy, CascadePolicy, CapabilityPolicy, RouterPolicy],
    Field(discriminator="strategy"),
]
