from __future__ import annotations

from types import SimpleNamespace

from dan.server.concierge.autonomy import (
    AutonomyPreference,
    AutonomySource,
    AutonomyResolution,
    build_autonomy_announcement,
    resolve_autonomy,
)


def _triage(*, intent: str = "agent", target: str = "file") -> SimpleNamespace:
    return SimpleNamespace(
        intent=intent,
        route=SimpleNamespace(target=target),
    )


def test_resolve_autonomy_auto_infers_aggressive_for_clear_directive() -> None:
    resolution = resolve_autonomy(
        text="Please implement and test this patch.",
        triage=_triage(),
        surface="cli",
        last_effective_level="balanced",
    )

    assert resolution.preferred_level == AutonomyPreference.AUTO.value
    assert resolution.effective_level == AutonomyPreference.AGGRESSIVE.value
    assert resolution.source == AutonomySource.INFERRED.value
    assert resolution.reason == "clear low-risk directive"
    assert resolution.announce_change is True


def test_resolve_autonomy_auto_infers_careful_for_risky_wording() -> None:
    resolution = resolve_autonomy(
        text="Delete the generated file and post the results.",
        triage=_triage(),
        surface="cli",
        last_effective_level="aggressive",
    )

    assert resolution.effective_level == AutonomyPreference.CAREFUL.value
    assert resolution.source == AutonomySource.INFERRED.value
    assert resolution.reason == "risky or irreversible wording"
    assert resolution.announce_change is True


def test_resolve_autonomy_same_effective_level_does_not_reannounce() -> None:
    resolution = resolve_autonomy(
        text="Delete the generated file and post the results.",
        triage=_triage(),
        surface="cli",
        last_effective_level="careful",
    )

    assert resolution.effective_level == AutonomyPreference.CAREFUL.value
    assert resolution.announce_change is False


def test_build_autonomy_announcement_only_emits_when_requested() -> None:
    silent = AutonomyResolution(
        preferred_level="auto",
        effective_level="aggressive",
        source="inferred",
        reason="clear low-risk directive",
        announce_change=False,
    )
    announced = silent.model_copy(update={"announce_change": True})

    assert build_autonomy_announcement(silent) == ""
    assert build_autonomy_announcement(announced) == (
        "Autonomy: switched to `aggressive` (clear low-risk directive)."
    )
