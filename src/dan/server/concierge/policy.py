from __future__ import annotations

import os
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .autonomy import autonomy_cost_confirm_threshold
from .models import IntentCategory


class ActionPolicy(str, Enum):
    AUTO = "auto"
    CONFIRM = "confirm"
    CLARIFY = "clarify"


class ExecutionPolicy(str, Enum):
    DIRECT = "direct"
    LLM_CONVERSATION = "llm_conversation"
    META_DELEGATE = "meta_delegate"


class ClarificationRequest(BaseModel):
    question: str
    options: list[str] | None = None
    max_rounds: int = 1


class ClarificationResponse(BaseModel):
    text: str
    selected_option: int | None = None


class BehaviorPolicy(BaseModel):
    handler_policies: dict[str, ActionPolicy] = Field(default_factory=dict)
    execution_policies: dict[str, ExecutionPolicy] = Field(default_factory=dict)
    cost_confirm_threshold: float = 1.0


DEFAULT_HANDLER_POLICIES: dict[IntentCategory, ActionPolicy] = {
    IntentCategory.ASK: ActionPolicy.AUTO,
    IntentCategory.AGENT: ActionPolicy.AUTO,
    IntentCategory.PLAN: ActionPolicy.AUTO,
}

DEFAULT_EXECUTION_POLICIES: dict[IntentCategory, ExecutionPolicy] = {
    IntentCategory.ASK: ExecutionPolicy.LLM_CONVERSATION,
    IntentCategory.AGENT: ExecutionPolicy.DIRECT,
    IntentCategory.PLAN: ExecutionPolicy.LLM_CONVERSATION,
}

_DESTRUCTIVE_KEYWORDS = ("delete", "remove", "cancel", "unpublish", "destroy")
_MUTATION_INTENTS = {IntentCategory.PLAN}
_MESSAGING_SURFACES = {"telegram", "whatsapp", "whatsapp-web", "email"}
_SIMPLE_MUTATION_KEYWORDS = ("add", "edit", "modify", "change", "update", "rename", "move", "remove")


def _surface_kind(surface: str) -> str:
    key = (surface or "").strip().lower()
    if ":" in key:
        key = key.split(":", 1)[0]
    return key


def estimate_action_cost(
    intent: IntentCategory,
    text: str,
    context: Any = None,
    action_hints: list[str] | None = None,
) -> float:
    hints = set(action_hints or [])
    if intent == IntentCategory.PLAN and "long_horizon_goal" in hints:
        return 2.0
    if intent == IntentCategory.PLAN:
        lower = text.lower()
        if any(kw in lower for kw in _SIMPLE_MUTATION_KEYWORDS):
            return 0.05
        return 0.50
    return 0.0


def resolve_policy(
    *,
    intent: IntentCategory,
    action_hints: list[str] | None,
    text: str,
    context: Any,
    user_profile: Any,
    surface: str,
    estimated_cost: float = 0.0,
    cost_confirm_threshold: float = 1.0,
    autonomy_resolution: Any | None = None,
) -> tuple[ActionPolicy, ExecutionPolicy]:
    action = DEFAULT_HANDLER_POLICIES[intent]
    execution = DEFAULT_EXECUTION_POLICIES[intent]

    overrides = getattr(user_profile, "action_policy_overrides", {}) or {}
    override = overrides.get(intent.value)
    if override:
        action = ActionPolicy(override)

    lower = text.lower()
    if any(keyword in lower for keyword in _DESTRUCTIVE_KEYWORDS):
        action = ActionPolicy.CONFIRM
    if "publish" in set(action_hints or []):
        action = ActionPolicy.CONFIRM

    if estimated_cost == 0.0:
        estimated_cost = estimate_action_cost(intent, text, context, action_hints)

    env_threshold = os.environ.get("DAN_COST_CONFIRM_THRESHOLD")
    if env_threshold is not None:
        try:
            cost_confirm_threshold = float(env_threshold)
        except ValueError:
            pass
    autonomy_level = getattr(autonomy_resolution, "effective_level", None)
    cost_confirm_threshold = autonomy_cost_confirm_threshold(
        cost_confirm_threshold,
        autonomy_level,
    )

    if estimated_cost > cost_confirm_threshold:
        action = ActionPolicy.CONFIRM

    if _surface_kind(surface) in _MESSAGING_SURFACES and intent in _MUTATION_INTENTS:
        action = ActionPolicy.CONFIRM

    return action, execution


