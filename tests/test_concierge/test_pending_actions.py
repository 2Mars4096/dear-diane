from __future__ import annotations

from types import SimpleNamespace

from dan.server.concierge.models import PendingAction
from dan.server.concierge.pending_actions import resolve_pending_reply
from dan.server.concierge.tier_executors import InstantExecutor


def test_resolve_pending_reply_confirm_contract_is_explicit() -> None:
    pending = PendingAction(
        kind="confirm",
        intent="ask",
        original_text="Explain the pending task",
    )

    resolution = resolve_pending_reply(pending, "yes")

    assert resolution.action == "resume"
    assert resolution.pending_kind == "confirm"
    assert resolution.replay_text == "Explain the pending task"
    assert resolution.metadata["skip_confirm"] is True
    assert resolution.metadata["pending_route_step"] == "approval_confirmation"


def test_resolve_pending_reply_clarify_choice_contract_is_explicit() -> None:
    pending = PendingAction(
        kind="clarify",
        intent="ask",
        original_text="Pick a file to continue",
        options=["/tmp/a.md", "/tmp/b.md"],
    )

    resolution = resolve_pending_reply(pending, "2")

    assert resolution.action == "resume"
    assert resolution.pending_kind == "clarify"
    assert resolution.replay_text == "Pick a file to continue"
    assert resolution.resolved_value == "/tmp/b.md"
    assert resolution.metadata["selected_option"] == 1
    assert resolution.metadata["selected_path"] == "/tmp/b.md"
    assert resolution.metadata["pending_route_step"] == "clarification_choice"


def test_resolve_pending_reply_clarify_retry_contract_is_explicit() -> None:
    pending = PendingAction(
        kind="clarify",
        intent="ask",
        original_text="Pick a file to continue",
        options=["/tmp/a.md", "/tmp/b.md"],
    )

    resolution = resolve_pending_reply(pending, "something else")

    assert resolution.action == "prompt_retry"
    assert resolution.pending_kind == "clarify"
    assert "Please reply with a number" in resolution.response_text
    assert "/tmp/a.md" in resolution.response_text
    assert "/tmp/b.md" in resolution.response_text


def test_instant_executor_pending_resolution_uses_shared_contract() -> None:
    session = SimpleNamespace(
        context=SimpleNamespace(
            project=SimpleNamespace(
                label="Pending Project",
                pending_action=PendingAction(
                    kind="clarify",
                    intent="ask",
                    original_text="Pick a file to continue",
                    options=["/tmp/a.md", "/tmp/b.md"],
                ),
            )
        ),
        msg=SimpleNamespace(text="something else"),
    )

    response = InstantExecutor._resolve_pending(session)

    assert response is not None
    assert response.endswith("Please reply with a number:\n1. /tmp/a.md\n2. /tmp/b.md")
