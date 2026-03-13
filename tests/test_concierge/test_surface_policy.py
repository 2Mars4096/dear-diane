"""Regression tests for messaging-surface policy normalization."""

from __future__ import annotations

from dan.server.concierge.models import IntentCategory
from dan.server.concierge.policy import ActionPolicy, resolve_policy


def test_resolve_policy_treats_telegram_alias_as_messaging_surface() -> None:
    action, _ = resolve_policy(
        intent=IntentCategory.PLAN,
        action_hints=None,
        text="build a workflow",
        context=None,
        user_profile=None,
        surface="telegram:scholar",
    )

    assert action == ActionPolicy.CONFIRM


def test_resolve_policy_treats_whatsapp_web_as_messaging_surface() -> None:
    action, _ = resolve_policy(
        intent=IntentCategory.PLAN,
        action_hints=None,
        text="handle this for me",
        context=None,
        user_profile=None,
        surface="whatsapp-web",
    )

    assert action == ActionPolicy.CONFIRM
