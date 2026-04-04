from __future__ import annotations

from dan.engine.user_profile import UserProfile
from dan.server.concierge.command_registry import get_default_registry
from dan.server.concierge.timezone_preferences import handle_timezone_command


def test_default_registry_includes_timezone_command() -> None:
    registry = get_default_registry()

    descriptor = registry.get("/timezone")

    assert descriptor is not None
    assert descriptor.handler == "dan.server.concierge.timezone_preferences.handle_timezone_command"
    assert "set" in descriptor.subcommands


def test_timezone_set_normalizes_and_persists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DAN_PROFILE_PATH", str(tmp_path / "profile.json"))
    profile = UserProfile()

    result = handle_timezone_command("/timezone set hong kong time", profile)

    assert "Asia/Hong_Kong" in result
    assert profile.preferred_timezone == "Asia/Hong_Kong"


def test_timezone_clear_removes_saved_preference(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DAN_PROFILE_PATH", str(tmp_path / "profile.json"))
    profile = UserProfile(preferred_timezone="Asia/Hong_Kong")

    result = handle_timezone_command("/timezone clear", profile)

    assert "Cleared saved timezone preference" in result
    assert profile.preferred_timezone == ""
